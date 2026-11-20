"""
Слой доступа к Google Sheets (заметки по источникам).

Здесь живут ВСЕ знания о структуре таблицы заметок:

* служебные колонки ``ID`` (1-я), ``SOURCE`` (2-я), ``TITLE`` (3-я) всегда
  стоят в начале, ``COMMENT`` — всегда последняя, ``SCREEN`` защищена от
  удаления (хранит ``file_id`` фото Telegram);
* новые пользовательские колонки вставляются перед ``COMMENT`` и могут
  отличаться по смыслу для разных источников.

Хендлеры работают только с ``NoteRepository`` и ``ColumnService`` и не знают
про gspread, номера строк и буквы колонок.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from config import MAX_COLUMNS, get_sheet

logger = logging.getLogger(__name__)

ID_HEADER = "ID"
SOURCE_HEADER = "SOURCE"
TITLE_HEADER = "TITLE"
SCREEN_HEADER = "SCREEN"
COMMENT_HEADER = "COMMENT"

#: Базовая шапка листа заметок (совпадает с config.NOTES_HEADERS).
BASE_HEADERS: list[str] = [
    ID_HEADER,
    SOURCE_HEADER,
    TITLE_HEADER,
    "DESCRIPTION",
    "URL",
    SCREEN_HEADER,
    COMMENT_HEADER,
]

#: Колонки, которые обязаны стоять в начале, в этом самом порядке.
LEADING_HEADERS: list[str] = [ID_HEADER, SOURCE_HEADER, TITLE_HEADER]
#: Позиции, которые колонка скриншотов занимает в базовой шапке.
#: Берём именно из шапки, а не из кода: если в BASE_HEADERS SCREEN переставят,
#: защита продолжит следовать за ней.
_SCREEN_BASE_POSITIONS: tuple[int, ...] = tuple(
    index + 1
    for index, header in enumerate(BASE_HEADERS)
    if header.upper() == SCREEN_HEADER
)  # 1-based позиции: SCREEN — 6-я колонка базовой шапки

#: Колонки, которые нельзя удалять. Обязательные ID/SOURCE/TITLE защищены
#: жёстко, скриншот — по имени и по штатной позиции (``screen_slot_index``).
PROTECTED_HEADERS: set[str] = {*LEADING_HEADERS, SCREEN_HEADER}
#: Колонки, которые нельзя переименовывать (у SCREEN служебное значение).
RENAME_PROTECTED_HEADERS: set[str] = set(LEADING_HEADERS)

_EMPTY_VALUES = {"", "—", "-"}
_PROMPT_ROW_NAME = "AI_PROMT"

# 1) весь лист читается ОДНИМ get() и дальше всё ищется в памяти;
# 2) результат живёт в памяти CACHE_TTL секунд — навигация по меню и карточкам
#    не тратит лимит; правки руками в браузере бот видит максимум через
#    CACHE_TTL (плюс первый запрос после паузы);
# 3) любая запись через бота сбрасывает кэш — своё видно сразу;
# 4) при 429 Quota exceeded бот отдаёт предыдущую копию данных, даже
#    просроченную, вместо падения с ошибкой.
CACHE_TTL = 300.0


class SheetCache:
    """
    TTL-кэш снимков листов, ключ (user_id, лист).

    user_id берётся из события Telegram, а не из callback_data — поэтому один
    пользователь физически не может прочитать кэш другого.
    Инвалидация точечная (после записи в таблицу) или глобальная (тесты).
    """

    def __init__(self, ttl: float = CACHE_TTL) -> None:
        self._ttl = ttl
        self._lock = threading.Lock()
        # (время записи, снимок); истёкшая копия не удаляется — она нужна
        # мягким фоллбэком при 429 Quota exceeded
        self._entries: dict[str, tuple[float, list[list[str]]]] = {}

    def drop_stale(self, sheet: Any, user_id: int | None = None) -> None:
        """Помечает снимок листа устаревшим: следующее чтение пойдёт в Sheets."""
        key = self._key(user_id, sheet)
        with self._lock:
            hit = self._entries.get(key)
            if hit is not None:
                # заменяем время на «бесконечно давно», снимок остаётся для 429
                self._entries[key] = (-self._ttl, hit[1])

    def get_values(
        self,
        sheet: Any,
        user_id: int | None = None,
        force: bool = False,
    ) -> list[list[str]]:
        """
        Весь лист одним запросом. Копию не делаем: вызывающий код только читает.

        ``force=True`` — игнорировать TTL и перечитать лист с сервера.
        При 429 (Quota exceeded) отдаём предыдущую копию данных, даже
        просроченную, вместо падения с ошибкой.
        """
        key = self._key(user_id, sheet)
        now = time.monotonic()
        with self._lock:
            hit = self._entries.get(key)
            if hit and not force and now - hit[0] < self._ttl:
                return hit[1]

        try:
            values = self._read_sheet(sheet)
        except Exception as e:
            with self._lock:
                hit = self._entries.get(key)
                stale = hit[1] if hit else None
            if stale is not None and "429" in str(e):
                logger.warning(
                    "Google Sheets quota exceeded — serving stale cache "
                    "for key %s (%.0f s old)",
                    key,
                    now - hit[0] if hit else 0,
                )
                return stale
            raise

        with self._lock:
            self._entries[key] = (now, values)
        return values

    @staticmethod
    def _read_sheet(sheet: Any) -> list[list[str]]:
        """Широкое чтение одним запросом; фоллбэк на get_all_values()."""
        try:
            return sheet.get(
                f"A1:{column_letter(MAX_COLUMNS)}",
                value_render_option="FORMATTED_VALUE",
            )
        except Exception as e:
            message = str(e).lower()
            grid_related = "exceeds grid limits" in message or "range" in message
            if not grid_related:
                raise
            return sheet.get_all_values()

    @staticmethod
    def _sheet_key(sheet: Any) -> str:
        """
        Стабильный идентификатор листа.

        ``id(sheet)`` нельзя: gspread после таймаута пересоздаёт объект
        ``Worksheet``, и только что созданная заметка исчезает из кэша.
        Берём внутренний ID листа (``id``), затем имя (``title``), и только
        в самом крайнем случае — ``id()`` объекта.
        """
        sheet_id = getattr(sheet, "id", None)
        if isinstance(sheet_id, int):
            return f"sheet:{sheet_id}"
        title = getattr(sheet, "title", None)
        if isinstance(title, str) and title:
            return f"title:{title}"
        return f"object:{id(sheet)}"

    @classmethod
    def _key(cls, user_id: int | None, sheet: Any) -> str:
        return f"values:{user_id}:{cls._sheet_key(sheet)}"

    def update_cell(
        self,
        sheet: Any,
        row_index: int,
        col_index: int,
        value: str,
        user_id: int | None = None,
    ) -> bool:
        """
        Правит одну ячейку в уже загруженном снимке — без запроса в Sheets.

        Возвращает True, если снимок есть в кэше и ячейка обновлена
        (``user_id=None`` — поиск по любому ключу этого листа); False —
        если кэш пуст или номера вне снимка (тогда кэш не трогаем).
        """
        if col_index < 1 or row_index < 1:
            return False
        with self._lock:
            entries = self._entries_with_user(sheet, user_id)
            for key in entries:
                values = self._entries[key][1]
                if row_index > len(values):
                    continue
                values[row_index - 1] = pad_row(values[row_index - 1], col_index)
                values[row_index - 1][col_index - 1] = value
                return True
            return False

    def replace_values(
        self, sheet: Any, values: list[list[str]], user_id: int | None = None
    ) -> bool:
        """
        Кладёт в кэш только что записанный снимок этого листа.

        Снимок появляется в кэше, даже если его там ещё не было (первая запись
        после старта или после истечения TTL) — именно на этом держится
        берёжный поиск ID на следующем шаге мастера. True — снимок сохранён.
        """
        with self._lock:
            if user_id is None:
                suffix = f":{self._sheet_key(sheet)}"
                keys = [key for key in self._entries if key.endswith(suffix)]
                if not keys:
                    return False
                for key in keys:
                    self._entries[key] = (time.monotonic(), values)
                return True
            self._entries[self._key(user_id, sheet)] = (time.monotonic(), values)
            return True

    def _entries_with_user(self, sheet: Any, user_id: int | None) -> list[str]:
        """Ключи кэша этого листа (с учётом user_id, если он известен)."""
        if user_id is not None:
            key = self._key(user_id, sheet)
            return [key] if key in self._entries else []
        suffix = f":{self._sheet_key(sheet)}"
        return [key for key in self._entries if key.endswith(suffix)]

    def invalidate(self, user_id: int | None = None) -> None:
        """
        Сбрасывает кэш после записи в таблицу.

        user_id=None — глобальный сброс (тесты, скрипты миграции).
        С user_id — точечно: только ключи этого пользователя.
        """
        with self._lock:
            if user_id is None:
                self._entries.clear()
                return
            prefix = f"values:{user_id}:"
            for key in [k for k in self._entries if k.startswith(prefix)]:
                self._entries.pop(key, None)


_cache = SheetCache()


def _cached_values(
    sheet: Any, user_id: int | None = None, force: bool = False
) -> list[list[str]]:
    """Модульный фасад над SheetCache."""
    return _cache.get_values(sheet, user_id, force)


def _drop_stale(sheet: Any, user_id: int | None = None) -> None:
    """Модульный фасад над SheetCache.drop_stale."""
    _cache.drop_stale(sheet, user_id)


def invalidate_cache(user_id: int | None = None) -> None:
    """Модульный фасад над SheetCache.invalidate."""
    _cache.invalidate(user_id)


def reset_cache_for_tests() -> None:
    """
    Полный сброс кэша снимков (только для тестов).

    Нужен, когда лист в тестах не имеет стабильного ключа (``id``/``title``)
    и разные ``FakeSheet`` попадают в один ключ ``object``.
    """
    _cache.invalidate()


def _remember_values(
    sheet: Any, values: list[list[str]], user_id: int | None = None
) -> bool:
    """Модульный фасад над SheetCache.replace_values (True — снимок сохранён)."""
    return _cache.replace_values(sheet, values, user_id)


def _patch_cached_cell(
    sheet: Any, row_index: int, col_index: int, value: str, user_id: int | None = None
) -> bool:
    """Модульный фасад над SheetCache.update_cell."""
    return _cache.update_cell(sheet, row_index, col_index, value, user_id)


# --- Чистые функции (без сети) ---


def is_empty(value: str | None) -> bool:
    """Пустая ли ячейка (пустая строка, прочерк, только пробелы)."""
    return (value or "").strip() in _EMPTY_VALUES


def column_letter(col_index: int) -> str:
    """Номер колонки (1-based) -> буква Sheets: 1 -> A, 27 -> AA."""
    letters = ""
    while col_index > 0:
        col_index, remainder = divmod(col_index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def generate_unique_id(existing: set[str]) -> str:
    """Новый короткий ID, которого нет в existing."""
    while True:
        candidate = uuid.uuid4().hex[:8]
        if candidate not in existing:
            return candidate


def cell(row: Sequence[str], index: int, default: str = "") -> str:
    """
    Значение ячейки по 0-based индексу без IndexError (строки в Sheets рваные).

    Отрицательный индекс — как и выход за длину, возвращает default:
    иначе row[-1] молча возвращал бы последний элемент списка.
    """
    if not 0 <= index < len(row):
        return default
    return row[index]


def normalize_headers(raw_headers: Iterable[str]) -> list[str]:
    """
    Заголовки первой строки листа: обрезаем пробелы и отбрасываем
    пустые колонки в хвосте (пустая колонка посреди шапки сохраняется —
    иначе номера колонок сдвинутся и данные уедут не туда).
    """
    headers = [header.strip() for header in raw_headers]
    while headers and not headers[-1]:
        headers.pop()
    return headers


def headers_of(values: list[list[str]]) -> list[str]:
    """Шапка из уже полученного снимка листа (без обращения к сети)."""
    return normalize_headers(values[0]) if values else []


def pad_row(row: Sequence[str], width: int) -> list[str]:
    """Добивает рваную строку пустыми ячейками до ширины шапки."""
    padded = list(row)
    if len(padded) < width:
        padded.extend([""] * (width - len(padded)))
    return padded


def _row_for(headers: list[str], source: str, title: str, note_id: str) -> list[str]:
    """Строка новой заметки во всю ширину шапки (ID, SOURCE, TITLE)."""
    row = [""] * len(headers)
    row[id_column_index(headers) - 1] = note_id
    row[source_column_index(headers) - 1] = source
    row[title_column_index(headers) - 1] = title
    return row


def _index_of(headers: Sequence[str], name: str) -> int | None:
    """1-based номер колонки с таким заголовком (без учёта регистра) или None."""
    wanted = name.strip().lower()
    for index, header in enumerate(headers, start=1):
        if header.strip().lower() == wanted:
            return index
    return None


def id_column_index(headers: list[str]) -> int:
    """Номер колонки ID (1-based). По договорённости это колонка A."""
    found = _index_of(headers, ID_HEADER)
    if found is not None:
        return found
    raise ValueError(
        f"Table header has no column '{ID_HEADER}'. The first column must be ID."
    )


def source_column_index(headers: list[str]) -> int:
    """Номер колонки SOURCE (1-based); по умолчанию B."""
    return _index_of(headers, SOURCE_HEADER) or 2


def title_column_index(headers: list[str]) -> int:
    """Номер колонки TITLE (1-based); по умолчанию C."""
    return _index_of(headers, TITLE_HEADER) or 3


def comment_column_index(headers: list[str]) -> int | None:
    """Номер колонки COMMENT или None, если её нет в шапке."""
    return _index_of(headers, COMMENT_HEADER)


def screen_column_index(headers: list[str]) -> int | None:
    """Номер колонки SCREEN или None, если её нет в шапке."""
    return _index_of(headers, SCREEN_HEADER)


def screen_slot_index(headers: Sequence[str]) -> int | None:
    """
    Штатная позиция колонки скриншотов (1-based) или None.

    Сначала ищем колонку по имени ``SCREEN``. Если её переименовали, берём
    её позицию перед ``COMMENT`` (если она есть в шапке), и только потом
    базовую позицию из ``_SCREEN_BASE_POSITIONS``.
    """
    by_name = _index_of(headers, SCREEN_HEADER)
    if by_name is not None:
        return by_name
    comment_col = comment_column_index(list(headers))
    if comment_col is not None and comment_col > len(LEADING_HEADERS) + 1:
        return comment_col - 1
    for position in _SCREEN_BASE_POSITIONS:
        if position <= len(headers):
            return position
    return None


def _is_data_row(row: Sequence[str], headers: Sequence[str]) -> bool:
    """Строка с ID и TITLE — заметка, а не шапка, пустышка или карточка правил."""
    title = cell(row, title_column_index(list(headers)) - 1).strip()
    if not title or title == _PROMPT_ROW_NAME:
        return False
    if title.strip().lower() == TITLE_HEADER.lower():
        return False
    return bool(cell(row, id_column_index(list(headers)) - 1).strip())


class _SheetBound:
    """
    Общая база сервисов, привязанных к листу пользователя.

    Лист подключается лениво: объект можно создать до первого запроса,
    а конструктор принимает готовый sheet — так тесты подставляют FakeSheet
    (dependency injection вместо monkeypatching).
    """

    def __init__(self, user_id: int | None = None, sheet: Any = None) -> None:
        self._user_id = user_id
        self._sheet = sheet

    @property
    def sheet(self) -> Any:
        if self._sheet is None:
            self._sheet = self._resolve_sheet()
        return self._sheet

    def _resolve_sheet(self) -> Any:
        raise NotImplementedError

    def _all_values(self, force: bool = False) -> list[list[str]]:
        """Весь лист одним запросом (кэшируется по (user_id, лист))."""
        return _cached_values(self.sheet, self._user_id, force)

    def all_values(self) -> list[list[str]]:
        """Публичный снимок листа — чтобы хендлер сделал ровно один запрос."""
        return self._all_values()

    @staticmethod
    def headers_from(values: list[list[str]]) -> list[str]:
        """Шапка из уже полученного снимка (без обращения к сети)."""
        return headers_of(values)


# --- Заметки ---


@dataclass(frozen=True)
class Note:
    """Строка таблицы заметок: номер строки в Sheets (1-based) и значения."""

    row_index: int
    values: list[str]

    @property
    def note_id(self) -> str:
        """Значение служебной колонки ID."""
        return cell(self.values, 0).strip()

    def value(self, col_index: int) -> str:
        """Значение колонки по номеру Sheets (1-based)."""
        return cell(self.values, col_index - 1)

    def field(self, headers: Sequence[str], name: str) -> str:
        """Значение колонки по её имени (без учёта регистра)."""
        index = _index_of(headers, name)
        return self.value(index) if index is not None else ""


class NoteRepository(_SheetBound):
    """
    Все операции над листом заметок. Хендлеры не трогают gspread напрямую.

    user_id берётся строго из события Telegram (from_user.id): для админа это
    основной лист ``MY_NOTES``, для остальных — личная вкладка ``Notes_{id}``.
    """

    def _resolve_sheet(self) -> Any:
        return get_sheet(self._user_id)

    # --- чтение ---

    def headers(self) -> list[str]:
        return headers_of(self._all_values())

    def list_sources(self) -> list[str]:
        """Уникальные источники (колонка SOURCE), по алфавиту без учёта регистра."""
        return [source for source, _ in self.list_sources_with_counts()]

    def list_sources_with_counts(self) -> list[tuple[str, int]]:
        """
        (источник, количество заметок) по алфавиту без учёта регистра.

        Строка-болванка (только SOURCE, без TITLE) даёт источник с нулём:
        он уже существует и ждёт первой заметки.
        """
        values = self._all_values()
        headers = headers_of(values)
        if not headers:
            return []
        source_col = source_column_index(headers) - 1
        title_col = title_column_index(headers) - 1
        seen: dict[str, tuple[str, int]] = {}
        for row in values[1:]:
            source = cell(row, source_col).strip()
            if not source:
                continue
            key = source.lower()
            name, count = seen.get(key, (source, 0))
            if cell(row, title_col).strip():
                count += 1
            seen[key] = (name, count)
        return [seen[key] for key in sorted(seen)]

    def list_notes(self) -> list[Note]:
        """Все заметки листа (с ID и TITLE), порядок — как в таблице."""
        return self._all_notes()[0]

    def _all_notes(self, force: bool = False) -> tuple[list[Note], list[str]]:
        """
        Заметки и шапка одним проходом, с дозаполнением пустых ID.

        Ручная строка в Google Таблице (SOURCE/TITLE есть, ID пуст) получает
        сгенерированный ID, который сразу записывается в ячейку — иначе
        такую заметку нельзя открыть по кнопке в Telegram.
        """
        values = self._all_values(force)
        headers = headers_of(values)
        if not headers:
            return [], []
        self._ensure_missing_ids(values, headers)
        width = len(headers)
        notes = [
            Note(row_index, pad_row(row, width))
            for row_index, row in enumerate(values[1:], start=2)
            if _is_data_row(row, headers)
        ]
        return notes, headers

    def _ensure_missing_ids(self, values: list[list[str]], headers: list[str]) -> None:
        """
        Дозаполняет пустые ID у строк с TITLE (правки руками).

        Строки-болванки (есть только SOURCE, TITLE пуст) остаются без ID:
        это не заметка, а ждущий источник.

        Работает по снимку ``values`` (строка 1 — шапка) и правит его же,
        чтобы вызывающий код сразу видел новые ID без повторного чтения.
        """
        id_col = id_column_index(headers)
        title_col = title_column_index(headers)
        existing = {
            cell(row, id_col - 1).strip()
            for row in values[1:]
            if cell(row, id_col - 1).strip()
        }
        col_letter = column_letter(id_col)
        updates: list[dict[str, Any]] = []
        for row_index, row in enumerate(values[1:], start=2):
            if cell(row, id_col - 1).strip():
                continue
            if not cell(row, title_col - 1).strip():
                continue
            new_id = generate_unique_id(existing)
            existing.add(new_id)
            while len(row) < len(headers):
                row.append("")
            row[id_col - 1] = new_id
            updates.append({"range": f"{col_letter}{row_index}", "values": [[new_id]]})
        if updates:
            try:
                self.sheet.batch_update(updates, value_input_option="RAW")
            except Exception as e:
                logger.warning("Could not persist generated IDs: %s", e)
            invalidate_cache(self._user_id)

    def list_by_source(self, source: str) -> list[Note]:
        """Заметки одного источника, отсортированные по TITLE без регистра."""
        wanted = (source or "").strip().lower()
        all_notes, headers = self._all_notes()
        if not headers:
            return []
        source_col = source_column_index(headers)
        title_col = title_column_index(headers)
        notes = [
            note
            for note in all_notes
            if note.value(source_col).strip().lower() == wanted
            and note.value(title_col).strip()
        ]
        notes.sort(key=lambda note: note.value(title_col).strip().lower())
        return notes

    def find_by_title(self, query: str) -> list[Note]:
        """
        Заметки по заголовку: точное совпадение TITLE приоритетнее частичного.

        Сравнение без учёта регистра и лишних пробелов. Если есть хотя бы
        одно точное совпадение — возвращаются только они, иначе — все
        заметки, где запрос входит в TITLE как подстрока.
        """
        wanted = (query or "").strip().lower()
        if not wanted:
            return []
        all_notes, headers = self._all_notes()
        if not headers:
            return []
        title_col = title_column_index(headers)
        exact: list[Note] = []
        partial: list[Note] = []
        for note in all_notes:
            title = note.value(title_col).strip().lower()
            if not title:
                continue
            if title == wanted:
                exact.append(note)
            elif wanted in title:
                partial.append(note)
        return exact or partial

    def get_ai_prompt(self) -> str:
        """
        Кастомные правила разбора для ✨ AI Note из служебной строки листа.

        Ищем строку, где ``TITLE`` (или ``SOURCE``) равен ``AI_PROMT`` и берём
        текст из ``DESCRIPTION``; если конкретно она пуста — первую непустую
        колонку после ``TITLE``. Пустая строка — своих правил у пользователя нет.
        """
        values = self._all_values()
        headers = headers_of(values)
        if not headers:
            return ""
        title_col = title_column_index(headers) - 1
        source_col = source_column_index(headers) - 1
        first_free = title_col + 1
        for row in values[1:]:
            names = {
                cell(row, title_col).strip().upper(),
                cell(row, source_col).strip().upper(),
            }
            if _PROMPT_ROW_NAME not in names:
                continue
            for index in range(first_free, len(headers)):
                text = cell(row, index).strip()
                if text:
                    return text
        return ""

    def locate(self, note_id: str) -> tuple[int, Note] | None:
        """
        (номер строки, заметка) или None, если записи с таким ID нет.

        Если в кэшированном снимке ID не нашёлся, кэш помечается устаревшим
        и лист перечитывается один раз: сразу после ``create`` Google Sheets
        может отдать снимок без новой строки (задержка репликации).
        """
        found = self._locate_in(self._all_notes()[0], note_id)
        if found is not None:
            return found
        _drop_stale(self.sheet, self._user_id)
        try:
            notes = self._all_notes(force=True)[0]
        except Exception as e:
            # Повторное чтение — страховка: живём на прошлом снимке
            logger.warning("Re-read after failed locate(%s): %s", note_id, e)
            return None
        return self._locate_in(notes, note_id)

    @staticmethod
    def _locate_in(notes: list[Note], note_id: str) -> tuple[int, Note] | None:
        """Поиск заметки по ID в готовом списке строк листа."""
        wanted = str(note_id).strip()
        for note in notes:
            if note.note_id == wanted:
                return note.row_index, note
        return None

    def get(self, note_id: str) -> Note | None:
        """Заметка по ID (лист дочитывается, если ID нет в кэше)."""
        found = self.locate(note_id)
        return found[1] if found else None

    def find_row(self, note_id: str) -> int | None:
        """Номер строки по ID или None — поиск в памяти, без sheet.find()."""
        found = self.locate(note_id)
        return found[0] if found else None

    # --- запись ---

    def new_id(self) -> str:
        """Уникальный ID с учётом уже существующих на листе (без коллизий)."""
        existing = {note.note_id for note in self.list_notes() if note.note_id}
        return generate_unique_id(existing)

    def create(self, source: str, title: str) -> str:
        """
        Создаёт заметку с новым ID и возвращает этот ID.

        Если для источника уже есть строка-болванка (только SOURCE, TITLE пуст),
        она заполняется на месте — дубликат источника не появляется. Иначе
        строка дописывается в конец во всю ширину шапки; в обоих случаях
        лист затем сортируется по SOURCE и TITLE.
        """
        # Шапка из снимка до записи: sort_notes затем опирается на кэш,
        # а не на повторное get_all_values().
        cached = self._all_values()
        headers = headers_of(cached) or list(BASE_HEADERS)
        if not headers_of(cached):  # пустой лист без шапки: восстанавливаем
            self.sheet.insert_row(headers, index=1, value_input_option="RAW")
            invalidate_cache(self._user_id)
            cached = [list(headers)]
        new_id = generate_unique_id(self._ids_in(headers, cached))
        source_name = (source or "").strip()
        title_name = (title or "").strip()

        # Снимок правим в памяти и сортируем, затем пишем одним update:
        # порядок в кэше и в таблице совпадает всегда, лишних append_row нет.
        values = self._padded_snapshot(headers, cached)
        blank_row = self._blank_source_row(headers, source_name, values)
        row = _row_for(headers, source_name, title_name, new_id)
        if blank_row is not None and blank_row - 1 < len(values):
            values[blank_row - 1] = row
        else:
            values.append(row)
        self._persist_sorted(headers, values)
        return new_id

    @staticmethod
    def _ids_in(headers: list[str], values: list[list[str]]) -> set[str]:
        """ID, которые уже заняты на листе (по сырому снимку)."""
        id_col = id_column_index(headers) - 1
        return {cell(row, id_col).strip() for row in values[1:]} - {""}

    @staticmethod
    def _padded_snapshot(
        headers: list[str], values: list[list[str]]
    ) -> list[list[str]]:
        """Копия снимка с шапкой и строками во всю ширину шапки."""
        width = len(headers)
        return [list(headers)] + [pad_row(row, width) for row in values[1:]]

    @staticmethod
    def _sorted_snapshot(
        headers: list[str], values: list[list[str]]
    ) -> list[list[str]]:
        """Снимок с шапкой и строками, отсортированными по SOURCE -> TITLE."""
        width = len(headers)
        source_col = source_column_index(headers) - 1
        title_col = title_column_index(headers) - 1
        data = [pad_row(row, width) for row in values[1:]]

        def key(row: list[str]) -> tuple[int, str, int, str]:
            source = cell(row, source_col).strip().lower()
            title = cell(row, title_col).strip().lower()
            return (not source, source, not title, title)

        data.sort(key=key)
        return [list(headers), *data]

    def _persist_sorted(
        self, headers: list[str], values: list[list[str]]
    ) -> list[list[str]]:
        """Сортирует снимок (SOURCE -> TITLE), пишет на лист и сохраняет в кэше."""
        sorted_values = self._sorted_snapshot(headers, values)
        self._write_snapshot(sorted_values)
        if not _remember_values(self.sheet, sorted_values, self._user_id):
            invalidate_cache(self._user_id)
        return sorted_values

    def _write_snapshot(self, values: list[list[str]]) -> None:
        """Пишет снимок в лист одним update (шапка — первой строкой)."""
        if len(values) < 2:
            return
        last_col = column_letter(len(values[0]))
        self.sheet.update(
            range_name=f"A2:{last_col}{len(values)}",
            values=values[1:],
            value_input_option="RAW",
        )

    def _blank_source_row(
        self, headers: list[str], source: str, values: list[list[str]]
    ) -> int | None:
        """Номер строки-болванки этого источника (SOURCE есть, TITLE пуст)."""
        wanted = source.strip().lower()
        if not wanted:
            return None
        source_col = source_column_index(headers) - 1
        title_col = title_column_index(headers) - 1
        for row_index, row in enumerate(values[1:], start=2):
            if cell(row, title_col).strip():
                continue
            if cell(row, source_col).strip().lower() == wanted:
                return row_index
        return None

    def add_source(self, source: str) -> bool:
        """
        Добавляет пустой источник (строку с одним SOURCE) и возвращает True.

        Если такой источник уже есть (с заметками или без), ничего не делает
        и возвращает False. ID и TITLE у болванки остаются пустыми: ID
        появится вместе с первой заметкой.
        """
        name = (source or "").strip()
        if not name:
            raise ValueError("Source name must not be empty.")
        values = self._all_values()
        headers = headers_of(values)
        if not headers:  # пустой лист без шапки: восстанавливаем базовую
            headers = list(BASE_HEADERS)
            self.sheet.insert_row(headers, index=1, value_input_option="RAW")
            values = [list(headers)]
            _remember_values(self.sheet, values, self._user_id)
        source_col = source_column_index(headers) - 1
        wanted = name.lower()
        if any(cell(row, source_col).strip().lower() == wanted for row in values[1:]):
            return False
        row = [""] * len(headers)
        row[source_col] = name
        values.append(row)
        self._persist_sorted(headers, values)
        return True

    def update_cell_at(self, row_index: int, col_index: int, value: str) -> None:
        """
        Запись ячейки по номерам строки и колонки (1-based).

        Кэш не сбрасывается, а правится на месте: следующий ``headers()``
        или ``get()`` уже видит новое значение без запроса в Sheets.
        """
        self.sheet.update_cell(row_index, col_index, value)
        if not _patch_cached_cell(
            self.sheet, row_index, col_index, value, self._user_id
        ):
            invalidate_cache(self._user_id)

    def delete(self, note_id: str) -> None:
        """Удаляет строку заметки (native delete_rows — сдвиг без «хвостов»)."""
        found = self.locate(note_id)
        if found is not None:
            self.sheet.delete_rows(found[0])
            invalidate_cache(self._user_id)

    # --- источники ---

    def _source_rows(
        self,
        source: str,
        values: list[list[str]] | None = None,
        headers: list[str] | None = None,
    ) -> list[int]:
        """Номера строк (1-based, сверху вниз) с этим SOURCE."""
        wanted = (source or "").strip().lower()
        if not wanted:
            return []
        if values is None:
            values = self._all_values()
        if headers is None:
            headers = headers_of(values)
        if not headers:
            return []
        source_col = source_column_index(headers) - 1
        return [
            row_index
            for row_index, row in enumerate(values[1:], start=2)
            if cell(row, source_col).strip().lower() == wanted
        ]

    def _count_source_notes(
        self, row_indexes: list[int], values: list[list[str]], headers: list[str]
    ) -> int:
        """Сколько из этих строк — реальные заметки (TITLE заполнен)."""
        title_col = title_column_index(headers)
        return sum(
            1
            for row_index in row_indexes
            if row_index - 1 < len(values)
            and cell(values[row_index - 1], title_col - 1).strip()
        )

    def rename_source(self, old_source: str, new_source: str) -> int:
        """
        Переименовывает источник во ВСЕХ его строках и возвращает их число.

        Строки-болванки переименовываются вместе с заметками (они тоже
        принадлежат источнику, иначе после переименования появился бы дубль).
        Сравнение с ``old_source`` — без учёта регистра и лишних пробелов;
        после записи лист пересортировывается (SOURCE -> TITLE).
        """
        name = (new_source or "").strip()
        if not name:
            raise ValueError("Source name must not be empty.")
        values = self._all_values()
        headers = headers_of(values)
        row_indexes = self._source_rows(old_source, values, headers)
        if not row_indexes:
            return 0
        source_col = source_column_index(headers)
        notes = self._count_source_notes(row_indexes, values, headers)
        for row_index in row_indexes:
            self.sheet.update_cell(row_index, source_col, name)
            if row_index - 1 < len(values):
                values[row_index - 1] = pad_row(values[row_index - 1], source_col)
                values[row_index - 1][source_col - 1] = name
        self._persist_sorted(headers, values)
        return notes

    def delete_source(self, source: str) -> int:
        """
        Удаляет источник целиком (включая болванку) и возвращает число заметок.

        Строки удаляются снизу вверх: при удалении сверху сдвигаются индексы
        ещё не удалённых строк, и чужие заметки уехали бы вместе с ними.
        """
        values = self._all_values()
        headers = headers_of(values)
        row_indexes = self._source_rows(source, values, headers)
        if not row_indexes:
            return 0
        notes = self._count_source_notes(row_indexes, values, headers)
        for row_index in reversed(row_indexes):
            self.sheet.delete_rows(row_index)
        invalidate_cache(self._user_id)
        return notes

    def sort_notes(self, remember: bool = True) -> None:
        """
        Сортирует данные листа: сначала по SOURCE, затем по TITLE.

        Обе сортировки — без учёта регистра; шапка (1-я строка) остаётся
        сверху. Пустые значения уходят в конец. Строки добиваются пустыми
        до ширины шапки: иначе рваная строка не затёрла бы правые ячейки
        соседа и данные «прилипли» бы к чужой записи.

        ``remember=True`` — записанные строки сразу кладутся в кэш: вызову
        ``create`` не нужен повторный ``get_all_values()``.
        """
        values = self._all_values()
        headers = headers_of(values)
        if not headers or len(values) < 2:
            return
        self._persist_sorted(headers, values)


# --- Сервис колонок (шапка листа заметок) ---


class ColumnService(_SheetBound):
    """
    Добавление, удаление, переименование и перестановка колонок.

    Инварианты шапки, которые нельзя нарушить:

    * ``ID``, ``SOURCE``, ``TITLE`` — первые три колонки в этом порядке;
    * ``COMMENT`` — всегда последняя;
    * ``SCREEN`` нельзя удалить, но переименовать можно;
    * новые колонки вставляются перед ``COMMENT``.

    user_id берётся из события Telegram, поэтому через колонки нельзя попасть
    в чужую шапку: сервис всегда получает лист через get_sheet(user_id).
    """

    def _resolve_sheet(self) -> Any:
        return get_sheet(self._user_id)

    def headers(self) -> list[str]:
        return headers_of(self._all_values())

    def _header(self, col_index: int, headers: list[str] | None = None) -> str:
        if headers is None:
            headers = self.headers()
        return cell(headers, col_index - 1).strip()

    def protected_columns(self, headers: list[str] | None = None) -> set[int]:
        """
        Номера колонок, которые нельзя удалить.

        * ``ID``, ``SOURCE``, ``TITLE`` — обязательные первые три;
        * ``COMMENT`` — последняя колонка (если она есть в шапке);
        * скриншот — по имени ``SCREEN`` или по штатной позиции, поэтому
          колонка остаётся защищённой и после переименования.
        """
        if headers is None:
            headers = self.headers()
        protected = {
            index
            for index, header in enumerate(headers, start=1)
            if header.strip().upper() in PROTECTED_HEADERS
        }
        comment_col = comment_column_index(headers)
        if comment_col is not None:
            protected.add(comment_col)
        screen_col = screen_slot_index(headers)
        if screen_col is not None:
            protected.add(screen_col)
        return protected

    def comment_column(self) -> int | None:
        """Номер колонки COMMENT или None, если её нет в шапке."""
        return comment_column_index(self.headers())

    def add(self, name: str) -> None:
        """
        Добавляет новую колонку перед COMMENT (или в конец шапки).

        Перед записью в конец сетка расширяется через add_cols, чтобы
        не падать с «exceeds grid limits» на заполненном листе.
        """
        new_name = (name or "").strip()
        if not new_name:
            raise ValueError("Column name must not be empty.")
        headers = self.headers()
        comment_col = comment_column_index(headers)
        if comment_col is not None:
            # insert_cols сдвигает COMMENT и все её данные вправо на 1 колонку
            self.sheet.insert_cols(
                [[new_name]], col=comment_col, value_input_option="RAW"
            )
        else:
            target_col = len(headers) + 1
            col_count = getattr(self.sheet, "cols", None) or getattr(
                self.sheet, "col_count", target_col
            )
            if isinstance(col_count, int) and target_col > col_count:
                self.sheet.add_cols(target_col - col_count)
            self.sheet.update_cell(1, target_col, new_name)
        invalidate_cache(self._user_id)

    def delete(self, col_index: int) -> None:
        """
        Удаляет пользовательскую колонку (1-based) через delete_columns —
        Sheets сам сдвигает данные влево, мусор справа не остаётся.
        """
        headers = self.headers()
        if col_index in self.protected_columns(headers):
            header = cell(headers, col_index - 1).strip() or "service"
            raise ValueError(
                f"Column {col_index} ({header}) is a service column "
                "and cannot be deleted."
            )
        self.sheet.delete_columns(col_index)
        invalidate_cache(self._user_id)

    def rename(self, col_index: int, new_name: str) -> None:
        """
        Переименовывает колонку (ID, SOURCE и TITLE защищены).

        Колонка скриншотов переименовывается свободно: смысл ячейки не меняется,
        а от удаления она всё равно защищена (``protected_columns``).
        """
        name = (new_name or "").strip()
        if not name:
            raise ValueError("Column name must not be empty.")
        headers = self.headers()
        header = self._header(col_index, headers)
        if header.upper() in RENAME_PROTECTED_HEADERS or not header:
            raise ValueError(
                f"Column {col_index} ({header}) is a service column "
                "and cannot be renamed."
            )
        self.sheet.update_cell(1, col_index, name)
        invalidate_cache(self._user_id)

    def reorder(self, order: list[int]) -> list[str]:
        """
        Переставляет все колонки листа по списку номеров 1-based.

        ``order`` — точная перестановка ``1..width``: без пропусков и дублей.
        Закреплённая структура проверяется здесь же: первые три позиции —
        ``ID``, ``SOURCE``, ``TITLE``, последняя — ``COMMENT``. Шапка и все
        строки перезаписываются, кэш сбрасывается. Возвращает новую шапку.
        """
        data = self._all_values()
        headers = headers_of(data)
        if not headers:
            return []
        width = len(headers)
        if sorted(order) != list(range(1, width + 1)):
            raise ValueError(f"Order must be a permutation of 1..{width}, got {order}")
        expected_leading = list(range(1, len(LEADING_HEADERS) + 1))
        if order[: len(expected_leading)] != expected_leading:
            raise ValueError(
                "Columns ID, SOURCE and TITLE are service columns and must stay "
                "first (in that order)."
            )
        if comment_column_index(headers) is not None and order[-1] != width:
            raise ValueError(
                f"Column {width} ({COMMENT_HEADER}) is a service column "
                "and must stay last."
            )
        rows = [pad_row(row, width) for row in data]
        reordered = [[cell(row, index - 1) for index in order] for row in rows]
        last_col = column_letter(width)
        self.sheet.update(
            range_name=f"A1:{last_col}{len(reordered)}",
            values=reordered,
            value_input_option="RAW",
        )
        invalidate_cache(self._user_id)
        return reordered[0]

    def reorder_middle(self, order: list[int]) -> list[str]:
        """
        Меняет порядок только изменяемых колонок (между TITLE и COMMENT).

        ``order`` — точная перестановка номеров 1..N средних колонок,
        1-based. Закреплённые ``ID``/``SOURCE``/``TITLE`` и ``COMMENT``
        подставляются программно, поэтому их нельзя ни сдвинуть, ни потерять.
        Вся запись листа живёт в :meth:`reorder`.
        """
        data = self._all_values()
        headers = headers_of(data)
        if not headers:
            return []
        middle_indexes = self.middle_indexes(headers)
        if sorted(order) != list(range(1, len(middle_indexes) + 1)):
            raise ValueError(
                f"Order must be a permutation of 1..{len(middle_indexes)}, got {order}"
            )
        width = len(headers)
        leading_len = min(len(LEADING_HEADERS), width)
        full_order = list(range(1, leading_len + 1))
        full_order.extend(middle_indexes[position - 1] for position in order)
        if comment_column_index(headers) is not None:
            full_order.append(width)
        return self.reorder(full_order)

    def middle_indexes(self, headers: list[str]) -> list[int]:
        """Номера (1-based) изменяемых колонок — между TITLE и COMMENT."""
        first_free = len(LEADING_HEADERS) + 1
        last_free = len(headers)
        if comment_column_index(headers) is not None:
            last_free = len(headers) - 1
        return [index for index in range(first_free, last_free + 1)]


__all__ = [
    "BASE_HEADERS",
    "CACHE_TTL",
    "COMMENT_HEADER",
    "ID_HEADER",
    "LEADING_HEADERS",
    "PROTECTED_HEADERS",
    "RENAME_PROTECTED_HEADERS",
    "SCREEN_HEADER",
    "SOURCE_HEADER",
    "TITLE_HEADER",
    "ColumnService",
    "NoteRepository",
    "SheetCache",
    "cell",
    "column_letter",
    "comment_column_index",
    "generate_unique_id",
    "headers_of",
    "id_column_index",
    "invalidate_cache",
    "is_empty",
    "normalize_headers",
    "pad_row",
    "screen_column_index",
    "screen_slot_index",
    "source_column_index",
    "title_column_index",
]
