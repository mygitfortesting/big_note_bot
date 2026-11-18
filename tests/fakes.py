"""
In-memory двойники Google Sheets и Telegram — без сети.

``FakeSheet`` повторяет ровно тот набор методов gspread, который использует
``services.sheets``: get / update / update_cell / append_row / insert_row /
insert_cols / delete_rows / delete_columns / batch_update / clear.
``FakeState`` заменяет ``FSMContext`` в тестах хендлеров.
"""

from __future__ import annotations

import re
from typing import Any

from config import NOTES_HEADERS

_CELL_RE = re.compile(r"^([A-Z]+)(\d+)$")


def col_to_number(letters: str) -> int:
    """'A' -> 1, 'AA' -> 27."""
    number = 0
    for char in letters:
        number = number * 26 + (ord(char) - 64)
    return number


def number_to_col(number: int) -> str:
    """1 -> 'A', 27 -> 'AA'."""
    letters = ""
    while number > 0:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


class FakeSheet:
    """Лист в памяти с теми же методами, что у gspread.Worksheet."""

    def __init__(
        self,
        headers: list[str] | None = None,
        rows: list[list[str]] | None = None,
    ) -> None:
        self.rows: list[list[str]] = []
        if headers is not None:
            self.rows.append(list(headers))
        for row in rows or []:
            self.rows.append(list(row))
        self.col_count = 30

    # --- чтение ---

    def _parse(self, range_name: str) -> tuple[int, int, int, int]:
        """'A2:G5' -> (row1, col1, row1, col2) (1-based)."""
        start, _, end = range_name.partition(":")
        end = end or start
        sm = _CELL_RE.match(start.replace("$", ""))
        em = _CELL_RE.match(end.replace("$", ""))
        if not sm or not em:
            raise ValueError(f"Unsupported range: {range_name}")
        return (
            int(sm.group(2)),
            col_to_number(sm.group(1)),
            int(em.group(2)),
            col_to_number(em.group(1)),
        )

    def _value(self, row: int, col: int) -> str:
        """Значение ячейки с учётом «рваных» строк."""
        if row - 1 >= len(self.rows):
            return ""
        source = self.rows[row - 1]
        return source[col - 1] if col - 1 < len(source) else ""

    def get(
        self, range_name: str, value_render_option: str = "FORMATTED_VALUE"
    ) -> list[list[str]]:
        row1, col1, row2, col2 = self._parse(range_name)
        return [
            [
                self._value(row, col)
                for col in range(col1, min(col2, self.col_count) + 1)
            ]
            for row in range(row1, row2 + 1)
        ]

    def get_all_values(self) -> list[list[str]]:
        return [list(row) for row in self.rows]

    # --- запись ---

    def _ensure_size(self, row: int, col: int) -> None:
        while len(self.rows) < row:
            self.rows.append([])
        if col > self.col_count:
            self.col_count = col
        width = max(col, len(self.rows[row - 1]))
        while len(self.rows[row - 1]) < width:
            self.rows[row - 1].append("")

    def update(
        self,
        range_name: str,
        values: list[list[str]],
        value_input_option: str = "RAW",
    ) -> None:
        row1, col1, _, _ = self._parse(range_name)
        for offset, values_row in enumerate(values):
            for col_offset, value in enumerate(values_row):
                self.update_cell(row1 + offset, col1 + col_offset, value)

    def update_cell(self, row: int, col: int, value: str) -> None:
        self._ensure_size(row, col)
        self.rows[row - 1][col - 1] = value

    def append_row(self, values: list[str], value_input_option: str = "RAW") -> None:
        self.rows.append(list(values))

    def insert_row(
        self,
        values: list[str],
        index: int = 1,
        value_input_option: str = "RAW",
    ) -> None:
        self.rows.insert(index - 1, list(values))

    def insert_cols(
        self, values: list[list[str]], col: int, value_input_option: str = "RAW"
    ) -> None:
        """Вставляет колонку (все строки сдвигаются вправо)."""
        for row_index, row in enumerate(self.rows):
            self._ensure_size(row_index + 1, col)
            row.insert(col - 1, values[0][0] if row_index == 0 else "")

    def add_cols(self, cols: int) -> None:
        self.col_count += cols

    def delete_rows(self, start_index: int, end_index: int | None = None) -> None:
        end = end_index or start_index
        del self.rows[start_index - 1 : end]

    def delete_columns(self, start_index: int, end_index: int | None = None) -> None:
        end = end_index or start_index
        for row in self.rows:
            if start_index - 1 < len(row):
                del row[start_index - 1 : end]

    def batch_update(
        self, data: list[dict[str, Any]], value_input_option: str = "RAW"
    ) -> None:
        for item in data:
            self.update(item["range"], item["values"], value_input_option)

    def clear(self) -> None:
        self.rows = []


def make_sheet(rows: list[list[str]] | None = None) -> FakeSheet:
    """Лист с базовой шапкой заметок и переданными строками."""
    return FakeSheet(headers=list(NOTES_HEADERS), rows=rows)


class FakeState:
    """Мини-замена aiogram FSMContext для тестов сценариев."""

    def __init__(self) -> None:
        self.state: Any = None
        self.data: dict[str, Any] = {}

    async def set_state(self, state: Any) -> None:
        self.state = state

    async def get_state(self) -> Any:
        return self.state

    async def update_data(self, **kwargs: Any) -> None:
        self.data.update(kwargs)

    async def get_data(self) -> dict[str, Any]:
        return dict(self.data)

    async def clear(self) -> None:
        self.state = None
        self.data = {}


class FakeUser:
    """Минимальный from_user."""

    def __init__(self, user_id: int = 1, username: str = "tester") -> None:
        self.id = user_id
        self.username = username
        self.full_name = "Test User"


class FakeMessage:
    """Сообщение: собирает всё, что бот отправил."""

    def __init__(self, text: str = "", user_id: int = 1) -> None:
        self.text = text
        self.from_user = FakeUser(user_id)
        self.sent: list[dict[str, Any]] = []

    async def answer(
        self, text: str, reply_markup: Any = None, **kwargs: Any
    ) -> "FakeMessage":
        self.sent.append({"text": text, "reply_markup": reply_markup, "kind": "text"})
        return self

    async def answer_photo(
        self, photo: str, caption: str = "", reply_markup: Any = None, **kw: Any
    ) -> "FakeMessage":
        self.sent.append(
            {"photo": photo, "caption": caption, "reply_markup": reply_markup}
        )
        return self

    async def delete(self) -> None:  # pragma: no cover - редко используется
        return None


class FakeCallback:
    """
    CallbackQuery без наследства от pydantic-модели aiogram.

    Наследование от ``types.CallbackQuery`` заставляет pydantic валидировать
    поля (``id``, ``from``, ``chat_instance``, ``data``) и запрещает писать
    в замороженные поля — двойник падал бы в ``__init__``. Здесь обычный
    Python-класс с теми же атрибутами; ``telegram_io`` читает их через
    ``getattr``, поэтому утиная типизация срабатывает.
    """

    def __init__(self, data: str, user_id: int = 1) -> None:
        self.id = f"cb-{data}-{user_id}"
        self.data = data
        self.from_user = FakeUser(user_id)
        self.chat_instance = f"chat-{user_id}"
        self.message = FakeMessage("", user_id)
        self.answers: list[dict[str, Any]] = []

    async def answer(
        self, text: str | None = None, show_alert: bool = False, **kw: Any
    ) -> None:
        """Гасит «часики»: ответы копятся в ``answers`` для проверок."""
        self.answers.append({"text": text, "show_alert": show_alert, **kw})
