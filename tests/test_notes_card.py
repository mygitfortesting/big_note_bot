"""Карточка заметки: поля, правка, скриншот, удаление и поиск по TITLE."""

from __future__ import annotations

import asyncio

from config import NOTES_HEADERS
from handlers import notes_handler
from handlers.notes_handler import NoteContext
from services import buttons, keyboards
from services.callback import Cb
from services.sheets import (
    COMMENT_HEADER,
    SCREEN_HEADER,
    SOURCE_HEADER,
    TITLE_HEADER,
    Note,
)
from services.text_utils import note_card_lines, split_messages
from tests.conftest import flat
from tests.factories import note_row, repo_for, rows_by_header
from tests.fakes import FakeMessage, FakeState

# --- Карточка: только заполненные поля ---


def test_card_outputs_only_filled_fields() -> None:
    """
    Пустые колонки в карточке пропускаются: у источника видны только его поля.

    Заметка «Blog» заполнена в DESCRIPTION/COMMENT, а URL/SCREEN пусты —
    в тексте не должно быть ни URL, ни SCREEN, ни ID.
    """
    values = ["id1", "Blog", "Post", "Desc", "", "", "Nice"]
    text = "\n".join(note_card_lines(NOTES_HEADERS, values))

    assert "SOURCE:" in text
    assert "TITLE:" in text
    assert "DESCRIPTION:" in text
    assert "COMMENT:" in text
    assert "URL" not in text
    assert SCREEN_HEADER not in text
    assert "<b>ID:</b>" not in text


def test_card_skips_placeholders() -> None:
    """Прочерки и пустые ячейки в карточку не попадают."""
    values = ["id1", "Blog", "Post", "-", "—", "", ""]
    text = "\n".join(note_card_lines(NOTES_HEADERS, values))

    assert "DESCRIPTION" not in text
    assert "URL" not in text


def test_card_escapes_html() -> None:
    """HTML из таблицы экранируется и не ломает разметку Telegram."""
    values = ["id1", "Blog", "<script>", "", "", "", ""]
    text = "\n".join(note_card_lines(NOTES_HEADERS, values))

    assert "&lt;script&gt;" in text
    assert "<script>" not in text


def test_card_shows_custom_columns() -> None:
    """Пользовательская колонка видна, служебная COMMENT — нет."""
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
    """Править текстом можно всё, кроме ID и SCREEN."""
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
    """file_id скриншота читается из колонки SCREEN."""
    ctx = NoteContext(
        note=Note(2, ["id1", "Blog", "Post", "", "", "FILE_ID", ""]),
        headers=list(NOTES_HEADERS),
        source="Blog",
    )
    assert ctx.screen == "FILE_ID"


def test_note_card_has_only_four_buttons() -> None:
    """В карточке нет кнопок правки отдельных полей — только 4 кнопки."""
    markup = keyboards.note_card("id1", "Blog")
    texts = [button.text for button in flat(markup)]

    assert texts == [
        buttons.EDIT_FIELD,
        buttons.DELETE_NOTE,
        buttons.PHOTO,
        buttons.BACK,
        buttons.TO_MAIN,
    ]
    datas = [button.callback_data for button in flat(markup)]
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


def test_edit_field_accepts_any_column_except_id_and_screen() -> None:
    """Полевая правка доступна для любой колонки, кроме ID и SCREEN."""
    headers = [*NOTES_HEADERS[:-1], "RATING", COMMENT_HEADER]
    ctx = NoteContext(
        note=Note(2, ["id1", "Shop", "Item", "", "", "", "", ""]),
        headers=headers,
        source="Shop",
    )
    markup = keyboards.edit_fields_only(ctx.editable_columns(), "id1")
    datas = [button.callback_data for button in flat(markup)]

    rating_col = headers.index("RATING") + 1
    screen_col = headers.index(SCREEN_HEADER) + 1
    assert Cb.note_edit_cell("id1", rating_col) in datas
    assert Cb.note_edit_cell("id1", 1) not in datas  # ID
    assert Cb.note_edit_cell("id1", screen_col) not in datas


# --- Скриншот ---


class _Photo:
    """Один размер фото: Telegram отдаёт список, бот берёт последний."""

    def __init__(self, file_id: str) -> None:
        self.file_id = file_id


def test_screenshot_saves_file_id_into_screen(use_repo) -> None:
    """Фото приходит сценарием: ``message.photo[-1].file_id`` пишется в SCREEN."""
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
    col = notes_handler.screen_column(repo)

    assert sheet.rows[0][col - 1] == SCREEN_HEADER


# --- Поиск по названию ---


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
    datas = [button.callback_data for button in flat(message.sent[-1]["reply_markup"])]
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
    keyboard = flat(sent["reply_markup"])
    assert [button.text for button in keyboard][:2] == ["1", "2"]
    assert any(
        button.callback_data.startswith(f"{Cb.NOTE_VIEW}:") for button in keyboard
    )


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
    datas = [button.callback_data for button in flat(sent["reply_markup"])]
    assert Cb.NOTE_ADD in datas
    assert Cb.TO_MAIN in datas


def test_search_ignores_commands(use_repo) -> None:
    """Слэш-команды не перехватываются поиском."""
    repo, _ = repo_for([note_row("1", "Blog", "FlexiTech")])
    use_repo(repo)
    message = FakeMessage("/start", user_id=1)

    asyncio.run(notes_handler.search_by_title(message))

    assert message.sent == []


# --- Вспомогательные тесты текста ---


def test_source_navigation_buttons(use_repo) -> None:
    """Notes by source — без Cancel; ввод TITLE — с Cancel."""
    by_source = keyboards.sources_with_counts([("Blog", 2)])
    datas = [button.callback_data for button in flat(by_source)]
    assert Cb.CANCEL_INPUT not in datas
    assert Cb.TO_MAIN in datas

    repo, _ = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    message = FakeMessage("", user_id=1)
    state = FakeState()

    asyncio.run(notes_handler.ask_note_title(message, state, "Blog"))

    prompt = message.sent[-1]
    title_datas = [button.callback_data for button in flat(prompt["reply_markup"])]
    assert Cb.CANCEL_INPUT in title_datas
    assert Cb.TO_MAIN in title_datas


def test_split_messages_respects_limit() -> None:
    """Длинный текст режется на части не длиннее лимита без потерь."""
    text = "\n".join(f"line {index}" for index in range(200))
    parts = split_messages(text, limit=100)

    assert all(len(part) <= 100 for part in parts)
    assert "".join(parts) == text


def test_split_messages_short_text_single_part() -> None:
    """Короткий текст — одна часть, пустой — ни одной."""
    assert split_messages("hello") == ["hello"]
    assert split_messages("") == []
