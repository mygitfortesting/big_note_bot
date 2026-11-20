"""
✨ AI Note: умное создание заметки из скриншота, ссылки или пересланного текста.

Сценарий: «✨ AI Note» ➔ сообщение (текст / ссылка / фото) ➔ статус анализа
➔ предпросмотр черновика «✨ AI Draft» с кнопками «✅ Save», «🗂 Change source»,
«✏️ Edit title» и «✖️ Cancel».

Разбор сообщения живёт в ``services.ai_parser`` (там же загрузка страницы по
ссылке), запись — в ``NoteRepository`` под ``user_lock(user_id)``. Карточку
готовой заметки открывает ``handlers.notes_handler.show_note``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import ai_api_key, ai_base_url, ai_model
from handlers.notes_common import repo_of
from services import buttons, keyboards
from services.ai_parser import (
    SERVICE_HEADERS,
    AiNoteDraft,
    AiNoteParser,
    AiVisionError,
    draft_fields,
    field_headers,
)
from services.callback import Cb
from services.sheets import (
    ID_HEADER,
    NoteRepository,
    headers_of,
    is_empty,
    screen_slot_index,
)
from services.telegram_io import (
    ack_callback,
    send_error_message,
    show_main_menu,
    status_message,
    user_id_of,
    user_lock,
)
from services.text_utils import escape_html, with_min_width

logger = logging.getLogger(__name__)

ai_router = Router(name="ai_note")

#: Колонки, которые пишет не ``draft_fields``, а сам сценарий.
ASSIGNED_HEADERS: set[str] = set(SERVICE_HEADERS)


class AiStates(StatesGroup):
    """Состояния ✨ AI Note (отдельно от NoteStates: роутеры не пересекаются)."""

    wait_message = State()  # ждём текст, ссылку или фото для разбора


#: Источник для черновика указан неверно (пусто или с символом ':').
NO_VALID_SOURCE = "⚠️ Source name must be non-empty and without ':' symbol."


# --- Разбор сообщения ---


def ai_client() -> Any:
    """Клиент модели из окружения; RuntimeError — AI-ключ не настроен."""
    key = ai_api_key()
    if not key:
        raise RuntimeError("AI_API_KEY is not set.")
    from openai import AsyncOpenAI

    return AsyncOpenAI(api_key=key, base_url=ai_base_url())


def ai_fields(headers: list[str]) -> list[str]:
    """Колонки для AI в порядке шапки (без ID/SOURCE/TITLE/SCREEN/COMMENT)."""
    return field_headers(headers, screen_slot_index(headers))


def ai_service() -> AiNoteParser:
    """Парсер модели из окружения (тесты подменяют эту функцию)."""
    return AiNoteParser(ai_client(), ai_model())


def _pick_source(source: str, known: list[str]) -> str:
    """Источник из ответа модели или ближайшее существующее имя (без регистра)."""
    wanted = (source or "").strip()
    for name in known:
        if wanted and name.strip().lower() == wanted.lower():
            return name.strip()
    return wanted


async def analyze(
    message: types.Message, repo: NoteRepository, image: bytes | None = None
) -> AiNoteDraft:
    """Разбор сообщения: чтение листа и модель — в отдельных потоках."""
    sources = await asyncio.to_thread(repo.list_sources)
    headers = await asyncio.to_thread(repo.headers)
    prompt = await asyncio.to_thread(repo.get_ai_prompt)
    parser = ai_service()
    file_id = message.photo[-1].file_id if message.photo else ""
    text = message.caption or "" if message.photo else (message.text or "")
    draft = await asyncio.to_thread(
        parser.parse,
        text,
        image,
        file_id,
        sources,
        ai_fields(headers),
        prompt,
    )
    draft.source = _pick_source(draft.source, sources)
    return draft


async def _download_photo(message: types.Message) -> bytes | None:
    """Байты самой большой фотографии сообщения (None — скачать не вышло)."""
    if not message.photo:
        return None
    download = getattr(message.bot, "download", None) if message.bot else None
    if not callable(download):
        return None
    try:
        buffer = await download(message.photo[-1])
        return bytes(buffer.getvalue())
    except Exception as e:
        logger.warning("Could not download AI Note photo: %s", e)
        return None


# --- Предпросмотр черновика ---


def _field_text(headers: list[str], values: dict[str, str]) -> str:
    """Текст предпросмотра: заполненные поля черновика (по именам колонок)."""
    lines = [buttons.AI_DRAFT_PROMPT, ""]
    for header in headers:
        value = values.get(header.strip(), "")
        if value and not is_empty(value):
            lines.append(f"<b>{escape_html(header)}:</b> {escape_html(value)}")
    return with_min_width("\n".join(lines))


def _has_content(draft: AiNoteDraft) -> bool:
    """Есть ли в черновике хоть что-то, что можно сохранить."""
    return bool(draft.title or draft.fields)


def preview_values(draft: AiNoteDraft) -> dict[str, str]:
    """Значения строк предпросмотра по именам колонок листа."""
    return {
        "SOURCE": draft.source,
        "TITLE": draft.title,
        **draft.fields,
        "SCREEN": "✅ screenshot attached" if draft.screen else "",
    }


def preview_headers(headers: list[str], draft: AiNoteDraft) -> list[str]:
    """
    Порядок строк предпросмотра — как в таблице пользователя.

    Все колонки, кроме ``ID`` и ``COMMENT``; неизвестные шапке поля черновика
    (если лист поменяли после разбора) дописываются в конце, перед ``SCREEN``.
    """
    order = [
        header.strip()
        for header in headers
        if header.strip().upper() not in {"ID", "COMMENT"}
    ]
    known = {name.upper() for name in order}
    extra = [name for name in draft.fields if name.upper() not in known]
    screen_at = next(
        (index for index, name in enumerate(order) if name.upper() == "SCREEN"),
        len(order),
    )
    return [*order[:screen_at], *extra, *order[screen_at:]]


async def _remember_headers(state: FSMContext, repo: NoteRepository) -> list[str]:
    """Актуальная шапка листа в FSMContext — для перерисовки предпросмотра."""
    headers = list(await asyncio.to_thread(repo.headers))
    if headers:
        await state.update_data(ai_headers=headers)
    return headers


async def show_draft(
    event: types.Message | types.CallbackQuery,
    state: FSMContext,
    draft: AiNoteDraft,
    repo: NoteRepository | None = None,
    headers: list[str] | None = None,
) -> None:
    """
    Сохраняет черновик в FSM и показывает предпросмотр с кнопками.

    Порядок полей — по актуальной шапке листа (``repo.headers()``), если она
    известна; иначе берём сохранённую в FSM. Заполненные моделью колонки
    (``PRICE``, ``SUMMARY``, ...) видно до нажатия «✅ Save».
    """
    if headers is None:
        if repo is not None:
            headers = await _remember_headers(state, repo)
        else:
            data = await state.get_data()
            saved = data.get("ai_headers") or []
            headers = [str(name) for name in saved]
    if not headers:
        headers = ["SOURCE", "TITLE", "SCREEN"]
    await state.update_data(ai_headers=headers)
    await state.set_state(AiStates.wait_message)
    await state.update_data(draft=draft, mode="draft")
    text = _field_text(preview_headers(headers, draft), preview_values(draft))
    await show_main_menu(event, text, keyboards.ai_draft())


async def _remember_sources(state: FSMContext, repo: NoteRepository) -> list[str]:
    """Список источников в FSMContext: в callback_data едет только номер."""
    sources = list(await asyncio.to_thread(repo.list_sources))
    await state.update_data(ai_source_list=sources)
    return sources


# --- Запись заметки ---


def _row_for_values(
    headers: list[str], source: str, title: str, values: dict[str, str]
) -> list[str]:
    """Строка листа: ID — пустой (его выдаст ``create``), остальное по именам."""
    row = [""] * len(headers)
    upper = {header.strip().upper(): index for index, header in enumerate(headers)}
    for name, value in values.items():
        index = upper.get(name.strip().upper())
        if index is not None:
            row[index] = value
    row[upper["SOURCE"]] = source
    row[upper["TITLE"]] = title
    return row


def _id_position(headers: list[str]) -> int:
    """Индекс колонки ID в шапке (0, если её нет — по договорённости это A)."""
    for index, name in enumerate(headers):
        if name.strip().upper() == ID_HEADER:
            return index
    return 0


def _append_note(repo: NoteRepository, draft: AiNoteDraft) -> str:
    """
    Создаёт заметку из черновика и возвращает её ID (вызывать в потоке).

    ``create`` уже записал строку и обновил снимок в кэше, поэтому поля
    черновика дописываются прямо в этот снимок: лишнего чтения листа нет.
    """
    source = draft.source.strip()
    title = draft.title.strip()
    note_id = repo.create(source, title)
    snapshot = repo.all_values()
    headers = headers_of(snapshot) or list(repo.headers())
    row = _row_for_values(headers, source, title, draft_fields(draft))
    id_col = _id_position(headers)
    found = next(
        (
            position
            for position, item in enumerate(snapshot)
            if position and item and item[id_col].strip() == note_id
        ),
        None,
    )
    if found is None:
        return note_id
    current = list(snapshot[found]) + [""] * (len(headers) - len(snapshot[found]))
    # ID остаётся прежним, остальные пустые ячейки заполняются черновиком.
    snapshot[found] = [current[index] or row[index] for index in range(len(headers))]
    repo._persist_sorted(headers, snapshot)
    return note_id


# --- Хендлеры ---


async def ask_ai_note(
    event: types.Message | types.CallbackQuery, state: FSMContext
) -> None:
    """Экран ✨ AI Note: ждём скриншот, ссылку или текст."""
    await state.clear()
    if not ai_api_key():
        await show_main_menu(event, buttons.AI_NO_KEY, keyboards.back_to_main())
        return
    await state.set_state(AiStates.wait_message)
    await state.update_data(mode="input")
    await show_main_menu(
        event, with_min_width(buttons.AI_NOTE_PROMPT), keyboards.new_source_cancel()
    )


@ai_router.callback_query(F.data == Cb.AI_NOTE)
async def cb_ai_note(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«✨ AI Note»: приём сообщения или объяснение, что ключ не настроен."""
    await ack_callback(callback)
    await ask_ai_note(callback, state)


@ai_router.message(AiStates.wait_message, F.text | F.photo)
async def got_ai_message(message: types.Message, state: FSMContext) -> None:
    """Текст, ссылка или фото: анализ и предпросмотр черновика."""
    data = await state.get_data()
    mode = str(data.get("mode", ""))
    repo = repo_of(message)
    if repo is None:
        return
    if mode == "source_new":
        await _save_new_source(message, state, repo)
        return
    if mode == "title":
        await _save_title(message, state, repo)
        return
    if mode == "draft":
        # В режиме предпросмотра текст — это подсказка, а не новое сообщение
        await status_message(message, buttons.AI_NOTE_PROMPT)
        return
    if not ai_api_key():
        await status_message(message, buttons.AI_NO_KEY)
        await ask_ai_note(message, state)
        return
    await status_message(message, buttons.AI_ANALYZING)
    try:
        image = await _download_photo(message)
        draft = await analyze(message, repo, image)
    except AiVisionError as e:
        # Модель из AI_MODEL не умеет смотреть картинки — подсказываем Vision.
        logger.warning("AI Note photo rejected by model: %s", e)
        await show_main_menu(
            message, buttons.AI_NO_VISION, keyboards.new_source_cancel()
        )
        return
    except Exception as e:
        logger.warning("AI Note parsing failed: %s", e)
        await send_error_message(message, e)
        await show_main_menu(message, buttons.AI_FAILED, keyboards.new_source_cancel())
        return
    if not _has_content(draft):
        await show_main_menu(
            message, buttons.AI_DRAFT_EMPTY, keyboards.new_source_cancel()
        )
        return
    await show_draft(message, state, draft, repo=repo)


async def _save_title(
    message: types.Message, state: FSMContext, repo: NoteRepository
) -> None:
    """Свой TITLE черновика: обновляем предпросмотр, в лист пока не пишем."""
    title = (message.text or "").strip()
    if not title:
        await status_message(message, buttons.AI_TITLE_EMPTY)
        return
    draft = _saved_draft(await state.get_data())
    if draft is None:
        await ask_ai_note(message, state)
        return
    draft.title = title
    await show_draft(message, state, draft, repo=repo)


async def _save_new_source(
    message: types.Message, state: FSMContext, repo: NoteRepository
) -> None:
    """Новый источник для черновика: сохраняем его и снова предпросмотр."""
    name = (message.text or "").strip()
    draft = _saved_draft(await state.get_data())
    if draft is None:
        await ask_ai_note(message, state)
        return
    if not name or ":" in name:
        await status_message(message, NO_VALID_SOURCE)
        return
    try:
        await asyncio.to_thread(repo.add_source, name)
    except Exception as e:
        await send_error_message(message, e)
        return
    draft.source = name
    await status_message(message, buttons.SOURCE_ADDED.format(source=escape_html(name)))
    await show_draft(message, state, draft, repo=repo)


@ai_router.callback_query(AiStates.wait_message, F.data == Cb.SRC_ADD)
async def cb_add_ai_source(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«➕ New source» в черновике: ждём имя нового источника.

    Только внутри сценария ✨ AI Note: кнопка из обычного меню остаётся
    за хендлером мастера (``wizard_handler``).
    """
    await ack_callback(callback)
    if _saved_draft(await state.get_data()) is None:
        await ask_ai_note(callback, state)
        return
    await state.update_data(mode="source_new")
    await show_main_menu(
        callback,
        buttons.NEW_SOURCE_PROMPT.format(existing=""),
        keyboards.new_source_cancel(),
    )


def _saved_draft(data: dict[str, Any]) -> AiNoteDraft | None:
    """Черновик из FSMContext (None — сценарий потерян после рестарта)."""
    draft = data.get("draft")
    return draft if isinstance(draft, AiNoteDraft) else None


@ai_router.callback_query(F.data == Cb.AI_TITLE)
async def cb_ai_title(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«✏️ Edit title»: просим заголовок для черновика."""
    await ack_callback(callback)
    draft = _saved_draft(await state.get_data())
    if draft is None:
        await ask_ai_note(callback, state)
        return
    await state.update_data(mode="title")
    await show_main_menu(
        callback,
        buttons.AI_TITLE_PROMPT.format(title=escape_html(draft.title or "—")),
        keyboards.new_source_cancel(),
    )


@ai_router.callback_query(F.data == Cb.AI_SOURCE)
async def cb_ai_source(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«🗂 Change source»: список источников кнопками (или новый)."""
    await ack_callback(callback)
    draft = _saved_draft(await state.get_data())
    repo = repo_of(callback)
    if draft is None or repo is None:
        await ask_ai_note(callback, state)
        return
    try:
        sources = await _remember_sources(state, repo)
    except Exception as e:
        await send_error_message(callback, e)
        return
    await state.update_data(mode="source")
    await show_main_menu(
        callback,
        f"🗂 <b>Change source</b>\n\nCurrent: <b>{escape_html(draft.source)}</b>",
        keyboards.ai_sources(sources),
    )


@ai_router.callback_query(F.data.startswith(f"{Cb.AI_SOURCE}:"))
async def cb_ai_pick_source(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Выбран существующий источник из списка черновика."""
    await ack_callback(callback)
    data = await state.get_data()
    draft = _saved_draft(data)
    index = Cb.parse_source_index(callback.data or "", Cb.AI_SOURCE)
    sources = [str(source) for source in data.get("ai_source_list") or []]
    if draft is None or index is None or not 0 <= index < len(sources):
        await show_main_menu(callback, buttons.SOURCE_LOST, keyboards.ai_draft())
        return
    draft.source = sources[index]
    await show_draft(callback, state, draft, repo=repo_of(callback))


@ai_router.callback_query(F.data == Cb.AI_SAVE)
async def cb_ai_save(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«✅ Save»: создаём заметку из черновика и открываем её карточку."""
    from handlers.notes_handler import show_note

    await ack_callback(callback)
    draft = _saved_draft(await state.get_data())
    repo = repo_of(callback)
    await state.clear()
    if repo is None:
        return
    if draft is None:
        draft = AiNoteDraft()
    if not draft.source.strip():
        draft.source = "AI Notes"
    user_id = user_id_of(callback) or repo._user_id
    async with user_lock(user_id):
        try:
            note_id = await asyncio.to_thread(_append_note, repo, draft)
        except Exception as e:
            await send_error_message(callback, e)
            return
    await status_message(callback, buttons.NOTE_SAVED)
    await show_note(callback, note_id)


__all__ = [
    "AiStates",
    "ai_client",
    "ai_router",
    "ai_service",
    "analyze",
    "ask_ai_note",
    "show_draft",
]
