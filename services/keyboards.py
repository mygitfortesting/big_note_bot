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


# --- Постоянные кнопки ---
# Каждая функция возвращает новую кнопку с фиксированным текстом и
# callback_data: меню собираются из готовых кирпичиков, без `_button(...)`
# прямо внутри списков.


def to_main_row() -> InlineKeyboardButton:
    """Кнопка «🏠 Main menu» — ведёт в главное меню."""
    return _button(buttons.TO_MAIN, Cb.TO_MAIN)


def cancel_row() -> InlineKeyboardButton:
    """Кнопка «✖️ Cancel» — отмена текущего ввода."""
    return _button(buttons.CANCEL, Cb.CANCEL_INPUT)


def add_note_button() -> InlineKeyboardButton:
    """«➕ Add note» — создать заметку (с выбором источника)."""
    return _button(buttons.ADD_NOTE, Cb.NOTE_ADD)


def add_source_button() -> InlineKeyboardButton:
    """«📂 Add source» — ввод имени нового источника."""
    return _button(buttons.ADD_SOURCE, Cb.SRC_ADD)


def new_source_button() -> InlineKeyboardButton:
    """«➕ New source» — то же действие, но ярлык экрана выбора."""
    return _button(buttons.NEW_SOURCE, Cb.SRC_ADD)


def sources_button() -> InlineKeyboardButton:
    """«🗂 Sources» — список источников с действиями."""
    return _button(buttons.SOURCES, Cb.MENU_SOURCES)


def columns_button() -> InlineKeyboardButton:
    """«📊 Columns» — управление колонками листа."""
    return _button(buttons.COLUMNS, Cb.MENU_COLUMNS)


def add_column_button() -> InlineKeyboardButton:
    """«➕ Add column» — новая пользовательская колонка."""
    return _button(buttons.ADD_COLUMN, Cb.COL_ADD)


def change_order_button() -> InlineKeyboardButton:
    """«🔢 Change order» — ввод нового порядка колонок."""
    return _button(buttons.CHANGE_ORDER, Cb.COL_REORDER)


def back_to_columns_button() -> InlineKeyboardButton:
    """«⬅️ Back» из меню колонки — к списку колонок."""
    return _button(buttons.BACK, Cb.MENU_COLUMNS)


def back_to_sources_button() -> InlineKeyboardButton:
    """«⬅️ Back» — к списку источников."""
    return _button(buttons.BACK, Cb.MENU_SOURCES)


# --- Кнопки мастера заполнения новой заметки ---


def wizard_next_button(note_id: str) -> InlineKeyboardButton:
    """«⏭ Next» — пропустить текущую колонку мастера заметки ``note_id``."""
    return _button(buttons.NEXT_FIELD, Cb.wizard_next(note_id))


def wizard_done_button(note_id: str) -> InlineKeyboardButton:
    """«✅ Done» — закончить мастер заметки ``note_id`` и открыть карточку."""
    return _button(buttons.DONE_NOTE, Cb.wizard_done(note_id))


def wizard_another_button(note_id: str) -> InlineKeyboardButton:
    """«➕ Add another note» — снова TITLE для источника этой заметки."""
    return _button(buttons.ADD_ANOTHER_NOTE, Cb.wizard_another(note_id))


# --- Кнопки с параметрами ---
# Источник передаётся номером из FSMContext (0-based), заметка/колонка —
# своим ID или номером: в callback_data не попадают длинные имена.


def source_all_notes_button(index: int) -> InlineKeyboardButton:
    """«📖 Show all notes» — все заметки источника целиком."""
    return _button(buttons.SHOW_ALL_NOTES, Cb.src_all_notes(index))


def source_add_note_button(
    index: int, label: str | None = None
) -> InlineKeyboardButton:
    """«➕ Add note» сразу для источника (``label`` — ярлык экрана)."""
    return _button(label or buttons.ADD_NOTE, Cb.src_new_note(index))


def source_rename_button(index: int, label: str | None = None) -> InlineKeyboardButton:
    """Переименование источника (``label`` — ярлык экрана)."""
    return _button(label or buttons.RENAME_SOURCE, Cb.src_rename(index))


def source_delete_button(index: int) -> InlineKeyboardButton:
    """Удалить источник (с подтверждением на следующем шаге)."""
    return _button(buttons.DELETE_SOURCE, Cb.src_del(index))


def source_delete_confirm_button(index: int) -> InlineKeyboardButton:
    """«✅ Yes, delete» — подтверждение удаления источника."""
    return _button(buttons.CONFIRM_DELETE, Cb.src_del_confirm(index))


def source_back_to_manage_button(index: int) -> InlineKeyboardButton:
    """«⬅️ Back» на экран источника — отмена подтверждения удаления."""
    return _button(buttons.BACK, Cb.src_manage(index))


def note_edit_menu_button(note_id: str) -> InlineKeyboardButton:
    """«✏️ Edit field» — выбор колонки для правки."""
    return _button(buttons.EDIT_FIELD, Cb.note_edit_menu(note_id))


def note_delete_button(note_id: str) -> InlineKeyboardButton:
    """«🗑 Delete» — удалить заметку."""
    return _button(buttons.DELETE_NOTE, Cb.note_del(note_id))


def note_photo_button(note_id: str) -> InlineKeyboardButton:
    """«🖼 Add/Replace screenshot» — прикрепить скриншот."""
    return _button(buttons.PHOTO, Cb.note_photo(note_id))


def note_view_back_button(note_id: str, source: str) -> InlineKeyboardButton:
    """«⬅️ Back» — из подтверждения удаления обратно в карточку."""
    return _button(buttons.BACK, Cb.note_view(note_id, source))


def note_source_back_button(source: str) -> InlineKeyboardButton:
    """«⬅️ Back» — из карточки к заметкам её источника."""
    return _button(buttons.BACK, Cb.src_notes(source))


def column_rename_button(col_index: int) -> InlineKeyboardButton:
    """«✏️ Rename» — переименовать пользовательскую колонку."""
    return _button(buttons.RENAME_COLUMN_SHORT, Cb.col_rename(col_index))


def column_delete_button(col_index: int) -> InlineKeyboardButton:
    """«🗑 Delete» — удалить пользовательскую колонку."""
    return _button(buttons.DELETE_COLUMN_SHORT, Cb.col_del(col_index))


# --- Навигация ---


def main_menu() -> InlineKeyboardMarkup:
    """Главное меню: основные разделы."""
    rows = [
        [add_note_button(), add_source_button()],
        [sources_button(), columns_button()],
    ]
    return _markup(rows)


def back_to_main() -> InlineKeyboardMarkup:
    """Одна кнопка «в главное меню» (справка, предупреждения)."""
    return _markup([[to_main_row()]])


def search_not_found() -> InlineKeyboardMarkup:
    """Ничего не нашлось: предложить добавить заметку или вернуться в меню."""
    return _markup([[add_note_button(), to_main_row()]])


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
        rows.append([new_source_button()])
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
    return _markup([*_rows(items, 2), [to_main_row()]])


# --- Управление источниками ---

#: Сколько кнопок-номеров в одном ряду списка источников.
SOURCE_NUMBER_ROW = 8


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
    rows.append([new_source_button(), to_main_row()])
    return _markup(rows)


def source_actions(
    index: int, note_ids: Sequence[str] = (), source: str = ""
) -> InlineKeyboardMarkup:
    """
    Экран источника: сразу заметки номерами и действия над источником.

    Кнопки-номера открывают карточку заметки (в callback_data — её ID),
    дальше — добавление заметки, правка/удаление источника и навигация.
    """
    rows = _note_number_rows(note_ids, source)
    rows.append([source_all_notes_button(index), source_add_note_button(index)])
    rows.append([source_rename_button(index), source_delete_button(index)])
    rows.append([back_to_sources_button(), to_main_row()])
    return _markup(rows)


def source_notes_back(
    index: int, note_ids: Sequence[str] = (), source: str = ""
) -> InlineKeyboardMarkup:
    """
    Полный список заметок источника: карточки пронумерованы, номера — кнопки.

    Кнопки-номера 1..N открывают карточку конкретной заметки, внизу —
    возврат на экран источника и главное меню.
    """
    rows = _note_number_rows(note_ids, source)
    rows.append([source_back_to_manage_button(index), to_main_row()])
    return _markup(rows)


def source_delete_confirm(index: int) -> InlineKeyboardMarkup:
    """Подтверждение удаления источника: удалить или отменить."""
    confirm_button = source_delete_confirm_button(index)
    cancel_button = source_back_to_manage_button(index)
    return _markup([[confirm_button, cancel_button]])


# --- Заметки ---


#: Сколько кнопок-номеров заметок в одном ряду.
NOTE_NUMBER_ROW = 5


def _note_number_rows(
    note_ids: Sequence[str], source: str
) -> list[list[InlineKeyboardButton]]:
    """Ряды кнопок-номеров 1..N: в тексте номер, в callback_data — ID."""
    numbers = [
        _numbered_note_button(position, note_id, source)
        for position, note_id in enumerate(note_ids[:PAGE_SIZE], start=1)
    ]
    return _rows(numbers, NOTE_NUMBER_ROW)


def _numbered_note_button(
    position: int, note_id: str, source: str
) -> InlineKeyboardButton:
    """Кнопка с порядковым номером, ведущая в карточку заметки."""
    return _button(str(position), Cb.note_view(note_id, source))


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
    rows = _note_number_rows(note_ids, source)
    if source_index is not None:
        add_button = source_add_note_button(source_index, buttons.ADD_NOTE_ROW)
        edit_button = source_rename_button(source_index, buttons.EDIT_SOURCE)
        rows.append([add_button, edit_button])
    rows.append([back_to_sources_button(), to_main_row()])
    return _markup(rows)


def note_card(note_id: str, source: str) -> InlineKeyboardMarkup:
    """Клавиатура карточки заметки: правка поля, скриншот, удаление и навигация."""
    back_button = note_source_back_button(source) if source else cancel_row()
    rows: list[list[InlineKeyboardButton]] = [
        [note_edit_menu_button(note_id), note_delete_button(note_id)],
        [note_photo_button(note_id)],
        [back_button, to_main_row()],
    ]
    return _markup(rows)


def note_delete_confirm(note_id: str, source: str) -> InlineKeyboardMarkup:
    """Подтверждение удаления заметки."""
    return _markup(
        [
            [note_delete_button(note_id)],
            [note_view_back_button(note_id, source)],
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
    return _markup([[note_view_back_button(note_id, source)]])


# --- Мастер заполнения ---


def note_wizard_step(note_id: str) -> InlineKeyboardMarkup:
    """
    Клавиатура шага мастера: пропустить, закончить, ещё заметка, меню.

    Ввод значения при этом присылается обычным сообщением в чат,
    поэтому кнопки только управляют сценарием. ``note_id`` едет в
    ``callback_data``: так кнопки работают и после сброса FSMContext.
    """
    return _markup(
        [
            [wizard_next_button(note_id), wizard_done_button(note_id)],
            [wizard_another_button(note_id)],
            [to_main_row()],
        ]
    )


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
    actions = [add_column_button()]
    if count >= 2:
        actions.append(change_order_button())
    rows.append(actions)
    rows.append([to_main_row()])
    return _markup(rows)


def column_actions(col_index: int, can_delete: bool) -> InlineKeyboardMarkup:
    """
    Меню выбранной колонки: только действия над ней самой.

    Порядок меняется на главном экране ``📋 Columns``. Удаление скрыто
    для защищённых колонок (например, ``SCREEN``).
    """
    actions = [column_rename_button(col_index)]
    if can_delete:
        actions.append(column_delete_button(col_index))
    rows: list[list[InlineKeyboardButton]] = [
        actions,
        [back_to_columns_button(), to_main_row()],
    ]
    return _markup(rows)


def columns_cancel() -> InlineKeyboardMarkup:
    """Отмена ввода нового порядка колонок."""
    return _markup([[cancel_row(), to_main_row()]])


__all__ = [
    "PAGE_SIZE",
    "add_column_button",
    "add_note_button",
    "add_source_button",
    "back_to_columns_button",
    "back_to_main",
    "back_to_sources_button",
    "cancel_row",
    "change_order_button",
    "column_actions",
    "column_delete_button",
    "column_rename_button",
    "columns_button",
    "columns_cancel",
    "columns_menu",
    "edit_fields_only",
    "main_menu",
    "new_source_button",
    "new_source_cancel",
    "note_card",
    "note_delete_button",
    "note_delete_confirm",
    "note_edit_menu_button",
    "note_photo_button",
    "note_source_back_button",
    "note_view_back_button",
    "notes",
    "note_wizard_step",
    "photo_cancel",
    "search_not_found",
    "source_actions",
    "source_add_note_button",
    "source_all_notes_button",
    "source_back_to_manage_button",
    "source_delete_button",
    "source_delete_confirm",
    "source_delete_confirm_button",
    "source_notes_back",
    "source_rename_button",
    "sources",
    "sources_button",
    "sources_manage",
    "sources_with_counts",
    "table_link",
    "to_main_row",
    "wizard_another_button",
    "wizard_done_button",
    "wizard_next_button",
]
