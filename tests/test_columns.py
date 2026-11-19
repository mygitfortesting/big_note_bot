"""Тесты экрана «📋 Columns»: нумерованный список и меню колонки."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from config import NOTES_HEADERS
from handlers import columns_handler
from services import buttons
from services.callback import Cb
from services.sheets import ColumnService
from services.text_utils import WIDTH_SPACER
from tests.conftest import callback, flat, message
from tests.factories import columns_for
from tests.fakes import FakeSheet, FakeState, FakeUser

#: Фикстура ``use_service`` из ``tests.conftest``.
UseService = Callable[[ColumnService], ColumnService]


def _open_columns(
    use_service: UseService,
) -> tuple[ColumnService, FakeSheet, FakeState]:
    """Сервис на FakeSheet, подменённый в хендлере, и открытое меню колонок."""
    service, sheet = columns_for()
    use_service(service)
    state = FakeState()
    asyncio.run(columns_handler.cb_columns_menu(callback(Cb.MENU_COLUMNS), state))
    return service, sheet, state


def test_columns_menu_lists_only_custom_columns(use_service: UseService) -> None:
    """Список «📋 Columns»: только пользовательские колонки, номера-кнопки."""
    _, _, state = _open_columns(use_service)
    menu = callback(Cb.MENU_COLUMNS)
    asyncio.run(columns_handler.cb_columns_menu(menu, state))

    message = menu.message.sent[-1]
    text = message["text"]
    # DESCRIPTION, URL, SCREEN — пользовательские (между TITLE и COMMENT)
    assert "1. <code>DESCRIPTION</code>" in text
    assert "2. <code>URL</code>" in text
    assert "3. <code>SCREEN</code>" in text
    assert "1. <code>ID</code>" not in text
    assert "Fixed:" not in text

    rows = message["reply_markup"].inline_keyboard
    texts = [button.text for button in flat(message["reply_markup"])]
    assert texts == [
        "1",
        "2",
        "3",
        buttons.ADD_COLUMN,
        buttons.CHANGE_ORDER,
        buttons.TO_MAIN,
    ]
    # «➕ Add column» и «🔢 Change order» — в одном ряду с меню.
    assert [button.text for button in rows[-2]] == [
        buttons.ADD_COLUMN,
        buttons.CHANGE_ORDER,
    ]
    assert Cb.COL_REORDER in [button.callback_data for button in rows[-2]]
    assert "ID" not in texts
    assert "SOURCE" not in texts
    assert "TITLE" not in texts
    assert "COMMENT" not in texts
    # Индексы колонок в шапке сохранены для меню действий.
    assert state.data["column_list"] == [4, 5, 6]


def test_column_number_opens_actions(use_service: UseService) -> None:
    """Нажатие на номер открывает меню действий выбранной колонки."""
    _, _, state = _open_columns(use_service)

    view_callback = callback(Cb.col_view(1))
    asyncio.run(columns_handler.cb_column_view(view_callback, state))

    sent = view_callback.message.sent[-1]
    assert "Column: URL" in sent["text"]
    # Короткий экран добивается до минимальной ширины пузыря.
    assert sent["text"].endswith(WIDTH_SPACER)
    buttons_flat = flat(sent["reply_markup"])
    texts = [button.text for button in buttons_flat]
    datas = [button.callback_data for button in buttons_flat]
    assert texts == [
        buttons.RENAME_COLUMN_SHORT,
        buttons.DELETE_COLUMN_SHORT,
        buttons.BACK,
        buttons.TO_MAIN,
    ]
    assert buttons.CHANGE_ORDER not in texts
    assert Cb.COL_REORDER not in datas
    assert Cb.col_rename(5) in datas
    assert not any("col_move" in (data or "") for data in datas)


def test_change_order_full_flow(use_service: UseService) -> None:
    """🔢 Change order: ввод «2 1 3» переставляет средние колонки."""
    _, sheet, state = _open_columns(use_service)

    order_callback = callback(Cb.COL_REORDER)
    asyncio.run(columns_handler.cb_column_reorder(order_callback, state))

    prompt = order_callback.message.sent[-1]
    assert "1. <code>DESCRIPTION</code>" in prompt["text"]
    assert "2. <code>URL</code>" in prompt["text"]
    # Готовая для копирования строка текущих номеров — одна, последней.
    assert prompt["text"].endswith("Copy: <code>1 2 3</code>")
    assert prompt["text"].count("Copy:") == 1
    datas = [button.callback_data for button in flat(prompt["reply_markup"])]
    assert Cb.CANCEL_INPUT in datas
    assert state.state == columns_handler.ColumnStates.wait_order

    order_message = message("2 1 3", user_id=1)
    asyncio.run(columns_handler.got_column_order(order_message, state))

    assert sheet.rows[0][3:6] == ["URL", "DESCRIPTION", "SCREEN"]
    assert sheet.rows[0][:3] == ["ID", "SOURCE", "TITLE"]
    assert sheet.rows[0][-1] == "COMMENT"
    assert state.state is None
    assert "1. <code>URL</code>" in order_message.sent[-1]["text"]


def test_order_cancel_returns_to_columns(use_service: UseService) -> None:
    """«✖️ Cancel» ввода порядка возвращает к списку «📋 Columns»."""
    _, sheet, state = _open_columns(use_service)
    asyncio.run(columns_handler.cb_column_reorder(callback(Cb.COL_REORDER), state))
    assert state.state == columns_handler.ColumnStates.wait_order

    cancel_callback = callback(Cb.CANCEL_INPUT)
    asyncio.run(columns_handler.cb_cancel_column_order(cancel_callback, state))

    assert state.state is None
    sent = cancel_callback.message.sent[-1]
    assert sent["text"].startswith(buttons.COLUMNS_PROMPT)
    assert "1. <code>DESCRIPTION</code>" in sent["text"]
    assert sheet.rows[0][:3] == ["ID", "SOURCE", "TITLE"]


def test_bad_order_keeps_user_in_order_state(use_service: UseService) -> None:
    """Неверный ввод: подсказка и ожидание повтора (без перестановки)."""
    _, sheet, state = _open_columns(use_service)
    asyncio.run(columns_handler.cb_column_reorder(callback(Cb.COL_REORDER), state))
    before = list(sheet.rows[0])

    bad_message = message("1 1", user_id=1)
    asyncio.run(columns_handler.got_column_order(bad_message, state))

    assert sheet.rows[0] == before
    assert "⚠️" in bad_message.sent[-1]["text"]
    assert state.state == columns_handler.ColumnStates.wait_order


def test_screen_column_cannot_be_deleted(use_service: UseService) -> None:
    """SCREEN защищена: кнопки удаления в её меню нет, переименование есть."""
    _, _, state = _open_columns(use_service)

    view_callback = callback(Cb.col_view(2))  # SCREEN
    asyncio.run(columns_handler.cb_column_view(view_callback, state))

    sent = view_callback.message.sent[-1]
    texts = [button.text for button in flat(sent["reply_markup"])]
    assert buttons.DELETE_COLUMN_SHORT not in texts
    assert buttons.RENAME_COLUMN_SHORT in texts


def test_columns_menu_add_column_prompt(use_service: UseService) -> None:
    """«➕ Add column» спрашивает имя новой колонки."""
    service, _ = columns_for()
    use_service(service)
    add_message = message("", user_id=1)
    add_message.from_user = FakeUser(1)
    state = FakeState()

    asyncio.run(columns_handler.cb_column_add(add_message, state))

    assert state.state == columns_handler.ColumnStates.wait_new_name


def test_new_column_gets_into_numbered_list(use_service: UseService) -> None:
    """Добавленная колонка появляется в нумерованном списке."""
    service, sheet = columns_for()
    use_service(service)
    state = FakeState()
    column_message = message("CONTACT", user_id=1)
    asyncio.run(state.set_state(columns_handler.ColumnStates.wait_new_name))

    asyncio.run(columns_handler.got_new_column_name(column_message, state))

    assert "CONTACT" in sheet.rows[0]
    body = column_message.sent[-1]["text"]
    assert "4. <code>CONTACT</code>" in body
    assert body.startswith(buttons.COLUMNS_PROMPT)
    assert NOTES_HEADERS[0] in body
