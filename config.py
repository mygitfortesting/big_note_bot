"""
Конфигурация Big Note Bot: окружение, доступ к Google Sheets, Dispatcher.

Здесь живут ВСЕ знания о том, какой лист соответствует какому пользователю:
админ пишет в основную вкладку ``MY_NOTES``, остальные — в личную
``Notes_{user_id}``, а служебный лист ``Users`` хранит реестр пользователей.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

# --- Названия листов ---

#: Лист заметок администратора.
MY_NOTES_SHEET = "MY_NOTES"
#: Служебный лист с реестром пользователей.
USERS_SHEET = "Users"
#: Шаблон имени личной вкладки: ``Notes_{user_id}``.
NOTES_SHEET_TEMPLATE = "Notes_{user_id}"

#: Шапка нового листа заметок. Порядок служебных колонок фиксирован:
#: ID -> SOURCE -> NAME ... -> SCREEN ... -> COMMENT.
NOTES_HEADERS = ["ID", "SOURCE", "NAME", "DESCRIPTION", "URL", "SCREEN", "COMMENT"]

#: Шапка служебного листа ``Users``.
USERS_HEADERS = ["USER_ID", "USERNAME", "FULL_NAME", "NOTES_SHEET", "CREATED_AT"]

MAX_COLUMNS = 60

# --- Окружение ---

BOT_TOKEN_ENV = "BOT_TOKEN"


ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# --- aiogram ---

_bot: Bot | None = None

dp = Dispatcher(storage=MemoryStorage())


def get_bot() -> Bot:
    """Создаёт экземпляр Bot один раз при первом вызове и кэширует его в _bot."""
    global _bot
    bot = _bot
    if bot is None:
        token = (os.getenv(BOT_TOKEN_ENV) or "").strip()
        if not token:
            raise RuntimeError(f"Environment variable {BOT_TOKEN_ENV} is not set.")
        bot = Bot(
            token=token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        _bot = bot
    return bot


def is_admin(user_id: int | None) -> bool:
    """Админ ли пользователь (работает с основной вкладкой MY_NOTES)."""
    return user_id == ADMIN_ID


def notes_sheet_name(user_id: int | None) -> str:
    """Имя листа заметок пользователя: админу — MY_NOTES, остальным — Notes_{id}."""
    if is_admin(user_id):
        return MY_NOTES_SHEET
    return NOTES_SHEET_TEMPLATE.format(user_id=user_id)


# --- Google Sheets ---


class _Worksheet(Protocol):
    """Минимальный протокол листа gspread, который использует проект."""

    title: str

    def get(
        self, range_name: str, value_render_option: str = ...
    ) -> list[list[str]]: ...

    def get_all_values(self) -> list[list[str]]: ...

    def update(
        self, range_name: str, values: list[list[str]], value_input_option: str = ...
    ) -> Any: ...

    def update_cell(self, row: int, col: int, value: str) -> Any: ...

    def append_row(self, values: list[str], value_input_option: str = ...) -> Any: ...

    def batch_update(
        self, data: list[dict[str, Any]], value_input_option: str = ...
    ) -> Any: ...

    def insert_row(
        self, values: list[str], index: int = ..., value_input_option: str = ...
    ) -> Any: ...

    def insert_cols(
        self, values: list[list[str]], col: int, value_input_option: str = ...
    ) -> Any: ...

    def add_cols(self, cols: int) -> Any: ...

    def delete_rows(self, start_index: int, end_index: int | None = ...) -> Any: ...

    def delete_columns(self, start_index: int, end_index: int | None = ...) -> Any: ...

    def clear(self) -> Any: ...


_spreadsheet: Any | None = None


def _credentials_file() -> str:
    """Путь к JSON сервисного аккаунта Google."""
    return os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")


def _spreadsheet_id() -> str:
    """ID Google-таблицы с заметками."""
    sheet_id = (os.getenv("SPREADSHEET_ID") or "").strip()
    if not sheet_id:
        raise RuntimeError("Environment variable SPREADSHEET_ID is not set.")
    return sheet_id


def get_spreadsheet() -> Any:
    """Ленивый gspread.Spreadsheet (авторизация один раз на процесс)."""
    global _spreadsheet
    if _spreadsheet is None:
        import gspread  # импорт внутри: модуль не нужен для чистых тестов

        client = gspread.service_account(filename=_credentials_file())
        _spreadsheet = client.open_by_key(_spreadsheet_id())
    return _spreadsheet


def get_table_url() -> str:
    """Ссылка на Google-таблицу для кнопки «Open Table»."""
    url = (os.getenv("SPREADSHEET_URL") or "").strip()
    if url:
        return url
    sheet_id = (os.getenv("SPREADSHEET_ID") or "").strip()
    return f"https://google.com{sheet_id}" if sheet_id else ""


def _ensure_sheet(title: str, headers: list[str] | None = None) -> _Worksheet:
    """Возвращает лист по имени, создавая его с шапкой ``headers`` при отсутствии."""
    target_headers = headers if headers is not None else NOTES_HEADERS
    spreadsheet = get_spreadsheet()
    try:
        worksheet = spreadsheet.worksheet(title)
    except Exception:
        worksheet = spreadsheet.add_worksheet(
            title=title,
            rows=100,
            cols=max(len(target_headers) + 5, 10),
        )
        worksheet.append_row(list(target_headers), value_input_option="RAW")
        logger.info("Created sheet %s with header", title)
    return worksheet


def get_sheet(user_id: int | None = None) -> _Worksheet:
    """
    Лист заметок пользователя.

    Админ получает ``MY_NOTES``, остальные — ``Notes_{user_id}``
    (вкладка создаётся при первом обращении, если её ещё нет).
    """
    return _ensure_sheet(notes_sheet_name(user_id))


def get_notes_sheet() -> _Worksheet:
    """Лист заметок администратора ``MY_NOTES``."""
    return _ensure_sheet(MY_NOTES_SHEET)


def get_users_sheet() -> _Worksheet:
    """Служебный лист ``Users`` с реестром пользователей."""
    return _ensure_sheet(USERS_SHEET, headers=USERS_HEADERS)


def setup_user_sheets(from_user: Any) -> str:
    """
    Готовит вкладки пользователя при первом /start.

    Для не-админа создаёт личную вкладку ``Notes_{user_id}`` и пишет запись
    в служебный лист ``Users`` (повторный /start дубль не создаёт).
    Админ работает с основной вкладкой ``MY_NOTES`` — но и он попадает
    в реестр, чтобы бот знал его лист.

    Возвращает имя листа заметок пользователя.
    """
    user_id = int(from_user.id)
    title = notes_sheet_name(user_id)
    get_sheet(user_id)  # создаёт лист с шапкой, если его ещё нет
    _register_user(from_user, title)
    return title


def _register_user(from_user: Any, title: str) -> None:
    """Добавляет пользователя в лист Users, если его там ещё нет."""
    import datetime as dt

    sheet = get_users_sheet()
    try:
        rows = sheet.get_all_values()
    except Exception:  # пустой лист — это нормально
        rows = []
    user_id = str(from_user.id)
    for row in rows[1:]:
        if row and row[0].strip() == user_id:
            return
    sheet.append_row(
        [
            user_id,
            str(getattr(from_user, "username", "") or ""),
            str(getattr(from_user, "full_name", "") or ""),
            title,
            dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        ],
        value_input_option="RAW",
    )


__all__ = [
    "MAX_COLUMNS",
    "MY_NOTES_SHEET",
    "NOTES_HEADERS",
    "NOTES_SHEET_TEMPLATE",
    "USERS_HEADERS",
    "USERS_SHEET",
    "dp",
    "get_bot",
    "get_notes_sheet",
    "get_sheet",
    "get_spreadsheet",
    "get_table_url",
    "get_users_sheet",
    "is_admin",
    "notes_sheet_name",
    "setup_user_sheets",
]
