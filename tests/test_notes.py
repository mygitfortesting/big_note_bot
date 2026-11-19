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
    ID_HEADER,
    SCREEN_HEADER,
    SOURCE_HEADER,
    TITLE_HEADER,
    Note,
)
from services.text_utils import note_card_lines, split_messages
from tests.factories import note_row, repo_for, rows_by_header
from tests.fakes import FakeCallback, FakeMessage, FakeState, FakeUser


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
    assert "TITLE:" in text
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


def test_card_fields_separated_by_blank_line() -> None:
    """Финальный текст карточки разделяет поля пустой строкой."""
    ctx = NoteContext(
        note=Note(2, ["id1", "Blog", "Post", "Desc", "", "", "Nice"]),
        headers=list(NOTES_HEADERS),
        source="Blog",
    )

    blocks = ctx.card_text().split("\n\n")

    assert len(blocks) == 5
    assert blocks[0].startswith("<b>SOURCE:</b>")
    assert "\n" not in ctx.card_text().replace("\n\n", "")


def test_note_context_editable_columns_exclude_id_and_screen() -> None:
    ctx = NoteContext(
        note=Note(2, ["id1", "Blog", "Post", "", "", "", ""]),
        headers=list(NOTES_HEADERS),
        source="Blog",
    )
    editable = [name for _, name in ctx.editable_columns()]

    assert "ID" not in editable
    assert SCREEN_HEADER not in editable
    assert TITLE_HEADER in editable
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
    assert sent["text"] == buttons.NEW_SOURCE_PROMPT.format(existing="\n")
    assert state.state == NoteStates.wait_source


def test_add_source_lists_existing_sources_and_cancel(use_repo) -> None:
    """«➕ Add source»: список существующих источников + кнопка «✖️ Cancel»."""
    repo, _ = repo_for(
        [
            note_row("1", "Telegram", "a"),
            note_row("2", "Instagram", "b"),
            note_row("3", "Instagram", "c"),
        ]
    )
    use_repo(repo)
    message = FakeMessage("", user_id=1)
    message.from_user = FakeUser(1)
    state = FakeState()

    asyncio.run(notes_handler.ask_new_source(message, state))

    assert message.sent, f"Bot sent nothing: {message.sent}"
    sent = message.sent[-1]
    text = sent["text"]
    assert "Existing:" in text
    assert "<code>Instagram</code>, <code>Telegram</code>" in text
    assert text.count("<code>Instagram</code>") == 1  # источники уникальны

    keyboard = sent["reply_markup"].inline_keyboard
    datas = [button.callback_data for row in keyboard for button in row]
    assert Cb.CANCEL_INPUT in datas
    assert Cb.TO_MAIN in datas
    cancel = [button for row in keyboard for button in row]
    assert any(
        button.text == buttons.CANCEL and button.callback_data == Cb.CANCEL_INPUT
        for button in cancel
    )
    assert state.state == NoteStates.wait_source


def test_add_source_escapes_existing_sources(use_repo) -> None:
    """Имя источника не может подменить разметку сообщения."""
    repo, _ = repo_for([note_row("1", "<b>Shop</b>", "a")])
    use_repo(repo)
    message = FakeMessage("", user_id=1)
    message.from_user = FakeUser(1)

    asyncio.run(notes_handler.ask_new_source(message, FakeState()))

    assert message.sent, f"Bot sent nothing: {message.sent}"
    text = message.sent[-1]["text"]
    assert "<code>&lt;b&gt;Shop&lt;/b&gt;</code>" in text
    assert "<b>Shop</b>" not in text


def test_add_source_without_sources_has_no_existing_line(use_repo) -> None:
    """Нет источников — стандартный промпт без строки «Existing:»."""
    repo, _ = repo_for([])
    use_repo(repo)
    message = FakeMessage("", user_id=1)
    message.from_user = FakeUser(1)

    asyncio.run(notes_handler.ask_new_source(message, FakeState()))

    assert message.sent, f"Bot sent nothing: {message.sent}"
    sent = message.sent[-1]
    assert sent["text"] == buttons.NEW_SOURCE_PROMPT.format(existing="\n")
    keyboard = sent["reply_markup"].inline_keyboard
    datas = [button.callback_data for row in keyboard for button in row]
    assert Cb.CANCEL_INPUT in datas


def test_note_card_has_only_four_buttons() -> None:
    """В карточке нет кнопок правки отдельных полей — только 4 кнопки."""
    markup = keyboards.note_card("id1", "Blog")
    keyboard = markup.inline_keyboard
    texts = [button.text for row in keyboard for button in row]

    assert texts == [
        buttons.EDIT_FIELD,
        buttons.DELETE_NOTE,
        buttons.PHOTO,
        buttons.BACK,
        buttons.TO_MAIN,
    ]
    datas = [button.callback_data for row in keyboard for button in row]
    assert Cb.note_edit_menu("id1") in datas
    assert Cb.note_edit_cell("id1", 2) not in datas
    field_labels = (
        f"✏️ {SOURCE_HEADER}",
        f"✏️ {TITLE_HEADER}",
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
    assert state.state == NoteStates.wait_title
    assert state.data["source"] == "YouTube"

    message = FakeMessage("Zebra", user_id=1)
    asyncio.run(notes_handler.got_note_title(message, state))

    data = rows_by_header(sheet)
    titles = [row[TITLE_HEADER] for row in data]
    assert titles == ["b", "Zebra"]
    assert all(row[SOURCE_HEADER] == "YouTube" for row in data)
    # После TITLE начинается мастер заполнения колонок.
    assert state.state == NoteStates.wizard_step


def test_new_source_is_added_as_blank_source(use_repo) -> None:
    """«🗂 Add source»: источник сохраняется сразу, без заметки и с 0."""
    repo, sheet = repo_for([])
    use_repo(repo)
    message = FakeMessage("Instagram", user_id=1)
    state = FakeState()
    asyncio.run(state.set_state(NoteStates.wait_source))

    asyncio.run(notes_handler.got_new_source(message, state))

    assert state.state is None
    assert any("Source added" in item["text"] for item in message.sent)
    # Шапка + строка-болванка: только SOURCE, без ID и TITLE.
    assert len(sheet.rows) == 2
    assert sheet.rows[1][0] == ""  # ID болванке не выдан
    assert sheet.rows[1][1] == "Instagram"
    assert sheet.rows[1][2] == ""
    assert repo.list_sources_with_counts() == [("Instagram", 0)]
    assert repo.list_notes() == []

    note_id = repo.create("Instagram", "My first post")

    data = rows_by_header(sheet)
    assert len(data) == 1  # та же строка, без дубликата
    assert data[0][ID_HEADER] == note_id
    assert data[0][SOURCE_HEADER] == "Instagram"
    assert data[0][TITLE_HEADER] == "My first post"
    assert repo.list_sources_with_counts() == [("Instagram", 1)]


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


def _callback(data: str, user_id: int = 1) -> Any:
    """CallbackQuery-двойник из ``tests.fakes`` (без pydantic-валидации)."""
    return FakeCallback(data, user_id)


def test_add_source_button_lists_existing_sources(use_repo) -> None:
    """Кнопка «➕ New source» ведёт на экран со списком существующих."""
    repo, _ = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    callback = _callback(Cb.SRC_ADD)
    state = FakeState()

    asyncio.run(notes_handler.cb_add_source(callback, state))

    assert callback.message.sent, f"Bot sent nothing: {callback.message.sent}"
    sent = callback.message.sent[-1]
    assert "Existing: <code>Blog</code>" in sent["text"]
    keyboard = sent["reply_markup"].inline_keyboard
    buttons_flat = [button for row in keyboard for button in row]
    datas = [button.callback_data for button in buttons_flat]
    assert Cb.CANCEL_INPUT in datas
    assert any(
        button.text == buttons.CANCEL and button.callback_data == Cb.CANCEL_INPUT
        for button in buttons_flat
    )
    assert state.state == NoteStates.wait_source


def test_sources_menu_numbers_and_opens_actions(use_repo) -> None:
    """«🗂 Sources» → список по номерам → выбор номера → действия."""
    repo, _ = repo_for([note_row("1", "Blog", "a"), note_row("2", "YouTube", "b")])
    use_repo(repo)
    state = FakeState()

    open_callback = _callback(Cb.MENU_SOURCES)
    asyncio.run(notes_handler.cb_manage_sources(open_callback, state))

    sent = open_callback.message.sent[-1]
    assert "1. Blog (1)" in sent["text"]
    assert "2. YouTube (1)" in sent["text"]
    numbers = [
        button.text for row in sent["reply_markup"].inline_keyboard for button in row
    ]
    assert "1" in numbers
    assert "2" in numbers
    assert state.data["source_list"] == ["Blog", "YouTube"]

    manage_callback = _callback(Cb.src_manage(0))
    asyncio.run(notes_handler.cb_source_manage(manage_callback, state))

    action = manage_callback.message.sent[-1]
    text = action["text"]
    assert "Notes:" not in text
    assert "<b>Blog</b>" in text
    assert "1. <code>a</code>" in text
    assert "Choose an action:" in text
    rows = action["reply_markup"].inline_keyboard
    texts = [button.text for row in rows for button in row]
    datas = [button.callback_data for row in rows for button in row]
    assert "1" in texts
    assert Cb.note_view("1", "Blog") in datas
    assert Cb.src_new_note(0) in datas
    assert Cb.src_rename(0) in datas
    assert Cb.src_del(0) in datas
    assert Cb.src_show_notes(0) not in datas
    assert "📂 Show notes" not in texts
    assert buttons.RENAME_SOURCE in texts
    assert buttons.DELETE_SOURCE in texts


def test_source_actions_legacy_show_notes(use_repo) -> None:
    """Старый callback «📂 Show notes» ведёт к списку заметок источника."""
    repo, _ = repo_for([note_row("1", "Blog", "a"), note_row("2", "Blog", "b")])
    use_repo(repo)
    state = FakeState()

    asyncio.run(notes_handler.cb_manage_sources(_callback(Cb.MENU_SOURCES), state))
    show_callback = _callback(Cb.src_show_notes(0))
    asyncio.run(notes_handler.cb_source_show_notes(show_callback, state))

    sent = show_callback.message.sent[-1]
    assert "2 note(s)" in sent["text"]
    texts = [
        button.text for row in sent["reply_markup"].inline_keyboard for button in row
    ]
    assert texts[:2] == ["1", "2"]


def test_empty_source_keeps_action_buttons(use_repo) -> None:
    """Пустой источник: «There are no notes…» с теми же действиями."""
    repo, _ = repo_for([])
    repo.add_source("Podcast")
    use_repo(repo)
    state = FakeState()

    asyncio.run(notes_handler.cb_manage_sources(_callback(Cb.MENU_SOURCES), state))
    show_callback = _callback(Cb.src_show_notes(0))
    asyncio.run(notes_handler.cb_source_show_notes(show_callback, state))

    sent = show_callback.message.sent[-1]
    assert "There are no notes in <b>Podcast</b> yet." in sent["text"]
    rows = sent["reply_markup"].inline_keyboard
    texts = [button.text for row in rows for button in row]
    datas = [button.callback_data for row in rows for button in row]
    # Список заметок: «➕ Add Note» + «✏️ Edit Source», затем действия и меню.
    assert texts == [
        buttons.ADD_NOTE_ROW,
        buttons.EDIT_SOURCE,
        buttons.BACK,
        buttons.TO_MAIN,
    ]
    assert Cb.src_new_note(0) in datas
    assert Cb.src_rename(0) in datas
    assert Cb.MENU_SOURCES in datas


def test_empty_source_manage_screen(use_repo) -> None:
    """Экран источника без заметок: заголовок и действия с подписями."""
    repo, _ = repo_for([])
    repo.add_source("Podcast")
    use_repo(repo)
    state = FakeState()

    asyncio.run(notes_handler.cb_manage_sources(_callback(Cb.MENU_SOURCES), state))
    manage_callback = _callback(Cb.src_manage(0))
    asyncio.run(notes_handler.cb_source_manage(manage_callback, state))

    sent = manage_callback.message.sent[-1]
    assert "<b>Podcast</b>" in sent["text"]
    assert "Choose an action:" in sent["text"]
    texts = [
        button.text for row in sent["reply_markup"].inline_keyboard for button in row
    ]
    assert texts == [
        buttons.SHOW_ALL_NOTES,
        buttons.ADD_NOTE,
        buttons.RENAME_SOURCE,
        buttons.DELETE_SOURCE,
        buttons.BACK,
        buttons.TO_MAIN,
    ]


def test_source_actions_show_all_notes(use_repo) -> None:
    """«📖 Show all notes»: полные поля всех заметок источника."""
    repo, _ = repo_for(
        [
            note_row("1", "Blog", "First", description="Desc one"),
            note_row("2", "Blog", "Second", comment="Note two"),
        ]
    )
    use_repo(repo)
    state = FakeState()

    asyncio.run(notes_handler.cb_manage_sources(_callback(Cb.MENU_SOURCES), state))
    manage_callback = _callback(Cb.src_manage(0))
    asyncio.run(notes_handler.cb_source_manage(manage_callback, state))

    manage = manage_callback.message.sent[-1]
    datas = [
        button.callback_data
        for row in manage["reply_markup"].inline_keyboard
        for button in row
    ]
    assert buttons.SHOW_ALL_NOTES in [
        button.text for row in manage["reply_markup"].inline_keyboard for button in row
    ]
    assert Cb.src_all_notes(0) in datas

    all_callback = _callback(Cb.src_all_notes(0))
    asyncio.run(notes_handler.cb_source_all_notes(all_callback, state))

    sent = all_callback.message.sent[-1]
    text = sent["text"]
    assert text.startswith("📂 <b>Blog</b> — 2 note(s):")
    assert "━━━━━━━━━━" in text
    assert "<b>TITLE:</b> First" in text
    assert "<b>DESCRIPTION:</b> Desc one" in text
    assert "<b>TITLE:</b> Second" in text
    assert "<b>COMMENT:</b> Note two" in text
    assert "ID" not in text and SCREEN_HEADER not in text
    assert "<b>1.</b>\n" in text
    assert "<b>2.</b>\n" in text
    keyboard = sent["reply_markup"].inline_keyboard
    texts = [button.text for row in keyboard for button in row]
    datas = [button.callback_data for row in keyboard for button in row]
    assert "1" in texts and "2" in texts
    assert Cb.note_view("1", "Blog") in datas
    assert Cb.note_view("2", "Blog") in datas
    assert Cb.src_manage(0) in datas
    assert Cb.TO_MAIN in datas


def test_notes_list_is_numbered_with_code_names(use_repo) -> None:
    """Список заметок: «1. <code>Заголовок</code>» и кнопки-номера."""
    repo, _ = repo_for(
        [note_row("a1", "Blog", "FlexiTech"), note_row("b2", "Blog", "Yandex")]
    )
    use_repo(repo)
    callback = _callback(Cb.src_notes("Blog"))

    asyncio.run(notes_handler.cb_source_notes(callback, FakeState()))

    sent = callback.message.sent[-1]
    assert "1. <code>FlexiTech</code>" in sent["text"]
    assert "2. <code>Yandex</code>" in sent["text"]
    keyboard = sent["reply_markup"].inline_keyboard
    texts = [button.text for row in keyboard for button in row]
    datas = [button.callback_data for row in keyboard for button in row]
    assert texts[:2] == ["1", "2"]
    assert Cb.note_view("a1", "Blog") in datas
    assert Cb.note_view("b2", "Blog") in datas
    assert not any(text.startswith("📝") for text in texts)
    assert buttons.ADD_NOTE_ROW in texts
    assert buttons.EDIT_SOURCE in texts


def test_notes_list_add_note_button_starts_name_prompt(use_repo) -> None:
    """«➕ Add Note» из списка заметок сразу просит TITLE для источника."""
    repo, _ = repo_for([note_row("a1", "Blog", "FlexiTech")])
    use_repo(repo)
    state = FakeState()

    asyncio.run(notes_handler.cb_source_notes(_callback(Cb.src_notes("Blog")), state))
    add_callback = _callback(Cb.src_new_note(0))
    asyncio.run(notes_handler.cb_source_new_note(add_callback, state))

    assert state.state == NoteStates.wait_title
    assert state.data["source"] == "Blog"
    prompt = add_callback.message.sent[-1]
    assert "Source: <b>Blog</b>" in prompt["text"]


def test_notes_list_edit_source_button_starts_rename(use_repo) -> None:
    """«✏️ Edit Source» из списка заметок запускает переименование."""
    repo, _ = repo_for([note_row("a1", "Blog", "FlexiTech")])
    use_repo(repo)
    state = FakeState()

    asyncio.run(notes_handler.cb_source_notes(_callback(Cb.src_notes("Blog")), state))
    edit_callback = _callback(Cb.src_rename(0))
    asyncio.run(notes_handler.cb_source_rename(edit_callback, state))

    assert state.state == NoteStates.wait_source_name
    assert state.data["source"] == "Blog"
    prompt = edit_callback.message.sent[-1]
    assert "Rename source" in prompt["text"]


def test_search_by_title_opens_single_card(use_repo) -> None:
    """Уникальное имя, отправленное текстом, сразу открывает карточку."""
    repo, _ = repo_for(
        [note_row("1", "Blog", "FlexiTech"), note_row("2", "Blog", "Yandex")]
    )
    use_repo(repo)
    message = FakeMessage("flexitech", user_id=1)

    asyncio.run(notes_handler.search_by_title(message))

    assert len(message.sent) == 1
    text = message.sent[-1]["text"]
    assert "FlexiTech" in text
    assert "yandex" not in text.lower()
    datas = [
        button.callback_data
        for row in message.sent[-1]["reply_markup"].inline_keyboard
        for button in row
    ]
    assert Cb.note_edit_menu("1") in datas


def test_search_by_title_lists_several_matches(use_repo) -> None:
    """Несколько совпадений — нумерованный список с кнопками-номерами."""
    repo, _ = repo_for(
        [
            note_row("1", "Blog", "Yandex Zen"),
            note_row("2", "News", "Yandex Music"),
        ]
    )
    use_repo(repo)
    message = FakeMessage("yandex", user_id=1)

    asyncio.run(notes_handler.search_by_title(message))

    sent = message.sent[-1]
    assert "1. <code>Yandex Zen</code> (<i>Blog</i>)" in sent["text"]
    assert "2. <code>Yandex Music</code> (<i>News</i>)" in sent["text"]
    keyboard = sent["reply_markup"].inline_keyboard
    texts = [button.text for row in keyboard for button in row]
    datas = [button.callback_data for row in keyboard for button in row]
    assert texts[:2] == ["1", "2"]
    assert any(data.startswith(f"{Cb.NOTE_VIEW}:") for data in datas)


def test_search_by_title_nothing_found(use_repo) -> None:
    """Ничего не нашлось — сообщение с экранированным запросом."""
    repo, _ = repo_for([note_row("1", "Blog", "FlexiTech")])
    use_repo(repo)
    message = FakeMessage("<script>", user_id=1)

    asyncio.run(notes_handler.search_by_title(message))

    sent = message.sent[-1]
    text = sent["text"]
    assert text.startswith(buttons.NOTHING_FOUND.split("{")[0])
    assert "&lt;script&gt;" in text
    assert "<script>" not in text
    rows = sent["reply_markup"].inline_keyboard
    datas = [button.callback_data for row in rows for button in row]
    assert Cb.NOTE_ADD in datas
    assert Cb.TO_MAIN in datas


def test_source_navigation_buttons(use_repo) -> None:
    """Notes by source — без Cancel; ввод TITLE — с Cancel."""
    by_source = keyboards.sources_with_counts([("Blog", 2)])
    datas = [
        button.callback_data for row in by_source.inline_keyboard for button in row
    ]
    assert Cb.CANCEL_INPUT not in datas
    assert Cb.TO_MAIN in datas

    repo, _ = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    message = FakeMessage("", user_id=1)
    state = FakeState()

    asyncio.run(notes_handler.ask_note_title(message, state, "Blog"))

    prompt = message.sent[-1]
    title_datas = [
        button.callback_data
        for row in prompt["reply_markup"].inline_keyboard
        for button in row
    ]
    assert Cb.CANCEL_INPUT in title_datas
    assert Cb.TO_MAIN in title_datas


def test_search_ignores_commands(use_repo) -> None:
    """Слэш-команды не перехватываются поиском."""
    repo, _ = repo_for([note_row("1", "Blog", "FlexiTech")])
    use_repo(repo)
    message = FakeMessage("/start", user_id=1)

    asyncio.run(notes_handler.search_by_title(message))

    assert message.sent == []


def test_source_actions_add_note_skips_source_choice(use_repo) -> None:
    """«➕ Add note» сразу просит TITLE для этого источника."""
    repo, sheet = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    state = FakeState()

    asyncio.run(notes_handler.cb_manage_sources(_callback(Cb.MENU_SOURCES), state))
    add_callback = _callback(Cb.src_new_note(0))
    asyncio.run(notes_handler.cb_source_new_note(add_callback, state))

    assert state.state == NoteStates.wait_title
    assert state.data["source"] == "Blog"
    prompt = add_callback.message.sent[-1]
    assert "Source: <b>Blog</b>" in prompt["text"]

    message = FakeMessage("Fresh", user_id=1)
    asyncio.run(notes_handler.got_note_title(message, state))

    data = rows_by_header(sheet)
    assert [row[SOURCE_HEADER] for row in data] == ["Blog", "Blog"]
    assert "Fresh" in [row[TITLE_HEADER] for row in data]


def test_note_wizard_step_keyboard_layout() -> None:
    """Клавиатура шага мастера: Next/Done, Add another, Main menu."""
    keyboard = keyboards.note_wizard_step("id1").inline_keyboard
    texts = [button.text for row in keyboard for button in row]
    datas = [button.callback_data for row in keyboard for button in row]

    assert texts == [
        buttons.NEXT_FIELD,
        buttons.DONE_NOTE,
        buttons.ADD_ANOTHER_NOTE,
        buttons.TO_MAIN,
    ]
    assert datas == [
        Cb.wizard_next("id1"),
        Cb.wizard_done("id1"),
        Cb.wizard_another("id1"),
        Cb.TO_MAIN,
    ]


def test_wizard_starts_after_title(use_repo) -> None:
    """После TITLE бот спрашивает первую оставшуюся колонку с кнопками."""
    repo, _ = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    state = FakeState()

    asyncio.run(notes_handler.cb_select_source(_callback(Cb.src_select("Blog")), state))
    message = FakeMessage("Fresh", user_id=1)
    asyncio.run(notes_handler.got_note_title(message, state))

    assert state.state == NoteStates.wizard_step
    assert state.data["source"] == "Blog"
    assert state.data["step"] == 0
    columns = state.data["columns"]
    assert [name for _, name in columns] == ["DESCRIPTION", "URL", "SCREEN", "COMMENT"]
    prompt = message.sent[-1]["text"]
    assert "<b>DESCRIPTION</b>" in prompt
    assert "New note: <b>Fresh</b>" in prompt
    keyboard = message.sent[-1]["reply_markup"].inline_keyboard
    texts = [button.text for row in keyboard for button in row]
    assert texts == [
        buttons.NEXT_FIELD,
        buttons.DONE_NOTE,
        buttons.ADD_ANOTHER_NOTE,
        buttons.TO_MAIN,
    ]


def test_wizard_saves_value_and_advances(use_repo) -> None:
    """Ввод значения на шаге мастера сохраняет ячейку и идёт дальше."""
    repo, sheet = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    state = FakeState()
    asyncio.run(notes_handler.cb_select_source(_callback(Cb.src_select("Blog")), state))
    title_message = FakeMessage("Fresh", user_id=1)
    asyncio.run(notes_handler.got_note_title(title_message, state))

    value_message = FakeMessage("Some description", user_id=1)
    asyncio.run(notes_handler.wizard_got_text(value_message, state))

    data = rows_by_header(sheet)
    assert data[1]["DESCRIPTION"] == "Some description"
    # Промежуточного «✅ Saved.» нет: только следующий шаг мастера.
    assert len(value_message.sent) == 1
    assert state.data["step"] == 1
    assert state.data["title"] == "Fresh"
    next_prompt = value_message.sent[-1]["text"]
    assert "<b>URL</b>" in next_prompt
    assert "New note: <b>Fresh</b>" in next_prompt

    done_callback = _callback(Cb.wizard_done(state.data["note_id"]))
    asyncio.run(notes_handler.cb_wizard_done(done_callback, state))

    assert state.state is None
    card = done_callback.message.sent[-1]["text"]
    assert "<b>DESCRIPTION:</b> Some description" in card


def test_wizard_done_opens_card(use_repo) -> None:
    """«✅ Done»: мастер завершается и открывается карточка заметки."""
    repo, _ = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    state = FakeState()
    asyncio.run(notes_handler.cb_select_source(_callback(Cb.src_select("Blog")), state))
    title_message = FakeMessage("Fresh", user_id=1)
    asyncio.run(notes_handler.got_note_title(title_message, state))
    note_id = state.data["note_id"]

    done_callback = _callback(Cb.wizard_done(note_id))
    asyncio.run(notes_handler.cb_wizard_done(done_callback, state))

    assert state.state is None
    assert len(done_callback.message.sent) == 1
    assert "Fresh" in done_callback.message.sent[-1]["text"]
    datas = [
        button.callback_data
        for row in done_callback.message.sent[-1]["reply_markup"].inline_keyboard
        for button in row
    ]
    assert Cb.note_edit_menu(note_id) in datas


def test_wizard_another_note_same_source(use_repo) -> None:
    """«➕ Add another note»: снова TITLE для того же источника."""
    repo, _ = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    state = FakeState()
    asyncio.run(notes_handler.cb_select_source(_callback(Cb.src_select("Blog")), state))
    title_message = FakeMessage("Fresh", user_id=1)
    asyncio.run(notes_handler.got_note_title(title_message, state))

    another_callback = _callback(Cb.wizard_another(state.data["note_id"]))
    asyncio.run(notes_handler.cb_wizard_another(another_callback, state))

    assert state.state == NoteStates.wait_title
    assert state.data["source"] == "Blog"
    prompt = another_callback.message.sent[-1]["text"]
    assert "Source: <b>Blog</b>" in prompt


def test_wizard_next_skips_to_next_column(use_repo) -> None:
    """«⏭ Next»: пропускает колонку без записи значения."""
    repo, _ = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    state = FakeState()
    asyncio.run(notes_handler.cb_select_source(_callback(Cb.src_select("Blog")), state))
    title_message = FakeMessage("Fresh", user_id=1)
    asyncio.run(notes_handler.got_note_title(title_message, state))

    next_callback = _callback(Cb.wizard_next(state.data["note_id"]))
    asyncio.run(notes_handler.cb_wizard_next(next_callback, state))

    assert state.state == NoteStates.wizard_step
    assert state.data["step"] == 1
    assert "<b>URL</b>" in next_callback.message.sent[-1]["text"]

    # Дальше SCREEN: жмём Next дважды, чтобы промотать URL и SCREEN.
    second_next = _callback(Cb.wizard_next(state.data["note_id"]))
    asyncio.run(notes_handler.cb_wizard_next(second_next, state))
    assert "SCREEN" in second_next.message.sent[-1]["text"]

    # Последняя колонка COMMENT: ещё одно нажатие завершает мастер карточкой.
    third_next = _callback(Cb.wizard_next(state.data["note_id"]))
    asyncio.run(notes_handler.cb_wizard_next(third_next, state))
    assert "COMMENT" in third_next.message.sent[-1]["text"]
    assert state.state == NoteStates.wizard_step

    fourth_next = _callback(Cb.wizard_next(state.data["note_id"]))
    asyncio.run(notes_handler.cb_wizard_next(fourth_next, state))
    assert state.state is None
    assert "Fresh" in fourth_next.message.sent[-1]["text"]


def test_wizard_buttons_work_after_fsm_reset(use_repo) -> None:
    """Кнопки мастера работают с пустым FSMContext (рестарт сервера)."""
    repo, _ = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    title = "Fresh after restart"
    note_id = repo.create("Blog", title)

    next_callback = _callback(Cb.wizard_next(note_id))
    next_state = FakeState()
    asyncio.run(notes_handler.cb_wizard_next(next_callback, next_state))

    assert next_state.state == NoteStates.wizard_step
    assert next_state.data["source"] == "Blog"
    assert next_state.data["title"] == title
    assert next_state.data["step"] == 1
    prompt = next_callback.message.sent[-1]["text"]
    assert "<b>URL</b>" in prompt
    assert f"New note: <b>{title}</b>" in prompt

    done_callback = _callback(Cb.wizard_done(note_id))
    done_state = FakeState()
    asyncio.run(notes_handler.cb_wizard_done(done_callback, done_state))

    assert done_state.state is None
    assert title in done_callback.message.sent[-1]["text"]

    another_callback = _callback(Cb.wizard_another(note_id))
    another_state = FakeState()
    asyncio.run(notes_handler.cb_wizard_another(another_callback, another_state))

    assert another_state.state == NoteStates.wait_title
    assert another_state.data["source"] == "Blog"
    assert "Source: <b>Blog</b>" in another_callback.message.sent[-1]["text"]


def test_wizard_photo_saves_file_id_into_screen(use_repo) -> None:
    """Фото на шаге SCREEN: file_id пишется в SCREEN, мастер идёт дальше."""
    repo, sheet = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    state = FakeState()
    source = "Blog"
    asyncio.run(notes_handler.cb_select_source(_callback(Cb.src_select(source)), state))
    title_message = FakeMessage("Fresh", user_id=1)
    asyncio.run(notes_handler.got_note_title(title_message, state))

    # DESCRIPTION → URL → SCREEN: проматываем две колонки кнопкой «Next».
    note_id = state.data["note_id"]
    asyncio.run(notes_handler.cb_wizard_next(_callback(Cb.wizard_next(note_id)), state))
    asyncio.run(notes_handler.cb_wizard_next(_callback(Cb.wizard_next(note_id)), state))
    assert state.data["columns"][state.data["step"]][1] == SCREEN_HEADER

    photo_message = FakeMessage("", user_id=1)
    photo_message.photo = [_Photo("small"), _Photo("wizard_file_id")]
    asyncio.run(notes_handler.wizard_got_photo(photo_message, state))

    screen_row = next(row for row in rows_by_header(sheet) if row[ID_HEADER] == note_id)
    assert screen_row[SCREEN_HEADER] == "wizard_file_id"
    assert state.state == NoteStates.wizard_step
    assert state.data["step"] == 3
    assert "<b>COMMENT</b>" in photo_message.sent[-1]["text"]


def test_wizard_completes_after_last_column(use_repo) -> None:
    """После последней колонки мастер закрывается и открывает карточку."""
    repo, _ = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    state = FakeState()
    source = "Blog"
    asyncio.run(notes_handler.cb_select_source(_callback(Cb.src_select(source)), state))
    title_message = FakeMessage("Fresh", user_id=1)
    asyncio.run(notes_handler.got_note_title(title_message, state))
    note_id = state.data["note_id"]

    value_message = FakeMessage("Some description", user_id=1)
    asyncio.run(notes_handler.wizard_got_text(value_message, state))
    next_callback = _callback(Cb.wizard_next(note_id))
    asyncio.run(notes_handler.cb_wizard_next(next_callback, state))
    asyncio.run(notes_handler.cb_wizard_next(next_callback, state))
    assert state.state == NoteStates.wizard_step
    assert state.data["columns"][state.data["step"]][1] == COMMENT_HEADER

    comment_message = FakeMessage("Last field", user_id=1)
    asyncio.run(notes_handler.wizard_got_text(comment_message, state))

    assert state.state is None
    card = comment_message.sent[-1]["text"]
    assert "<b>DESCRIPTION:</b> Some description" in card
    assert "<b>COMMENT:</b> Last field" in card
    datas = [
        button.callback_data
        for row in comment_message.sent[-1]["reply_markup"].inline_keyboard
        for button in row
    ]
    assert Cb.note_edit_menu(note_id) in datas


def test_saved_source_alerts_on_stale_index(use_repo) -> None:
    """Устаревший индекс: список из FSM пуст — alert SOURCE_LOST, без падения."""
    repo, _ = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    state = FakeState()

    callback = _callback(Cb.src_manage(0))
    asyncio.run(notes_handler.cb_source_manage(callback, state))

    assert callback.answers, "Bot answered nothing"
    assert callback.answers[-1]["text"] == buttons.SOURCE_LOST
    assert callback.answers[-1]["show_alert"] is True
    assert callback.message.sent == []


@pytest.mark.parametrize("saved", [["Blog"], ["Blog", "News"]])
def test_saved_source_alerts_on_out_of_range_index(use_repo, saved: list[str]) -> None:
    """Индекс вне сохранённого списка (например, после удаления) — alert."""
    repo, _ = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    state = FakeState()
    saved_counts = [1] * len(saved)
    asyncio.run(state.update_data(source_list=saved, source_counts=saved_counts))

    callback = _callback(Cb.src_manage(len(saved)))
    asyncio.run(notes_handler.cb_source_manage(callback, state))

    assert callback.answers, "Bot answered nothing"
    assert callback.answers[-1]["text"] == buttons.SOURCE_LOST
    assert callback.message.sent == []


def test_sources_menu_rename_flow(use_repo) -> None:
    """Переименование источника: Rename → новое имя → отчёт и возврат."""
    repo, sheet = repo_for([note_row("1", "Blog", "a"), note_row("2", "Blog", "b")])
    use_repo(repo)
    state = FakeState()

    asyncio.run(notes_handler.cb_manage_sources(_callback(Cb.MENU_SOURCES), state))
    rename_callback = _callback(Cb.src_rename(0))
    asyncio.run(notes_handler.cb_source_rename(rename_callback, state))

    assert state.state == NoteStates.wait_source_name
    assert state.data["source"] == "Blog"
    prompt = rename_callback.message.sent[-1]
    assert "Blog" in prompt["text"]
    cancel_datas = [
        button.callback_data
        for row in prompt["reply_markup"].inline_keyboard
        for button in row
    ]
    assert Cb.CANCEL_INPUT in cancel_datas

    message = FakeMessage("Podcast", user_id=1)
    asyncio.run(notes_handler.got_renamed_source(message, state))

    assert any("Updated notes: <b>2</b>" in item["text"] for item in message.sent)
    data = rows_by_header(sheet)
    assert {row[SOURCE_HEADER] for row in data} == {"Podcast"}
    # Отчёт и список заметок — всего 2 сообщения (без общего списка источников).
    assert len(message.sent) == 2
    assert "1. Podcast (2)" not in message.sent[-1]["text"]
    # После переименования бот открывает заметки переименованного источника:
    # последнее сообщение — заголовок списка, а не «1. Podcast (2)».
    assert any("<b>Podcast</b> — 2 note(s):" in item["text"] for item in message.sent)
    assert state.data["source_list"] == ["Podcast"]
    assert state.state is None


def test_sources_menu_delete_flow(use_repo) -> None:
    """Удаление источника: Delete → подтверждение → Yes, delete."""
    repo, sheet = repo_for(
        [
            note_row("1", "Blog", "a"),
            note_row("2", "Blog", "b"),
            note_row("3", "X", "c"),
        ]
    )
    use_repo(repo)
    state = FakeState()

    asyncio.run(notes_handler.cb_manage_sources(_callback(Cb.MENU_SOURCES), state))
    delete_callback = _callback(Cb.src_del(0))
    asyncio.run(notes_handler.cb_source_delete(delete_callback, state))

    confirm = delete_callback.message.sent[-1]
    assert "Notes that will be deleted: <b>2</b>" in confirm["text"]
    datas = [
        button.callback_data
        for row in confirm["reply_markup"].inline_keyboard
        for button in row
    ]
    assert Cb.src_del_confirm(0) in datas

    confirm_callback = _callback(Cb.src_del_confirm(0))
    asyncio.run(notes_handler.cb_source_delete_confirm(confirm_callback, state))

    assert "Removed notes: <b>2</b>" in confirm_callback.message.sent[-2]["text"]
    data = rows_by_header(sheet)
    assert [row[SOURCE_HEADER] for row in data] == ["X"]
    assert state.data["source_list"] == ["X"]


def test_split_messages_respects_limit() -> None:
    text = "\n".join(f"line {index}" for index in range(200))
    parts = split_messages(text, limit=100)

    assert all(len(part) <= 100 for part in parts)
    assert "".join(parts) == text


def test_split_messages_short_text_single_part() -> None:
    assert split_messages("hello") == ["hello"]
    assert split_messages("") == []
