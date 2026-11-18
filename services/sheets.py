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

#: Старое имя колонки заголовка: принимается при чтении чужих листов и
#: в хелперах, чтобы шаблоны, созданные до переименования, не ломались.
LEGACY_NAME_HEADER = "NAME"

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
        # (время записи, снимок); истёкшая копия не удаляется — она нужна
        # мягким фоллбэком при 429 Quota exceeded
        self._entries: dict[str, tuple[float, list[list[str]]]] = {}

    def get_values(self, sheet: Any, user_id: int | None = None) -> list[list[str]]:
        """
        Весь лист одним запросом. Копию не делаем: вызывающий код только читает.

        При 429 (Quota exceeded) отдаём предыдущую копию данных, даже
        просроченную, вместо падения с ошибкой.
        """
        key = self._key(user_id, sheet)
        hit = self._entries.get(key)
        now = time.monotonic()
        if hit and now - hit[0] < self._ttl:
            return hit[1]

        try:
            values = self._read_sheet(sheet)
        except Exception as e:
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
    def _key(user_id: int | None, sheet: Any) -> str:
        return f"values:{user_id}:{id(sheet)}"

    def invalidate(self, user_id: int | None = None) -> None:
        """
        Сбрасывает кэш после записи в таблицу.

        user_id=None — глобальный сброс (тесты, скрипты миграции).
        С user_id — точечно: только ключи этого пользователя.
        """
        if user_id is None:
            self._entries.clear()
            return
        prefix = f"values:{user_id}:"
        for key in [k for k in self._entries if k.startswith(prefix)]:
            self._entries.pop(key, None)


_cache = SheetCache()


def _cached_values(sheet: Any, user_id: int | None = None) -> list[list[str]]:
    """Модульный фасад над SheetCache."""
    return _cache.get_values(sheet, user_id)


def invalidate_cache(user_id: int | None = None) -> None:
    """Модульный фасад над SheetCache.invalidate."""
    _cache.invalidate(user_id)


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
    """
    Номер колонки TITLE (1-based); по умолчанию C.

    Листы, созданные до переименования, могут держать здесь ``NAME`` —
    читаем и её, чтобы данные не «потерялись» из-за старых шапок.
    """
    found = _index_of(headers, TITLE_HEADER)
    if found is None:
        found = _index_of(headers, LEGACY_NAME_HEADER)
    return found or 3


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
    её штатное место в шапке: позиция из ``BASE_HEADERS`` или — для шапки
    с пользовательскими колонками — колонка перед ``COMMENT``.
    """
    by_name = _index_of(headers, SCREEN_HEADER)
    if by_name is not None:
        return by_name
    for position in _SCREEN_BASE_POSITIONS:
        if position <= len(headers):
            return position
    comment_col = comment_column_index(list(headers))
    if comment_col is not None and comment_col > len(LEADING_HEADERS) + 1:
        return comment_col - 1
    return None


def _is_data_row(row: Sequence[str], headers: Sequence[str]) -> bool:
    """Строка с ID и TITLE — заметка, а не шапка, пустышка или карточка правил."""
    title = cell(row, title_column_index(list(headers)) - 1).strip()
    if not title or title == _PROMPT_ROW_NAME:
        return False
    if title.strip().lower() in {TITLE_HEADER.lower(), LEGACY_NAME_HEADER.lower()}:
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

    def _all_values(self) -> list[list[str]]:
        """Весь лист одним запросом (кэшируется по (user_id, лист))."""
        return _cached_values(self.sheet, self._user_id)

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
        """(источник, количество заметок) по алфавиту без учёта регистра."""
        values = self._all_values()
        headers = headers_of(values)
        if not headers:
            return []
        source_col = source_column_index(headers) - 1
        seen: dict[str, tuple[str, int]] = {}
        for row in values[1:]:
            source = cell(row, source_col).strip()
            if not source:
                continue
            key = source.lower()
            name, count = seen.get(key, (source, 0))
            seen[key] = (name, count + 1)
        return [seen[key] for key in sorted(seen)]

    def list_notes(self) -> list[Note]:
        """Все заметки листа (с ID и TITLE), порядок — как в таблице."""
        return self._all_notes()[0]

    def _all_notes(self) -> tuple[list[Note], list[str]]:
        """
        Заметки и шапка одним проходом, с дозаполнением пустых ID.

        Ручная строка в Google Таблице (SOURCE/TITLE есть, ID пуст) получает
        сгенерированный ID, который сразу записывается в ячейку — иначе
        такую заметку нельзя открыть по кнопке в Telegram.
        """
        values = self._all_values()
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
        Дозаполняет пустые ID у строк с SOURCE или TITLE (правки руками).

        Работает по снимку ``values`` (строка 1 — шапка) и правит его же,
        чтобы вызывающий код сразу видел новые ID без повторного чтения.
        """
        id_col = id_column_index(headers)
        source_col = source_column_index(headers)
        title_col = title_column_index(headers)
        existing = {
            cell(row, id_col - 1).strip()
            for row in values[1:]
            if cell(row, id_col - 1).strip()
        }
        changed = False
        for row_index, row in enumerate(values[1:], start=2):
            if cell(row, id_col - 1).strip():
                continue
            if not (
                cell(row, source_col - 1).strip() or cell(row, title_col - 1).strip()
            ):
                continue
            new_id = generate_unique_id(existing)
            existing.add(new_id)
            while len(row) < len(headers):
                row.append("")
            row[id_col - 1] = new_id
            self.sheet.update_cell(row_index, id_col, new_id)
            changed = True
        if changed:
            invalidate_cache(self._user_id)

    def list_by_source(self, source: str) -> list[Note]:
        """Заметки одного источника, отсортированные по TITLE без регистра."""
        wanted = (source or "").strip().lower()
        all_notes, headers = self._all_notes()
        if not headers:
            return []
        source_col = source_column_index(headers)
        notes = [
            note
            for note in all_notes
            if note.value(source_col).strip().lower() == wanted
        ]
        title_col = title_column_index(headers)
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

    def locate(self, note_id: str) -> tuple[int, Note] | None:
        """(номер строки, заметка) или None, если записи с таким ID нет."""
        wanted = str(note_id).strip()
        for note in self._all_notes()[0]:
            if note.note_id == wanted:
                return note.row_index, note
        return None

    def get(self, note_id: str) -> Note | None:
        """Заметка по ID. Ищется в уже загруженном листе — без лишних запросов."""
        found = self.locate(note_id)
        return found[1] if found else None

    def find_row(self, note_id: str) -> int | None:
        """Номер строки по ID или None — поиск в памяти, без sheet.find()."""
        found = self.locate(note_id)
        return found[0] if found else None

    def display_title(self, note_id: str) -> str:
        """TITLE заметки; если пусто — «ID xxx»."""
        note = self.get(note_id)
        if note is None:
            return f"ID {note_id}"
        headers = self.headers()
        title = note.value(title_column_index(headers)).strip()
        return title or f"ID {note.note_id}"

    # --- запись ---

    def new_id(self) -> str:
        """Уникальный ID с учётом уже существующих на листе (без коллизий)."""
        existing = {note.note_id for note in self.list_notes() if note.note_id}
        return generate_unique_id(existing)

    def create(self, source: str, title: str) -> str:
        """
        Создаёт заметку с новым ID и возвращает этот ID.

        Строка пишется во всю ширину шапки (пустые ячейки), чтобы не сдвинуть
        колонки; после записи таблица сортируется по SOURCE и TITLE.
        """
        headers = self.headers()
        if not headers:  # пустой лист без шапки: восстанавливаем базовую
            headers = list(BASE_HEADERS)
            self.sheet.insert_row(headers, index=1, value_input_option="RAW")
            invalidate_cache(self._user_id)
        note_id = self.new_id()

        row = [""] * len(headers)
        row[id_column_index(headers) - 1] = note_id
        row[source_column_index(headers) - 1] = (source or "").strip()
        row[title_column_index(headers) - 1] = (title or "").strip()

        self.sheet.append_row(row, value_input_option="RAW")
        invalidate_cache(self._user_id)
        self.sort_notes()
        return note_id

    def update_cell(self, note_id: str, header: str, value: str) -> None:
        """Записывает значение в колонку с именем ``header`` у заметки ``note_id``."""
        found = self.locate(note_id)
        if found is None:
            return
        row_index, _ = found
        col_index = _index_of(self.headers(), header)
        if col_index is None:
            raise ValueError(f"Table header has no column '{header}'.")
        self.sheet.update_cell(row_index, col_index, value)
        invalidate_cache(self._user_id)

    def update_cell_at(self, row_index: int, col_index: int, value: str) -> None:
        """Запись ячейки по номерам строки и колонки (1-based)."""
        self.sheet.update_cell(row_index, col_index, value)
        invalidate_cache(self._user_id)

    def delete(self, note_id: str) -> None:
        """Удаляет строку заметки (native delete_rows — сдвиг без «хвостов»)."""
        found = self.locate(note_id)
        if found is not None:
            self.sheet.delete_rows(found[0])
            invalidate_cache(self._user_id)

    # --- источники ---

    def _source_rows(self, source: str) -> list[int]:
        """Номера строк (1-based, сверху вниз) с этим SOURCE."""
        wanted = (source or "").strip().lower()
        if not wanted:
            return []
        headers = self.headers()
        if not headers:
            return []
        source_col = source_column_index(headers) - 1
        return [
            row_index
            for row_index, row in enumerate(self._all_values()[1:], start=2)
            if cell(row, source_col).strip().lower() == wanted
        ]

    def rename_source(self, old_source: str, new_source: str) -> int:
        """
        Переименовывает источник во ВСЕХ его заметках и возвращает их число.

        Сравнение с ``old_source`` — без учёта регистра и лишних пробелов.
        После записи лист пересортировывается (SOURCE -> TITLE) и кэш
        сбрасывается, поэтому новая группировка видна сразу.
        """
        name = (new_source or "").strip()
        if not name:
            raise ValueError("Source name must not be empty.")
        row_indexes = self._source_rows(old_source)
        if not row_indexes:
            return 0
        headers = self.headers()
        source_col = source_column_index(headers)
        for row_index in row_indexes:
            self.sheet.update_cell(row_index, source_col, name)
        invalidate_cache(self._user_id)
        self.sort_notes()
        return len(row_indexes)

    def delete_source(self, source: str) -> int:
        """
        Удаляет все заметки источника и возвращает их число.

        Строки удаляются снизу вверх: при удалении сверху сдвигаются индексы
        ещё не удалённых строк, и чужие заметки уехали бы вместе с ними.
        """
        row_indexes = self._source_rows(source)
        if not row_indexes:
            return 0
        for row_index in reversed(row_indexes):
            self.sheet.delete_rows(row_index)
        invalidate_cache(self._user_id)
        return len(row_indexes)

    def sort_notes(self) -> None:
        """
        Сортирует данные листа: сначала по SOURCE, затем по TITLE.

        Обе сортировки — без учёта регистра; шапка (1-я строка) остаётся
        сверху. Пустые значения уходят в конец. Строки добиваются пустыми
        до ширины шапки: иначе рваная строка не затёрла бы правые ячейки
        соседа и данные «прилипли» бы к чужой записи.
        """
        values = self._all_values()
        headers = headers_of(values)
        if not headers or len(values) < 2:
            return
        width = len(headers)
        source_col = source_column_index(headers) - 1
        title_col = title_column_index(headers) - 1
        data = [pad_row(row, width) for row in values[1:]]

        def key(row: list[str]) -> tuple[int, str, int, str]:
            source = cell(row, source_col).strip().lower()
            title = cell(row, title_col).strip().lower()
            return (not source, source, not title, title)

        data.sort(key=key)
        last_col = column_letter(width)
        self.sheet.update(
            range_name=f"A2:{last_col}{len(data) + 1}",
            values=data,
            value_input_option="RAW",
        )
        invalidate_cache(self._user_id)


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

    def _header(self, col_index: int) -> str:
        headers = self.headers()
        return cell(headers, col_index - 1).strip()

    def protected_columns(self) -> set[int]:
        """
        Номера колонок, которые нельзя удалить.

        * ``ID``, ``SOURCE``, ``TITLE`` — обязательные первые три;
        * ``COMMENT`` — последняя колонка (если она есть в шапке);
        * скриншот — по имени ``SCREEN`` или по штатной позиции, поэтому
          колонка остаётся защищённой и после переименования.
        """
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

    def editable_columns(self) -> list[tuple[int, str]]:
        """(номер, имя) колонок для переименования (кроме ID/SOURCE/TITLE)."""
        return [
            (index, header)
            for index, header in enumerate(self.headers(), start=1)
            if header.strip().upper() not in RENAME_PROTECTED_HEADERS
        ]

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
        comment_col = self.comment_column()
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
        if col_index in self.protected_columns():
            header = self._header(col_index) or "service"
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
        header = self._header(col_index)
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
    "LEGACY_NAME_HEADER",
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
