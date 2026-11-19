"""
Мастер заполнения новой заметки: выбор источника, ввод TITLE, пошаговое
заполнение колонок и кнопки «⏭ Next» / «✅ Done» / «➕ Add another note».

Модуль сам собирает ``wizard_router``. Карточку заметки открывает
``handlers.notes_handler.show_note`` (импорт внутри функций — без циклов).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from handlers.notes_common import (
    SCREEN_HEADER_UPPER,
    WIZARD_SKIP_HEADERS,
    NoteContext,
    NoteStates,
    clean_value,
    note_context,
    repo_of,
)
from services import buttons, keyboards
from services.callback import Cb, Cmd
from services.sheets import NoteRepository, title_column_index
from services.telegram_io import (
    ack_callback,
    send_error_message,
    show_main_menu,
    status_message,
)
from services.text_utils import escape_html, with_min_width

logger = logging.getLogger(__name__)

wizard_router = Router(name="wizard")


async def ask_note_source(
    event: types.Message | types.CallbackQuery, state: FSMContext | None = None
) -> None:
    """
    Шаг 1 добавления заметки: выбор источника.

    Показываем кнопки всех существующих SOURCE и кнопку «➕ Новый источник».
    """
    repo = repo_of(event)
    if repo is None:
        return
    if state is not None:
        await state.clear()
    try:
        sources = await asyncio.to_thread(repo.list_sources)
        if not sources:
            await ask_new_source(event, state)
            return
        markup = keyboards.sources(sources, prefix=Cb.SRC_SELECT, with_new=True)
        await show_main_menu(event, buttons.NEW_NOTE_PROMPT, markup)
    except Exception as e:
        # Логируем стек: без него упавший сценарий выглядит как «бот молчит»
        logger.exception("Could not show sources for /add_note")
        await send_error_message(event, e)


async def ask_new_source(
    event: types.Message | types.CallbackQuery, state: FSMContext | None = None
) -> None:
    """Шаг 1 «🗂 Add source»: показываем список существующих и ждём имя."""
    if state is not None:
        await state.set_state(NoteStates.wait_source)
    repo = repo_of(event)
    if repo is None:
        return
    try:
        sources = await asyncio.to_thread(repo.list_sources)
    except Exception as e:
        # Список источников — только подсказка: экран всё равно открывается.
        logger.warning("Could not list sources for /add_source: %s", e)
        sources = []
    existing_block = ""
    if sources:
        codes = ", ".join(f"<code>{escape_html(source)}</code>" for source in sources)
        existing_block = f"Existing: {codes}\n"

    text = buttons.NEW_SOURCE_PROMPT.format(existing=f"{existing_block}\n")
    await show_main_menu(event, text, keyboards.new_source_cancel())


async def ask_note_title(
    event: types.Message | types.CallbackQuery, state: FSMContext, source: str
) -> None:
    """Шаг 2: просим TITLE заметки для выбранного/нового источника."""
    await state.set_state(NoteStates.wait_title)
    await state.update_data(source=source)
    await show_main_menu(
        event,
        buttons.NOTE_TITLE_PROMPT.format(source=escape_html(source)),
        keyboards.new_source_cancel(),
    )


@wizard_router.message(Command(Cmd.ADD_NOTE))
async def cmd_add_note(message: types.Message, state: FSMContext) -> None:
    """/add_note: выбор источника (кнопки) или «➕ Новый источник»."""
    await ask_note_source(message, state)


@wizard_router.message(Command(Cmd.ADD_SOURCE))
async def cmd_add_source(message: types.Message, state: FSMContext) -> None:
    """/add_source: имя нового источника, сразу затем — первая заметка."""
    await ask_new_source(message, state)


@wizard_router.callback_query(F.data == Cb.NOTE_ADD)
async def cb_add_note(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«➕ Add note»: сначала кнопки существующих источников + «Новый источник»."""
    await ack_callback(callback)
    await ask_note_source(callback, state)


@wizard_router.callback_query(F.data == Cb.SRC_ADD)
async def cb_add_source(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«🗂 Add source»: ввод имени нового источника."""
    await ack_callback(callback)
    await ask_new_source(callback, state)


@wizard_router.callback_query(F.data.startswith(f"{Cb.SRC_SELECT}:"))
async def cb_select_source(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Выбран существующий источник при добавлении заметки."""
    await ack_callback(callback)
    source = Cb.parse_source(callback.data or "", Cb.SRC_SELECT)
    if not source:
        return
    await ask_note_title(callback, state, source)


@wizard_router.message(NoteStates.wait_title, F.text)
async def got_note_title(message: types.Message, state: FSMContext) -> None:
    """Создаём заметку выбранного источника и запускаем мастер колонок."""
    data = await state.get_data()
    source = str(data.get("source", "")).strip()
    title = (message.text or "").strip()
    await state.clear()
    if not source:
        await status_message(message, "⚠️ Source is lost, start again.")
        await _main_menu()(message)
        return
    repo = repo_of(message)
    if repo is None:
        return
    try:
        note_id = await asyncio.to_thread(repo.create, source, title)
        # Тот же объект репозитория дальше: его кэш уже содержит новую заметку.
        await start_note_wizard(message, repo, note_id, source, title, state)
    except Exception as e:
        await send_error_message(message, e)


# --- Шаги мастера ---


async def start_note_wizard(
    event: types.Message,
    repo: NoteRepository,
    note_id: str,
    source: str,
    title: str,
    state: FSMContext,
) -> None:
    """
    После ввода TITLE предлагает заполнить остальные колонки по шагам.

    Список колонок (без ID, SOURCE и TITLE), ``title`` и текущий индекс
    хранятся в FSMContext; если заполнять больше нечего — сразу карточка.
    """
    headers = await asyncio.to_thread(repo.headers)
    columns = _wizard_columns(headers)
    if not columns:
        await _show_card(event, note_id)
        await state.clear()
        return
    await state.set_state(NoteStates.wizard_step)
    await state.update_data(
        note_id=note_id, source=source, title=title, columns=columns, step=0
    )
    await _ask_wizard_column(event, note_id, title, columns, 0)


def _wizard_columns(headers: list[str]) -> list[tuple[int, str]]:
    """Колонки мастера: всё, кроме ID, SOURCE и TITLE (как (номер, имя))."""
    return [
        (index, header)
        for index, header in enumerate(headers, start=1)
        if header.strip().upper() not in WIZARD_SKIP_HEADERS
    ]


async def _ask_wizard_column(
    event: types.Message | types.CallbackQuery,
    note_id: str,
    title: str,
    columns: list[tuple[int, str]],
    step: int,
) -> None:
    """Запрос значения текущей колонки мастера (или карточка, если всё)."""
    if step >= len(columns):
        return
    header = columns[step][1]
    name = _wizard_note_title(title)
    if header.strip().upper() == SCREEN_HEADER_UPPER:
        text = buttons.WIZARD_SCREEN_PROMPT.format(name=name)
    else:
        text = buttons.WIZARD_FIELD_PROMPT.format(name=name, field=escape_html(header))
    await show_main_menu(
        event, with_min_width(text), keyboards.note_wizard_step(note_id)
    )


def _wizard_note_title(title: str) -> str:
    """Заголовок заметки для промпта мастера — TITLE, введённый пользователем."""
    return f"New note: <b>{escape_html(title)}</b>"


async def _wizard_data(
    state: FSMContext,
) -> tuple[str, str, str, list[tuple[int, str]], int]:
    """Данные шага мастера: note_id, source, title, колонки, индекс."""
    data = await state.get_data()
    note_id = str(data.get("note_id", ""))
    source = str(data.get("source", ""))
    title = str(data.get("title", ""))
    columns = [(int(i), str(h)) for i, h in data.get("columns", [])]
    step = int(data.get("step", 0) or 0)
    return note_id, source, title, columns, step


async def _advance_wizard(
    event: types.Message | types.CallbackQuery,
    state: FSMContext,
    note_id: str,
    title: str,
    columns: list[tuple[int, str]],
    step: int,
) -> None:
    """Переход к следующей колонке или выход из мастера в карточку."""
    next_step = step + 1
    await state.update_data(step=next_step)
    if next_step >= len(columns):
        await _show_card(event, note_id)
        await state.clear()
        return
    await _ask_wizard_column(event, note_id, title, columns, next_step)


async def _show_card(event: types.Message | types.CallbackQuery, note_id: str) -> None:
    """Карточка заметки (ленивый импорт — без циклических зависимостей)."""
    from handlers.notes_handler import show_note

    await show_note(event, note_id)


def _main_menu() -> Callable[..., Awaitable[None]]:
    """Ленивый доступ к ``notes_handler.send_main_menu`` (без циклов)."""
    from handlers.notes_handler import send_main_menu

    return send_main_menu


@wizard_router.message(NoteStates.wizard_step, F.photo)
async def wizard_got_photo(message: types.Message, state: FSMContext) -> None:
    """Фото на шаге SCREEN: сохраняем file_id и переходим к следующей колонке."""
    note_id, source, title, columns, step = await _wizard_data(state)
    if not note_id or step >= len(columns):
        await state.clear()
        return
    header = columns[step][1]
    if header.strip().upper() != SCREEN_HEADER_UPPER:
        await status_message(message, "⚠️ Please send text for this field.")
        return
    repo = repo_of(message)
    if repo is None:
        return
    if not message.photo:
        await status_message(message, "⚠️ Please send a photo.")
        return
    file_id = message.photo[-1].file_id
    try:
        new_id = await asyncio.to_thread(
            _write_or_recreate,
            repo,
            note_id,
            source,
            title,
            columns[step][0],
            file_id,
        )
    except Exception as e:
        await send_error_message(message, e)
        return
    # Промежуточное «saved» не отправляем: сразу следующий шаг мастера.
    await _advance_wizard(message, state, new_id, title, columns, step)


@wizard_router.message(NoteStates.wizard_step, F.text)
async def wizard_got_text(message: types.Message, state: FSMContext) -> None:
    """Текст на обычном шаге мастера: пишем в ячейку и идём дальше."""
    note_id, source, title, columns, step = await _wizard_data(state)
    if not note_id or step >= len(columns):
        await state.clear()
        return
    col_index, header = columns[step]
    if header.strip().upper() == SCREEN_HEADER_UPPER:
        await status_message(message, buttons.WIZARD_SCREEN_WRONG_TYPE)
        return
    repo = repo_of(message)
    if repo is None:
        return
    value = clean_value(message.text)
    try:
        new_id = await asyncio.to_thread(
            _write_or_recreate, repo, note_id, source, title, col_index, value
        )
    except Exception as e:
        await send_error_message(message, e)
        return
    # Промежуточное «saved» не отправляем: сразу следующий шаг мастера.
    # После пересоздания строки дальше идём по новому ID заметки.
    await _advance_wizard(message, state, new_id, title, columns, step)


def _write_or_recreate(
    repo: NoteRepository,
    note_id: str,
    source: str,
    title: str,
    col_index: int,
    value: str,
) -> str:
    """
    Пишет значение в колонку заметки; возвращает актуальный ID заметки.

    ``locate`` уже перечитывает лист при промахе по ID, поэтому ValueError
    означает, что строку действительно потеряли (удалена вручную). Тогда
    заметка создаётся заново с тем же SOURCE/TITLE — мастер не обрывается
    на первом же поле введённым значением.
    """
    try:
        repo.update_cell_at(_row_index(repo, note_id), col_index, value)
        return note_id
    except ValueError:
        new_id = repo.create(source, title)
        repo.update_cell_at(_row_index(repo, new_id), col_index, value)
        return new_id


def _row_index(repo: NoteRepository, note_id: str) -> int:
    """Номер строки заметки; ValueError, если её нет на листе."""
    row_index = repo.find_row(note_id)
    if row_index is None:
        raise ValueError(f"Note {note_id} not found.")
    return row_index


@wizard_router.message(NoteStates.wizard_step)
async def wizard_got_wrong_type(message: types.Message, state: FSMContext) -> None:
    """Не текст и не фото: просим то, что подходит текущей колонке."""
    _, _, _, columns, step = await _wizard_data(state)
    if step >= len(columns):
        await state.clear()
        return
    if columns[step][1].strip().upper() == SCREEN_HEADER_UPPER:
        await status_message(message, buttons.WIZARD_SCREEN_WRONG_TYPE)
    else:
        await status_message(message, "⚠️ Please send text for this field.")


# --- Кнопки мастера ---


@wizard_router.callback_query(F.data.startswith(f"{Cb.WIZARD_NEXT}:"))
async def cb_wizard_next(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«⏭ Next»: пропустить текущую колонку мастера."""
    await ack_callback(callback)
    note_id, _, title, columns, step = await _wizard_data(state)
    if not note_id:
        # FSMContext пуст (рестарт сервера): восстанавливаем шаг из листа
        note_id = Cb.parse_wizard_note_id(callback.data or "", Cb.WIZARD_NEXT) or ""
        if not note_id:
            await _main_menu()(callback)
            return
        await _restore_wizard(callback, state, note_id)
        return
    await _advance_wizard(callback, state, note_id, title, columns, step)


async def _restore_wizard(
    callback: types.CallbackQuery, state: FSMContext, note_id: str
) -> None:
    """
    Продолжает мастер после потери FSMContext.

    SOURCE, TITLE и колонки берём из листа: текущий шаг неизвестен, поэтому
    начинаем с шага 1 (первые колонки просто пропускаются кнопкой Next).
    """
    repo = repo_of(callback)
    if repo is None:
        return
    try:
        ctx: NoteContext | None = await asyncio.to_thread(note_context, repo, note_id)
    except Exception as e:
        await send_error_message(callback, e)
        return
    if ctx is None:
        await _show_card(callback, note_id)
        return
    columns = _wizard_columns(ctx.headers)
    if not columns:
        await _show_card(callback, note_id)
        return
    await state.set_state(NoteStates.wizard_step)
    await state.update_data(
        note_id=note_id,
        source=ctx.source,
        title=ctx.note.value(title_column_index(ctx.headers)).strip(),
        columns=columns,
        step=1,
    )
    await _ask_wizard_column(callback, note_id, ctx.title, columns, 1)


@wizard_router.callback_query(F.data.startswith(f"{Cb.WIZARD_DONE}:"))
async def cb_wizard_done(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«✅ Done»: закончить мастер и открыть готовую карточку."""
    await ack_callback(callback)
    note_id, _, _, _, _ = await _wizard_data(state)
    await state.clear()
    if not note_id:
        note_id = Cb.parse_wizard_note_id(callback.data or "", Cb.WIZARD_DONE) or ""
    if not note_id:
        await _main_menu()(callback)
        return
    await _show_card(callback, note_id)


@wizard_router.callback_query(F.data.startswith(f"{Cb.WIZARD_ANOTHER}:"))
async def cb_wizard_another(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«➕ Add another note»: снова TITLE для того же источника."""
    await ack_callback(callback)
    note_id, source, _, _, _ = await _wizard_data(state)
    if not note_id:
        note_id = Cb.parse_wizard_note_id(callback.data or "", Cb.WIZARD_ANOTHER) or ""
    if not source and note_id:
        source = await _source_of_note(callback, note_id)
    if not source:
        await _main_menu()(callback)
        return
    await ask_note_title(callback, state, source)


async def _source_of_note(callback: types.CallbackQuery, note_id: str) -> str:
    """SOURCE заметки из листа (пусто, если заметки уже нет)."""
    repo = repo_of(callback)
    if repo is None:
        return ""
    try:
        ctx = await asyncio.to_thread(note_context, repo, note_id)
    except Exception as e:
        await send_error_message(callback, e)
        return ""
    return ctx.source if ctx is not None else ""


__all__ = [
    "ask_new_source",
    "ask_note_source",
    "ask_note_title",
    "start_note_wizard",
    "wizard_router",
]
