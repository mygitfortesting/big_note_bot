"""Карточка заметки: поля, правка, скриншот, удаление и поиск по TITLE."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from config import NOTES_HEADERS
from handlers import notes_handler, sources_handler
from handlers.notes_handler import NoteContext
from services import buttons, keyboards
from services.callback import Cb
from services.sheets import (
    COMMENT_HEADER,
    SCREEN_HEADER,
    SOURCE_HEADER,
    TITLE_HEADER,
    Note,
    NoteRepository,
)
from services.telegram_io import _is_quota_error
from services.text_utils import (
    MIN_BUBBLE_WIDTH,
    WIDTH_SPACER,
    note_card_lines,
    plain_text,
    split_messages,
    with_min_width,
)
from tests.conftest import callback, flat, message
from tests.factories import note_row, repo_for, rows_by_header
from tests.fakes import FakeState

#: Фикстура ``use_repo``: замена ``repo_of`` репозиторием теста.
UseRepo = Callable[[NoteRepository], NoteRepository]


def _make_context(headers: list[str] | None = None, **fields: str) -> NoteContext:
    """``NoteContext`` из значений по именам колонок (а не позициями)."""
    names = list(headers) if headers is not None else list(NOTES_HEADERS)
    values = [fields.get(name, "") for name in names]
    return NoteContext(
        note=Note(2, values),
        headers=names,
        source=fields.get(SOURCE_HEADER, ""),
    )


# --- Карточка: только заполненные поля ---


@pytest.mark.parametrize(
    ("headers", "values", "expected", "absent"),
    [
        pytest.param(
            NOTES_HEADERS,
            ["id1", "Blog", "Post", "Desc", "", "", "Nice"],
            ["SOURCE:", "TITLE:", "DESCRIPTION:", "COMMENT:"],
            ["URL", SCREEN_HEADER, "<b>ID:</b>"],
            id="only-filled-fields",
        ),
        pytest.param(
            NOTES_HEADERS,
            ["id1", "Blog", "Post", "-", "—", "", ""],
            ["TITLE:"],
            ["DESCRIPTION", "URL"],
            id="skips-placeholders",
        ),
        pytest.param(
            NOTES_HEADERS,
            ["id1", "Blog", "<script>", "", "", "", ""],
            ["&lt;script&gt;"],
            ["<script>"],
            id="escapes-html",
        ),
        pytest.param(
            [*NOTES_HEADERS[:-1], "PRICE", COMMENT_HEADER],
            ["id1", "Shop", "Item", "", "", "", "10$", ""],
            ["PRICE:"],
            [COMMENT_HEADER],
            id="custom-columns",
        ),
    ],
)
def test_card_lines_formatting(
    headers: list[str],
    values: list[str],
    expected: list[str],
    absent: list[str],
) -> None:
    """Карточка: только заполненные поля, без прочерков, с экранированием HTML."""
    text = "\n".join(note_card_lines(headers, values))

    assert all(chunk in text for chunk in expected)
    assert all(chunk not in text for chunk in absent)


def test_card_fields_separated_by_blank_line() -> None:
    """Финальный текст карточки разделяет поля пустой строкой."""
    ctx = _make_context(
        ID="id1",
        SOURCE="Blog",
        TITLE="Post",
        DESCRIPTION="Desc",
        COMMENT="Nice",
    )

    blocks = ctx.card_text().split("\n\n")

    assert len(blocks) == 5
    assert blocks[0].startswith("<b>SOURCE:</b>")
    assert "\n" not in ctx.card_text().replace("\n\n", "")


def test_note_context_editable_columns_exclude_id_and_screen() -> None:
    """Править текстом можно всё, кроме ID и SCREEN."""
    ctx = _make_context(ID="id1", SOURCE="Blog", TITLE="Post")
    editable = [name for _, name in ctx.editable_columns()]

    assert "ID" not in editable and SCREEN_HEADER not in editable
    assert TITLE_HEADER in editable and COMMENT_HEADER in editable


def test_note_context_reads_screen_file_id() -> None:
    """file_id скриншота читается из колонки SCREEN."""
    ctx = _make_context(SOURCE="Blog", TITLE="Post", SCREEN="FILE_ID")

    assert ctx.screen == "FILE_ID"


def test_note_card_buttons() -> None:
    """В карточке 5 кнопок, и правка открывает меню колонок, не ячейку."""
    markup = keyboards.note_card("id1", "Blog")
    keyboard = flat(markup)
    texts = [button.text for button in keyboard]
    datas = [button.callback_data for button in keyboard]

    assert texts == [
        buttons.EDIT_FIELD,
        buttons.DELETE_NOTE,
        buttons.PHOTO,
        buttons.BACK,
        buttons.TO_MAIN,
    ]
    assert not any(
        label in texts
        for label in (
            f"✏️ {SOURCE_HEADER}",
            f"✏️ {TITLE_HEADER}",
            "✏️ DESCRIPTION",
            "✏️ URL",
            f"✏️ {COMMENT_HEADER}",
        )
    )
    assert Cb.note_edit_menu("id1") in datas
    assert Cb.note_del_ask("id1") in datas
    assert Cb.note_edit_cell("id1", 2) not in datas
    # «⬅️ Back» ведёт к источнику по note_id, а не по сырому имени.
    assert Cb.note_back("id1") in datas
    no_source_markup = keyboards.note_card("id1", "")
    assert flat(no_source_markup)[-2].callback_data == Cb.CANCEL_INPUT


def test_note_card_back_opens_source_with_colon(use_repo: UseRepo) -> None:
    """«⬅️ Back» в карточке из источника «Resepies: Breakfast» открывает его заметки."""
    source = "Resepies: Breakfast"
    repo, _ = repo_for(
        [
            note_row("a1", source, "Pancakes"),
            note_row("b2", source, "Omelette"),
            note_row("c3", "Blog", "Other"),
        ]
    )
    use_repo(repo)
    state = FakeState()
    asyncio.run(sources_handler.cb_source_notes(callback(Cb.src_notes("Blog")), state))

    back_callback = callback(Cb.note_back("a1"))
    asyncio.run(sources_handler.cb_note_back(back_callback, state))

    # callback_data содержит только note_id: «:» в имени не режет разбор.
    assert back_callback.data == "note_back:a1"
    sent = back_callback.message.sent[-1]
    assert all(part in sent["text"] for part in (source, "Pancakes", "Omelette"))
    assert "Other" not in sent["text"]
    keyboard = flat(sent["reply_markup"])
    assert [button.text for button in keyboard][:2] == ["1", "2"]
    assert all(
        data in [button.callback_data for button in keyboard]
        for data in (Cb.note_view("a1", source), Cb.note_view("b2", source))
    )


def test_note_back_reports_missing_note(use_repo: UseRepo) -> None:
    """Заметку удалили — всплывающее SOURCE_LOST вместо пустого экрана."""
    repo, _ = repo_for([note_row("a1", "Blog", "Post")])
    use_repo(repo)
    back_callback = callback(Cb.note_back("gone"))

    asyncio.run(sources_handler.cb_note_back(back_callback, FakeState()))

    assert back_callback.answers[-1]["text"] == buttons.SOURCE_LOST
    assert back_callback.message.sent == []


def test_edit_field_accepts_any_column_except_id_and_screen() -> None:
    """Полевая правка доступна для любой колонки, кроме ID и SCREEN."""
    headers = [*NOTES_HEADERS[:-1], "RATING", COMMENT_HEADER]
    ctx = _make_context(headers=headers, SOURCE="Shop", TITLE="Item")
    markup = keyboards.edit_fields_only(ctx.editable_columns(), "id1")
    datas = [button.callback_data for button in flat(markup)]

    rating_col = headers.index("RATING") + 1
    screen_col = headers.index(SCREEN_HEADER) + 1
    assert Cb.note_edit_cell("id1", rating_col) in datas
    # ID и SCREEN не редактируются текстом.
    assert Cb.note_edit_cell("id1", 1) not in datas
    assert Cb.note_edit_cell("id1", screen_col) not in datas


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "Blog",
            [
                "<code>Blog</code>",
                "<code>Resepies: Breakfast</code>",
                "<code>YouTube</code>",
            ],
            id="plain",
        ),
        pytest.param(
            "<script>",
            [
                "<code>&lt;script&gt;</code>",
                "<code>Resepies: Breakfast</code>",
            ],
            id="escaped",
        ),
    ],
)
def test_edit_source_prompt_lists_existing_sources(
    use_repo: UseRepo, source: str, expected: list[str]
) -> None:
    """Правка SOURCE показывает существующие источники в <code>...</code>."""
    repo, _ = repo_for(
        [
            note_row("a1", "Blog", "Post"),
            note_row("b2", "Resepies: Breakfast", "Pancakes"),
            note_row("c3", "YouTube", "Video"),
            note_row("d4", source, "Other"),
        ]
    )
    use_repo(repo)
    state = FakeState()
    source_col = NOTES_HEADERS.index(SOURCE_HEADER) + 1
    edit_callback = callback(Cb.note_edit_cell("d4", source_col))

    asyncio.run(notes_handler.cb_edit_cell(edit_callback, state))

    text = edit_callback.message.sent[-1]["text"]
    assert all(part in text for part in expected)
    assert "or <code>-</code>" not in text
    assert state.state == notes_handler.NoteStates.wait_value


def test_edit_regular_column_hides_source_list(use_repo: UseRepo) -> None:
    """Для обычной колонки (URL) список источников не выводится."""
    repo, _ = repo_for(
        [note_row("a1", "Blog", "Post"), note_row("b2", "YouTube", "Video")]
    )
    use_repo(repo)
    state = FakeState()
    url_col = NOTES_HEADERS.index("URL") + 1
    url_callback = callback(Cb.note_edit_cell("a1", url_col))

    asyncio.run(notes_handler.cb_edit_cell(url_callback, state))

    text = url_callback.message.sent[-1]["text"]
    assert "Blog" not in text and "YouTube" not in text
    assert "or <code>-</code>" in text
    assert state.state == notes_handler.NoteStates.wait_value


def test_edit_source_rejects_clearing(use_repo: UseRepo) -> None:
    """«-» в SOURCE не очищает его, а просит ввести непустое имя."""
    repo, sheet = repo_for([note_row("a1", "Blog", "Post")])
    use_repo(repo)
    state = FakeState()
    source_col = NOTES_HEADERS.index(SOURCE_HEADER) + 1
    asyncio.run(state.update_data(note_id="a1", col_index=source_col))
    clear_message = message("-", user_id=1)

    asyncio.run(notes_handler.got_field_value(clear_message, state))

    assert [sent["text"] for sent in clear_message.sent] == [buttons.EDIT_SOURCE_EMPTY]
    assert rows_by_header(sheet)[0][SOURCE_HEADER] == "Blog"
    assert state.state == notes_handler.NoteStates.wait_value


def test_edit_regular_column_still_clears(use_repo: UseRepo) -> None:
    """В обычной колонке «-» по-прежнему очищает ячейку."""
    repo, sheet = repo_for([note_row("a1", "Blog", "Post")])
    use_repo(repo)
    state = FakeState()
    comment_col = NOTES_HEADERS.index(COMMENT_HEADER) + 1
    asyncio.run(state.update_data(note_id="a1", col_index=comment_col))
    repo.update_cell_at(2, comment_col, "Old")
    clear_message = message("-", user_id=1)

    asyncio.run(notes_handler.got_field_value(clear_message, state))

    assert buttons.EDIT_SOURCE_EMPTY not in [
        sent["text"] for sent in clear_message.sent
    ]
    assert buttons.NOTE_SAVED in [sent["text"] for sent in clear_message.sent]
    assert rows_by_header(sheet)[0][COMMENT_HEADER] == ""


# --- Скриншот ---


class _Photo:
    """Один размер фото: Telegram отдаёт список, бот берёт последний."""

    def __init__(self, file_id: str) -> None:
        self.file_id = file_id


def test_screenshot_saves_file_id_into_screen(
    use_repo: UseRepo,
) -> None:
    """Фото приходит сценарием: ``message.photo[-1].file_id`` пишется в SCREEN."""
    repo, sheet = repo_for([note_row("1", "Blog", "Post")])
    use_repo(repo)
    state = FakeState()
    asyncio.run(state.update_data(note_id="1"))
    photo_message = message("", user_id=7)
    photo_message.photo = [_Photo("small"), _Photo("big_file_id")]

    asyncio.run(notes_handler.got_photo(photo_message, state))

    data = rows_by_header(sheet)
    assert data[0][SCREEN_HEADER] == "big_file_id"
    assert state.state is None


def test_screenshot_uses_screen_column_not_comment(
    use_repo: UseRepo,
) -> None:
    """SCREEN берётся по имени колонки, даже если пользовательские вклинились."""
    headers = [*NOTES_HEADERS[:-1], "RATING", COMMENT_HEADER]
    repo, sheet = repo_for(
        [["1", "Blog", "Post", "", "", "", "", ""]],
        headers=headers,
    )
    use_repo(repo)
    state = FakeState()
    asyncio.run(state.update_data(note_id="1"))
    rating_message = message("", user_id=7)
    rating_message.photo = [_Photo("rating_safe_id")]

    asyncio.run(notes_handler.got_photo(rating_message, state))

    data = rows_by_header(sheet)
    assert data[0][SCREEN_HEADER] == "rating_safe_id"
    assert not any(data[0][name] for name in ("RATING", COMMENT_HEADER))
    assert state.state is None


def test_screenshot_without_photo_sends_warning(
    use_repo: UseRepo,
) -> None:
    """Пустой список фото: предупреждение без записи в таблицу."""
    repo, sheet = repo_for([note_row("1", "Blog", "Post")])
    use_repo(repo)
    state = FakeState()
    asyncio.run(state.update_data(note_id="1"))
    empty_photo_message = message("", user_id=7)
    empty_photo_message.photo = []

    asyncio.run(notes_handler.got_photo(empty_photo_message, state))

    assert len(empty_photo_message.sent) == 1
    assert "Please send a photo" in empty_photo_message.sent[0]["text"]
    assert sheet.rows[1] == note_row("1", "Blog", "Post") and state.state is None


def test_screenshot_without_screen_column_reports_error(
    use_repo: UseRepo,
) -> None:
    """Нет колонки SCREEN: ValueError перехвачен, бот не падает."""
    headers = [header for header in NOTES_HEADERS if header != SCREEN_HEADER]
    repo, sheet = repo_for(
        [["1", "Blog", "Post", "", "", ""]],
        headers=headers,
    )
    use_repo(repo)
    state = FakeState()
    asyncio.run(state.update_data(note_id="1"))
    no_screen_message = message("", user_id=7)
    no_screen_message.photo = [_Photo("file_id")]

    asyncio.run(notes_handler.got_photo(no_screen_message, state))

    assert len(no_screen_message.sent) == 1
    assert "SCREEN" in no_screen_message.sent[0]["text"]
    assert sheet.rows[1] == ["1", "Blog", "Post", "", "", ""] and state.state is None


# --- Поиск по названию ---


def test_search_by_title_opens_single_card(
    use_repo: UseRepo,
) -> None:
    """Уникальное имя, отправленное текстом, сразу открывает карточку."""
    repo, _ = repo_for(
        [note_row("1", "Blog", "FlexiTech"), note_row("2", "Blog", "Yandex")]
    )
    use_repo(repo)
    search_message = message("flexitech", user_id=1)

    asyncio.run(notes_handler.search_by_title(search_message))

    assert len(search_message.sent) == 1
    text = search_message.sent[-1]["text"].lower()
    assert "flexitech" in text and "yandex" not in text
    datas = [
        button.callback_data for button in flat(search_message.sent[-1]["reply_markup"])
    ]
    assert Cb.note_edit_menu("1") in datas


def test_search_by_title_lists_several_matches(
    use_repo: UseRepo,
) -> None:
    """Несколько совпадений — нумерованный список с кнопками-номерами."""
    repo, _ = repo_for(
        [
            note_row("1", "Blog", "Yandex Zen"),
            note_row("2", "News", "Yandex Music"),
        ]
    )
    use_repo(repo)
    multi_message = message("yandex", user_id=1)

    asyncio.run(notes_handler.search_by_title(multi_message))

    sent = multi_message.sent[-1]
    assert all(
        part in sent["text"]
        for part in (
            "1. <code>Yandex Zen</code> (<i>Blog</i>)",
            "2. <code>Yandex Music</code> (<i>News</i>)",
        )
    )
    keyboard = flat(sent["reply_markup"])
    assert [button.text for button in keyboard][:2] == ["1", "2"]
    assert any(
        button.callback_data.startswith(f"{Cb.NOTE_VIEW}:") for button in keyboard
    )


def test_search_by_title_nothing_found(
    use_repo: UseRepo,
) -> None:
    """Ничего не нашлось — сообщение с экранированным запросом."""
    repo, _ = repo_for([note_row("1", "Blog", "FlexiTech")])
    use_repo(repo)
    nothing_message = message("<script>", user_id=1)

    asyncio.run(notes_handler.search_by_title(nothing_message))

    sent = nothing_message.sent[-1]
    text = sent["text"]
    assert text.startswith(buttons.NOTHING_FOUND.split("{")[0])
    assert "&lt;script&gt;" in text and "<script>" not in text
    datas = [button.callback_data for button in flat(sent["reply_markup"])]
    assert Cb.NOTE_ADD in datas and Cb.TO_MAIN in datas


def test_search_ignores_commands(
    use_repo: UseRepo,
) -> None:
    """Слэш-команды не перехватываются поиском."""
    repo, _ = repo_for([note_row("1", "Blog", "FlexiTech")])
    use_repo(repo)
    command_message = message("/start", user_id=1)

    asyncio.run(notes_handler.search_by_title(command_message))

    assert command_message.sent == []


# --- Вспомогательные тесты текста ---


def test_source_navigation_buttons(
    use_repo: UseRepo,
) -> None:
    """Sources manage — без Cancel; ввод TITLE — с Cancel."""
    by_source = keyboards.sources_manage(2)
    datas = [button.callback_data for button in flat(by_source)]
    assert Cb.CANCEL_INPUT not in datas and Cb.TO_MAIN in datas

    repo, _ = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    title_message = message("", user_id=1)
    state = FakeState()

    asyncio.run(notes_handler.ask_note_title(title_message, state, "Blog"))

    prompt = title_message.sent[-1]
    title_datas = [button.callback_data for button in flat(prompt["reply_markup"])]
    assert Cb.CANCEL_INPUT in title_datas and Cb.TO_MAIN in title_datas


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


# --- Минимальная ширина пузыря с кнопками ---


def test_with_min_width() -> None:
    """Распорка нужна только короткому тексту и никогда не создаёт ``\n\n``."""
    short_targets = [
        "<b>" + "a" * 10 + "</b>",
        "Column: URL\n\n",
        "",
        "hi",
    ]
    for target in short_targets:
        padded = with_min_width(target)

        assert padded.endswith(WIDTH_SPACER)
        assert padded.count(WIDTH_SPACER) == 1 and "\n\n" not in padded
        assert with_min_width(padded) == padded
    # Текст с готовой распоркой — уже с одним переводом строки.
    assert with_min_width("Column: URL\n\n") == f"Column: URL\n{WIDTH_SPACER}"

    long_text = "Short line\n" + "x" * MIN_BUBBLE_WIDTH
    assert with_min_width(long_text) == long_text
    assert with_min_width(with_min_width(long_text)) == long_text
    assert WIDTH_SPACER not in with_min_width(long_text)


def test_note_delete_ask_shows_confirmation(use_repo: UseRepo) -> None:
    """Нажатие 'Delete' в карточке показывает подтверждение удаления."""
    repo, _ = repo_for([note_row("1", "Blog", "Note Title")])
    use_repo(repo)
    cb = callback(Cb.note_del_ask("1"))

    asyncio.run(notes_handler.cb_note_delete_ask(cb))

    assert len(cb.message.sent) == 1
    sent = cb.message.sent[-1]
    text = sent["text"]
    assert all(part in text for part in ("Delete note", "Note Title"))
    datas = [button.callback_data for button in flat(sent["reply_markup"])]
    assert Cb.note_del("1") in datas and Cb.note_view("1", "Blog") in datas


def test_note_delete_executes_under_user_lock(use_repo: UseRepo) -> None:
    """Подтверждение удаления удаляет строку и возвращает к списку заметок."""
    repo, sheet = repo_for([note_row("1", "Blog", "Note Title")])
    use_repo(repo)
    cb = callback(Cb.note_del("1"))
    state = FakeState()

    asyncio.run(notes_handler.cb_note_delete(cb, state))

    assert len(sheet.rows) == 1 and repo.get("1") is None  # Только шапка


def test_source_name_with_colon_accepted_in_handlers(use_repo: UseRepo) -> None:
    """Хендлеры принимают источник с ':' и при создании, и при переименовании."""
    repo, _ = repo_for([note_row("1", "Blog", "Title")])
    use_repo(repo)

    # Добавление
    msg_add = message("Resepies: Breakfast")
    state_add = FakeState()
    asyncio.run(sources_handler.got_new_source(msg_add, state_add))
    assert all("must not contain" not in sent["text"] for sent in msg_add.sent)
    assert "Resepies: Breakfast" in repo.list_sources()

    # Переименование
    msg_rename = message("Another: Source")
    state_rename = FakeState()
    asyncio.run(state_rename.update_data(source="Blog"))
    asyncio.run(sources_handler.got_renamed_source(msg_rename, state_rename))
    assert all("must not contain" not in sent["text"] for sent in msg_rename.sent)
    assert "Another: Source" in repo.list_sources()
    assert "Blog" not in repo.list_sources()


def test_source_with_colon_opens_notes_from_buttons(use_repo: UseRepo) -> None:
    """Источник с ':' открывается из списка, а карточка возвращает в него."""
    source = "Resepies: Breakfast"
    repo, _ = repo_for(
        [note_row("a1", source, "Pancakes"), note_row("b2", "Blog", "Other")]
    )
    use_repo(repo)
    state = FakeState()
    asyncio.run(sources_handler.show_sources_manage(message("/start"), state, repo))

    # Номер источника в списке -> заметки источника: имя идёт из FSM-списка.
    manage_callback = callback(Cb.src_manage(1))
    asyncio.run(sources_handler.cb_source_manage(manage_callback, state))

    sent = manage_callback.message.sent[-1]
    assert source in sent["text"] and "Pancakes" in sent["text"]
    datas = [button.callback_data for button in flat(sent["reply_markup"])]
    assert Cb.note_view("a1", source) in datas

    # Из карточки «⬅️ Back» возвращает в этот же источник без обрезки имени.
    asyncio.run(notes_handler.show_note(manage_callback, "a1"))
    note_card = manage_callback.message.sent[-1]
    back_button = flat(note_card["reply_markup"])[-2]
    assert back_button.callback_data == Cb.note_back("a1")

    back_callback = callback(back_button.callback_data)
    asyncio.run(sources_handler.cb_note_back(back_callback, state))
    back_text = back_callback.message.sent[-1]["text"]
    assert source in back_text and "Pancakes" in back_text
    assert "Other" not in back_text


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("Resepies: Breakfast", id="one-colon"),
        pytest.param("Blog: A: B", id="two-colons"),
        pytest.param("Time 12:30", id="time"),
    ],
)
def test_parse_note_keeps_colons_in_source(source: str) -> None:
    """``note_view:<id>:<source>`` не режет двоеточия внутри имени источника."""
    data = Cb.note_view("a1", source)

    ref = Cb.parse_note(data, Cb.NOTE_VIEW)

    assert ref is not None
    assert ref.note_id == "a1"
    assert ref.source == source


def test_plain_text_cleans_tags_and_unescapes_entities() -> None:
    """plain_text вырезает HTML-теги и декодирует HTML-сущности."""
    raw = "<b>Bold &amp; text</b> with <a href='http://x'>link</a>"
    assert plain_text(raw) == "Bold & text with link"


def test_is_quota_error_logic() -> None:
    """_is_quota_error проверяет 429 в APIError или слова quota/rate рядом с 429."""
    import gspread.exceptions

    class DummyResponse:
        status_code = 429
        text = "Quota exceeded"

        def json(self) -> dict[str, object]:
            return {"error": {"code": 429, "message": "Quota exceeded"}}

    api_err = gspread.exceptions.APIError(DummyResponse())
    assert _is_quota_error(api_err) is True

    assert _is_quota_error(Exception("429: rate limit exceeded")) is True
    assert _is_quota_error(Exception("Quota exceeded (code 429)")) is True
    assert _is_quota_error(Exception("Random 429 happened")) is False
    assert _is_quota_error(Exception("Other error")) is False
