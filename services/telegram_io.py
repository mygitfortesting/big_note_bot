"""Отправка сообщений и callback-ответов (единственная точка выхода в Telegram)."""

import asyncio
import logging
from typing import Any, TypeAlias, cast

import gspread.exceptions
from aiogram import types

from services.text_utils import escape_html, plain_text, split_messages

logger = logging.getLogger(__name__)
Target: TypeAlias = types.Message | types.CallbackQuery


def _message_of(target: Target) -> types.Message | None:
    """
    Сообщение, к которому относится target (сам Message или сообщение кнопки).

    Проверка duck-typed: у кнопки достаточно атрибутов ``message`` и ``data``,
    у сообщения — метода ``answer``. Никаких ``isinstance``: двойники в
    тестах — обычные Python-классы, а не pydantic-модели aiogram.
    Настоящие ошибки Telegram приходят из ``answer``/``answer_photo`` и
    попадают в ``send_error_message`` — это правильнее, чем молча признать
    событие «не сообщением» и не отправить ничего.
    """
    if _looks_like_callback(target):
        candidate: Any = getattr(target, "message", None)
    else:
        candidate = target
    if not callable(getattr(candidate, "answer", None)):
        return None
    return cast(types.Message, candidate)


def _looks_like_callback(target: Any) -> bool:
    """Событие с атрибутами ``message`` и ``data`` (callback или двойник)."""
    return hasattr(target, "message") and hasattr(target, "data")


def require_from_user(target: Target) -> types.User | None:
    """
    Пользователь события или None (анонимные админы групп без from_user).

    Только ``getattr``: работает и для настоящего Message/CallbackQuery,
    и для тестовых двойников.
    """
    user = getattr(target, "from_user", None)
    return cast(types.User, user) if user is not None else None


def user_id_of(target: Target) -> int | None:
    """ID пользователя из события (НИКОГДА не из callback_data)."""
    user = require_from_user(target)
    return user.id if user is not None else None


_user_locks: dict[int, asyncio.Lock] = {}


def user_lock(user_id: int | None = None) -> asyncio.Lock:
    """Возвращает asyncio.Lock для сериализации записей конкретного пользователя."""
    key = user_id if user_id is not None else 0
    lock = _user_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[key] = lock
    return lock


async def ack_callback(callback: types.CallbackQuery, text: str | None = None) -> None:
    """Гасит «часики» на кнопке; текст показывается всплывашкой."""
    try:
        await callback.answer(text=text)
    except Exception as e:  # протухший callback не должен ломать сценарий
        logger.debug("callback.answer failed: %s", e)


async def alert(
    callback: types.CallbackQuery, text: str, show_alert: bool = True
) -> None:
    """Всплывающее окно с предупреждением (например, о защищённой колонке)."""
    try:
        await callback.answer(text=text, show_alert=show_alert)
    except Exception as e:
        logger.warning("callback.alert failed: %s", e)


async def hide_callback_message(callback: types.CallbackQuery) -> None:
    """Удаляет сообщение, под которым была нажата кнопка (если можно)."""
    message = getattr(callback, "message", None)
    delete = getattr(message, "delete", None)
    if not callable(delete):
        return
    try:
        await delete()
    except Exception as e:
        # >48 часов или уже удалено — не критично для сценария
        logger.debug("Could not hide callback message: %s", e)


async def show_main_menu(target: Target, text: str, markup=None) -> None:
    """Отправляет главное меню в чат события (сообщения или кнопки)."""
    message = _message_of(target)
    if message is not None:
        await message.answer(text, reply_markup=markup)


async def send_parts(target: Target, text: str, markup=None, **kwargs) -> None:
    """Отправляет длинный текст частями; кнопки — только на последней части."""
    message = _message_of(target)
    if message is None:
        return
    parts = split_messages(text)
    for index, part in enumerate(parts):
        is_last = index == len(parts) - 1
        part_markup = markup if is_last else None
        await message.answer(part, reply_markup=part_markup, **kwargs)


async def status_message(target: Target, text: str, **kwargs) -> None:
    """Короткий ответ без клавиатуры (подтверждение, ошибка, подсказка)."""
    message = _message_of(target)
    if message is not None:
        await message.answer(text, **kwargs)


async def reply(target: Target, text: str, markup=None, **kwargs) -> None:
    """
    Отправляет сообщение в чат, из которого пришёл target.
    Для CallbackQuery дополнительно «гасит часики» на кнопке.
    """
    message = _message_of(target)
    if message is not None:
        await message.answer(text, reply_markup=markup, **kwargs)


async def safe_reply(target: Target, text: str, **kwargs) -> None:
    """
    Отправляет сообщение; если Telegram не принял разметку — повторяет
    тем же текстом без разметки.
    Никогда не бросает исключение: пользователь всегда получит хоть какой-то ответ.
    """
    try:
        await reply(target, text, **kwargs)
    except Exception as e:
        logger.warning("Не удалось отправить сообщение с разметкой: %s", e)
        try:
            await reply(target, plain_text(text))
        except Exception:
            logger.exception("Fallback plain-text reply also failed")


def _is_quota_error(error: Exception) -> bool:
    """Google Sheets вернул 429 (превышен лимит запросов в минуту)."""
    if isinstance(error, gspread.exceptions.APIError):
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None) or getattr(error, "code", None)
        if status == 429:
            return True
    msg = str(error).lower()
    return "429" in msg and ("quota" in msg or "rate" in msg)


async def send_error_message(target: Target, error: Exception) -> None:
    """
    Универсальный обработчик ошибок (QA-утилита).
    Пишет сбой в лог и показывает пользователю понятное сообщение.
    """
    logger.exception("Error occurred: %s", error)

    if _is_quota_error(error):
        error_text = (
            "⏳ Google Sheets временно ограничил частоту запросов "
            "(лимит 60 чтений в минуту).\n"
            "Подождите ~30 секунд и повторите действие."
        )
    else:
        error_text = f"👹 Mistake: {escape_html(str(error))}"

    await safe_reply(target, error_text, parse_mode="HTML")
