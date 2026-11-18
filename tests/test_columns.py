"""Тесты экрана «📋 Columns»: нумерованный список и меню колонки."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

import pytest

from config import NOTES_HEADERS
from handlers import columns_handler
from services import buttons
from services.callback import Cb
from services.sheets import ColumnService
from tests.factories import columns_for
from tests.fakes import FakeCallback, FakeMessage, FakeState, FakeUser


@pytest.fixture()
def use_service(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[ColumnService], ColumnService]:
    """Подменяет ``_service`` хендлера готовым сервисом на FakeSheet."""

    def _install(service: ColumnService) -> ColumnService:
        def _fake_service(event: Any) -> ColumnService:
            return service

        monkeypatch.setattr(columns_handler, "_service", _fake_service)
        return service

    return _install


def _callback(data: str, user_id: int = 1) -> Any:
    return FakeCallback(data, user_id)


def _flat(markup: Any) -> list[Any]:
    return [button for row in markup.inline_keyboard for button in row]


def test_columns_menu_lists_only_custom_columns(use_service) -> None:
    """Список «📋 Columns»: только пользовательские колонки, номера-кнопки."""
    service, _ = columns_for()
    use_service(service)
    callback = _callback(Cb.MENU_COLUMNS)
    state = FakeState()

    asyncio.run(columns_handler.cb_columns_menu(callback, state))

    sent = callback.message.sent[-1]
    text = sent["text"]
    # DESCRIPTION, URL, SCREEN — пользовательские (между NAME и COMMENT)
    assert "1. <b>DESCRIPTION</b>" in text
    assert "2. <b>URL</b>" in text
    assert "3. <b>SCREEN</b>" in text
    assert "1. <b>ID</b>" not in text
    assert "Fixed:" in text

    texts = [button.text for button in _flat(sent["reply_markup"])]
    assert texts == ["1", "2", "3", buttons.ADD_COLUMN, buttons.TO_MAIN]
    assert "ID" not in texts
    assert "SOURCE" not in texts
    assert "NAME" not in texts
    assert "COMMENT" not in texts
    # Индексы колонок в шапке сохранены для меню действий.
    assert state.data["column_list"] == [4, 5, 6]


def test_column_number_opens_actions(use_service) -> None:
    """Нажатие на номер открывает меню действий выбранной колонки."""
    service, _ = columns_for()
    use_service(service)
    state = FakeState()
    asyncio.run(columns_handler.cb_columns_menu(_callback(Cb.MENU_COLUMNS), state))

    callback = _callback(Cb.col_view(1))
    asyncio.run(columns_handler.cb_column_view(callback, state))

    sent = callback.message.sent[-1]
    assert "Column: URL" in sent["text"]
    flat = _flat(sent["reply_markup"])
    texts = [button.text for button in flat]
    datas = [button.callback_data for button in flat]
    assert buttons.CHANGE_ORDER in texts
    assert buttons.RENAME_SOURCE in texts
    assert buttons.DELETE_SOURCE in texts
    assert Cb.COL_REORDER in datas
    assert Cb.col_rename(5) in datas
    assert not any("col_move" in (data or "") for data in datas)


def test_change_order_full_flow(use_service) -> None:
    """🔢 Change order: ввод «2 1 3» переставляет средние колонки."""
    service, sheet = columns_for()
    use_service(service)
    state = FakeState()
    asyncio.run(columns_handler.cb_columns_menu(_callback(Cb.MENU_COLUMNS), state))

    order_callback = _callback(Cb.COL_REORDER)
    asyncio.run(columns_handler.cb_column_reorder(order_callback, state))

    prompt = order_callback.message.sent[-1]
    assert "1. <b>DESCRIPTION</b>" in prompt["text"]
    assert "2. <b>URL</b>" in prompt["text"]
    datas = [button.callback_data for button in _flat(prompt["reply_markup"])]
    assert Cb.CANCEL_INPUT in datas
    assert state.state == columns_handler.ColumnStates.wait_order

    message = FakeMessage("2 1 3", user_id=1)
    asyncio.run(columns_handler.got_column_order(message, state))

    assert sheet.rows[0][3:6] == ["URL", "DESCRIPTION", "SCREEN"]
    assert sheet.rows[0][:3] == ["ID", "SOURCE", "NAME"]
    assert sheet.rows[0][-1] == "COMMENT"
    assert state.state is None
    assert "1. <b>URL</b>" in message.sent[-1]["text"]


def test_bad_order_keeps_user_in_order_state(use_service) -> None:
    """Неверный ввод: подсказка и ожидание повтора (без перестановки)."""
    service, sheet = columns_for()
    use_service(service)
    state = FakeState()
    asyncio.run(columns_handler.cb_columns_menu(_callback(Cb.MENU_COLUMNS), state))
    asyncio.run(columns_handler.cb_column_reorder(_callback(Cb.COL_REORDER), state))
    before = list(sheet.rows[0])

    message = FakeMessage("1 1", user_id=1)
    asyncio.run(columns_handler.got_column_order(message, state))

    assert sheet.rows[0] == before
    assert "⚠️" in message.sent[-1]["text"]
    assert state.state == columns_handler.ColumnStates.wait_order


def test_screen_column_cannot_be_deleted(use_service) -> None:
    """SCREEN защищена: кнопки удаления в её меню нет, переименование есть."""
    service, _ = columns_for()
    use_service(service)
    state = FakeState()
    asyncio.run(columns_handler.cb_columns_menu(_callback(Cb.MENU_COLUMNS), state))

    callback = _callback(Cb.col_view(2))  # SCREEN
    asyncio.run(columns_handler.cb_column_view(callback, state))

    texts = [button.text for button in _flat(callback.message.sent[-1]["reply_markup"])]
    assert buttons.DELETE_SOURCE not in texts
    assert buttons.RENAME_SOURCE in texts


def test_columns_menu_add_column_prompt(use_service) -> None:
    """«➕ Add column» спрашивает имя новой колонки."""
    service, _ = columns_for()
    use_service(service)
    message = FakeMessage("", user_id=1)
    message.from_user = FakeUser(1)
    state = FakeState()

    asyncio.run(columns_handler.cb_column_add(message, state))

    assert state.state == columns_handler.ColumnStates.wait_new_name


def test_new_column_gets_into_numbered_list(use_service) -> None:
    """Добавленная колонка появляется в нумерованном списке."""
    service, sheet = columns_for()
    use_service(service)
    state = FakeState()
    message = FakeMessage("CONTACT", user_id=1)
    asyncio.run(state.set_state(columns_handler.ColumnStates.wait_new_name))

    asyncio.run(columns_handler.got_new_column_name(message, state))

    assert "CONTACT" in sheet.rows[0]
    body = message.sent[-1]["text"]
    assert "4. <b>CONTACT</b>" in body
    assert body.startswith(buttons.COLUMNS_PROMPT)
    assert NOTES_HEADERS[0] in body
