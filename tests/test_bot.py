"""Сборка приложения: роутеры, команды, меню и синхронизация с таблицей."""

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
from tests.fakes import FakeMessage, FakeState

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


def test_cmd_help_answers_html_with_back_button() -> None:
    """«/help» отвечает размеченным текстом справки и кнопкой в главное меню."""
    help_message = message("/help")
    state = FakeState()
    state.state = "stale-scenario"

    asyncio.run(bot.cmd_help(help_message, state))

    assert [sent["text"] for sent in help_message.sent] == [buttons.HELP_TEXT]
    assert help_message.sent[0]["reply_markup"] is not None
    assert flat(help_message.sent[0]["reply_markup"])[0].callback_data == Cb.TO_MAIN
    assert state.state is None


# --- /help: ошибки отправки ---


def test_cmd_help_reports_answer_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сбой отправки справки уходит в send_error_message, а не в лог aiogram."""
    help_message = message("/help")
    failures: list[Exception] = []

    async def _broken_answer(
        text: str, reply_markup: Any = None, **kwargs: Any
    ) -> FakeMessage:
        raise RuntimeError("Simulated Telegram failure")

    async def _error(target: Any, error: Exception) -> None:
        failures.append(error)

    monkeypatch.setattr(help_message, "answer", _broken_answer)
    monkeypatch.setattr(bot, "send_error_message", _error)

    asyncio.run(bot.cmd_help(help_message, FakeState()))

    assert [str(error) for error in failures] == ["Simulated Telegram failure"]


# --- /start ---


def test_cmd_start_registers_sheets_and_opens_menu(
    use_repo: UseRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/start создаёт вкладки пользователя (в потоке) и открывает главное меню."""
    repo, _ = repo_for()
    use_repo(repo)
    registered: list[Any] = []

    def _setup(from_user: Any) -> str:
        registered.append(from_user)
        return f"Notes_{from_user.id}"

    monkeypatch.setattr(bot, "setup_user_sheets", _setup)
    start_message = message("/start", user_id=42)
    state = FakeState()
    state.state = "stale-scenario"

    asyncio.run(bot.cmd_start(start_message, state))

    assert [user.id for user in registered] == [42]
    assert state.state is None
    assert [sent["text"] for sent in start_message.sent] == [buttons.MAIN_MENU_PROMPT]


def test_cmd_start_reports_setup_error(
    use_repo: UseRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сбой регистрации вкладок виден пользователю, меню при этом не открывается."""
    repo, _ = repo_for()
    use_repo(repo)

    def _broken(from_user: Any) -> str:
        raise RuntimeError("Simulated Sheets failure")

    monkeypatch.setattr(bot, "setup_user_sheets", _broken)
    start_message = message("/start", user_id=42)

    asyncio.run(bot.cmd_start(start_message, FakeState()))

    texts = [sent["text"] for sent in start_message.sent]
    assert any("Simulated Sheets failure" in text for text in texts)
    assert buttons.MAIN_MENU_PROMPT not in texts


def test_cmd_start_ignores_event_without_user() -> None:
    """Сообщение без from_user не регистрирует вкладки и ничего не отправляет."""
    start_message = message("/start")
    setattr(start_message, "from_user", None)

    asyncio.run(bot.cmd_start(start_message, FakeState()))

    assert start_message.sent == []


# --- /table: только админ ---


def test_cmd_table_shows_link_for_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Админ получает ссылку на таблицу с кнопкой открытия в браузере."""
    monkeypatch.setattr(bot, "is_admin", lambda user_id: user_id == 7)
    monkeypatch.setattr(bot, "get_table_url", lambda: "https://example.com/sheet")
    table_message = message("/table", user_id=7)
    state = FakeState()

    asyncio.run(bot.cmd_table(table_message, state))

    assert [sent["text"] for sent in table_message.sent] == [buttons.TABLE_LINK_PROMPT]
    buttons_of_reply = flat(table_message.sent[0]["reply_markup"])
    assert [button.url for button in buttons_of_reply] == [
        "https://example.com/sheet",
        None,
    ]
    assert state.state is None


def test_cmd_table_denies_regular_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """Не-админу уходит только ADMIN_ONLY, ссылка на таблицу не показывается."""
    monkeypatch.setattr(bot, "is_admin", lambda user_id: False)
    table_message = message("/table", user_id=8)

    asyncio.run(bot.cmd_table(table_message, FakeState()))

    assert [sent["text"] for sent in table_message.sent] == [buttons.ADMIN_ONLY]


def test_cmd_table_ignores_event_without_user() -> None:
    """Без from_user «/table» молча завершается: ни отказа, ни ссылки."""
    table_message = message("/table")
    setattr(table_message, "from_user", None)

    asyncio.run(bot.cmd_table(table_message, FakeState()))

    assert table_message.sent == []


def test_cmd_table_reports_answer_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ошибка отправки ссылки уходит в send_error_message вместо тишины."""
    monkeypatch.setattr(bot, "is_admin", lambda user_id: True)
    table_message = message("/table", user_id=7)
    failures: list[Exception] = []

    async def _broken_answer(
        text: str, reply_markup: Any = None, **kwargs: Any
    ) -> FakeMessage:
        raise RuntimeError("Simulated Telegram failure")

    async def _error(target: Any, error: Exception) -> None:
        failures.append(error)

    monkeypatch.setattr(table_message, "answer", _broken_answer)
    monkeypatch.setattr(bot, "send_error_message", _error)

    asyncio.run(bot.cmd_table(table_message, FakeState()))

    assert [str(error) for error in failures] == ["Simulated Telegram failure"]


# --- Навигация: back_to_main и «🔄 Sync» ---


def test_back_to_main_clears_state_and_opens_menu(use_repo: UseRepo) -> None:
    """«🏠 Main menu»: кнопка погашена, сообщение скрыто, меню открыто."""
    repo, _ = repo_for()
    use_repo(repo)
    back_callback = callback(Cb.TO_MAIN)
    state = FakeState()
    state.state = "stale-scenario"

    asyncio.run(bot.back_to_main(back_callback, state))

    assert state.state is None
    assert back_callback.answers == [{"text": None, "show_alert": False}]
    assert [sent["text"] for sent in back_callback.message.sent] == [
        buttons.MAIN_MENU_PROMPT
    ]


# --- Синхронизация: /refresh и «🔄 Sync» ---


def _prepare_sync(
    use_repo: UseRepo, monkeypatch: pytest.MonkeyPatch, user_id: int = 1
) -> tuple[Any, list[Any]]:
    """Репозиторий на FakeSheet и журнал вызовов ``invalidate_cache``.

    В таблицу добавлена строка «руками» уже после заполнения кеша:
    без сброса кеша бот её не видит. ``user_id`` тот же, что у события: иначе
    снимок прогревается под одним ключом, а сбрасывается под другим.
    Шпион не подменяет поведение — после записи в журнал вызывает настоящую
    ``invalidate_cache``.
    """
    repo, sheet = repo_for([note_row("1", "Blog", "a")], user_id=user_id)
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
    """«/refresh»: ключ кэша — user_id события, лист перечитан, статус отправлен."""
    repo, calls = _prepare_sync(use_repo, monkeypatch, user_id=99)
    refresh_message = message("/refresh", user_id=99)

    asyncio.run(bot.sync_notes(refresh_message, FakeState()))

    assert calls == [99]
    assert [note.note_id for note in repo.list_notes()] == ["1", "2"]
    assert [sent["text"] for sent in refresh_message.sent] == [buttons.SYNCED]


def test_cb_sync_invalidates_cache_and_refreshes_notes(
    use_repo: UseRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """«🔄 Sync» делает то же, что /refresh: тот же сброс и свежие данные."""
    repo, calls = _prepare_sync(use_repo, monkeypatch)
    sync_callback = callback(Cb.SYNC)
    state = FakeState()

    asyncio.run(bot.sync_notes(sync_callback, state))

    assert calls == [1]
    assert repo.get("2") is not None
    assert sync_callback.answers == [{"text": None, "show_alert": False}]
    # Одно сообщение: подтверждение синхронизации уже с меню, дубля нет.
    assert [sent["text"] for sent in sync_callback.message.sent] == [buttons.SYNCED]


def test_sync_reports_read_error(
    use_repo: UseRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ошибка чтения после сброса кэша показывается, а меню всё равно открыто."""
    repo, _ = _prepare_sync(use_repo, monkeypatch, user_id=1)

    def _broken() -> list[list[str]]:
        raise RuntimeError("Simulated Sheets failure")

    monkeypatch.setattr(repo, "all_values", _broken)
    refresh_message = message("/refresh", user_id=1)

    asyncio.run(bot.sync_notes(refresh_message, FakeState()))

    texts = [sent["text"] for sent in refresh_message.sent]
    assert any("Simulated Sheets failure" in text for text in texts)
    assert texts[-1] == buttons.MAIN_MENU_PROMPT


# --- Запуск ---


def test_serve_health_logs_start_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Падение health-check в потоке попадает в лог, а не исчезает молча."""

    def _broken() -> None:
        raise OSError("port is busy")

    monkeypatch.setattr(bot, "run_health_server", _broken)

    with caplog.at_level("ERROR"):
        bot._serve_health()

    assert "health-check server failed to start" in caplog.text
    assert "port is busy" in caplog.text


class _RecordingThread:
    """Двойник ``threading.Thread``: запоминает цель и флаг daemon, не стартуя."""

    def __init__(self, target: Callable[[], None], daemon: bool = False) -> None:
        self.target = target
        self.daemon = daemon
        self.started = False
        self.instances.append(self)

    def start(self) -> None:
        self.started = True

    instances: list["_RecordingThread"] = []


def test_start_health_server_runs_target_in_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Фоновый поток запускается как daemon и вызывает ``_serve_health``."""
    monkeypatch.setattr(bot.threading, "Thread", _RecordingThread)
    monkeypatch.setattr(_RecordingThread, "instances", [])

    bot._start_health_server()

    assert len(_RecordingThread.instances) == 1
    thread = _RecordingThread.instances[0]
    assert thread.started is True
    assert thread.target is bot._serve_health
    assert thread.daemon is True


def test_setup_bot_fails_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Невалидный BOT_TOKEN останавливает запуск до старта polling."""

    def _no_bot() -> Any:
        raise RuntimeError("Environment variable BOT_TOKEN is not set.")

    monkeypatch.setattr(bot, "get_bot", _no_bot)

    with pytest.raises(RuntimeError, match="BOT_TOKEN"):
        asyncio.run(bot._setup_bot())


def test_main_wires_startup_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    """``main`` запускает health-check, настройку бота и polling по порядку."""
    steps: list[str] = []

    logging_calls: list[dict[str, Any]] = []

    def _basic_config(**kwargs: Any) -> None:
        logging_calls.append(kwargs)

    monkeypatch.setattr(bot.logging, "basicConfig", _basic_config)
    monkeypatch.setattr(bot, "_start_health_server", lambda: steps.append("health"))

    async def _setup() -> None:
        steps.append("setup")

    async def _polling() -> None:
        steps.append("polling")

    monkeypatch.setattr(bot, "_setup_bot", _setup)
    monkeypatch.setattr(bot, "_run_polling", _polling)

    asyncio.run(bot.main())

    assert steps == ["health", "setup", "polling"]
    assert logging_calls == [{"level": bot.logging.INFO}]
