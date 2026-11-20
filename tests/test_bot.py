"""Сборка приложения: роутеры, синхронизация с таблицей, главное меню и /help."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

import bot
from handlers import ai_note_handler, columns_handler, notes_handler
from services import buttons, keyboards, sheets
from services.callback import Cb
from services.sheets import NoteRepository
from tests.conftest import callback, flat, message
from tests.factories import note_row, repo_for
from tests.fakes import FakeState

#: Фикстура ``use_repo`` из ``tests.conftest``.
UseRepo = Callable[[NoteRepository], NoteRepository]


def test_register_routers_wiring() -> None:
    """
    Сборка приложения: каждый роутер один раз, ✨ AI Note перед заметками.

    ``dp.include_routers`` бросает RuntimeError на второй ``include_router``
    того же роутера, поэтому дубль ✨ AI Note (в пакете и в ``bot.py``)
    ловится здесь локально, до деплоя.
    """
    bot.register_routers()
    attached = list(bot.dp.sub_routers)

    for router in (
        bot.router,
        ai_note_handler.ai_router,
        notes_handler.router,
        columns_handler.router,
    ):
        assert attached.count(router) == 1
    # Пакет заметок не тянет ai_router: подключение живёт только в bot.py.
    assert ai_note_handler.ai_router not in list(notes_handler.router.sub_routers)
    # AiStates должны ловить сообщения раньше общего поиска по TITLE.
    assert attached.index(ai_note_handler.ai_router) < attached.index(
        notes_handler.router
    )
    # Повторная сборка — ошибка aiogram, а не тихий дубль.
    with pytest.raises(RuntimeError, match="already attached"):
        bot.register_routers()
    assert list(bot.dp.sub_routers) == attached


# --- Меню и /help ---


def test_main_menu_has_sync_button() -> None:
    """В главном меню есть «🔄 Sync» и она отправляет Cb.SYNC."""
    keyboard = flat(keyboards.main_menu())

    assert (buttons.SYNC, Cb.SYNC) in [
        (button.text, button.callback_data) for button in keyboard
    ]
    assert buttons.COLUMNS in [button.text for button in keyboard]


def test_help_text_sections() -> None:
    """Справка разбита на три раздела и упоминает ключевые кнопки."""
    text = buttons.HELP_TEXT

    assert all(
        part in text
        for part in ("📝 Creating notes", "🗂 Browsing &amp; Editing", "⚙️ Table")
    )
    assert all(
        part in text
        for part in (buttons.AI_NOTE, buttons.SOURCES, buttons.COLUMNS, buttons.SYNC)
    )
    assert "/refresh" in text and "/table" in text


# --- Синхронизация: /refresh и «🔄 Sync» ---


def _prepare_sync(
    use_repo: UseRepo, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, list[Any]]:
    """Репозиторий на FakeSheet и журнал вызовов ``invalidate_cache``.

    В таблицу добавлена строка «руками» уже после заполнения кеша:
    без сброса кеша бот её не видит. Шпион не подменяет поведение —
    после записи в журнал вызывает настоящую ``invalidate_cache``.
    """
    repo, sheet = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    repo.all_values()
    sheet.rows.append(note_row("2", "Blog", "b"))
    real_invalidate = sheets.invalidate_cache
    calls: list[Any] = []

    def _spy(user_id: int | None = None) -> None:
        calls.append(user_id)
        real_invalidate(user_id)

    monkeypatch.setattr(bot, "invalidate_cache", _spy)
    return repo, calls


def test_cmd_refresh_clears_cache_and_rereads(
    use_repo: UseRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """«/refresh»: сбрасывает кеш, сразу перечитывает лист и показывает статус."""
    repo, calls = _prepare_sync(use_repo, monkeypatch)
    refresh_message = message("/refresh", user_id=1)
    state = FakeState()

    asyncio.run(bot.cmd_refresh(refresh_message, state))

    assert calls == [repo._user_id]
    assert [note.note_id for note in repo.list_notes()] == ["1", "2"]
    assert buttons.SYNCED in [sent["text"] for sent in refresh_message.sent]
    assert refresh_message.sent[-1]["text"] == buttons.MAIN_MENU_PROMPT


def test_cb_sync_reuses_same_handler(
    use_repo: UseRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """«🔄 Sync» делает то же, что /refresh: тот же сброс и свежие данные."""
    repo, calls = _prepare_sync(use_repo, monkeypatch)
    sync_callback = callback(Cb.SYNC)
    state = FakeState()

    asyncio.run(bot.cb_sync(sync_callback, state))

    assert calls == [repo._user_id]
    assert repo.get("2") is not None
    assert sync_callback.answers == [{"text": None, "show_alert": False}]
    assert buttons.SYNCED in [sent["text"] for sent in sync_callback.message.sent]


def test_sync_reports_read_error(
    use_repo: UseRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ошибка чтения после сброса кеша показывается, а меню всё равно открыто."""
    repo, _ = _prepare_sync(use_repo, monkeypatch)

    def _broken() -> list[list[str]]:
        raise RuntimeError("Simulated Sheets failure")

    monkeypatch.setattr(repo, "all_values", _broken)
    refresh_message = message("/refresh", user_id=1)

    asyncio.run(bot.cmd_refresh(refresh_message, FakeState()))

    texts = [sent["text"] for sent in refresh_message.sent]
    assert any("Simulated Sheets failure" in text for text in texts)
    assert texts[-1] == buttons.MAIN_MENU_PROMPT
