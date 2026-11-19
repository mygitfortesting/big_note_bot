"""
Хендлеры источников: добавление, нумерованный список, заметки источника,
переименование и удаление. Модуль сам собирает ``sources_router``.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext

from handlers.notes_common import (
    NoteStates,
    note_title,
    repo_of,
)
from handlers.wizard_handler import ask_note_title
from services import buttons, keyboards
from services.callback import Cb
from services.sheets import Note, NoteRepository
from services.telegram_io import (
    ack_callback,
    alert,
    send_error_message,
    send_parts,
    show_main_menu,
    status_message,
)
from services.text_utils import (
    escape_html,
    note_card_lines,
    notes_digest_text,
    with_min_width,
)

logger = logging.getLogger(__name__)

sources_router = Router(name="sources")


async def send_notes_list(
    event: types.Message | types.CallbackQuery,
    repo: NoteRepository,
    source: str,
    notes: list[Note],
    state: FSMContext | None = None,
) -> None:
    """Список заметок источника: номера, заголовки и кнопки-номера."""
    source_index = await _index_of_source(source, state) if state else None
    if not notes:
        # Пустой источник — та же клавиатура, что и у непустого списка:
        # только кнопки-номера не рисуются, остальные действия те же.
        await show_main_menu(
            event,
            with_min_width(buttons.NO_NOTES.format(source=escape_html(source))),
            keyboards.notes([], source, source_index=source_index),
        )
        return
    headers = repo.headers()
    lines = [
        f"{index}. <code>{escape_html(note_title(note, headers))}</code>"
        for index, note in enumerate(notes, start=1)
    ]
    header = buttons.NOTES_PROMPT.format(source=escape_html(source), count=len(notes))
    text = header + "\n\n" + "\n".join(lines)
    markup = keyboards.notes(
        [note.note_id for note in notes], source, source_index=source_index
    )
    await send_parts(event, text, markup)


async def show_sources_manage(
    event: types.Message | types.CallbackQuery,
    state: FSMContext | None = None,
    repo: NoteRepository | None = None,
) -> None:
    """
    «🗂 Sources»: пронумерованный список «1. Source (N)» и кнопки-номера.

    Список вместе с количеством заметок сохраняется в FSMContext: в
    callback_data уходит только номер, поэтому длина имён источников
    и их количество не упираются в лимит 64 байта.

    ``repo`` можно передать готовым: вызывающий код (переименование
    источника) уже его получил и не создаёт второй объект.
    """
    if repo is None:
        repo = repo_of(event)
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
            await _remember_source_list(state, repo, counts)
    if not counts:
        await show_main_menu(event, buttons.NO_SOURCES, keyboards.back_to_main())
        return
    lines = [
        f"{index}. {escape_html(source)} -> {count}"
        for index, (source, count) in enumerate(counts, start=1)
    ]
    text = buttons.SOURCES_MANAGE_PROMPT + "\n\n" + "\n".join(lines)
    await send_parts(event, text, keyboards.sources_manage(len(counts)))


async def _remember_source_list(
    state: FSMContext,
    repo: NoteRepository,
    counts: list[tuple[str, int]] | None = None,
) -> None:
    """Сохраняет упорядоченный список источников для кнопок-номеров."""
    if counts is None:
        try:
            counts = await asyncio.to_thread(repo.list_sources_with_counts)
        except Exception as e:
            logger.warning("Could not remember source list: %s", e)
            return
    await state.update_data(
        source_list=[source for source, _ in counts],
        source_counts=[count for _, count in counts],
    )


async def _index_of_source(source: str, state: FSMContext) -> int | None:
    """Индекс источника в сохранённом списке (для кнопок списка заметок)."""
    data = await state.get_data()
    sources = [str(item) for item in data.get("source_list") or []]
    wanted = source.strip().lower()
    for index, saved in enumerate(sources):
        if saved.strip().lower() == wanted:
            return index
    return None


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


async def _resolve_saved_source(
    callback: types.CallbackQuery, state: FSMContext, prefix: str
) -> tuple[int, str, int] | None:
    """(номер в списке, источник, количество заметок) из callback_data + FSM."""
    index = Cb.parse_source_index(callback.data or "", prefix)
    if index is None:
        return None
    saved = await _saved_source(callback, state, index)
    if saved is None:
        return None
    source, count = saved
    return index, source, count


@sources_router.callback_query(F.data.startswith(f"{Cb.SRC_NOTES}:"))
async def cb_source_notes(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Все заметки источника по алфавиту (TITLE без учёта регистра)."""
    await ack_callback(callback)
    await state.clear()
    source = Cb.parse_source(callback.data or "", Cb.SRC_NOTES)
    if not source:
        return
    repo = repo_of(callback)
    if repo is None:
        return
    try:
        await _remember_source_list(state, repo)
        notes = await asyncio.to_thread(repo.list_by_source, source)
        await send_notes_list(callback, repo, source, notes, state)
    except Exception as e:
        await send_error_message(callback, e)


@sources_router.message(NoteStates.wait_source, F.text)
async def got_new_source(message: types.Message, state: FSMContext) -> None:
    """
    Получили имя источника: сохраняем его как пустой источник-болванку.

    ID и TITLE появятся вместе с первой заметкой (``repo.create`` заполнит
    эту же строку), а сейчас пользователь видит источник в списке.
    """
    source = (message.text or "").strip()
    if not source:
        await status_message(message, "⚠️ Source name must not be empty.")
        return
    await state.clear()
    repo = repo_of(message)
    if repo is None:
        return
    try:
        added = await asyncio.to_thread(repo.add_source, source)
    except ValueError:
        await status_message(message, "⚠️ Source name must not be empty.")
        return
    except Exception as e:
        await send_error_message(message, e)
        return
    template = buttons.SOURCE_ADDED if added else buttons.SOURCE_EXISTS
    text = template.format(source=escape_html(source))
    await status_message(message, text)
    await show_sources_manage(message, state)


@sources_router.callback_query(F.data == Cb.MENU_SOURCES)
async def cb_manage_sources(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«🗂 Sources»: нумерованный список источников."""
    await ack_callback(callback)
    await show_sources_manage(callback, state)


def _source_screen_text(source: str, notes: list[Note], headers: list[str]) -> str:
    """Заголовок источника, нумерованный список заметок и «Choose an action»."""
    head = buttons.SOURCE_MANAGE_PROMPT.format(source=escape_html(source))
    if not notes:
        return with_min_width(head)
    lines = [
        f"{position}. <code>{escape_html(note_title(note, headers))}</code>"
        for position, note in enumerate(notes, start=1)
    ]
    return with_min_width(head + "\n" + "\n".join(lines))


@sources_router.callback_query(F.data.startswith(f"{Cb.SRC_MANAGE}:"))
async def cb_source_manage(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Нажатие на номер: заметки источника — сразу номерами и действиями."""
    await ack_callback(callback)
    resolved = await _resolve_saved_source(callback, state, Cb.SRC_MANAGE)
    if resolved is None:
        return
    index, source, _ = resolved
    repo = repo_of(callback)
    if repo is None:
        return
    try:
        notes = await asyncio.to_thread(repo.list_by_source, source)
        headers = repo.headers()
    except Exception as e:
        await send_error_message(callback, e)
        return
    await show_main_menu(
        callback,
        _source_screen_text(source, notes, headers),
        keyboards.source_actions(index, [note.note_id for note in notes], source),
    )


@sources_router.callback_query(F.data.startswith(f"{Cb.SRC_SHOW_NOTES}:"))
async def cb_source_show_notes(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    """Совместимость: старые сообщения с «📂 Show notes»."""
    await ack_callback(callback)
    resolved = await _resolve_saved_source(callback, state, Cb.SRC_SHOW_NOTES)
    if resolved is None:
        return
    _, source, _ = resolved
    repo = repo_of(callback)
    if repo is None:
        return
    try:
        notes = await asyncio.to_thread(repo.list_by_source, source)
        await send_notes_list(callback, repo, source, notes, state)
    except Exception as e:
        await send_error_message(callback, e)


def _notes_digest(repo: NoteRepository, notes: list[Note]) -> list[str]:
    """Готовые карточки заметок: только поля, без ID/SCREEN и распорок."""
    headers = repo.headers()
    return [
        "\n".join(note_card_lines(headers, note.values, spacer=False)) for note in notes
    ]


async def _send_all_notes(
    event: types.Message | types.CallbackQuery,
    repo: NoteRepository,
    index: int,
    source: str,
) -> None:
    """Все заметки источника с полным содержанием (одним или частями)."""
    notes = await asyncio.to_thread(repo.list_by_source, source)
    if not notes:
        await show_main_menu(
            event,
            buttons.NO_NOTES.format(source=escape_html(source)),
            keyboards.source_notes_back(index),
        )
        return
    cards = await asyncio.to_thread(_notes_digest, repo, notes)
    text = notes_digest_text(source, len(notes), cards)
    markup = keyboards.source_notes_back(
        index, [note.note_id for note in notes], source
    )
    await send_parts(event, text, markup)


@sources_router.callback_query(F.data.startswith(f"{Cb.SRC_ALL_NOTES}:"))
async def cb_source_all_notes(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«📖 Show all notes»: полное содержимое всех заметок источника."""
    await ack_callback(callback)
    resolved = await _resolve_saved_source(callback, state, Cb.SRC_ALL_NOTES)
    if resolved is None:
        return
    index, source, _ = resolved
    repo = repo_of(callback)
    if repo is None:
        return
    try:
        await _send_all_notes(callback, repo, index, source)
    except Exception as e:
        await send_error_message(callback, e)


@sources_router.callback_query(F.data.startswith(f"{Cb.SRC_NEW_NOTE}:"))
async def cb_source_new_note(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«➕ Add note»: сразу к имени заметки для выбранного источника."""
    await ack_callback(callback)
    resolved = await _resolve_saved_source(callback, state, Cb.SRC_NEW_NOTE)
    if resolved is None:
        return
    _, source, _ = resolved
    await ask_note_title(callback, state, source)


@sources_router.callback_query(F.data.startswith(f"{Cb.SRC_RENAME}:"))
async def cb_source_rename(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«✏️ Rename»: запрашиваем новое имя источника."""
    await ack_callback(callback)
    resolved = await _resolve_saved_source(callback, state, Cb.SRC_RENAME)
    if resolved is None:
        return
    _, source, count = resolved
    await state.update_data(source=source)
    await state.set_state(NoteStates.wait_source_name)
    await show_main_menu(
        callback,
        buttons.SOURCE_RENAME_PROMPT.format(source=escape_html(source), count=count),
        keyboards.new_source_cancel(),
    )


def _source_note_count(source: str, counts: list[tuple[str, int]]) -> int:
    """Количество заметок источника в готовом списке (SOURCE без регистра)."""
    wanted = source.strip().lower()
    for saved, count in counts:
        if saved.strip().lower() == wanted:
            return count
    return 0


async def _go_to_renamed_source(
    message: types.Message,
    repo: NoteRepository,
    state: FSMContext,
    new_source: str,
) -> None:
    """Список источников в FSM + экран заметок нового имени (без лишнего экрана)."""
    counts = await asyncio.to_thread(repo.list_sources_with_counts)
    await _remember_source_list(state, repo, counts)
    if not new_source or not _source_note_count(new_source, counts):
        await show_sources_manage(event=message, repo=repo, state=state)
        return
    notes = await asyncio.to_thread(repo.list_by_source, new_source)
    await send_notes_list(message, repo, new_source, notes, state)


@sources_router.message(NoteStates.wait_source_name, F.text)
async def got_renamed_source(message: types.Message, state: FSMContext) -> None:
    """Новое имя источника: переименовываем все его заметки."""
    data = await state.get_data()
    old_source = str(data.get("source", "")).strip()
    new_source = (message.text or "").strip()
    await state.clear()
    repo = repo_of(message)
    if repo is None:
        return
    if not old_source:
        await status_message(message, buttons.SOURCE_LOST)
        await show_sources_manage(event=message, repo=repo, state=state)
        return
    try:
        updated = await asyncio.to_thread(repo.rename_source, old_source, new_source)
    except ValueError:
        await status_message(message, "⚠️ Source name must not be empty.")
        await show_sources_manage(event=message, repo=repo, state=state)
        return
    except Exception as e:
        await send_error_message(message, e)
        return
    await status_message(message, buttons.SOURCE_RENAMED.format(count=updated))
    try:
        # Общий экран «🗂 Sources» отдельным сообщением не шлём: список
        # источников обновляем в FSM и сразу открываем заметки нового имени.
        await _go_to_renamed_source(message, repo, state, new_source)
    except Exception as e:
        # Переименование уже прошло — об ошибке списка сообщаем, а не падаем
        await send_error_message(message, e)


@sources_router.callback_query(F.data.startswith(f"{Cb.SRC_DEL}:"))
async def cb_source_delete(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«🗑 Delete»: подтверждение с количеством удаляемых заметок."""
    await ack_callback(callback)
    resolved = await _resolve_saved_source(callback, state, Cb.SRC_DEL)
    if resolved is None:
        return
    index, source, count = resolved
    confirm = buttons.SOURCE_DELETE_CONFIRM.format(
        source=escape_html(source), count=count
    )
    await show_main_menu(
        callback, with_min_width(confirm), keyboards.source_delete_confirm(index)
    )


@sources_router.callback_query(F.data.startswith(f"{Cb.SRC_DEL_CONFIRM}:"))
async def cb_source_delete_confirm(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    """«✅ Yes, delete»: удаляет источник со всеми его заметками."""
    await ack_callback(callback)
    resolved = await _resolve_saved_source(callback, state, Cb.SRC_DEL_CONFIRM)
    if resolved is None:
        return
    _, source, _ = resolved
    repo = repo_of(callback)
    if repo is None:
        return
    try:
        deleted = await asyncio.to_thread(repo.delete_source, source)
    except Exception as e:
        await send_error_message(callback, e)
        return
    await status_message(callback, buttons.SOURCE_DELETED.format(count=deleted))
    await show_sources_manage(callback, state)


__all__ = [
    "send_notes_list",
    "show_sources_manage",
    "sources_router",
]
