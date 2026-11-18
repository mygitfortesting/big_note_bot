"""Тесты сценариев заметок: карточка, добавление, скриншот — без сети."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

import pytest

from config import NOTES_HEADERS
from handlers import notes_handler
from handlers.notes_handler import NoteContext, NoteStates
from services import buttons, keyboards
from services.callback import Cb
from services.sheets import (
    COMMENT_HEADER,
    NAME_HEADER,
    SCREEN_HEADER,
    SOURCE_HEADER,
    Note,
)
from services.text_utils import note_card_lines, split_messages
from tests.factories import note_row, repo_for, rows_by_header
from tests.fakes import FakeMessage, FakeState, FakeUser


@pytest.fixture()
def use_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Any], Any]:
    """
    Подменяет ``_repo`` хендлера готовым репозиторием на FakeSheet.

    Сигнатура макета повторяет настоящую: ``_repo(event)``. Если макет
    принимает другие аргументы, хендлер выбрасывает TypeError, его ловит
    ``send_error_message`` — и ``message.sent`` остаётся пустым, а тест
    падает на ``sent[-1]`` с IndexError, хотя логика верна.
    """

    def _install(repo: Any) -> Any:
        def _fake_repo(event: Any) -> Any:  # event — как у настоящей _repo
            return repo

        monkeypatch.setattr(notes_handler, "_repo", _fake_repo)
        return repo

    return _install


# --- Карточка: только заполненные поля ---


def test_card_outputs_only_filled_fields() -> None:
    """
    Пустые колонки в карточке пропускаются: у источника видны только его поля.

    Заметка «Blog» заполнена в DESCRIPTION/COMMENT, а URL/SCREEN пусты —
    в тексте не должно быть ни URL, ни SCREEN, ни ID.
    """
    values = ["id1", "Blog", "Post", "Desc", "", "", "Nice"]
    lines = note_card_lines(NOTES_HEADERS, values)
    text = "\n".join(lines)

    assert "SOURCE:" in text
    assert "NAME:" in text
    assert "DESCRIPTION:" in text
    assert "COMMENT:" in text
    assert "URL" not in text
    assert SCREEN_HEADER not in text
    assert "<b>ID:</b>" not in text


def test_card_skips_placeholders() -> None:
    values = ["id1", "Blog", "Post", "-", "—", "", ""]
    text = "\n".join(note_card_lines(NOTES_HEADERS, values))

    assert "DESCRIPTION" not in text
    assert "URL" not in text


def test_card_escapes_html() -> None:
    values = ["id1", "Blog", "<script>", "", "", "", ""]
    text = "\n".join(note_card_lines(NOTES_HEADERS, values))

    assert "&lt;script&gt;" in text
    assert "<script>" not in text


def test_card_shows_custom_columns() -> None:
    headers = [*NOTES_HEADERS[:-1], "PRICE", COMMENT_HEADER]
    values = ["id1", "Shop", "Item", "", "", "", "10$", ""]
    text = "\n".join(note_card_lines(headers, values))

    assert "PRICE:" in text
    assert COMMENT_HEADER not in text


def test_note_context_editable_columns_exclude_id_and_screen() -> None:
    ctx = NoteContext(
        note=Note(2, ["id1", "Blog", "Post", "", "", "", ""]),
        headers=list(NOTES_HEADERS),
        source="Blog",
    )
    editable = [name for _, name in ctx.editable_columns()]

    assert "ID" not in editable
    assert SCREEN_HEADER not in editable
    assert NAME_HEADER in editable
    assert COMMENT_HEADER in editable


def test_note_context_reads_screen_file_id() -> None:
    ctx = NoteContext(
        note=Note(2, ["id1", "Blog", "Post", "", "", "FILE_ID", ""]),
        headers=list(NOTES_HEADERS),
        source="Blog",
    )
    assert ctx.screen == "FILE_ID"


# --- Скриншот ---


class _Photo:
    """Один размер фото: Telegram отдаёт список, бот берёт последний."""

    def __init__(self, file_id: str) -> None:
        self.file_id = file_id


def test_screenshot_saves_file_id_into_screen(use_repo) -> None:
    """
    Фото приходит сценарием: ``message.photo[-1].file_id`` пишется в SCREEN.
    """
    repo, sheet = repo_for([note_row("1", "Blog", "Post")])
    use_repo(repo)
    state = FakeState()
    asyncio.run(state.update_data(note_id="1"))
    message = FakeMessage("", user_id=7)
    message.photo = [_Photo("small"), _Photo("big_file_id")]

    asyncio.run(notes_handler.got_photo(message, state))

    data = rows_by_header(sheet)
    assert data[0][SCREEN_HEADER] == "big_file_id"
    assert state.state is None


def test_screenshot_uses_screen_column_not_comment(use_repo) -> None:
    """SCREEN берётся по имени колонки, даже если пользовательские вклинились."""
    headers = [*NOTES_HEADERS[:-1], "RATING", COMMENT_HEADER]
    repo, sheet = repo_for([["1", "Blog", "Post", "", "", "", "", ""]], headers=headers)
    use_repo(repo)
    col = notes_handler._screen_column(repo)

    assert sheet.rows[0][col - 1] == SCREEN_HEADER


# --- Добавление заметки с выбором источника ---


def test_add_note_shows_sources_and_new_source_button(use_repo) -> None:
    """«➕ Add note»: сначала кнопки источников + «➕ Новый источник»."""
    repo, _ = repo_for([note_row("1", "Blog", "a"), note_row("2", "YouTube", "b")])
    use_repo(repo)
    message = FakeMessage("", user_id=1)
    message.from_user = FakeUser(1)
    state = FakeState()

    asyncio.run(notes_handler.cmd_add_note(message, state))

    # Пустой sent означает, что сценарий упал до отправки: показываем текст
    # того, что бот всё-таки ответил (send_error_message), чтобы видеть причину.
    assert message.sent, f"Bot sent nothing: {message.sent}"
    sent = message.sent[-1]
    assert sent["text"] == buttons.NEW_NOTE_PROMPT
    keyboard = sent["reply_markup"].inline_keyboard
    datas = [button.callback_data for row in keyboard for button in row]
    assert Cb.src_select("Blog") in datas
    assert Cb.src_select("YouTube") in datas
    assert Cb.SRC_ADD in datas
    texts = [button.text for row in keyboard for button in row]
    assert "🗂 Blog" in texts
    assert "🗂 YouTube" in texts
    assert buttons.NEW_SOURCE in texts
    assert state.state is None


def test_add_note_without_sources_asks_for_new_source(use_repo) -> None:
    """Нет источников — бот сразу просит имя нового источника."""
    repo, _ = repo_for([])
    use_repo(repo)
    message = FakeMessage("", user_id=1)
    message.from_user = FakeUser(1)
    state = FakeState()

    asyncio.run(notes_handler.cmd_add_note(message, state))

    assert message.sent, f"Bot sent nothing: {message.sent}"
    sent = message.sent[-1]
    assert sent["text"] == buttons.NEW_SOURCE_PROMPT
    assert state.state == NoteStates.wait_source


def test_note_card_has_only_four_buttons() -> None:
    """В карточке нет кнопок правки отдельных полей — только 4 кнопки."""
    markup = keyboards.note_card("id1", "Blog")
    keyboard = markup.inline_keyboard
    texts = [button.text for row in keyboard for button in row]

    assert texts == [
        buttons.EDIT_FIELD,
        buttons.PHOTO,
        buttons.DELETE_NOTE,
        buttons.BACK,
        buttons.TO_MAIN,
    ]
    datas = [button.callback_data for row in keyboard for button in row]
    assert Cb.note_edit_menu("id1") in datas
    assert Cb.note_edit_cell("id1", 2) not in datas
    field_labels = (
        f"✏️ {SOURCE_HEADER}",
        f"✏️ {NAME_HEADER}",
        "✏️ DESCRIPTION",
        "✏️ URL",
        f"✏️ {COMMENT_HEADER}",
    )
    assert not any(label in texts for label in field_labels)


def test_select_source_then_name_creates_note(use_repo) -> None:
    """Выбор источника ➔ ввод имени ➔ заметка создана и отсортирована."""
    repo, sheet = repo_for([note_row("1", "YouTube", "b")])
    use_repo(repo)
    state = FakeState()

    asyncio.run(
        notes_handler.cb_select_source(_callback(Cb.src_select("YouTube")), state)
    )
    assert state.state == NoteStates.wait_name
    assert state.data["source"] == "YouTube"

    message = FakeMessage("Zebra", user_id=1)
    asyncio.run(notes_handler.got_note_name(message, state))

    data = rows_by_header(sheet)
    names = [row[NAME_HEADER] for row in data]
    assert names == ["b", "Zebra"]
    assert all(row[SOURCE_HEADER] == "YouTube" for row in data)
    assert state.state is None


def test_new_source_asks_for_first_note_not_empty_row(use_repo) -> None:
    """«🗂 Add source»: после имени источника бот сразу просит имя заметки."""
    repo, sheet = repo_for([])
    use_repo(repo)
    message = FakeMessage("Instagram", user_id=1)
    state = FakeState()
    asyncio.run(state.set_state(NoteStates.wait_source))

    asyncio.run(notes_handler.got_new_source(message, state))

    assert state.state == NoteStates.wait_name
    assert state.data["source"] == "Instagram"
    assert sheet.rows == [NOTES_HEADERS]  # пустая строка не появилась

    name_message = FakeMessage("My first post", user_id=1)
    asyncio.run(notes_handler.got_note_name(name_message, state))

    data = rows_by_header(sheet)
    assert len(data) == 1
    assert data[0][SOURCE_HEADER] == "Instagram"
    assert data[0][NAME_HEADER] == "My first post"


def test_edit_field_accepts_any_column_except_id_and_screen() -> None:
    """Полевая правка доступна для любой колонки, кроме ID и SCREEN."""
    headers = [*NOTES_HEADERS[:-1], "RATING", COMMENT_HEADER]
    ctx = NoteContext(
        note=Note(2, ["id1", "Shop", "Item", "", "", "", "", ""]),
        headers=headers,
        source="Shop",
    )
    markup = keyboards.edit_fields_only(ctx.editable_columns(), "id1")
    datas = [button.callback_data for row in markup.inline_keyboard for button in row]

    rating_col = headers.index("RATING") + 1
    screen_col = headers.index(SCREEN_HEADER) + 1
    assert Cb.note_edit_cell("id1", rating_col) in datas
    assert Cb.note_edit_cell("id1", 1) not in datas  # ID
    assert Cb.note_edit_cell("id1", screen_col) not in datas


# --- Вспомогательные объекты ---


class _FakeCallback:
    """CallbackQuery, достаточный для сценариев навигации."""

    def __init__(self, data: str, user_id: int = 1) -> None:
        self.data = data
        self.from_user = FakeUser(user_id)
        self.answers: list[dict[str, Any]] = []
        self.message = FakeMessage("", user_id)

    async def answer(self, text: str | None = None, **kw: Any) -> None:
        self.answers.append({"text": text, **kw})


def _callback(data: str, user_id: int = 1) -> Any:
    return _FakeCallback(data, user_id)


def test_split_messages_respects_limit() -> None:
    text = "\n".join(f"line {index}" for index in range(200))
    parts = split_messages(text, limit=100)

    assert all(len(part) <= 100 for part in parts)
    assert "".join(parts) == text


def test_split_messages_short_text_single_part() -> None:
    assert split_messages("hello") == ["hello"]
    assert split_messages("") == []
