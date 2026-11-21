"""Источники: нумерованный список, заметки источника, переименование, удаление."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from handlers import sources_handler, wizard_handler
from handlers.notes_common import NoteStates
from services import buttons
from services.callback import Cb
from services.sheets import (
    SCREEN_HEADER,
    SOURCE_HEADER,
    TITLE_HEADER,
    NoteRepository,
)
from tests.conftest import callback, flat, message
from tests.factories import note_row, repo_for, rows_by_header
from tests.fakes import FakeState

#: Фикстура ``use_repo`` из ``tests.conftest``.
UseRepo = Callable[[NoteRepository], NoteRepository]


def _open_sources(use_repo: UseRepo) -> FakeState:
    """Подменённый репозиторий и открытое меню «🗂 Sources» (0-based индексы)."""
    repo, _ = repo_for([note_row("1", "Blog", "a"), note_row("2", "Blog", "b")])
    use_repo(repo)
    state = FakeState()
    asyncio.run(sources_handler.cb_manage_sources(callback(Cb.MENU_SOURCES), state))
    return state


def test_new_source_is_added_as_blank_source(use_repo: UseRepo) -> None:
    """«🗂 Add source»: источник сохраняется сразу, без заметки и с 0."""
    repo, sheet = repo_for([])
    use_repo(repo)
    source_message = message("Instagram", user_id=1)
    state = FakeState()
    asyncio.run(state.set_state(NoteStates.wait_source))

    asyncio.run(sources_handler.got_new_source(source_message, state))

    assert state.state is None
    assert any("Source added" in item["text"] for item in source_message.sent)
    # Шапка + строка-болванка: только SOURCE, без ID и TITLE.
    assert len(sheet.rows) == 2
    assert sheet.rows[1][:3] == ["", "Instagram", ""]
    assert repo.list_sources_with_counts() == [("Instagram", 0)]
    assert repo.list_notes() == []


def test_sources_menu_numbers_and_opens_actions(use_repo: UseRepo) -> None:
    """«🗂 Sources» → список по номерам → выбор номера → действия."""
    repo, _ = repo_for([note_row("1", "Blog", "a"), note_row("2", "YouTube", "b")])
    use_repo(repo)
    state = FakeState()

    open_callback = callback(Cb.MENU_SOURCES)
    asyncio.run(sources_handler.cb_manage_sources(open_callback, state))

    sent = open_callback.message.sent[-1]
    assert "1. Blog -> 1" in sent["text"] and "2. YouTube -> 1" in sent["text"]
    numbers = [button.text for button in flat(sent["reply_markup"])]
    assert "1" in numbers and "2" in numbers
    assert state.data["source_list"] == ["Blog", "YouTube"]

    manage_callback = callback(Cb.src_manage(0))
    asyncio.run(sources_handler.cb_source_manage(manage_callback, state))

    action = manage_callback.message.sent[-1]
    text = action["text"]
    assert "Notes:" not in text
    assert all(
        part in text
        for part in ("<b>Blog</b>", "1. <code>a</code>", "Choose an action:")
    )
    keyboard = flat(action["reply_markup"])
    texts = [button.text for button in keyboard]
    datas = [button.callback_data for button in keyboard]
    assert "1" in texts
    assert all(
        data in datas
        for data in (
            Cb.note_view("1", "Blog"),
            Cb.src_new_note(0),
            Cb.src_rename(0),
            Cb.src_del(0),
        )
    )
    assert Cb.src_show_notes(0) not in datas
    assert all(
        label in texts for label in (buttons.RENAME_SOURCE, buttons.DELETE_SOURCE)
    )
    assert "📂 Show notes" not in texts


def test_source_actions_legacy_show_notes(use_repo: UseRepo) -> None:
    """Старый callback «📂 Show notes» ведёт к единому экрану источника."""
    state = _open_sources(use_repo)
    show_callback = callback(Cb.src_show_notes(0))
    asyncio.run(sources_handler.cb_source_show_notes(show_callback, state))

    sent = show_callback.message.sent[-1]
    assert all(
        part in sent["text"]
        for part in (
            "<b>Blog</b>",
            "1. <code>a</code>",
            "2. <code>b</code>",
            "Choose an action:",
        )
    )
    texts = [button.text for button in flat(sent["reply_markup"])]
    assert texts[:2] == ["1", "2"]


def test_empty_source_manage_screen(use_repo: UseRepo) -> None:
    """Экран источника без заметок: заголовок и действия с подписями."""
    repo, _ = repo_for([])
    repo.add_source("Podcast")
    use_repo(repo)
    state = FakeState()

    asyncio.run(sources_handler.cb_manage_sources(callback(Cb.MENU_SOURCES), state))
    manage_callback = callback(Cb.src_manage(0))
    asyncio.run(sources_handler.cb_source_manage(manage_callback, state))

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


def test_source_actions_show_all_notes(use_repo: UseRepo) -> None:
    """«📖 Show all notes»: полные поля всех заметок источника."""
    repo, _ = repo_for(
        [
            note_row("1", "Blog", "First", description="Desc one"),
            note_row("2", "Blog", "Second", comment="Note two"),
        ]
    )
    use_repo(repo)
    state = FakeState()

    asyncio.run(sources_handler.cb_manage_sources(callback(Cb.MENU_SOURCES), state))
    manage_callback = callback(Cb.src_manage(0))
    asyncio.run(sources_handler.cb_source_manage(manage_callback, state))

    manage = manage_callback.message.sent[-1]
    keyboard = flat(manage["reply_markup"])
    datas = [button.callback_data for button in keyboard]
    assert buttons.SHOW_ALL_NOTES in [button.text for button in keyboard]
    assert Cb.src_all_notes(0) in datas

    all_callback = callback(Cb.src_all_notes(0))
    asyncio.run(sources_handler.cb_source_all_notes(all_callback, state))

    sent = all_callback.message.sent[-1]
    text = sent["text"]
    # Компактная сводка: черта сразу под шапкой и без пустых строк.
    assert text.startswith("📂 <b>Blog</b> — 2 note(s):\n━━━━━━━━━━\n")
    assert text.endswith("<b>COMMENT:</b> Note two") and "\n\n" not in text
    assert all(
        part in text
        for part in (
            "<b>TITLE:</b> First\n<b>DESCRIPTION:</b> Desc one",
            "<b>TITLE:</b> Second\n<b>COMMENT:</b> Note two",
            "<b>1.</b>\n",
            "<b>2.</b>\n",
        )
    )
    assert "ID" not in text and SCREEN_HEADER not in text
    keyboard = flat(sent["reply_markup"])
    texts = [button.text for button in keyboard]
    datas = [button.callback_data for button in keyboard]
    assert "1" in texts and "2" in texts
    assert all(
        data in datas
        for data in (
            Cb.note_view("1", "Blog"),
            Cb.note_view("2", "Blog"),
            Cb.src_manage(0),
            Cb.TO_MAIN,
        )
    )


def test_notes_list_is_numbered_with_code_names(use_repo: UseRepo) -> None:
    """Legacy «src_notes» открывает тот же единый экран источника."""
    repo, _ = repo_for(
        [note_row("a1", "Blog", "FlexiTech"), note_row("b2", "Blog", "Yandex")]
    )
    use_repo(repo)
    notes_callback = callback(Cb.src_notes("Blog"))

    asyncio.run(sources_handler.cb_source_notes(notes_callback, FakeState()))

    sent = notes_callback.message.sent[-1]
    assert "<b>Blog</b>" in sent["text"]
    keyboard = flat(sent["reply_markup"])
    texts = [button.text for button in keyboard]
    datas = [button.callback_data for button in keyboard]
    assert texts[:2] == ["1", "2"]
    assert all(
        data in datas
        for data in (Cb.note_view("a1", "Blog"), Cb.note_view("b2", "Blog"))
    )
    assert all(
        label in texts
        for label in (buttons.ADD_NOTE, buttons.RENAME_SOURCE, buttons.DELETE_SOURCE)
    )


def test_source_with_colon_opens_notes_from_buttons(use_repo: UseRepo) -> None:
    """Источник с ':' открывается из списка, а карточка возвращает в него.

    Сценарий собирает всё, что зависит от двоеточия в имени: номер в
    «🗂 Sources», кнопки-номера заметок с именем источника в callback_data
    и «⬅️ Back» из карточки (там — только ``note_id``).
    """
    source = "Resepies: Breakfast"
    repo, _ = repo_for(
        [note_row("a1", "Blog", "Other"), note_row("b2", source, "Pancakes")]
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
    assert Cb.note_view("b2", source) in datas

    # «⬅️ Back» из карточки ведёт по note_id, а не по сырому имени.
    back_callback = callback(Cb.note_back("b2"))
    asyncio.run(sources_handler.cb_note_back(back_callback, state))

    assert back_callback.data == "note_back:b2"
    back_sent = back_callback.message.sent[-1]
    assert source in back_sent["text"] and "Pancakes" in back_sent["text"]
    assert "Other" not in back_sent["text"]


def test_source_screen_same_from_menu_and_note_back(use_repo: UseRepo) -> None:
    """«🗂 Sources» и «⬅️ Back» из карточки открывают один и тот же экран.

    FSM после карточки (рестарт/поиск) пуст — список источников и номер
    всё равно берутся из таблицы, а `source_list` заполняется заново.
    """
    repo, _ = repo_for([note_row("1", "Blog", "a"), note_row("2", "YouTube", "b")])
    use_repo(repo)
    expected_texts = [
        "1",
        buttons.SHOW_ALL_NOTES,
        buttons.ADD_NOTE,
        buttons.RENAME_SOURCE,
        buttons.DELETE_SOURCE,
        buttons.BACK,
        buttons.TO_MAIN,
    ]

    from_menu = FakeState()
    asyncio.run(sources_handler.cb_manage_sources(callback(Cb.MENU_SOURCES), from_menu))
    manage_callback = callback(Cb.src_manage(1))
    asyncio.run(sources_handler.cb_source_manage(manage_callback, from_menu))
    from_menu_sent = manage_callback.message.sent[-1]

    # «⬅️ Back» из карточки заметки YouTube, FSM пуст (как после рестарта).
    empty_state = FakeState()
    back_callback = callback(Cb.note_back("2"))
    asyncio.run(sources_handler.cb_note_back(back_callback, empty_state))
    back_sent = back_callback.message.sent[-1]

    for sent, state in (
        (from_menu_sent, from_menu),
        (back_sent, empty_state),
    ):
        assert all(
            part in sent["text"]
            for part in ("<b>YouTube</b>", "1. <code>b</code>", "Choose an action:")
        )
        keyboard = flat(sent["reply_markup"])
        assert [button.text for button in keyboard] == expected_texts
        datas = [button.callback_data for button in keyboard]
        assert all(
            data in datas
            for data in (
                Cb.note_view("2", "YouTube"),
                Cb.src_all_notes(1),
                Cb.src_new_note(1),
                Cb.src_rename(1),
                Cb.src_del(1),
            )
        )
        assert state.data["source_list"] == ["Blog", "YouTube"]
    assert from_menu_sent["text"] == back_sent["text"]


def test_notes_list_edit_source_button_starts_rename(use_repo: UseRepo) -> None:
    """«✏️ Edit source» из единого экрана запускает переименование."""
    repo, _ = repo_for([note_row("a1", "Blog", "FlexiTech")])
    use_repo(repo)
    state = FakeState()

    asyncio.run(sources_handler.cb_source_notes(callback(Cb.src_notes("Blog")), state))
    edit_callback = callback(Cb.src_rename(0))
    asyncio.run(sources_handler.cb_source_rename(edit_callback, state))

    assert state.state == NoteStates.wait_source_name
    assert state.data["source"] == "Blog"
    prompt = edit_callback.message.sent[-1]
    assert "Rename source" in prompt["text"]


def test_source_actions_add_note_skips_source_choice(use_repo: UseRepo) -> None:
    """«➕ Add note» сразу просит TITLE для этого источника."""
    repo, sheet = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    state = FakeState()

    asyncio.run(sources_handler.cb_manage_sources(callback(Cb.MENU_SOURCES), state))
    add_callback = callback(Cb.src_new_note(0))
    asyncio.run(sources_handler.cb_source_new_note(add_callback, state))

    assert state.state == NoteStates.wait_title
    assert state.data["source"] == "Blog"
    prompt = add_callback.message.sent[-1]
    assert "Source: <b>Blog</b>" in prompt["text"]

    title_message = message("Fresh", user_id=1)
    asyncio.run(wizard_handler.got_note_title(title_message, state))

    data = rows_by_header(sheet)
    assert [row[SOURCE_HEADER] for row in data] == ["Blog", "Blog"]
    assert "Fresh" in [row[TITLE_HEADER] for row in data]


@pytest.mark.parametrize(
    "saved",
    [
        pytest.param([], id="empty-fsm"),
        pytest.param(["Blog"], id="out-of-range"),
        pytest.param(["Blog", "News"], id="out-of-range-two"),
    ],
)
def test_saved_source_alerts_on_invalid_index(
    use_repo: UseRepo, saved: list[str]
) -> None:
    """Индекс вне сохранённого списка — alert SOURCE_LOST без падения."""
    repo, _ = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    state = FakeState()
    if saved:
        await_counts = [1] * len(saved)
        asyncio.run(state.update_data(source_list=saved, source_counts=await_counts))

    stale_callback = callback(Cb.src_manage(len(saved)))
    asyncio.run(sources_handler.cb_source_manage(stale_callback, state))

    assert stale_callback.answers, "Bot answered nothing"
    assert stale_callback.answers[-1] == {
        "text": buttons.SOURCE_LOST,
        "show_alert": True,
    }
    assert stale_callback.message.sent == []


def test_sources_menu_rename_flow(use_repo: UseRepo) -> None:
    """Переименование источника: Rename → новое имя → отчёт и возврат."""
    repo, sheet = repo_for([note_row("1", "Blog", "a"), note_row("2", "Blog", "b")])
    use_repo(repo)
    state = FakeState()

    asyncio.run(sources_handler.cb_manage_sources(callback(Cb.MENU_SOURCES), state))
    rename_callback = callback(Cb.src_rename(0))
    asyncio.run(sources_handler.cb_source_rename(rename_callback, state))

    assert state.state == NoteStates.wait_source_name
    assert state.data["source"] == "Blog"
    prompt = rename_callback.message.sent[-1]
    assert "Blog" in prompt["text"]
    prompt_datas = [button.callback_data for button in flat(prompt["reply_markup"])]
    assert Cb.CANCEL_INPUT in prompt_datas

    rename_message = message("Podcast", user_id=1)
    asyncio.run(sources_handler.got_renamed_source(rename_message, state))

    assert any(
        "Updated notes: <b>2</b>" in item["text"] for item in rename_message.sent
    )
    data = rows_by_header(sheet)
    assert {row[SOURCE_HEADER] for row in data} == {"Podcast"}
    # Отчёт и единый экран источника — всего 2 сообщения (без списка источников).
    assert len(rename_message.sent) == 2
    screen_text = rename_message.sent[-1]["text"]
    assert "1. Podcast (2)" not in screen_text
    assert "<b>Podcast</b>" in screen_text
    assert "Choose an action:" in screen_text
    assert state.data["source_list"] == ["Podcast"] and state.state is None


def test_sources_menu_delete_flow(use_repo: UseRepo) -> None:
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

    asyncio.run(sources_handler.cb_manage_sources(callback(Cb.MENU_SOURCES), state))
    delete_callback = callback(Cb.src_del(0))
    asyncio.run(sources_handler.cb_source_delete(delete_callback, state))

    confirm = delete_callback.message.sent[-1]
    assert "Notes that will be deleted: <b>2</b>" in confirm["text"]
    confirm_datas = [button.callback_data for button in flat(confirm["reply_markup"])]
    assert Cb.src_del_confirm(0) in confirm_datas

    confirm_callback = callback(Cb.src_del_confirm(0))
    asyncio.run(sources_handler.cb_source_delete_confirm(confirm_callback, state))

    assert "Removed notes: <b>2</b>" in confirm_callback.message.sent[-2]["text"]
    data = rows_by_header(sheet)
    assert [row[SOURCE_HEADER] for row in data] == ["X"]
    assert state.data["source_list"] == ["X"]
