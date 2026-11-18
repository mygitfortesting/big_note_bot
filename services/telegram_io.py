"""Отправка сообщений и callback-ответов (единственная точка выхода в Telegram)."""

import logging
from typing import Any, TypeAlias, cast

from aiogram import types

from services.text_utils import escape_html, plain_text, split_messages

logger = logging.getLogger(__name__)
Target: TypeAlias = types.Message | types.CallbackQuery


def _message_of(target: Target) -> types.Message | None:
    """
    Сообщение, к которому относится target (сам Message или сообщение кнопки).

    Проверка duck-typed: достаточно метода ``answer``. Настоящие ошибки
    Telegram приходят из ``answer``/``answer_photo`` и попадают в
    ``send_error_message`` — это правильнее, чем молча признать событие
    «не сообщением» и не отправить ничего.
    """
    if isinstance(target, types.CallbackQuery):
        candidate: Any = target.message
    else:
        candidate = target
    if not callable(getattr(candidate, "answer", None)):
        return None
    return cast(types.Message, candidate)


def require_from_user(target: Target) -> types.User | None:
    """Пользователь события или None (анонимные админы групп без from_user)."""
    return cast(types.User | None, getattr(target, "from_user", None))


def user_id_of(target: Target) -> int | None:
    """ID пользователя из события (НИКОГДА не из callback_data)."""
    user = require_from_user(target)
    return user.id if user is not None else None


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
    message = callback.message
    if not isinstance(message, types.Message):
        return
    try:
        await message.delete()
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
    return "429" in str(error) or "Quota exceeded" in str(error)


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
