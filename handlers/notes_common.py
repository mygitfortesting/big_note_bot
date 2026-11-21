"""
Общие утилиты хендлеров заметок: состояния, репозиторий, карточка.

Модуль не содержит роутера: он собирает ``NoteStates`` и ``NoteContext`` и
предоставляет хелперы, которыми пользуются ``notes_handler``,
``wizard_handler`` и ``sources_handler``.
"""

from __future__ import annotations

from dataclasses import dataclass

from aiogram import types
from aiogram.fsm.state import State, StatesGroup

from services.sheets import (
    Note,
    NoteRepository,
    screen_column_index,
    source_column_index,
    title_column_index,
)
from services.telegram_io import require_from_user
from services.text_utils import escape_html, note_card_text

#: Колонки, которые НЕЛЬЗЯ править текстом: ID — ключ записи,
#: SCREEN — только через приём фото (в ней file_id Telegram).
NON_EDITABLE_HEADERS = {"ID", "SCREEN"}
#: Значения, которыми пользователь «очищает» ячейку.
CLEAR_VALUES = {"-", "—", "clear", "none", "пусто"}
#: Колонки, которые мастер заполнения не спрашивает (ID и SOURCE уже
#: известны, TITLE введён только что, SCREEN спрашивается отдельно).
WIZARD_SKIP_HEADERS = {"ID", "SOURCE", "TITLE"}
#: Служебная колонка со скриншотом: в мастере для неё ждём фото.
SCREEN_HEADER_UPPER = "SCREEN"


class NoteStates(StatesGroup):
    """Состояния ввода сценариев заметок."""

    wait_source = State()  # ввод имени нового источника
    wait_title = State()  # ввод заголовка (TITLE) заметки
    wizard_step = State()  # шаг мастера заполнения колонок новой заметки
    wait_value = State()  # ввод значения колонки
    wait_photo = State()  # ожидание фото для SCREEN
    wait_source_name = State()  # новое имя существующего источника


@dataclass(frozen=True)
class NoteContext:
    """Всё, что нужно для карточки: заметка, шапка и её строки."""

    note: Note
    headers: list[str]
    source: str

    @property
    def note_id(self) -> str:
        return self.note.note_id

    @property
    def screen(self) -> str:
        """file_id скриншота (пусто, если фото нет)."""
        index = screen_column_index(self.headers)
        return self.note.value(index).strip() if index else ""

    @property
    def title(self) -> str:
        """TITLE заметки (или «ID xxx», если заголовок пустой)."""
        return note_title(self.note, self.headers)

    def editable_columns(self) -> list[tuple[int, str]]:
        """(номер, имя) колонок для текстовой правки — всё, кроме ID и SCREEN."""
        return [
            (index, header)
            for index, header in enumerate(self.headers, start=1)
            if header.strip().upper() not in NON_EDITABLE_HEADERS
        ]

    def card_text(self) -> str:
        """Карточка: только заполненные колонки, между полями — пустая строка."""
        return note_card_text(self.headers, self.note.values) or (
            f"<b>📝 {escape_html(self.title)}</b>"
        )

    def cell_value(self, col_index: int) -> str:
        """Текущее значение ячейки без пробелов (пусто — ячейки нет)."""
        return self.note.value(col_index).strip()

    def current_value(self, col_index: int) -> str:
        """Заполненное значение для подсказки «Current: ...» (иначе пусто)."""
        value = self.cell_value(col_index)
        return "" if value in CLEAR_VALUES else value


def repo_of(
    event: types.Message | types.CallbackQuery,
) -> NoteRepository | None:
    """Репозиторий листа пользователя события (None, если нет from_user)."""
    user = require_from_user(event)
    if user is None:
        return None
    return NoteRepository(user.id)


def source_of(headers: list[str], note: Note) -> str:
    """Источник заметки — нужен для возврата к списку и заголовка карточки."""
    return note.value(source_column_index(headers)).strip()


def note_context(repo: NoteRepository, note_id: str) -> NoteContext | None:
    """Заметка + шапка в одном снимке листа (один запрос к Sheets)."""
    note = repo.get(note_id)
    if note is None:
        return None
    headers = repo.headers()
    return NoteContext(note=note, headers=headers, source=source_of(headers, note))


def note_title(note: Note, headers: list[str]) -> str:
    """TITLE заметки для списков и карточки (или «ID xxx», если пусто)."""
    title = note.value(title_column_index(headers)).strip()
    return title or f"ID {note.note_id}"


def row_of(repo: NoteRepository, note_id: str) -> int:
    """Номер строки заметки; ValueError, если её уже удалили."""
    row = repo.find_row(note_id)
    if row is None:
        raise ValueError(f"Note {note_id} not found.")
    return row


def write_note_cell(
    repo: NoteRepository, note_id: str, col_index: int, value: str
) -> None:
    """Синхронно пишет значение в ячейку заметки (вызывать через to_thread)."""
    repo.update_cell_at(row_of(repo, note_id), col_index, value)


def clean_value(text: str | None) -> str:
    """Значение из сообщения: пробелы срезаются, «-» и др. очищают ячейку."""
    value = (text or "").strip()
    return "" if value.lower() in CLEAR_VALUES else value
