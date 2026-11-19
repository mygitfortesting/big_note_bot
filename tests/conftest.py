"""Общие фикстуры и хелперы тестов: подмена репозитория, callback и кнопки."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

import pytest

from handlers import columns_handler
from services.callback import Cb
from services.sheets import NoteRepository
from services.text_utils import escape_html
from tests.fakes import FakeCallback, FakeMessage

#: Имена модулей с собственной ссылкой ``repo_of``. Берём модули через
#: ``sys.modules``: так подмена находит и реэкспорт в ``notes_handler``.
_REPO_MODULES = (
    "handlers.notes_common",
    "handlers.notes_handler",
    "handlers.wizard_handler",
    "handlers.sources_handler",
)


@pytest.fixture()
def use_repo(monkeypatch: pytest.MonkeyPatch) -> Callable[[Any], Any]:
    """
    Подменяет репозиторий хендлеров заметок готовым ``NoteRepository``.

    Сигнатура макета повторяет настоящую: ``repo_of(event)``. Подменяется
    глобальный ``repo_of`` в каждом модуле хендлеров заметок; реэкспорт из
    ``notes_handler`` использует тот же словарь, поэтому работает тоже.
    """

    def _install(repo: NoteRepository) -> NoteRepository:
        def _fake_repo(event: Any) -> Any:  # event — как у настоящей repo_of
            return repo

        # Глобальный ``repo_of`` у функций реэкспорта — словарь модуля-источника,
        # поэтому подмены на уровне модулей достаточно для любого вызова.
        for module_name in _REPO_MODULES:
            module = sys.modules.get(module_name)
            if module is not None:
                monkeypatch.setattr(module, "repo_of", _fake_repo)
        return repo

    return _install


@pytest.fixture()
def use_service(monkeypatch: pytest.MonkeyPatch) -> Callable[[Any], Any]:
    """Подменяет ``_service`` хендлера колонок готовым сервисом на FakeSheet."""

    def _install(service: Any) -> Any:
        def _fake_service(event: Any) -> Any:
            return service

        monkeypatch.setattr(columns_handler, "_service", _fake_service)
        return service

    return _install


def callback(data: str, user_id: int = 1) -> FakeCallback:
    """CallbackQuery-двойник без pydantic-валидации."""
    return FakeCallback(data, user_id)


def message(text: str = "", user_id: int = 1) -> FakeMessage:
    """FakeMessage-двойник для handlers, ожидающих ``types.Message``."""
    return FakeMessage(text, user_id)


def flat(markup: Any) -> list[Any]:
    """Все кнопки клавиатуры одним списком."""
    return [button for row in markup.inline_keyboard for button in row]


__all__ = [
    "Cb",
    "callback",
    "escape_html",
    "flat",
    "message",
    "use_repo",
    "use_service",
]
