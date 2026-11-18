"""
Точка входа: health-check для Render, регистрация роутеров, запуск polling.

Каждый раздел бота — отдельный Router в своём модуле.
Здесь только общие команды и запуск.
"""

import asyncio
import logging
import threading

from aiogram import F, Router, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext

from config import (
    dp,
    get_bot,
    get_table_url,
    is_admin,
    setup_user_sheets,
)
from handlers import columns_handler, notes_handler
from services import buttons, keyboards
from services.callback import Cb, Cmd
from services.healthcheck import run_health_server
from services.telegram_io import (
    ack_callback,
    hide_callback_message,
    require_from_user,
    send_error_message,
)

logger = logging.getLogger(__name__)

router = Router(name="common")

# --- Общие команды ---


@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext) -> None:
    """
    Первый вход: регистрация пользователя и его вкладок + главное меню.

    Для не-админа создаётся вкладка ``Notes_{user_id}`` и строка в служебном
    листе ``Users``; админ работает с основной вкладкой ``MY_NOTES``.

    Медленные синхронные вызовы Google API уходят в поток
    (asyncio.to_thread), чтобы /start одного пользователя не задерживал
    ответы другим.
    """
    from_user = require_from_user(message)
    if from_user is None:
        return
    await state.clear()
    try:
        await asyncio.to_thread(setup_user_sheets, from_user)
        await notes_handler.send_main_menu(message)
    except Exception as e:
        await send_error_message(message, e)


async def _require_admin(message: types.Message) -> bool:
    """Гвард доступа: from_user + админ; не-админу уходит ADMIN_ONLY."""
    from_user = require_from_user(message)
    if from_user is None:
        return False
    if not is_admin(from_user.id):
        await message.answer(buttons.ADMIN_ONLY)
        return False
    return True


@router.message(Command(Cmd.HELP))
async def cmd_help(message: types.Message, state: FSMContext) -> None:
    """Справка по боту — доступна всем, рядом с /start в меню команд."""
    await state.clear()
    await message.answer(
        buttons.HELP_TEXT,
        reply_markup=keyboards.back_to_main(),
        parse_mode="HTML",
    )


@router.message(Command(Cmd.TABLE))
async def cmd_table(message: types.Message, state: FSMContext) -> None:
    """Ссылка на Google-таблицу — только для админа."""
    if not await _require_admin(message):
        return
    await state.clear()
    try:
        await message.answer(
            buttons.TABLE_LINK_PROMPT,
            reply_markup=keyboards.table_link(get_table_url()),
            parse_mode="HTML",
        )
    except Exception as e:
        await send_error_message(message, e)


# /add_note и /add_source обрабатываются в notes_handler: сценарии полностью
# описаны там, а bot.py оставляет за собой только общие команды.


@router.callback_query(F.data == Cb.TO_MAIN)
async def back_to_main(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Возврат в главное меню из любого раздела."""
    await ack_callback(callback)
    await state.clear()
    await hide_callback_message(callback)
    await notes_handler.send_main_menu(callback)


# --- Запуск ---


def register_routers() -> None:
    """Порядок важен: общие команды -> заметки -> колонки."""
    dp.include_routers(
        router,
        notes_handler.router,
        columns_handler.router,
    )


async def set_bot_commands() -> None:
    """Регистрирует слэш-команды в меню Telegram."""
    await get_bot().set_my_commands(
        [
            types.BotCommand(command=Cmd.START, description=buttons.START),
            types.BotCommand(command=Cmd.HELP, description=buttons.HELP),
            types.BotCommand(command=Cmd.ADD_SOURCE, description=buttons.ADD_SOURCE),
            types.BotCommand(command=Cmd.ADD_NOTE, description=buttons.ADD_NOTE),
            types.BotCommand(command=Cmd.TABLE, description=buttons.OPEN_TABLE),
        ]
    )


async def main() -> None:
    """Запуск: health-check, роутеры, команды в меню и polling."""
    logging.basicConfig(level=logging.INFO)  # до первого обращения к боту
    try:
        get_bot()  # ранняя проверка BOT_TOKEN: падаем до старта polling
    except Exception as e:
        logger.error("Fatal: BOT_TOKEN is invalid or missing — bot not started: %s", e)
        raise
    threading.Thread(target=run_health_server, daemon=True).start()

    register_routers()
    await set_bot_commands()
    logger.info("Bot Administrator successfully started!")
    try:
        await dp.start_polling(get_bot())
    except Exception as e:
        logger.error("Fatal: polling stopped with an error: %s", e)
        raise


if __name__ == "__main__":
    asyncio.run(main())
