"""
Хендлеры заметок: главное меню, карточка, правка полей, скриншот,
удаление заметки и поиск по TITLE.

Все обращения к таблице идут через ``NoteRepository``: здесь нет ни gspread,
ни номеров колонок. ``user_id`` всегда берётся из события Telegram
(``from_user.id``), поэтому один пользователь не может открыть лист другого.

Модуль остаётся точкой входа пакета: он собирает общий ``router`` из
роутеров мастера и источников и реэкспортирует публичные символы.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from handlers.ai_note_handler import ai_router
from handlers.notes_common import (
    NON_EDITABLE_HEADERS,
    NoteContext,
    NoteStates,
    clean_value,
    note_context,
    note_title,
    repo_of,
    write_note_cell,
)
from handlers.sources_handler import (
    show_sources_manage,
    sources_router,
)
from handlers.wizard_handler import (
    ask_new_source,
    ask_note_source,
    ask_note_title,
    start_note_wizard,
    wizard_router,
)
from services import buttons, keyboards
from services.callback import Cb
from services.sheets import (
    NoteRepository,
    screen_column_index,
    source_column_index,
)
from services.telegram_io import (
    ack_callback,
    alert,
    send_error_message,
    send_parts,
    show_main_menu,
    status_message,
    user_id_of,
    user_lock,
)
from services.text_utils import escape_html, with_min_width

logger = logging.getLogger(__name__)

card_router = Router(name="notes_card")

#: Общий роутер пакета: мастер ➔ источники ➔ карточка и поиск ➔ ✨ AI Note.
router = Router(name="notes")
router.include_router(wizard_router)
router.include_router(sources_router)
router.include_router(card_router)
router.include_router(ai_router)


# --- Главное меню ---


async def send_main_menu(event: types.Message | types.CallbackQuery) -> None:
    """Показывает главное меню (кнопки: Add note, Notes by source, ...)."""
    await show_main_menu(event, buttons.MAIN_MENU_PROMPT, keyboards.main_menu())


# --- Карточка заметки ---


async def show_note(event: types.Message | types.CallbackQuery, note_id: str) -> None:
    """
    Отправляет карточку заметки.

    В тексте — только заполненные колонки. Если заполнена SCREEN (там file_id
    фото Telegram), отправляем само фото с этой подписью.
    """
    repo = repo_of(event)
    if repo is None:
        return
    try:
        ctx = await asyncio.to_thread(note_context, repo, note_id)
    except Exception as e:
        await send_error_message(event, e)
        return
    if ctx is None:
        await show_main_menu(event, "⚠️ Note not found.", keyboards.back_to_main())
        return
    text = ctx.card_text()
    markup = keyboards.note_card(ctx.note_id, ctx.source)
    # Утиная типизация: у кнопки есть message, у обычного сообщения — нет.
    # ``answer_photo`` проверяем через getattr (вызываемость, а не isinstance),
    # чтобы работали и двойники в тестах, и mypy видел конкретный метод.
    target = event.message if isinstance(event, types.CallbackQuery) else event
    answer_photo = getattr(target, "answer_photo", None)
    if ctx.screen and callable(answer_photo):
        try:
            await answer_photo(ctx.screen, caption=text, reply_markup=markup)
            return
        except Exception as e:
            # file_id мог устареть (фото удалено) — показываем хотя бы текст
            logger.warning("answer_photo failed for %s: %s", ctx.screen, e)
    await send_parts(event, text, markup)


@card_router.callback_query(F.data.startswith(f"{Cb.NOTE_VIEW}:"))
async def cb_note_view(callback: types.CallbackQuery, state: FSMContext) -> None:
    await ack_callback(callback)
    await state.clear()
    ref = Cb.parse_note(callback.data or "", Cb.NOTE_VIEW)
    if ref is None:
        return
    await show_note(callback, ref.note_id)


# --- Правка полей ---


@card_router.callback_query(F.data.startswith(f"{Cb.NOTE_EDIT_MENU}:"))
async def cb_edit_menu(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«✏️ Edit field»: выбор любой колонки, кроме ID и SCREEN."""
    await ack_callback(callback)
    ref = Cb.parse_note(callback.data or "", Cb.NOTE_EDIT_MENU)
    if ref is None:
        return
    repo = repo_of(callback)
    if repo is None:
        return
    try:
        ctx = await asyncio.to_thread(note_context, repo, ref.note_id)
    except Exception as e:
        await send_error_message(callback, e)
        return
    if ctx is None:
        return
    await show_main_menu(
        callback,
        f"✏️ <b>{escape_html(ctx.title)}</b>\n\nChoose a field to edit:",
        keyboards.edit_fields_only(ctx.editable_columns(), ctx.note_id),
    )


@card_router.callback_query(F.data.startswith(f"{Cb.NOTE_EDIT_CELL}:"))
async def cb_edit_cell(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Правка конкретной колонки: запоминаем заметку и колонку, ждём текст."""
    await ack_callback(callback)
    parsed = Cb.parse_note_cell(callback.data or "")
    if parsed is None:
        return
    note_id, col_index = parsed
    repo = repo_of(callback)
    if repo is None:
        return
    try:
        ctx = await asyncio.to_thread(note_context, repo, note_id)
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
            name=escape_html(ctx.title), field=escape_html(field)
        ),
        keyboards.photo_cancel(note_id, ctx.source),
    )


@card_router.message(NoteStates.wait_value, F.text)
async def got_field_value(message: types.Message, state: FSMContext) -> None:
    """Записывает текст в выбранную колонку и снова открывает карточку."""
    data = await state.get_data()
    note_id = str(data.get("note_id", ""))
    col_index = int(data.get("col_index", 0) or 0)
    await state.clear()
    repo = repo_of(message)
    if repo is None or not note_id or not col_index:
        return
    value = clean_value(message.text)
    user_id = user_id_of(message) or repo._user_id
    async with user_lock(user_id):
        try:
            await asyncio.to_thread(write_note_cell, repo, note_id, col_index, value)
            await status_message(message, buttons.NOTE_SAVED)
            await show_note(message, note_id)
        except Exception as e:
            await send_error_message(message, e)


# --- Скриншот ---


@card_router.callback_query(F.data.startswith(f"{Cb.NOTE_PHOTO}:"))
async def cb_photo(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«🖼 Add/Replace screenshot»: ждём фото и сохраним file_id в SCREEN."""
    await ack_callback(callback)
    ref = Cb.parse_note(callback.data or "", Cb.NOTE_PHOTO)
    if ref is None:
        return
    repo = repo_of(callback)
    if repo is None:
        return
    try:
        ctx = await asyncio.to_thread(note_context, repo, ref.note_id)
    except Exception as e:
        await send_error_message(callback, e)
        return
    if ctx is None:
        return
    await state.set_state(NoteStates.wait_photo)
    await state.update_data(note_id=ref.note_id)
    await show_main_menu(
        callback,
        buttons.PHOTO_PROMPT.format(name=escape_html(ctx.title)),
        keyboards.photo_cancel(ref.note_id, ctx.source),
    )


@card_router.message(NoteStates.wait_photo, F.photo)
async def got_photo(message: types.Message, state: FSMContext) -> None:
    """
    Сохраняет ``message.photo[-1].file_id`` в колонку SCREEN.

    Берём последнее (самое большое) фото из списка размеров Telegram.
    """
    data = await state.get_data()
    note_id = str(data.get("note_id", ""))
    await state.clear()
    repo = repo_of(message)
    if repo is None or not note_id:
        return
    if not message.photo:
        await status_message(message, "⚠️ Please send a photo.")
        return
    file_id = message.photo[-1].file_id
    user_id = user_id_of(message) or repo._user_id
    async with user_lock(user_id):
        try:
            col_index = await asyncio.to_thread(screen_column, repo)
            await asyncio.to_thread(write_note_cell, repo, note_id, col_index, file_id)
            await status_message(message, buttons.PHOTO_SAVED)
            await show_note(message, note_id)
        except Exception as e:
            await send_error_message(message, e)


@card_router.message(NoteStates.wait_photo)
async def got_photo_wrong_type(message: types.Message, state: FSMContext) -> None:
    """Вместо фото прислали текст — просим фото ещё раз."""
    await status_message(message, "⚠️ Please send the screenshot as a photo.")


def screen_column(repo: NoteRepository) -> int:
    """Номер колонки SCREEN; ValueError, если служебной колонки нет."""
    index = screen_column_index(repo.headers())
    if index is None:
        raise ValueError("Table header has no column 'SCREEN'.")
    return index


# --- Удаление заметки ---


@card_router.callback_query(F.data.startswith(f"{Cb.NOTE_DEL_ASK}:"))
async def cb_note_delete_ask(callback: types.CallbackQuery) -> None:
    """«🗑 Delete»: спрашивает подтверждение удаления заметки."""
    await ack_callback(callback)
    ref = Cb.parse_note(callback.data or "", Cb.NOTE_DEL_ASK)
    if ref is None:
        return
    repo = repo_of(callback)
    if repo is None:
        return
    try:
        ctx = await asyncio.to_thread(note_context, repo, ref.note_id)
    except Exception as e:
        await send_error_message(callback, e)
        return
    if ctx is None:
        await alert(callback, "⚠️ Note not found.")
        return
    prompt = (
        f"🗑 <b>Delete note</b> <b>{escape_html(ctx.title)}</b>?\n\n"
        "This cannot be undone."
    )
    await show_main_menu(
        callback,
        with_min_width(prompt),
        keyboards.note_delete_confirm(ctx.note_id, ctx.source),
    )


@card_router.callback_query(F.data.startswith(f"{Cb.NOTE_DEL}:"))
async def cb_note_delete(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«🗑 Delete note»: удаляет строку заметки и возвращает к источнику."""
    from handlers.sources_handler import send_notes_list as send_list

    await ack_callback(callback)
    await state.clear()
    ref = Cb.parse_note(callback.data or "", Cb.NOTE_DEL)
    if ref is None:
        return
    repo = repo_of(callback)
    if repo is None:
        return
    note_id = ref.note_id
    user_id = user_id_of(callback) or repo._user_id
    async with user_lock(user_id):
        try:
            ctx = await asyncio.to_thread(note_context, repo, note_id)
            source = ctx.source if ctx is not None else ref.source
            await asyncio.to_thread(repo.delete, note_id)
        except Exception as e:
            await send_error_message(callback, e)
            return
    await status_message(callback, buttons.NOTE_DELETED)
    if not source:
        await send_main_menu(callback)
        return
    try:
        notes = await asyncio.to_thread(repo.list_by_source, source)
        await send_list(callback, repo, source, notes, state)
    except Exception as e:
        # Строка уже удалена: падение списка не должно оставлять бота молчащим
        await send_error_message(callback, e)


# --- Поиск заметки по заголовку (текстом в чат) ---


@card_router.message(StateFilter(None), F.text)
async def search_by_title(message: types.Message) -> None:
    """
    Текст вне диалога — поиск заметки по TITLE.

    Одно совпадение открывает карточку сразу, несколько — пронумерованный
    список с кнопками-номерами, ни одного — понятное сообщение.
    """
    query = (message.text or "").strip()
    if not query or query.startswith("/"):
        return  # команды обрабатываются своими хендлерами
    repo = repo_of(message)
    if repo is None:
        return
    try:
        found = await asyncio.to_thread(repo.find_by_title, query)
    except Exception as e:
        await send_error_message(message, e)
        return
    if not found:
        await show_main_menu(
            message,
            buttons.NOTHING_FOUND.format(query=escape_html(query)),
            keyboards.search_not_found(),
        )
        return
    if len(found) == 1:
        await show_note(message, found[0].note_id)
        return
    headers = repo.headers()
    source_col = source_column_index(headers)
    lines = [
        f"{index}. <code>{escape_html(note_title(note, headers))}</code> "
        f"(<i>{escape_html(note.value(source_col).strip())}</i>)"
        for index, note in enumerate(found, start=1)
    ]
    header = buttons.SEARCH_RESULTS.format(count=len(found))
    text = header + "\n\n" + "\n".join(lines)
    markup = keyboards.notes([note.note_id for note in found], "")
    await send_parts(message, text, markup)


# --- Общие служебные утилиты ---


@card_router.callback_query(F.data == Cb.CANCEL_INPUT)
async def cb_cancel(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«Отмена» под запросом ввода: меню вместо ожидания текста."""
    await ack_callback(callback)
    await state.clear()
    await send_main_menu(callback)


__all__ = [
    "NoteContext",
    "NoteStates",
    "ask_new_source",
    "ask_note_source",
    "ask_note_title",
    "router",
    "send_main_menu",
    "show_note",
    "show_sources_manage",
    "start_note_wizard",
]
