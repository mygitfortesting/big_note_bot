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
from handlers import ai_note_handler, columns_handler, notes_handler
from services import buttons, keyboards
from services.callback import Cb, Cmd
from services.healthcheck import run_health_server
from services.sheets import invalidate_cache
from services.telegram_io import (
    ack_callback,
    hide_callback_message,
    require_from_user,
    send_error_message,
    show_main_menu,
    user_id_of,
)

logger = logging.getLogger(__name__)

router = Router(name="common")

# --- Общие команды ---


async def _start_session(
    message: types.Message,
    state: FSMContext,
    text: str,
    markup: types.InlineKeyboardMarkup,
) -> None:
    """
    Общий шаг команд: сброс сценария + ответ в HTML, ошибки — пользователю.

    Используется ``/start`` и ``/table``: оба начинают с чистого состояния
    и отвечают размеченным сообщением, а сбой отправки не должен оставлять
    бота молчащим.
    """
    await state.clear()
    try:
        await message.answer(text, reply_markup=markup, parse_mode="HTML")
    except Exception as e:
        await send_error_message(message, e)


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
    except Exception as e:
        await send_error_message(message, e)
        return
    await notes_handler.send_main_menu(message)


async def _require_admin(message: types.Message) -> types.User | None:
    """Гвард доступа: пользователь события, если он админ, иначе None.

    Не-админу сразу уходит ``ADMIN_ONLY``; ``None`` означает «сценарий
    не продолжается», а при успехе возвращается типизированный ``from_user``,
    чтобы хендлеру не приходилось искать его повторно.
    """
    from_user = require_from_user(message)
    if from_user is None:
        return None
    if not is_admin(from_user.id):
        await message.answer(buttons.ADMIN_ONLY)
        return None
    return from_user


@router.message(Command(Cmd.HELP))
async def cmd_help(message: types.Message, state: FSMContext) -> None:
    """Справка по боту — доступна всем, рядом с /start в меню команд."""
    await _start_session(message, state, buttons.HELP_TEXT, keyboards.back_to_main())


@router.message(Command(Cmd.TABLE))
async def cmd_table(message: types.Message, state: FSMContext) -> None:
    """Ссылка на Google-таблицу — только для админа."""
    from_user = await _require_admin(message)
    if from_user is None:
        return
    await _start_session(
        message,
        state,
        buttons.TABLE_LINK_PROMPT,
        keyboards.table_link(get_table_url()),
    )


# /add_note и /add_source обрабатываются в notes_handler: сценарии полностью
# описаны там, а bot.py оставляет за собой только общие команды.


async def _ack_and_hide(callback: types.CallbackQuery) -> None:
    """Гасит «часики» на кнопке и убирает сообщение, под которым её нажали.

    Обе утилиты сами глушат ошибки Telegram (протухший callback, >48 часов),
    поэтому сценарий продолжается даже на старом сообщении.
    """
    await ack_callback(callback)
    await hide_callback_message(callback)


@router.callback_query(F.data == Cb.TO_MAIN)
async def back_to_main(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Возврат в главное меню из любого раздела."""
    await _ack_and_hide(callback)
    await state.clear()
    await notes_handler.send_main_menu(callback)


# --- Синхронизация с Google Sheets ---


async def sync_table(
    event: types.Message | types.CallbackQuery, state: FSMContext
) -> None:
    """
    Сбрасывает кэш листа и сразу перечитывает его с сервера.

    Нужно после ручных правок в Google Sheets: без этого бот живёт на
    снимке из кэша (TTL 300 с) и новая строка видна только через несколько
    минут. Ошибка чтения не оставляет бота молчащим — уходит в
    ``send_error_message``, меню всё равно открывается.
    """
    await state.clear()
    repo = notes_handler.repo_of(event)
    if repo is None:
        return
    # user_id — из события Telegram, а не из внутренних полей репозитория:
    # ключ кэша должен совпадать с тем, что использует NoteRepository.
    user_id = user_id_of(event)
    await asyncio.to_thread(invalidate_cache, user_id)
    try:
        # Прогрев: чтение кладёт свежий снимок обратно в кэш.
        await asyncio.to_thread(repo.all_values)
    except Exception as e:
        await send_error_message(event, e)
        await notes_handler.send_main_menu(event)
        return
    await show_main_menu(event, buttons.SYNCED)


@router.message(Command(Cmd.REFRESH))
async def cmd_refresh(message: types.Message, state: FSMContext) -> None:
    """«/refresh»: сбросить кэш и перечитать таблицу."""
    await sync_table(message, state)


@router.callback_query(F.data == Cb.SYNC)
async def cb_sync(callback: types.CallbackQuery, state: FSMContext) -> None:
    """«🔄 Sync» в главном меню: то же, что /refresh."""
    await _ack_and_hide(callback)
    await sync_table(callback, state)


# --- Запуск ---


def register_routers() -> None:
    """
    Порядок важен: общие команды ➔ ✨ AI Note ➔ заметки ➔ колонки.

    ``ai_router`` подключается только здесь: его ``AiStates`` должны ловить
    сообщения раньше общего текстового поиска в ``notes_handler``.
    """
    dp.include_routers(
        router,
        ai_note_handler.ai_router,
        notes_handler.router,
        columns_handler.router,
    )


async def set_bot_commands() -> None:
    """Регистрирует слэш-команды в меню Telegram."""
    await get_bot().set_my_commands(
        [
            types.BotCommand(command=Cmd.START, description=buttons.START),
            types.BotCommand(command=Cmd.ADD_SOURCE, description=buttons.ADD_SOURCE),
            types.BotCommand(command=Cmd.ADD_NOTE, description=buttons.ADD_NOTE),
            types.BotCommand(command=Cmd.HELP, description=buttons.HELP),
            types.BotCommand(command=Cmd.TABLE, description=buttons.OPEN_TABLE),
            types.BotCommand(command=Cmd.REFRESH, description=buttons.SYNC),
        ]
    )


def _serve_health() -> None:
    """Поток health-check: ошибка старта пишется в лог, а не умирает молча.

    Поток daemon: непойманное исключение убило бы его без единой строки
    в логе, и Render посчитал бы сервис упавшим без причины.
    """
    try:
        run_health_server()
    except Exception:
        logger.exception("Fatal: health-check server failed to start")


def _start_health_server() -> None:
    """Поднимает health-check Render в фоновом daemon-потоке."""
    threading.Thread(target=_serve_health, daemon=True).start()


async def _setup_bot() -> None:
    """Проверка токена, сборка роутеров и регистрация слэш-команд в меню."""
    try:
        get_bot()  # ранняя проверка BOT_TOKEN: падаем до старта polling
    except Exception as e:
        logger.error("Fatal: BOT_TOKEN is invalid or missing — bot not started: %s", e)
        raise
    register_routers()
    await set_bot_commands()
    logger.info("Bot Administrator successfully started!")


async def _run_polling() -> None:
    """Блокирующий polling Telegram: ошибка логируется и пробрасывается."""
    try:
        await dp.start_polling(get_bot())
    except Exception as e:
        logger.error("Fatal: polling stopped with an error: %s", e)
        raise


async def main() -> None:
    """Запуск: health-check, роутеры, команды в меню и polling."""
    logging.basicConfig(level=logging.INFO)  # до первого обращения к боту
    _start_health_server()
    await _setup_bot()
    await _run_polling()


if __name__ == "__main__":
    asyncio.run(main())
