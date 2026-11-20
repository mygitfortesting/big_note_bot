"""Мастер новой заметки: выбор источника, шаги колонок, восстановление FSM."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from handlers import wizard_handler
from handlers.notes_common import NoteStates
from services import buttons
from services.callback import Cb
from services.sheets import (
    COMMENT_HEADER,
    ID_HEADER,
    SCREEN_HEADER,
    SOURCE_HEADER,
    TITLE_HEADER,
    NoteRepository,
)
from tests.conftest import callback, flat, message
from tests.factories import note_row, repo_for, rows_by_header
from tests.fakes import FakeSheet, FakeState

#: Фикстура ``use_repo`` из ``tests.conftest``.
UseRepo = Callable[[NoteRepository], NoteRepository]


class _Photo:
    """Один размер фото: Telegram отдаёт список, бот берёт последний."""

    def __init__(self, file_id: str) -> None:
        self.file_id = file_id


def _start_wizard(
    use_repo: UseRepo,
    title: str = "Fresh",
    source: str = "Blog",
) -> tuple[FakeSheet, FakeState, Any]:
    """Выбор источника ➔ ввод TITLE: заметка создана, мастер на первом шаге."""
    repo, sheet = repo_for([note_row("1", source, "a")])
    use_repo(repo)
    state = FakeState()
    asyncio.run(wizard_handler.cb_select_source(callback(Cb.src_select(source)), state))
    title_message = message(title, user_id=1)
    asyncio.run(wizard_handler.got_note_title(title_message, state))
    return sheet, state, title_message


# --- Добавление источника ---


def test_add_note_shows_sources_and_new_source_button(use_repo: UseRepo) -> None:
    """«➕ Add note»: сначала кнопки источников + «➕ Новый источник»."""
    repo, _ = repo_for([note_row("1", "Blog", "a"), note_row("2", "YouTube", "b")])
    use_repo(repo)
    add_message = message("", user_id=1)
    state = FakeState()

    asyncio.run(wizard_handler.cmd_add_note(add_message, state))

    assert add_message.sent, "Bot sent nothing"
    sent = add_message.sent[-1]
    assert sent["text"] == buttons.NEW_NOTE_PROMPT
    keyboard = flat(sent["reply_markup"])
    datas = [button.callback_data for button in keyboard]
    assert all(
        data in datas for data in (Cb.src_select("Blog"), Cb.src_select("YouTube"))
    )
    assert Cb.SRC_ADD in datas
    texts = [button.text for button in keyboard]
    assert all(part in texts for part in ("🗂 Blog", "🗂 YouTube", buttons.NEW_SOURCE))
    assert state.state is None


def test_add_note_without_sources_asks_for_new_source(use_repo: UseRepo) -> None:
    """Нет источников — бот сразу просит имя нового источника."""
    repo, _ = repo_for([])
    use_repo(repo)
    empty_message = message("", user_id=1)
    state = FakeState()

    asyncio.run(wizard_handler.cmd_add_note(empty_message, state))

    assert empty_message.sent, "Bot sent nothing"
    sent = empty_message.sent[-1]
    assert sent["text"] == buttons.NEW_SOURCE_PROMPT.format(existing="\n")
    assert state.state == NoteStates.wait_source


def test_add_source_lists_existing_sources_and_cancel(use_repo: UseRepo) -> None:
    """«🆕 Add source» и кнопка «➕ New source»: список + «✖️ Cancel»."""
    repo, _ = repo_for(
        [
            note_row("1", "Telegram", "a"),
            note_row("2", "Instagram", "b"),
            note_row("3", "Instagram", "c"),
        ]
    )
    use_repo(repo)
    state = FakeState()

    asyncio.run(wizard_handler.ask_new_source(message("", user_id=1), state))
    add_callback = callback(Cb.SRC_ADD)
    asyncio.run(wizard_handler.cb_add_source(add_callback, state))

    assert add_callback.message.sent, "Bot sent nothing"
    sent = add_callback.message.sent[-1]
    text = sent["text"]
    assert all(
        part in text
        for part in ("Existing:", "<code>Instagram</code>, <code>Telegram</code>")
    )
    assert text.count("<code>Instagram</code>") == 1  # источники уникальны

    keyboard = flat(sent["reply_markup"])
    datas = [button.callback_data for button in keyboard]
    assert Cb.CANCEL_INPUT in datas and Cb.TO_MAIN in datas
    assert any(
        button.text == buttons.CANCEL and button.callback_data == Cb.CANCEL_INPUT
        for button in keyboard
    )
    assert state.state == NoteStates.wait_source


@pytest.mark.parametrize(
    ("rows", "expected", "absent"),
    [
        pytest.param(
            [note_row("1", "Telegram", "a"), note_row("2", "Instagram", "b")],
            ["<code>Instagram</code>, <code>Telegram</code>"],
            [],
            id="lists-unique-sources",
        ),
        pytest.param(
            [note_row("1", "<b>Shop</b>", "a")],
            ["<code>&lt;b&gt;Shop&lt;/b&gt;</code>"],
            ["<b>Shop</b>"],
            id="escapes-html",
        ),
        pytest.param(
            [],
            [buttons.NEW_SOURCE_PROMPT.format(existing="\n")],
            ["Existing:"],
            id="no-sources-no-existing-line",
        ),
    ],
)
def test_add_source_prompt_formatting(
    use_repo: UseRepo, rows: list[list[str]], expected: list[str], absent: list[str]
) -> None:
    """Промпт «🗂 Add source»: уникальный список, экранирование, Cancel."""
    repo, _ = repo_for(rows)
    use_repo(repo)
    prompt_message = message("", user_id=1)
    state = FakeState()

    asyncio.run(wizard_handler.ask_new_source(prompt_message, state))

    sent = prompt_message.sent[-1]
    text = sent["text"]
    assert all(chunk in text for chunk in expected)
    assert all(chunk not in text for chunk in absent)
    datas = [button.callback_data for button in flat(sent["reply_markup"])]
    assert Cb.CANCEL_INPUT in datas and state.state == NoteStates.wait_source


def test_select_source_then_name_creates_note(use_repo: UseRepo) -> None:
    """Выбор источника ➔ ввод имени ➔ заметка создана и отсортирована."""
    repo, sheet = repo_for([note_row("1", "YouTube", "b")])
    use_repo(repo)
    state = FakeState()

    asyncio.run(
        wizard_handler.cb_select_source(callback(Cb.src_select("YouTube")), state)
    )
    assert state.state == NoteStates.wait_title
    assert state.data["source"] == "YouTube"

    title_message = message("Zebra", user_id=1)
    asyncio.run(wizard_handler.got_note_title(title_message, state))

    data = rows_by_header(sheet)
    titles = [row[TITLE_HEADER] for row in data]
    assert titles == ["b", "Zebra"]
    assert all(row[SOURCE_HEADER] == "YouTube" for row in data)
    # После TITLE начинается мастер заполнения колонок.
    assert state.state == NoteStates.wizard_step


# --- Шаги мастера ---


def test_wizard_starts_after_title(use_repo: UseRepo) -> None:
    """После TITLE бот спрашивает первую оставшуюся колонку с кнопками."""
    _, state, started = _start_wizard(use_repo, title="Fresh")

    assert state.state == NoteStates.wizard_step
    assert state.data["source"] == "Blog"
    assert state.data["step"] == 0
    columns = state.data["columns"]
    column_names = [name for _, name in columns]
    assert column_names == ["DESCRIPTION", "URL", "SCREEN", "COMMENT"]
    prompt = started.sent[-1]["text"]
    assert "<b>DESCRIPTION</b>" in prompt
    # В заголовке мастера — TITLE заметки, а не технический note_id.
    assert "New note: <b>Fresh</b>" in prompt
    texts = [button.text for button in flat(started.sent[-1]["reply_markup"])]
    assert texts == [
        buttons.NEXT_FIELD,
        buttons.DONE_NOTE,
        buttons.ADD_ANOTHER_NOTE,
        buttons.TO_MAIN,
    ]


def test_wizard_done_opens_card(use_repo: UseRepo) -> None:
    """«✅ Done»: мастер завершается и открывается карточка заметки."""
    _, state, _ = _start_wizard(use_repo)
    note_id = state.data["note_id"]

    done_callback = callback(Cb.wizard_done(note_id))
    asyncio.run(wizard_handler.cb_wizard_done(done_callback, state))

    assert state.state is None
    assert len(done_callback.message.sent) == 1
    assert "Fresh" in done_callback.message.sent[-1]["text"]
    card_markup = done_callback.message.sent[-1]["reply_markup"]
    datas = [button.callback_data for button in flat(card_markup)]
    assert Cb.note_edit_menu(note_id) in datas


def test_wizard_another_note_same_source(use_repo: UseRepo) -> None:
    """«➕ Add another note»: снова TITLE для того же источника."""
    _, state, _ = _start_wizard(use_repo)

    another_callback = callback(Cb.wizard_another(state.data["note_id"]))
    asyncio.run(wizard_handler.cb_wizard_another(another_callback, state))

    assert state.state == NoteStates.wait_title
    assert state.data["source"] == "Blog"
    prompt = another_callback.message.sent[-1]["text"]
    assert "Source: <b>Blog</b>" in prompt


def test_wizard_full_walkthrough(use_repo: UseRepo) -> None:
    """Текст ➔ Next ➔ фото SCREEN ➔ последнее поле ➔ карточка заметки."""
    sheet, state, _ = _start_wizard(use_repo)
    note_id = state.data["note_id"]

    # DESCRIPTION — текст в ячейку, без промежуточного «Saved».
    value_message = message("Some description", user_id=1)
    asyncio.run(wizard_handler.wizard_got_text(value_message, state))
    assert len(value_message.sent) == 1
    assert "<b>URL</b>" in value_message.sent[-1]["text"]
    assert state.data["step"] == 1

    # URL и SCREEN пропускаем кнопкой Next.
    for expected in ("SCREEN", "COMMENT"):
        next_callback = callback(Cb.wizard_next(note_id))
        asyncio.run(wizard_handler.cb_wizard_next(next_callback, state))
        assert expected in next_callback.message.sent[-1]["text"]
    assert state.state == NoteStates.wizard_step
    assert state.data["columns"][state.data["step"]][1] == COMMENT_HEADER

    # Последнее поле: мастер закрывается и открывает карточку с текстами.
    comment_message = message("Last field", user_id=1)
    asyncio.run(wizard_handler.wizard_got_text(comment_message, state))

    assert state.state is None
    card = comment_message.sent[-1]["text"]
    assert "<b>DESCRIPTION:</b> Some description" in card
    assert "<b>COMMENT:</b> Last field" in card
    card_markup = comment_message.sent[-1]["reply_markup"]
    datas = [button.callback_data for button in flat(card_markup)]
    assert Cb.note_edit_menu(note_id) in datas

    row = next(row for row in rows_by_header(sheet) if row[ID_HEADER] == note_id)
    assert row["DESCRIPTION"] == "Some description"
    assert row[COMMENT_HEADER] == "Last field"


def test_wizard_buttons_work_after_fsm_reset(use_repo: UseRepo) -> None:
    """Кнопки мастера работают с пустым FSMContext (рестарт сервера)."""
    repo, _ = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    title = "Fresh after restart"
    note_id = repo.create("Blog", title)

    next_callback = callback(Cb.wizard_next(note_id))
    next_state = FakeState()
    asyncio.run(wizard_handler.cb_wizard_next(next_callback, next_state))

    assert next_state.state == NoteStates.wizard_step
    assert next_state.data["source"] == "Blog"
    assert next_state.data["title"] == title
    assert next_state.data["step"] == 1
    prompt = next_callback.message.sent[-1]["text"]
    assert "<b>URL</b>" in prompt and f"New note: <b>{title}</b>" in prompt

    done_callback = callback(Cb.wizard_done(note_id))
    done_state = FakeState()
    asyncio.run(wizard_handler.cb_wizard_done(done_callback, done_state))

    assert done_state.state is None
    assert title in done_callback.message.sent[-1]["text"]

    another_callback = callback(Cb.wizard_another(note_id))
    another_state = FakeState()
    asyncio.run(wizard_handler.cb_wizard_another(another_callback, another_state))

    assert another_state.state == NoteStates.wait_title
    assert another_state.data["source"] == "Blog"
    assert "Source: <b>Blog</b>" in another_callback.message.sent[-1]["text"]


def test_wizard_photo_uses_next_then_saves_file_id(use_repo: UseRepo) -> None:
    """На шаге SCREEN фото пишет file_id в SCREEN и ведёт к COMMENT."""
    sheet, state, _ = _start_wizard(use_repo)

    # DESCRIPTION → URL → SCREEN: проматываем две колонки кнопкой «Next».
    note_id = state.data["note_id"]
    for _ in range(2):
        next_callback = callback(Cb.wizard_next(note_id))
        asyncio.run(wizard_handler.cb_wizard_next(next_callback, state))
    assert state.data["columns"][state.data["step"]][1] == SCREEN_HEADER

    photo_message = message("", user_id=1)
    photo_message.photo = [_Photo("small"), _Photo("wizard_file_id")]
    asyncio.run(wizard_handler.wizard_got_photo(photo_message, state))

    screen_row = next(row for row in rows_by_header(sheet) if row[ID_HEADER] == note_id)
    assert screen_row[SCREEN_HEADER] == "wizard_file_id"
    assert state.state == NoteStates.wizard_step and state.data["step"] == 3
    assert "<b>COMMENT</b>" in photo_message.sent[-1]["text"]
