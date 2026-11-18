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
                _button(buttons.SOURCES, Cb.MENU_SOURCES),
                _button(buttons.BY_SOURCE, Cb.SRC_LIST),
            ],
            [_button(buttons.COLUMNS, Cb.MENU_COLUMNS)],
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


def new_source_cancel() -> InlineKeyboardMarkup:
    """Экран ввода нового источника: отмена рядом с возвратом в меню."""
    return _markup([[cancel_row(), to_main_row()]])


def table_link(url: str) -> InlineKeyboardMarkup:
    """Ссылка на Google-таблицу."""
    row: list[InlineKeyboardButton] = []
    if url:
        row.append(InlineKeyboardButton(text=buttons.OPEN_TABLE, url=url))
    row.append(to_main_row())
    return _markup([row])


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


# --- Управление источниками ---

#: Сколько кнопок-номеров в одном ряду списка источников.
SOURCE_NUMBER_ROW = 5


def sources_manage(count: int) -> InlineKeyboardMarkup:
    """
    «🗂 Sources»: кнопки-номера источников + «➕ Add source» + меню.

    В ``callback_data`` уходит только номер: имя источника может не влезть
    в 64 байта callback_data, а сам список лежит в FSMContext.
    """
    numbers = [
        _button(str(index + 1), Cb.src_manage(index))
        for index in range(min(count, PAGE_SIZE))
    ]
    rows = _rows(numbers, SOURCE_NUMBER_ROW)
    rows.append([_button(buttons.NEW_SOURCE, Cb.SRC_ADD)])
    rows.append([to_main_row()])
    return _markup(rows)


def source_actions(index: int) -> InlineKeyboardMarkup:
    """
    Действия над источником.

    Первый ряд — заметки источника, второй — правка/удаление источника,
    третий — возврат в список источников и главное меню.
    """
    return _markup(
        [
            [
                _button(buttons.SHOW_NOTES, Cb.src_show_notes(index)),
                _button(buttons.ADD_NOTE, Cb.src_new_note(index)),
            ],
            [
                _button(buttons.RENAME_SOURCE, Cb.src_rename(index)),
                _button(buttons.DELETE_SOURCE, Cb.src_del(index)),
            ],
            [_button(buttons.BACK, Cb.MENU_SOURCES), to_main_row()],
        ]
    )


def source_delete_confirm(index: int) -> InlineKeyboardMarkup:
    """Подтверждение удаления источника: удалить или отменить."""
    return _markup(
        [
            [
                _button(buttons.CONFIRM_DELETE, Cb.src_del_confirm(index)),
                _button(buttons.CANCEL, Cb.src_manage(index)),
            ],
        ]
    )


# --- Заметки ---


#: Сколько кнопок-номеров заметок в одном ряду.
NOTE_NUMBER_ROW = 5


def notes(
    note_ids: Sequence[str], source: str, source_index: int | None = None
) -> InlineKeyboardMarkup:
    """
    Список заметок источника: кнопки-номера 1..N открывают карточку.

    В тексте кнопки — порядковый номер (как в пронумерованном списке),
    в ``callback_data`` — настоящий ID заметки. Если ``source_index``
    известен, под номерами появляются «➕ Add Note» и «✏️ Edit Source» для
    этого источника (в callback_data снова только номер — 64 байта хватит).
    """
    items = [
        _button(str(index), Cb.note_view(note_id, source))
        for index, note_id in enumerate(note_ids[:PAGE_SIZE], start=1)
    ]
    rows = _rows(items, NOTE_NUMBER_ROW)
    if source_index is not None:
        rows.append(
            [
                _button(buttons.ADD_NOTE_ROW, Cb.src_new_note(source_index)),
                _button(buttons.EDIT_SOURCE, Cb.src_rename(source_index)),
            ]
        )
    rows.append([_button(buttons.BACK, Cb.SRC_LIST), to_main_row()])
    return _markup(rows)


def note_card(note_id: str, source: str) -> InlineKeyboardMarkup:
    """Клавиатура карточки заметки: правка поля, скриншот, удаление и навигация."""
    rows: list[list[InlineKeyboardButton]] = [
        [
            _button(buttons.EDIT_FIELD, Cb.note_edit_menu(note_id)),
            _button(buttons.DELETE_NOTE, Cb.note_del(note_id)),
        ],
        [_button(buttons.PHOTO, Cb.note_photo(note_id))],
    ]
    nav_button = _button(buttons.BACK, Cb.src_notes(source)) if source else cancel_row()
    rows.append([nav_button, to_main_row()])
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


def columns_menu(count: int) -> InlineKeyboardMarkup:
    """
    «📋 Columns»: кнопки-номера пользовательских колонок и действия над шапкой.

    В ``callback_data`` уходит позиция в списке (0-based), а не имя:
    имена колонок могут быть длинными, а лимит callback_data — 64 байта.
    «🔢 Change order» меняет порядок всего списка, поэтому живёт здесь же,
    рядом с «➕ Add column», а не в меню отдельной колонки.
    """
    numbers = [
        _button(str(position + 1), Cb.col_view(position))
        for position in range(min(count, PAGE_SIZE))
    ]
    rows = _rows(numbers, NOTE_NUMBER_ROW)
    actions = [_button(buttons.ADD_COLUMN, Cb.COL_ADD)]
    if count >= 2:
        actions.append(_button(buttons.CHANGE_ORDER, Cb.COL_REORDER))
    rows.append(actions)
    rows.append([to_main_row()])
    return _markup(rows)


def column_actions(col_index: int, can_delete: bool) -> InlineKeyboardMarkup:
    """
    Меню выбранной колонки: только действия над ней самой.

    Порядок меняется на главном экране ``📋 Columns``. Удаление скрыто
    для защищённых колонок (например, ``SCREEN``).
    """
    edit = [_button(buttons.RENAME_SOURCE, Cb.col_rename(col_index))]
    if can_delete:
        edit.append(_button(buttons.DELETE_SOURCE, Cb.col_del(col_index)))
    rows: list[list[InlineKeyboardButton]] = [
        edit,
        [_button(buttons.BACK, Cb.MENU_COLUMNS), to_main_row()],
    ]
    return _markup(rows)


def columns_cancel() -> InlineKeyboardMarkup:
    """Отмена ввода нового порядка колонок."""
    return _markup([[cancel_row(), to_main_row()]])


__all__ = [
    "PAGE_SIZE",
    "back_to_main",
    "cancel_row",
    "new_source_cancel",
    "column_actions",
    "columns_cancel",
    "columns_menu",
    "edit_fields_only",
    "main_menu",
    "note_card",
    "note_delete_confirm",
    "notes",
    "photo_cancel",
    "source_actions",
    "source_delete_confirm",
    "sources",
    "sources_manage",
    "sources_with_counts",
    "table_link",
    "to_main_row",
]
