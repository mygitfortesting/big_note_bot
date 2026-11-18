"""
Хендлеры управления колонками листа заметок.

Добавление, удаление, переименование и изменение порядка пользовательских
колонок. Защита ``ID``/``SOURCE``/``TITLE`` (в начале) и ``COMMENT`` (в конце)
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
    ID_HEADER,
    SOURCE_HEADER,
    TITLE_HEADER,
    ColumnService,
)
from services.telegram_io import (
    ack_callback,
    alert,
    require_from_user,
    send_error_message,
    show_main_menu,
    status_message,
)
from services.text_utils import escape_html, with_min_width

logger = logging.getLogger(__name__)

router = Router(name="columns")

#: Колонки, которые нельзя переименовывать (удаление запрещает ColumnService).
LOCKED_FOR_RENAME = {ID_HEADER, SOURCE_HEADER, TITLE_HEADER}


class ColumnStates(StatesGroup):
    """Состояния ввода сценариев управления колонками."""

    wait_new_name = State()  # имя новой колонки
    wait_rename = State()  # новое имя существующей колонки
    wait_order = State()  # новый порядок колонок цифрами


def _service(
    event: types.Message | types.CallbackQuery,
) -> ColumnService | None:
    """Сервис колонок листа пользователя события."""
    user = require_from_user(event)
    if user is None:
        return None
    return ColumnService(user.id)


def _editable_columns(svc: ColumnService) -> list[tuple[int, str]]:
    """
    (номер в шапке, имя) только пользовательских колонок.

    Закреплённые ``ID``/``SOURCE``/``TITLE`` (в начале) и ``COMMENT``
    (в конце) в список не попадают — они упоминаются в тексте подсказкой.
    """
    headers = svc.headers()
    return [
        (col_index, headers[col_index - 1].strip())
        for col_index in svc.middle_indexes(headers)
        if headers[col_index - 1].strip()
    ]


def _columns_text(columns: list[tuple[int, str]]) -> str:
    """Текст экрана: нумерованный список пользовательских колонок."""
    if not columns:
        return buttons.COLUMNS_PROMPT
    lines = [
        f"{position}. <code>{escape_html(name)}</code>"
        for position, (_, name) in enumerate(columns, start=1)
    ]
    return buttons.COLUMNS_PROMPT + "\n\n" + "\n".join(lines)


async def show_columns(
    event: types.Message | types.CallbackQuery,
    state: FSMContext | None = None,
) -> None:
    """Меню «📋 Columns»: нумерованный список и кнопки-номера."""
    svc = _service(event)
    if svc is None:
        return
    try:
        columns = await asyncio.to_thread(_editable_columns, svc)
    except Exception as e:
        await send_error_message(event, e)
        return
    if state is not None:
        await state.update_data(
            column_list=[col_index for col_index, _ in columns],
            column_names=[name for _, name in columns],
        )
    text = _columns_text(columns)
    await show_main_menu(event, text, keyboards.columns_menu(len(columns)))


async def _column_at(
    callback: types.CallbackQuery, state: FSMContext, position: int
) -> tuple[int, str] | None:
    """(номер в шапке, имя) колонки из сохранённого списка или None."""
    data = await state.get_data()
    indexes = data.get("column_list") or []
    names = data.get("column_names") or []
    if not 0 <= position < len(indexes):
        await alert(callback, "⚠️ This column no longer exists.")
        return None
    name = str(names[position]) if position < len(names) else ""
    return int(indexes[position]), name


@router.callback_query(F.data == Cb.MENU_COLUMNS)
async def cb_columns_menu(callback: types.CallbackQuery, state: FSMContext) -> None:
    await ack_callback(callback)
    await state.clear()
    await show_columns(callback, state)


# --- Меню выбранной колонки ---


def _show_column_state(
    svc: ColumnService, col_index: int, name: str
) -> tuple[str, types.InlineKeyboardMarkup]:
    """Текст и клавиатура меню одной колонки (без обращения к сети)."""
    can_delete = col_index not in svc.protected_columns()
    text = with_min_width(buttons.COLUMN_VIEW_PROMPT.format(field=escape_html(name)))
    markup = keyboards.column_actions(col_index, can_delete)
    return text, markup


async def _open_column(
    callback: types.CallbackQuery, state: FSMContext, position: int
) -> None:
    """Открывает меню колонки по позиции в сохранённом списке."""
    saved = await _column_at(callback, state, position)
    if saved is None:
        return
    col_index, name = saved
    svc = _service(callback)
    if svc is None:
        return
    try:
        text, markup = await asyncio.to_thread(_show_column_state, svc, col_index, name)
    except Exception as e:
        await send_error_message(callback, e)
        return
    await show_main_menu(callback, text, markup)


@router.callback_query(F.data.startswith(f"{Cb.COL_VIEW}:"))
async def cb_column_view(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Нажатие на номер: меню действий выбранной колонки."""
    await ack_callback(callback)
    position = Cb.parse_col(callback.data or "", Cb.COL_VIEW)
    if position is None:
        return
    await _open_column(callback, state, position)


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
        await show_columns(message, state)
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
    await show_columns(callback, state)


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
    """Переименовывает выбранную колонку (ID/SOURCE/TITLE отклоняются)."""
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
        await show_columns(message, state)
    except ValueError as e:
        await status_message(message, f"🔒 {escape_html(str(e))}")
    except Exception as e:
        await send_error_message(message, e)


# --- Порядок ---


def _ordered_text(svc: ColumnService) -> str:
    """
    Нумерованный список изменяемых колонок для ввода порядка.

    В конце — готовая для копирования строка текущих номеров, чтобы
    пользователь поменял в ней цифры местами и отправил обратно.
    """
    columns = _editable_columns(svc)
    lines = [
        f"{position}. <code>{escape_html(name)}</code>"
        for position, (_, name) in enumerate(columns, start=1)
    ]
    current = " ".join(str(number) for number in range(1, len(columns) + 1))
    lines.append(f"<code>{current}</code>")
    return buttons.COLUMN_ORDER_PROMPT + "\n\n" + "\n\n Copy: ".join(lines)


@router.callback_query(F.data == Cb.COL_REORDER)
async def cb_column_reorder(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«🔢 Change order»: просим новый порядок номеров через пробел."""
    await ack_callback(callback)
    svc = _service(callback)
    if svc is None:
        return
    try:
        text = await asyncio.to_thread(_ordered_text, svc)
        total = len(await asyncio.to_thread(_editable_columns, svc))
    except Exception as e:
        await send_error_message(callback, e)
        return
    if total < 2:
        await alert(callback, "⚠️ There is nothing to reorder yet.")
        return
    await state.set_state(ColumnStates.wait_order)
    await show_main_menu(callback, text, keyboards.columns_cancel())


@router.callback_query(ColumnStates.wait_order, F.data == Cb.CANCEL_INPUT)
async def cb_cancel_column_order(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    """«✖️ Cancel» ввода порядка — возврат к списку «📋 Columns»."""
    await ack_callback(callback)
    await state.clear()
    await show_columns(callback, state)


def _parse_order(text: str, total: int) -> list[int] | None:
    """Разбирает «2 1 3» в список номеров; None — ввод неверный."""
    parts = (text or "").replace(",", " ").split()
    if len(parts) != total or not all(part.isdigit() for part in parts):
        return None
    numbers = [int(part) for part in parts]
    if sorted(numbers) != list(range(1, total + 1)):
        return None
    return numbers


@router.message(ColumnStates.wait_order, F.text)
async def got_column_order(message: types.Message, state: FSMContext) -> None:
    """Новый порядок колонок цифрами через пробел."""
    svc = _service(message)
    if svc is None:
        return
    try:
        total = len(await asyncio.to_thread(_editable_columns, svc))
    except Exception as e:
        await send_error_message(message, e)
        return
    order = _parse_order(message.text or "", total)
    if order is None:
        example = " ".join(str(number) for number in range(1, total + 1))
        await status_message(
            message,
            buttons.COLUMN_ORDER_BAD_INPUT.format(low=1, high=total, example=example),
        )
        return
    try:
        await asyncio.to_thread(svc.reorder_middle, order)
    except ValueError as e:
        await status_message(message, f"⚠️ {escape_html(str(e))}")
        return
    except Exception as e:
        await send_error_message(message, e)
        return
    await state.clear()
    await status_message(message, buttons.COLUMN_ORDER_SAVED)
    await show_columns(message, state)


__all__ = [
    "ColumnStates",
    "router",
    "show_columns",
]
