"""Сборка приложения: роутеры подключаются ровно один раз и в нужном порядке."""

from __future__ import annotations

import pytest

import bot
from handlers import ai_note_handler, columns_handler, notes_handler


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
