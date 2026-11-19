"""Источники: нумерованный список, заметки источника, переименование, удаление."""

from __future__ import annotations

import asyncio

import pytest

from handlers import notes_handler
from handlers.notes_handler import NoteStates
from services import buttons
from services.callback import Cb
from services.sheets import SCREEN_HEADER, SOURCE_HEADER, TITLE_HEADER
from tests.conftest import callback, flat
from tests.factories import note_row, repo_for, rows_by_header
from tests.fakes import FakeMessage, FakeState


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


def test_sources_menu_numbers_and_opens_actions(use_repo) -> None:
    """«🗂 Sources» → список по номерам → выбор номера → действия."""
    repo, _ = repo_for([note_row("1", "Blog", "a"), note_row("2", "YouTube", "b")])
    use_repo(repo)
    state = FakeState()

    open_callback = callback(Cb.MENU_SOURCES)
    asyncio.run(notes_handler.cb_manage_sources(open_callback, state))

    sent = open_callback.message.sent[-1]
    assert "1. Blog (1)" in sent["text"]
    assert "2. YouTube (1)" in sent["text"]
    numbers = [button.text for button in flat(sent["reply_markup"])]
    assert "1" in numbers
    assert "2" in numbers
    assert state.data["source_list"] == ["Blog", "YouTube"]

    manage_callback = callback(Cb.src_manage(0))
    asyncio.run(notes_handler.cb_source_manage(manage_callback, state))

    action = manage_callback.message.sent[-1]
    text = action["text"]
    assert "Notes:" not in text
    assert "<b>Blog</b>" in text
    assert "1. <code>a</code>" in text
    assert "Choose an action:" in text
    keyboard = flat(action["reply_markup"])
    texts = [button.text for button in keyboard]
    datas = [button.callback_data for button in keyboard]
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

    asyncio.run(notes_handler.cb_manage_sources(callback(Cb.MENU_SOURCES), state))
    show_callback = callback(Cb.src_show_notes(0))
    asyncio.run(notes_handler.cb_source_show_notes(show_callback, state))

    sent = show_callback.message.sent[-1]
    assert "2 note(s)" in sent["text"]
    texts = [button.text for button in flat(sent["reply_markup"])]
    assert texts[:2] == ["1", "2"]


def test_empty_source_keeps_action_buttons(use_repo) -> None:
    """Пустой источник: «There are no notes…» с теми же действиями."""
    repo, _ = repo_for([])
    repo.add_source("Podcast")
    use_repo(repo)
    state = FakeState()

    asyncio.run(notes_handler.cb_manage_sources(callback(Cb.MENU_SOURCES), state))
    show_callback = callback(Cb.src_show_notes(0))
    asyncio.run(notes_handler.cb_source_show_notes(show_callback, state))

    sent = show_callback.message.sent[-1]
    assert "There are no notes in <b>Podcast</b> yet." in sent["text"]
    keyboard = flat(sent["reply_markup"])
    texts = [button.text for button in keyboard]
    datas = [button.callback_data for button in keyboard]
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

    asyncio.run(notes_handler.cb_manage_sources(callback(Cb.MENU_SOURCES), state))
    manage_callback = callback(Cb.src_manage(0))
    asyncio.run(notes_handler.cb_source_manage(manage_callback, state))

    sent = manage_callback.message.sent[-1]
    assert "<b>Podcast</b>" in sent["text"]
    assert "Choose an action:" in sent["text"]
    texts = [button.text for button in flat(sent["reply_markup"])]
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

    asyncio.run(notes_handler.cb_manage_sources(callback(Cb.MENU_SOURCES), state))
    manage_callback = callback(Cb.src_manage(0))
    asyncio.run(notes_handler.cb_source_manage(manage_callback, state))

    manage = manage_callback.message.sent[-1]
    keyboard = flat(manage["reply_markup"])
    datas = [button.callback_data for button in keyboard]
    assert buttons.SHOW_ALL_NOTES in [button.text for button in keyboard]
    assert Cb.src_all_notes(0) in datas

    all_callback = callback(Cb.src_all_notes(0))
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
    keyboard = flat(sent["reply_markup"])
    texts = [button.text for button in keyboard]
    datas = [button.callback_data for button in keyboard]
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
    notes_callback = callback(Cb.src_notes("Blog"))

    asyncio.run(notes_handler.cb_source_notes(notes_callback, FakeState()))

    sent = notes_callback.message.sent[-1]
    assert "1. <code>FlexiTech</code>" in sent["text"]
    assert "2. <code>Yandex</code>" in sent["text"]
    keyboard = flat(sent["reply_markup"])
    texts = [button.text for button in keyboard]
    datas = [button.callback_data for button in keyboard]
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

    asyncio.run(notes_handler.cb_source_notes(callback(Cb.src_notes("Blog")), state))
    add_callback = callback(Cb.src_new_note(0))
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

    asyncio.run(notes_handler.cb_source_notes(callback(Cb.src_notes("Blog")), state))
    edit_callback = callback(Cb.src_rename(0))
    asyncio.run(notes_handler.cb_source_rename(edit_callback, state))

    assert state.state == NoteStates.wait_source_name
    assert state.data["source"] == "Blog"
    prompt = edit_callback.message.sent[-1]
    assert "Rename source" in prompt["text"]


def test_source_actions_add_note_skips_source_choice(use_repo) -> None:
    """«➕ Add note» сразу просит TITLE для этого источника."""
    repo, sheet = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    state = FakeState()

    asyncio.run(notes_handler.cb_manage_sources(callback(Cb.MENU_SOURCES), state))
    add_callback = callback(Cb.src_new_note(0))
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


def test_saved_source_alerts_on_stale_index(use_repo) -> None:
    """Устаревший индекс: список из FSM пуст — alert SOURCE_LOST, без падения."""
    repo, _ = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    state = FakeState()

    stale_callback = callback(Cb.src_manage(0))
    asyncio.run(notes_handler.cb_source_manage(stale_callback, state))

    assert stale_callback.answers, "Bot answered nothing"
    assert stale_callback.answers[-1]["text"] == buttons.SOURCE_LOST
    assert stale_callback.answers[-1]["show_alert"] is True
    assert stale_callback.message.sent == []


@pytest.mark.parametrize("saved", [["Blog"], ["Blog", "News"]])
def test_saved_source_alerts_on_out_of_range_index(use_repo, saved: list[str]) -> None:
    """Индекс вне сохранённого списка (например, после удаления) — alert."""
    repo, _ = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    state = FakeState()
    asyncio.run(state.update_data(source_list=saved, source_counts=[1] * len(saved)))

    stale_callback = callback(Cb.src_manage(len(saved)))
    asyncio.run(notes_handler.cb_source_manage(stale_callback, state))

    assert stale_callback.answers, "Bot answered nothing"
    assert stale_callback.answers[-1]["text"] == buttons.SOURCE_LOST
    assert stale_callback.message.sent == []


def test_sources_menu_rename_flow(use_repo) -> None:
    """Переименование источника: Rename → новое имя → отчёт и возврат."""
    repo, sheet = repo_for([note_row("1", "Blog", "a"), note_row("2", "Blog", "b")])
    use_repo(repo)
    state = FakeState()

    asyncio.run(notes_handler.cb_manage_sources(callback(Cb.MENU_SOURCES), state))
    rename_callback = callback(Cb.src_rename(0))
    asyncio.run(notes_handler.cb_source_rename(rename_callback, state))

    assert state.state == NoteStates.wait_source_name
    assert state.data["source"] == "Blog"
    prompt = rename_callback.message.sent[-1]
    assert "Blog" in prompt["text"]
    prompt_datas = [button.callback_data for button in flat(prompt["reply_markup"])]
    assert Cb.CANCEL_INPUT in prompt_datas

    message = FakeMessage("Podcast", user_id=1)
    asyncio.run(notes_handler.got_renamed_source(message, state))

    assert any("Updated notes: <b>2</b>" in item["text"] for item in message.sent)
    data = rows_by_header(sheet)
    assert {row[SOURCE_HEADER] for row in data} == {"Podcast"}
    # Отчёт и список заметок — всего 2 сообщения (без общего списка источников).
    assert len(message.sent) == 2
    assert "1. Podcast (2)" not in message.sent[-1]["text"]
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

    asyncio.run(notes_handler.cb_manage_sources(callback(Cb.MENU_SOURCES), state))
    delete_callback = callback(Cb.src_del(0))
    asyncio.run(notes_handler.cb_source_delete(delete_callback, state))

    confirm = delete_callback.message.sent[-1]
    assert "Notes that will be deleted: <b>2</b>" in confirm["text"]
    confirm_datas = [button.callback_data for button in flat(confirm["reply_markup"])]
    assert Cb.src_del_confirm(0) in confirm_datas

    confirm_callback = callback(Cb.src_del_confirm(0))
    asyncio.run(notes_handler.cb_source_delete_confirm(confirm_callback, state))

    assert "Removed notes: <b>2</b>" in confirm_callback.message.sent[-2]["text"]
    data = rows_by_header(sheet)
    assert [row[SOURCE_HEADER] for row in data] == ["X"]
    assert state.data["source_list"] == ["X"]
