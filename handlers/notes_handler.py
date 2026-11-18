"""
Хендлеры заметок: главное меню, добавление, навигация по источникам,
карточка заметки, правка полей и скриншоты.

Все обращения к таблице идут через ``NoteRepository``: здесь нет ни gspread,
ни номеров колонок. ``user_id`` всегда берётся из события Telegram
(``from_user.id``), поэтому один пользователь не может открыть лист другого.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from services import buttons, keyboards
from services.callback import Cb, Cmd
from services.sheets import (
    Note,
    NoteRepository,
    name_column_index,
    screen_column_index,
    source_column_index,
)
from services.telegram_io import (
    ack_callback,
    alert,
    require_from_user,
    send_error_message,
    send_parts,
    show_main_menu,
    status_message,
)
from services.text_utils import escape_html, note_card_lines

logger = logging.getLogger(__name__)

router = Router(name="notes")

#: Колонки, которые НЕЛЬЗЯ править текстом: ID — ключ записи,
#: SCREEN — только через приём фото (в ней file_id Telegram).
NON_EDITABLE_HEADERS = {"ID", "SCREEN"}
#: Значения, которыми пользователь «очищает» ячейку.
CLEAR_VALUES = {"-", "—", "clear", "none", "пусто"}


class NoteStates(StatesGroup):
    """Состояния ввода сценариев заметок."""

    wait_source = State()  # ввод имени нового источника
    wait_name = State()  # ввод названия заметки
    wait_value = State()  # ввод значения колонки
    wait_photo = State()  # ожидание фото для SCREEN
    wait_source_name = State()  # новое имя существующего источника


@dataclass(frozen=True)
class NoteContext:
    """Всё, что нужно для карточки: заметка, шапка и её строки."""

    note: Note
    headers: list[str]
    source: str

    @property
    def note_id(self) -> str:
        return self.note.note_id

    @property
    def screen(self) -> str:
        """file_id скриншота (пусто, если фото нет)."""
        index = screen_column_index(self.headers)
        return self.note.value(index).strip() if index else ""

    @property
    def name(self) -> str:
        """NAME заметки (или «ID xxx», если имя пустое)."""
        name = self.note.value(name_column_index(self.headers)).strip()
        return name or f"ID {self.note_id}"

    def editable_columns(self) -> list[tuple[int, str]]:
        """(номер, имя) колонок для текстовой правки — всё, кроме ID и SCREEN."""
        return [
            (index, header)
            for index, header in enumerate(self.headers, start=1)
            if header.strip().upper() not in NON_EDITABLE_HEADERS
        ]

    def card_text(self) -> str:
        """Карточка: ТОЛЬКО заполненные колонки (пустые пропускаются)."""
        lines = note_card_lines(self.headers, self.note.values)
        return "\n".join(lines) or f"<b>📝 {escape_html(self.name)}</b>"


# --- Утилиты ---


def _repo(event: types.Message | types.CallbackQuery) -> NoteRepository | None:
    """Репозиторий листа пользователя события (None, если нет from_user)."""
    user = require_from_user(event)
    if user is None:
        return None
    return NoteRepository(user.id)


def _source_of(headers: list[str], note: Note) -> str:
    """Источник заметки — нужен для возврата к списку и заголовка карточки."""
    return note.value(source_column_index(headers)).strip()


def _note_context(repo: NoteRepository, note_id: str) -> NoteContext | None:
    """Заметка + шапка в одном снимке листа (один запрос к Sheets)."""
    note = repo.get(note_id)
    if note is None:
        return None
    headers = repo.headers()
    source = _source_of(headers, note)
    return NoteContext(note=note, headers=headers, source=source)


# --- Главное меню ---


async def send_main_menu(event: types.Message | types.CallbackQuery) -> None:
    """Показывает главное меню (кнопки: Add note, Notes by source, ...)."""
    await show_main_menu(event, buttons.MAIN_MENU_PROMPT, keyboards.main_menu())


@router.message(Command(Cmd.ADD_NOTE))
async def cmd_add_note(message: types.Message, state: FSMContext) -> None:
    """/add_note: выбор источника (кнопки) или «➕ Новый источник»."""
    await ask_note_source(message, state)


@router.message(Command(Cmd.ADD_SOURCE))
async def cmd_add_source(message: types.Message, state: FSMContext) -> None:
    """/add_source: имя нового источника, сразу затем — первая заметка."""
    await ask_new_source(message, state)


@router.callback_query(F.data == Cb.NOTE_ADD)
async def cb_add_note(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«➕ Add note»: сначала кнопки существующих источников + «Новый источник»."""
    await ack_callback(callback)
    await ask_note_source(callback, state)


@router.callback_query(F.data == Cb.SRC_ADD)
async def cb_add_source(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«🗂 Add source»: ввод имени нового источника."""
    await ack_callback(callback)
    await ask_new_source(callback, state)


async def ask_note_source(
    event: types.Message | types.CallbackQuery, state: FSMContext | None = None
) -> None:
    """
    Шаг 1 добавления заметки: выбор источника.

    Показываем кнопки всех существующих SOURCE и кнопку «➕ Новый источник».
    """
    repo = _repo(event)
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
    """
    Шаг 1 «🗂 Add source»: показываем список существующих и ждём имя.
    """
    if state is not None:
        await state.set_state(NoteStates.wait_source)
    repo = _repo(event)
    if repo is None:
        return
    try:
        sources = await asyncio.to_thread(repo.list_sources)
    except Exception as e:
        logger.warning("Could not list sources for /add_source: %s", e)
        sources = []
    existing_block = ""
    if sources:
        codes = ", ".join(f"<code>{escape_html(source)}</code>" for source in sources)
        existing_block = f"Existing: {codes}\n"

    text = buttons.NEW_SOURCE_PROMPT.format(existing=f"{existing_block}\n")
    await show_main_menu(event, text, keyboards.new_source_cancel())


async def ask_note_name(
    event: types.Message | types.CallbackQuery, state: FSMContext, source: str
) -> None:
    """Шаг 2: просим название заметки для выбранного/нового источника."""
    await state.set_state(NoteStates.wait_name)
    await state.update_data(source=source)
    await show_main_menu(
        event,
        buttons.NOTE_NAME_PROMPT.format(source=escape_html(source)),
        keyboards.back_to_main(),
    )


# --- Ввод источника и названия ---


@router.message(NoteStates.wait_source, F.text)
async def got_new_source(message: types.Message, state: FSMContext) -> None:
    """
    Получили имя источника: сразу переходим к названию первой заметки —
    в таблицу не должна попасть пустая строка без заметки.
    """
    source = (message.text or "").strip()
    if not source:
        await status_message(message, "⚠️ Source name must not be empty.")
        return
    await ask_note_name(message, state, source)


@router.message(NoteStates.wait_name, F.text)
async def got_note_name(message: types.Message, state: FSMContext) -> None:
    """Создаём заметку выбранного источника и открываем её карточку."""
    data = await state.get_data()
    source = str(data.get("source", "")).strip()
    name = (message.text or "").strip()
    await state.clear()
    if not source:
        await status_message(message, "⚠️ Source is lost, start again.")
        await send_main_menu(message)
        return
    repo = _repo(message)
    if repo is None:
        return
    try:
        note_id = await asyncio.to_thread(repo.create, source, name)
        await show_note(message, note_id)
    except Exception as e:
        await send_error_message(message, e)


@router.callback_query(F.data.startswith(f"{Cb.SRC_SELECT}:"))
async def cb_select_source(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Выбран существующий источник при добавлении заметки."""
    await ack_callback(callback)
    source = Cb.parse_source(callback.data or "", Cb.SRC_SELECT)
    if not source:
        return
    await ask_note_name(callback, state, source)


# --- Навигация по источникам ---


@router.callback_query(F.data == Cb.SRC_LIST)
async def cb_sources_list(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«📂 Notes by source»: список источников с количеством заметок."""
    await ack_callback(callback)
    await state.clear()
    repo = _repo(callback)
    if repo is None:
        return
    try:
        counts = await asyncio.to_thread(_source_counts, repo)
    except Exception as e:
        await send_error_message(callback, e)
        return
    if not counts:
        await show_main_menu(callback, buttons.NO_SOURCES, keyboards.back_to_main())
        return
    await show_main_menu(
        callback, buttons.SOURCES_PROMPT, keyboards.sources_with_counts(counts)
    )


def _source_counts(repo: NoteRepository) -> list[tuple[str, int]]:
    """(источник, количество заметок) — один проход по листу."""
    return [
        (source, len(repo.list_by_source(source))) for source in repo.list_sources()
    ]


@router.callback_query(F.data.startswith(f"{Cb.SRC_NOTES}:"))
async def cb_source_notes(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Все заметки источника по алфавиту (NAME без учёта регистра)."""
    await ack_callback(callback)
    await state.clear()
    source = Cb.parse_source(callback.data or "", Cb.SRC_NOTES)
    if not source:
        return
    repo = _repo(callback)
    if repo is None:
        return
    try:
        notes = await asyncio.to_thread(repo.list_by_source, source)
        await _send_notes_list(callback, repo, source, notes)
    except Exception as e:
        await send_error_message(callback, e)


async def _send_notes_list(
    event: types.Message | types.CallbackQuery,
    repo: NoteRepository,
    source: str,
    notes: list[Note],
) -> None:
    if not notes:
        await show_main_menu(
            event,
            buttons.NO_NOTES.format(source=escape_html(source)),
            keyboards.back_to_main(),
        )
        return
    headers = repo.headers()
    name_col = name_column_index(headers)
    lines = []
    for note in notes:
        name = note.value(name_col).strip() or f"ID {note.note_id}"
        lines.append(f"• {escape_html(name)}")
    header = buttons.NOTES_PROMPT.format(source=escape_html(source), count=len(notes))
    text = header + "\n\n" + "\n".join(lines)
    markup = keyboards.notes([note.note_id for note in notes], source)
    await send_parts(event, text, markup)


# --- Карточка заметки ---


async def show_note(event: types.Message | types.CallbackQuery, note_id: str) -> None:
    """
    Отправляет карточку заметки.

    В тексте — только заполненные колонки. Если заполнена SCREEN (там file_id
    фото Telegram), отправляем само фото с этой подписью.
    """
    repo = _repo(event)
    if repo is None:
        return
    try:
        ctx = await asyncio.to_thread(_note_context, repo, note_id)
    except Exception as e:
        await send_error_message(event, e)
        return
    if ctx is None:
        await show_main_menu(event, "⚠️ Note not found.", keyboards.back_to_main())
        return
    text = ctx.card_text()
    markup = keyboards.note_card(ctx.note_id, ctx.source)
    # Утиная типизация: у кнопки есть message, у обычного сообщения — нет.
    message = getattr(event, "message", None) or event
    if message is None:
        return
    if ctx.screen:
        try:
            await message.answer_photo(ctx.screen, caption=text, reply_markup=markup)
            return
        except Exception as e:
            # file_id мог устареть (фото удалено) — показываем хотя бы текст
            logger.warning("answer_photo failed for %s: %s", ctx.screen, e)
    await send_parts(event, text, markup)


@router.callback_query(F.data.startswith(f"{Cb.NOTE_VIEW}:"))
async def cb_note_view(callback: types.CallbackQuery, state: FSMContext) -> None:
    await ack_callback(callback)
    await state.clear()
    ref = Cb.parse_note(callback.data or "", Cb.NOTE_VIEW)
    if ref is None:
        return
    await show_note(callback, ref.note_id)


# --- Правка полей ---


@router.callback_query(F.data.startswith(f"{Cb.NOTE_EDIT_MENU}:"))
async def cb_edit_menu(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«✏️ Edit field»: выбор любой колонки, кроме ID и SCREEN."""
    await ack_callback(callback)
    ref = Cb.parse_note(callback.data or "", Cb.NOTE_EDIT_MENU)
    if ref is None:
        return
    repo = _repo(callback)
    if repo is None:
        return
    try:
        ctx = await asyncio.to_thread(_note_context, repo, ref.note_id)
    except Exception as e:
        await send_error_message(callback, e)
        return
    if ctx is None:
        return
    await show_main_menu(
        callback,
        f"✏️ <b>{escape_html(ctx.name)}</b>\n\nChoose a field to edit:",
        keyboards.edit_fields_only(ctx.editable_columns(), ctx.note_id),
    )


@router.callback_query(F.data.startswith(f"{Cb.NOTE_EDIT_CELL}:"))
async def cb_edit_cell(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Правка конкретной колонки: запоминаем заметку и колонку, ждём текст."""
    await ack_callback(callback)
    parsed = Cb.parse_note_cell(callback.data or "")
    if parsed is None:
        return
    note_id, col_index = parsed
    repo = _repo(callback)
    if repo is None:
        return
    try:
        ctx = await asyncio.to_thread(_note_context, repo, note_id)
    except Exception as e:
        await send_error_message(callback, e)
        return
    if ctx is None or not (1 <= col_index <= len(ctx.headers)):
        await alert(callback, "⚠️ This column no longer exists.")
        return
    field = ctx.headers[col_index - 1]
    if field.strip().upper() in NON_EDITABLE_HEADERS:
        await alert(callback, "🔒 This column is read-only.")
        return
    await state.set_state(NoteStates.wait_value)
    await state.update_data(note_id=note_id, col_index=col_index)
    await show_main_menu(
        callback,
        buttons.EDIT_FIELD_PROMPT.format(
            name=escape_html(ctx.name), field=escape_html(field)
        ),
        keyboards.photo_cancel(note_id, ctx.source),
    )


@router.message(NoteStates.wait_value, F.text)
async def got_field_value(message: types.Message, state: FSMContext) -> None:
    """Записывает текст в выбранную колонку и снова открывает карточку."""
    data = await state.get_data()
    note_id = str(data.get("note_id", ""))
    col_index = int(data.get("col_index", 0) or 0)
    await state.clear()
    repo = _repo(message)
    if repo is None or not note_id or not col_index:
        return
    value = (message.text or "").strip()
    if value.lower() in CLEAR_VALUES:
        value = ""
    try:
        await asyncio.to_thread(
            repo.update_cell_at, _row_of(repo, note_id), col_index, value
        )
        await status_message(message, buttons.NOTE_SAVED)
        await show_note(message, note_id)
    except Exception as e:
        await send_error_message(message, e)


def _row_of(repo: NoteRepository, note_id: str) -> int:
    """Номер строки заметки; ValueError, если её уже удалили."""
    row = repo.find_row(note_id)
    if row is None:
        raise ValueError(f"Note {note_id} not found.")
    return row


# --- Скриншот ---


@router.callback_query(F.data.startswith(f"{Cb.NOTE_PHOTO}:"))
async def cb_photo(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«🖼 Add/Replace screenshot»: ждём фото и сохраним file_id в SCREEN."""
    await ack_callback(callback)
    ref = Cb.parse_note(callback.data or "", Cb.NOTE_PHOTO)
    if ref is None:
        return
    repo = _repo(callback)
    if repo is None:
        return
    try:
        ctx = await asyncio.to_thread(_note_context, repo, ref.note_id)
    except Exception as e:
        await send_error_message(callback, e)
        return
    if ctx is None:
        return
    await state.set_state(NoteStates.wait_photo)
    await state.update_data(note_id=ref.note_id)
    await show_main_menu(
        callback,
        buttons.PHOTO_PROMPT.format(name=escape_html(ctx.name)),
        keyboards.photo_cancel(ref.note_id, ctx.source),
    )


@router.message(NoteStates.wait_photo, F.photo)
async def got_photo(message: types.Message, state: FSMContext) -> None:
    """
    Сохраняет ``message.photo[-1].file_id`` в колонку SCREEN.

    Берём последнее (самое большое) фото из списка размеров Telegram.
    """
    data = await state.get_data()
    note_id = str(data.get("note_id", ""))
    await state.clear()
    repo = _repo(message)
    if repo is None or not note_id:
        return
    if not message.photo:
        await status_message(message, "⚠️ Please send a photo.")
        return
    file_id = message.photo[-1].file_id
    try:
        row = await asyncio.to_thread(_row_of, repo, note_id)
        col_index = _screen_column(repo)
        await asyncio.to_thread(repo.update_cell_at, row, col_index, file_id)
        await status_message(message, buttons.PHOTO_SAVED)
        await show_note(message, note_id)
    except Exception as e:
        await send_error_message(message, e)


@router.message(NoteStates.wait_photo)
async def got_photo_wrong_type(message: types.Message, state: FSMContext) -> None:
    """Вместо фото прислали текст — просим фото ещё раз."""
    await status_message(message, "⚠️ Please send the screenshot as a photo.")


def _screen_column(repo: NoteRepository) -> int:
    """Номер колонки SCREEN; ValueError, если служебной колонки нет."""
    index = screen_column_index(repo.headers())
    if index is None:
        raise ValueError("Table header has no column 'SCREEN'.")
    return index


# --- Удаление ---


@router.callback_query(F.data.startswith(f"{Cb.NOTE_DEL}:"))
async def cb_note_delete(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«🗑 Delete note»: удаляет строку заметки и возвращает к источнику."""
    await ack_callback(callback)
    await state.clear()
    ref = Cb.parse_note(callback.data or "", Cb.NOTE_DEL)
    if ref is None:
        return
    repo = _repo(callback)
    if repo is None:
        return
    note_id = ref.note_id
    try:
        ctx = await asyncio.to_thread(_note_context, repo, note_id)
        source = ctx.source if ctx is not None else ref.source
        await asyncio.to_thread(repo.delete, note_id)
    except Exception as e:
        await send_error_message(callback, e)
        return
    await status_message(callback, buttons.NOTE_DELETED)
    if source:
        notes = await asyncio.to_thread(repo.list_by_source, source)
        await _send_notes_list(callback, repo, source, notes)
    else:
        await send_main_menu(callback)


# --- Управление источниками: список по номерам ---


async def show_sources_manage(
    event: types.Message | types.CallbackQuery,
    state: FSMContext | None = None,
) -> None:
    """
    «🗂 Sources»: пронумерованный список «1. Source (N)» и кнопки-номера.

    Список вместе с количеством заметок сохраняется в FSMContext: в
    callback_data уходит только номер, поэтому длина имён источников
    и их количество не упираются в лимит 64 байта.
    """
    repo = _repo(event)
    if repo is None:
        return
    try:
        counts = await asyncio.to_thread(repo.list_sources_with_counts)
    except Exception as e:
        logger.exception("Could not list sources for /sources")
        await send_error_message(event, e)
        return
    if state is not None:
        await state.clear()
        if counts:
            await state.update_data(
                source_list=[source for source, _ in counts],
                source_counts=[count for _, count in counts],
            )
    if not counts:
        await show_main_menu(event, buttons.NO_SOURCES, keyboards.back_to_main())
        return
    lines = [
        f"{index}. {escape_html(source)} ({count})"
        for index, (source, count) in enumerate(counts, start=1)
    ]
    text = buttons.SOURCES_MANAGE_PROMPT + "\n\n" + "\n".join(lines)
    await send_parts(event, text, keyboards.sources_manage(len(counts)))


async def _saved_source(
    callback: types.CallbackQuery, state: FSMContext, index: int
) -> tuple[str, int] | None:
    """(источник, количество заметок) из сохранённого списка или None."""
    data = await state.get_data()
    sources = data.get("source_list") or []
    counts = data.get("source_counts") or []
    if not 0 <= index < len(sources):
        await alert(callback, buttons.SOURCE_LOST)
        return None
    count = int(counts[index]) if index < len(counts) else 0
    return str(sources[index]), count


@router.callback_query(F.data == Cb.MENU_SOURCES)
async def cb_manage_sources(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«🗂 Sources»: нумерованный список источников."""
    await ack_callback(callback)
    await show_sources_manage(callback, state)


@router.callback_query(F.data.startswith(f"{Cb.SRC_MANAGE}:"))
async def cb_source_manage(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Нажатие на номер: экран действий над выбранным источником."""
    await ack_callback(callback)
    index = Cb.parse_source_index(callback.data or "", Cb.SRC_MANAGE)
    if index is None:
        return
    saved = await _saved_source(callback, state, index)
    if saved is None:
        return
    source, _ = saved
    await show_main_menu(
        callback,
        buttons.SOURCE_MANAGE_PROMPT.format(source=escape_html(source)),
        keyboards.source_actions(index),
    )


@router.callback_query(F.data.startswith(f"{Cb.SRC_SHOW_NOTES}:"))
async def cb_source_show_notes(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    """«📂 Show notes»: заметки выбранного источника."""
    await ack_callback(callback)
    index = Cb.parse_source_index(callback.data or "", Cb.SRC_SHOW_NOTES)
    if index is None:
        return
    saved = await _saved_source(callback, state, index)
    if saved is None:
        return
    source, _ = saved
    repo = _repo(callback)
    if repo is None:
        return
    try:
        notes = await asyncio.to_thread(repo.list_by_source, source)
        await _send_notes_list(callback, repo, source, notes)
    except Exception as e:
        await send_error_message(callback, e)


@router.callback_query(F.data.startswith(f"{Cb.SRC_NEW_NOTE}:"))
async def cb_source_new_note(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«➕ Add note»: сразу к имени заметки для выбранного источника."""
    await ack_callback(callback)
    index = Cb.parse_source_index(callback.data or "", Cb.SRC_NEW_NOTE)
    if index is None:
        return
    saved = await _saved_source(callback, state, index)
    if saved is None:
        return
    source, _ = saved
    await ask_note_name(callback, state, source)


@router.callback_query(F.data.startswith(f"{Cb.SRC_RENAME}:"))
async def cb_source_rename(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«✏️ Rename»: запрашиваем новое имя источника."""
    await ack_callback(callback)
    index = Cb.parse_source_index(callback.data or "", Cb.SRC_RENAME)
    if index is None:
        return
    saved = await _saved_source(callback, state, index)
    if saved is None:
        return
    source, count = saved
    await state.update_data(source=source)
    await state.set_state(NoteStates.wait_source_name)
    await show_main_menu(
        callback,
        buttons.SOURCE_RENAME_PROMPT.format(source=escape_html(source), count=count),
        keyboards.new_source_cancel(),
    )


@router.message(NoteStates.wait_source_name, F.text)
async def got_renamed_source(message: types.Message, state: FSMContext) -> None:
    """Новое имя источника: переименовываем все его заметки."""
    data = await state.get_data()
    old_source = str(data.get("source", "")).strip()
    new_source = (message.text or "").strip()
    await state.clear()
    repo = _repo(message)
    if repo is None:
        return
    if not old_source:
        await status_message(message, buttons.SOURCE_LOST)
        await show_sources_manage(message, state)
        return
    try:
        updated = await asyncio.to_thread(repo.rename_source, old_source, new_source)
    except ValueError:
        await status_message(message, "⚠️ Source name must not be empty.")
        await show_sources_manage(message, state)
        return
    except Exception as e:
        await send_error_message(message, e)
        return
    await status_message(message, buttons.SOURCE_RENAMED.format(count=updated))
    await show_sources_manage(message, state)


@router.callback_query(F.data.startswith(f"{Cb.SRC_DEL}:"))
async def cb_source_delete(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«🗑 Delete»: подтверждение с количеством удаляемых заметок."""
    await ack_callback(callback)
    index = Cb.parse_source_index(callback.data or "", Cb.SRC_DEL)
    if index is None:
        return
    saved = await _saved_source(callback, state, index)
    if saved is None:
        return
    source, count = saved
    await show_main_menu(
        callback,
        buttons.SOURCE_DELETE_CONFIRM.format(source=escape_html(source), count=count),
        keyboards.source_delete_confirm(index),
    )


@router.callback_query(F.data.startswith(f"{Cb.SRC_DEL_CONFIRM}:"))
async def cb_source_delete_confirm(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    """«✅ Yes, delete»: удаляет источник со всеми его заметками."""
    await ack_callback(callback)
    index = Cb.parse_source_index(callback.data or "", Cb.SRC_DEL_CONFIRM)
    if index is None:
        return
    saved = await _saved_source(callback, state, index)
    if saved is None:
        return
    source, _ = saved
    repo = _repo(callback)
    if repo is None:
        return
    try:
        deleted = await asyncio.to_thread(repo.delete_source, source)
    except Exception as e:
        await send_error_message(callback, e)
        return
    await status_message(callback, buttons.SOURCE_DELETED.format(count=deleted))
    await show_sources_manage(callback, state)


# --- Общие служебные утилиты ---


@router.callback_query(F.data == Cb.CANCEL_INPUT)
async def cb_cancel(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«Отмена» под запросом ввода: меню вместо ожидания текста."""
    await ack_callback(callback)
    await state.clear()
    await send_main_menu(callback)


__all__ = [
    "NoteContext",
    "NoteStates",
    "ask_note_name",
    "ask_note_source",
    "ask_new_source",
    "router",
    "send_main_menu",
    "show_sources_manage",
    "show_note",
]
