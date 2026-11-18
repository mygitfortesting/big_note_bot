"""
Клавиатуры бота. Здесь только сборка ``InlineKeyboardMarkup``:
никто не ходит в сеть и не знает про Google Sheets — на вход приходят
уже готовые строки (источники, заметки, колонки).
"""

from __future__ import annotations

from typing import Iterable, Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services import buttons
from services.callback import Cb

#: Сколько кнопок показывать в одном списке (Telegram: до 100, но листать
#: такое количество неудобно, поэтому длинные списки режутся).
PAGE_SIZE = 40


def _rows(
    items: Iterable[InlineKeyboardButton], per_row: int = 1
) -> list[list[InlineKeyboardButton]]:
    """Раскладывает кнопки по рядам по ``per_row`` штук."""
    row: list[InlineKeyboardButton] = []
    rows: list[list[InlineKeyboardButton]] = []
    for item in items:
        row.append(item)
        if len(row) == per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def _markup(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _button(text: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data)


# --- Навигация ---


def main_menu() -> InlineKeyboardMarkup:
    """Главное меню: основные разделы."""
    return _markup(
        [
            [
                _button(buttons.ADD_NOTE, Cb.NOTE_ADD),
                _button(buttons.ADD_SOURCE, Cb.SRC_ADD),
            ],
            [
                _button(buttons.COLUMNS, Cb.MENU_COLUMNS),
                _button(buttons.BY_SOURCE, Cb.SRC_LIST),
            ],
        ]
    )


def back_to_main() -> InlineKeyboardMarkup:
    """Одна кнопка «в главное меню» (справка, предупреждения)."""
    return _markup([[to_main_row()]])


def to_main_row() -> InlineKeyboardButton:
    """Кнопка «🏠 Main menu» — ведёт в главное меню."""
    return _button(buttons.TO_MAIN, Cb.TO_MAIN)


def cancel_row() -> InlineKeyboardButton:
    return _button(buttons.CANCEL, Cb.CANCEL_INPUT)


def table_link(url: str) -> InlineKeyboardMarkup:
    """Ссылка на Google-таблицу."""
    rows: list[list[InlineKeyboardButton]] = []
    if url:
        rows.append([InlineKeyboardButton(text=buttons.OPEN_TABLE, url=url)])
    rows.append([to_main_row()])
    return _markup(rows)


# --- Источники ---


def sources(
    items: Sequence[str],
    prefix: str = Cb.SRC_NOTES,
    with_new: bool = False,
) -> InlineKeyboardMarkup:
    """
    Список источников кнопками.

    ``with_new`` добавляет кнопку «➕ Новый источник» — режим добавления заметки:
    сначала выбираем существующий источник или создаём новый.
    """
    choose = prefix == Cb.SRC_SELECT
    items_buttons = [
        _button(
            f"🗂 {source}", Cb.src_select(source) if choose else Cb.src_notes(source)
        )
        for source in items[:PAGE_SIZE]
    ]
    rows = _rows(items_buttons, 2)
    if with_new:
        rows.append([_button(buttons.NEW_SOURCE, Cb.SRC_ADD)])
    rows.append([cancel_row(), to_main_row()])
    return _markup(rows)


def sources_with_counts(
    counts: Sequence[tuple[str, int]],
) -> InlineKeyboardMarkup:
    """«📂 Notes by source»: источник и количество заметок."""
    items = [
        _button(f"{source} · {count}", Cb.src_notes(source))
        for source, count in counts[:PAGE_SIZE]
    ]
    return _markup([*_rows(items, 2), [cancel_row(), to_main_row()]])


# --- Заметки ---


def notes(note_ids: Sequence[str], source: str) -> InlineKeyboardMarkup:
    """Список заметок источника: каждая кнопка открывает карточку."""
    items = [
        _button(f"📝 {note_id}", Cb.note_view(note_id, source))
        for note_id in note_ids[:PAGE_SIZE]
    ]
    rows = _rows(items, 1)
    rows.append([_button(buttons.BACK, Cb.SRC_LIST), to_main_row()])
    return _markup(rows)


def note_card(note_id: str, source: str) -> InlineKeyboardMarkup:
    """
    Клавиатура карточки заметки: только 4 кнопки — правка поля, скриншот,
    удаление и возврат к списку источника.
    """
    rows: list[list[InlineKeyboardButton]] = [
        [_button(buttons.EDIT_FIELD, Cb.note_edit_menu(note_id))],
        [_button(buttons.PHOTO, Cb.note_photo(note_id))],
        [_button(buttons.DELETE_NOTE, Cb.note_del(note_id))],
    ]
    if source:
        rows.append([_button(buttons.BACK, Cb.src_notes(source))])
    rows.append([to_main_row()])
    return _markup(rows)


def note_delete_confirm(note_id: str, source: str) -> InlineKeyboardMarkup:
    """Подтверждение удаления заметки."""
    return _markup(
        [
            [_button(buttons.DELETE_NOTE, Cb.note_del(note_id))],
            [_button(buttons.BACK, Cb.note_view(note_id, source))],
        ]
    )


def edit_fields_only(
    columns: Sequence[tuple[int, str]], note_id: str
) -> InlineKeyboardMarkup:
    """Меню «✏️ Edit field»: по кнопке на каждую редактируемую колонку."""
    items = [
        _button(name, Cb.note_edit_cell(note_id, col_index))
        for col_index, name in columns
    ]
    return _markup([*_rows(items, 2), [cancel_row()]])


def photo_cancel(note_id: str, source: str) -> InlineKeyboardMarkup:
    """Отмена ввода скриншота — возврат в карточку."""
    return _markup([[_button(buttons.BACK, Cb.note_view(note_id, source))]])


# --- Колонки ---


def columns_menu(
    columns: Sequence[tuple[int, str, bool]],
) -> InlineKeyboardMarkup:
    """
    Управление колонками.

    ``columns`` — (номер, имя, защищена?) в порядке шапки. Для пользовательских
    колонок доступны переименование и удаление, для всех — сдвиг влево/вправо.
    """
    rows: list[list[InlineKeyboardButton]] = [[_button(buttons.ADD_COLUMN, Cb.COL_ADD)]]
    for col_index, name, protected in columns:
        rows.append(
            [
                _button(buttons.MOVE_LEFT, Cb.col_move(col_index, "left")),
                _button(name[:24] or "—", Cb.MENU_COLUMNS),
                _button(buttons.MOVE_RIGHT, Cb.col_move(col_index, "right")),
            ]
        )
        if not protected:
            rows.append(
                [
                    _button(
                        f"{buttons.RENAME_COLUMN} {name[:18]}",
                        Cb.col_rename(col_index),
                    ),
                    _button(buttons.DELETE_COLUMN, Cb.col_del(col_index)),
                ]
            )
    rows.append([to_main_row()])
    return _markup(rows)


__all__ = [
    "PAGE_SIZE",
    "back_to_main",
    "cancel_row",
    "columns_menu",
    "edit_fields_only",
    "main_menu",
    "note_card",
    "note_delete_confirm",
    "notes",
    "photo_cancel",
    "sources",
    "sources_with_counts",
    "table_link",
    "to_main_row",
]
