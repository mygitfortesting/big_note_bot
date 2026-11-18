"""
Хендлеры управления колонками листа заметок.

Добавление, удаление, переименование и изменение порядка пользовательских
колонок. Защита ``ID``/``SOURCE``/``NAME`` (в начале) и ``COMMENT`` (в конце)
живёт в ``ColumnService``; здесь только UI и обработка отказов — пользователь
получает всплывающее объяснение вместо исключения.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from services import buttons, keyboards
from services.callback import Cb
from services.sheets import (
    COMMENT_HEADER,
    ID_HEADER,
    LEADING_HEADERS,
    NAME_HEADER,
    SOURCE_HEADER,
    ColumnService,
    comment_column_index,
)
from services.telegram_io import (
    ack_callback,
    alert,
    require_from_user,
    send_error_message,
    show_main_menu,
    status_message,
)
from services.text_utils import escape_html

logger = logging.getLogger(__name__)

router = Router(name="columns")

#: Колонки, которые нельзя переименовывать (удаление запрещает ColumnService).
LOCKED_FOR_RENAME = {ID_HEADER, SOURCE_HEADER, NAME_HEADER}


class ColumnStates(StatesGroup):
    """Состояния ввода сценариев управления колонками."""

    wait_new_name = State()  # имя новой колонки
    wait_rename = State()  # новое имя существующей колонки


def _service(
    event: types.Message | types.CallbackQuery,
) -> ColumnService | None:
    """Сервис колонок листа пользователя события."""
    user = require_from_user(event)
    if user is None:
        return None
    return ColumnService(user.id)


def _columns(svc: ColumnService) -> list[tuple[int, str, bool]]:
    """
    (номер, имя, можно ли переименовать?) для шапки.

    Меню показывает кнопку переименования только для тех колонок, где флаг
    ``True``. Удаление отдельно закрыто в ``ColumnService``: ``SCREEN`` тоже
    удалить нельзя, хотя имя у неё менять разрешено.
    """
    result: list[tuple[int, str, bool]] = []
    for index, header in enumerate(svc.headers(), start=1):
        upper = header.strip().upper()
        result.append((index, header, upper not in LOCKED_FOR_RENAME))
    return result


async def show_columns(event: types.Message | types.CallbackQuery) -> None:
    """Меню «📋 Columns»: список колонок и действия над ними."""
    svc = _service(event)
    if svc is None:
        return
    try:
        columns = await asyncio.to_thread(_columns, svc)
    except Exception as e:
        await send_error_message(event, e)
        return
    hints = ", ".join(LEADING_HEADERS)
    text = f"{buttons.COLUMNS_PROMPT}\n\n<b>Fixed:</b> {hints} → {COMMENT_HEADER}"
    await show_main_menu(event, text, keyboards.columns_menu(columns))


@router.callback_query(F.data == Cb.MENU_COLUMNS)
async def cb_columns_menu(callback: types.CallbackQuery, state: FSMContext) -> None:
    await ack_callback(callback)
    await state.clear()
    await show_columns(callback)


# --- Добавление ---


@router.callback_query(F.data == Cb.COL_ADD)
async def cb_column_add(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Кнопка «➕ Add column»: спрашиваем имя новой колонки."""
    await ack_callback(callback)
    await state.set_state(ColumnStates.wait_new_name)
    await show_main_menu(callback, buttons.COLUMN_ADD_PROMPT, keyboards.back_to_main())


@router.message(ColumnStates.wait_new_name, F.text)
async def got_new_column_name(message: types.Message, state: FSMContext) -> None:
    """Новая колонка вставляется перед COMMENT (это делает ColumnService)."""
    name = (message.text or "").strip()
    await state.clear()
    svc = _service(message)
    if svc is None or not name:
        await status_message(message, "⚠️ Column name must not be empty.")
        return
    try:
        await asyncio.to_thread(svc.add, name)
        await status_message(message, buttons.NOTE_SAVED)
        await show_columns(message)
    except Exception as e:
        await send_error_message(message, e)


# --- Удаление ---


@router.callback_query(F.data.startswith(f"{Cb.COL_DEL}:"))
async def cb_column_delete(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Удаляет пользовательскую колонку; служебные не трогаются."""
    await ack_callback(callback)
    col_index = Cb.parse_col(callback.data or "", Cb.COL_DEL)
    if col_index is None:
        return
    svc = _service(callback)
    if svc is None:
        return
    try:
        await asyncio.to_thread(svc.delete, col_index)
    except ValueError as e:
        await alert(callback, f"🔒 {escape_html(str(e))}")
        return
    except Exception as e:
        await send_error_message(callback, e)
        return
    await show_columns(callback)


# --- Переименование ---


@router.callback_query(F.data.startswith(f"{Cb.COL_RENAME}:"))
async def cb_column_rename(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Спрашиваем новое имя для пользовательской колонки."""
    await ack_callback(callback)
    col_index = Cb.parse_col(callback.data or "", Cb.COL_RENAME)
    if col_index is None:
        return
    svc = _service(callback)
    if svc is None:
        return
    headers = await asyncio.to_thread(svc.headers)
    if not 1 <= col_index <= len(headers):
        await alert(callback, "⚠️ This column no longer exists.")
        return
    field = headers[col_index - 1]
    if field.strip().upper() in LOCKED_FOR_RENAME:
        await alert(callback, f"🔒 {field} is a service column.")
        return
    await state.set_state(ColumnStates.wait_rename)
    await state.update_data(col_index=col_index)
    await show_main_menu(
        callback,
        buttons.COLUMN_RENAME_PROMPT.format(field=escape_html(field)),
        keyboards.back_to_main(),
    )


@router.message(ColumnStates.wait_rename, F.text)
async def got_renamed_column(message: types.Message, state: FSMContext) -> None:
    """Переименовывает выбранную колонку (ID/SOURCE/NAME отклоняются)."""
    data = await state.get_data()
    col_index = int(data.get("col_index", 0) or 0)
    name = (message.text or "").strip()
    await state.clear()
    svc = _service(message)
    if svc is None or not col_index:
        return
    try:
        await asyncio.to_thread(svc.rename, col_index, name)
        await status_message(message, buttons.NOTE_SAVED)
        await show_columns(message)
    except ValueError as e:
        await status_message(message, f"🔒 {escape_html(str(e))}")
    except Exception as e:
        await send_error_message(message, e)


# --- Порядок ---


@router.callback_query(F.data.startswith(f"{Cb.COL_MOVE}:"))
async def cb_column_move(callback: types.CallbackQuery, state: FSMContext) -> None:
    """
    Сдвиг пользовательской колонки влево/вправо.

    Новый порядок формируется здесь и передаётся в ``ColumnService.reorder``,
    который сам проверит инварианты: ID/SOURCE/NAME в начале, COMMENT в конце.
    """
    await ack_callback(callback)
    parsed = Cb.parse_col_move(callback.data or "")
    if parsed is None:
        return
    col_index, direction = parsed
    if direction not in ("left", "right"):
        return
    svc = _service(callback)
    if svc is None:
        return
    try:
        new_order = await asyncio.to_thread(_shifted_order, svc, col_index, direction)
        await asyncio.to_thread(svc.reorder, new_order)
    except ValueError as e:
        await alert(callback, f"🔒 {escape_html(str(e))}")
        return
    except Exception as e:
        await send_error_message(callback, e)
        return
    await show_columns(callback)


def _shifted_order(svc: ColumnService, col_index: int, direction: str) -> list[int]:
    """
    Порядок колонок после сдвига одной из них.

    Служебные колонки (первые три и последняя) не двигаются: попытка
    сдвинуть их сразу отклоняется, чтобы не дёргать Sheets ради ошибки.
    """
    headers = svc.headers()
    width = len(headers)
    if not 1 <= col_index <= width:
        raise ValueError("This column no longer exists.")
    header = headers[col_index - 1].strip().upper()
    first_free = len(LEADING_HEADERS) + 1
    last_free = width
    comment_col = comment_column_index(headers)
    if comment_col is not None:
        last_free = width - 1
    if col_index < first_free or col_index > last_free:
        header = headers[col_index - 1]
        raise ValueError(f"{header} is a service column and stays.")
    target = col_index - 1 if direction == "left" else col_index + 1
    if target < first_free or target > last_free:
        raise ValueError("This column is already at the edge.")
    order = list(range(1, width + 1))
    left, right = order[col_index - 1], order[target - 1]
    order[col_index - 1], order[target - 1] = right, left
    return order


__all__ = [
    "ColumnStates",
    "router",
    "show_columns",
]
