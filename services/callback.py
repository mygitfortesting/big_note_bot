"""
Единый реестр идентификаторов команд и инлайн-кнопок (callback_data).

Имена собираются ТОЛЬКО отсюда: опечатка в хендлере или клавиатуре
невозможна, потому что и там и там используется одна константа.
Параметрические кнопки кодируются ``<prefix>:<payload>`` и разбираются
статическими методами ``Cb``.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Cmd", "Cb"]


class Cmd:
    """Имена слэш-команд бота (без ведущего слэша): /start, /help, ..."""

    START = "start"
    HELP = "help"
    ADD_NOTE = "add_note"
    ADD_SOURCE = "add_source"
    TABLE = "table"


@dataclass(frozen=True)
class NoteRef:
    """Разобранная параметрическая кнопка заметки."""

    note_id: str
    source: str = ""


class Cb:
    """Константы callback_data и сборщики с параметрами."""

    # --- Навигация и главные разделы ---
    #: «🏠 Main menu» — возврат в главное меню из любого раздела.
    TO_MAIN = "to_main"
    CANCEL_INPUT = "cancel_input"
    #: Список источников с количеством заметок «📂 Notes by source».
    SRC_LIST = "src_list"
    #: «📋 Columns» — управление колонками листа.
    MENU_COLUMNS = "menu_columns"
    #: «🗂 Sources» — управление источниками (переименование/удаление).
    MENU_SOURCES = "menu_sources"

    # --- Источники ---
    #: «➕ Add note»: выбор источника из списка или «Новый источник».
    NOTE_ADD = "note_add"
    #: «🗂 Add source» / «➕ New source»: ввод имени нового источника.
    SRC_ADD = "src_add"
    #: Параметрические кнопки управления источниками (номер в списке).
    SRC_MANAGE = "src_manage"
    SRC_RENAME = "src_rename"
    SRC_DEL = "src_del"
    SRC_DEL_CONFIRM = "src_del_confirm"
    SRC_SHOW_NOTES = "src_show_notes"
    SRC_NEW_NOTE = "src_new_note"
    #: «📖 Show all notes»: все заметки источника с полным содержанием.
    SRC_ALL_NOTES = "src_all"

    # --- Мастер заполнения новой заметки ---
    #: «⏭ Next» — следующая колонка мастера (``wiz_next:<note_id>``).
    WIZARD_NEXT = "wiz_next"
    #: «✅ Done» — закончить мастер (``wiz_done:<note_id>``).
    WIZARD_DONE = "wiz_done"
    #: «➕ Add another note» — ещё одна заметка (``wiz_another:<note_id>``).
    WIZARD_ANOTHER = "wiz_another"

    # --- Кнопки с параметрами ---
    SRC_SELECT = "src_select"
    SRC_NOTES = "src_notes"
    NOTE_VIEW = "note_view"
    NOTE_EDIT_MENU = "note_edit_menu"
    NOTE_EDIT_CELL = "note_edit_cell"
    NOTE_PHOTO = "note_photo"
    NOTE_DEL = "note_del"
    COL_ADD = "col_add"
    COL_DEL = "col_del"
    COL_RENAME = "col_rename"
    #: Открыть меню колонки по её номеру в списке (0-based).
    COL_VIEW = "col_view"
    #: «🔢 Change order»: ввод нового порядка колонок цифрами.
    COL_REORDER = "col_reorder"

    _SEP = ":"

    @classmethod
    def _join(cls, prefix: str, *parts: object) -> str:
        """Собирает callback_data ``prefix:part1:part2``."""
        encoded = [str(part).replace(cls._SEP, " ") for part in parts]
        return cls._SEP.join([prefix, *encoded])

    @classmethod
    def _split(cls, data: str, prefix: str) -> tuple[str, ...] | None:
        """Возвращает хвост callback_data, если он начинается с ``prefix:``."""
        marker = prefix + cls._SEP
        if not data.startswith(marker):
            return None
        return tuple(data[len(marker) :].split(cls._SEP))

    # --- Источники ---

    @classmethod
    def src_select(cls, source: str) -> str:
        """Выбрать существующий источник (для новой заметки)."""
        return cls._join(cls.SRC_SELECT, source)

    @classmethod
    def src_notes(cls, source: str) -> str:
        """Открыть список заметок источника."""
        return cls._join(cls.SRC_NOTES, source)

    @classmethod
    def parse_source(cls, data: str, prefix: str) -> str | None:
        """Источник из callback ``prefix:<source>``."""
        parts = cls._split(data, prefix)
        if not parts or not "".join(parts):
            return None
        return cls._SEP.join(parts)

    # --- Управление источниками (номер в списке, а не имя) ---

    @classmethod
    def src_manage(cls, index: int) -> str:
        """Открыть управление источником с номером ``index`` (0-based)."""
        return cls._join(cls.SRC_MANAGE, index)

    @classmethod
    def src_rename(cls, index: int) -> str:
        """Переименовать источник с номером ``index`` (0-based)."""
        return cls._join(cls.SRC_RENAME, index)

    @classmethod
    def src_del(cls, index: int) -> str:
        """Спросить подтверждение удаления источника ``index`` (0-based)."""
        return cls._join(cls.SRC_DEL, index)

    @classmethod
    def src_del_confirm(cls, index: int) -> str:
        """Подтверждённое удаление источника ``index`` (0-based)."""
        return cls._join(cls.SRC_DEL_CONFIRM, index)

    @classmethod
    def src_show_notes(cls, index: int) -> str:
        """Открыть список заметок источника ``index`` (0-based)."""
        return cls._join(cls.SRC_SHOW_NOTES, index)

    @classmethod
    def src_new_note(cls, index: int) -> str:
        """Сразу создать заметку для источника ``index`` (0-based)."""
        return cls._join(cls.SRC_NEW_NOTE, index)

    @classmethod
    def src_all_notes(cls, index: int) -> str:
        """Все заметки источника ``index`` (0-based) с полным содержанием."""
        return cls._join(cls.SRC_ALL_NOTES, index)

    @classmethod
    def parse_source_index(cls, data: str, prefix: str) -> int | None:
        """Номер источника (0-based) из callback ``prefix:<index>``."""
        parts = cls._split(data, prefix)
        if not parts or not parts[0].isdigit():
            return None
        return int(parts[0])

    # --- Заметки ---

    @classmethod
    def note_view(cls, note_id: str, source: str = "") -> str:
        """Карточка заметки (источник нужен для возврата к его списку)."""
        return cls._join(cls.NOTE_VIEW, note_id, source)

    @classmethod
    def note_edit_menu(cls, note_id: str) -> str:
        """Меню выбора колонки для правки."""
        return cls._join(cls.NOTE_EDIT_MENU, note_id)

    @classmethod
    def note_edit_cell(cls, note_id: str, col_index: int) -> str:
        """Редактирование конкретной колонки заметки."""
        return cls._join(cls.NOTE_EDIT_CELL, note_id, col_index)

    @classmethod
    def note_photo(cls, note_id: str) -> str:
        """Прикрепить/заменить скриншот заметки."""
        return cls._join(cls.NOTE_PHOTO, note_id)

    @classmethod
    def note_del(cls, note_id: str) -> str:
        """Удалить заметку."""
        return cls._join(cls.NOTE_DEL, note_id)

    @classmethod
    def parse_note(cls, data: str, prefix: str) -> NoteRef | None:
        """Разбирает ``prefix:<note_id>[:<tail>]``."""
        parts = cls._split(data, prefix)
        if not parts or not parts[0]:
            return None
        tail = parts[1] if len(parts) > 1 else ""
        return NoteRef(note_id=parts[0], source=tail)

    @classmethod
    def parse_note_cell(cls, data: str) -> tuple[str, int] | None:
        """Разбирает callback правки ячейки: (note_id, номер колонки)."""
        parts = cls._split(data, cls.NOTE_EDIT_CELL)
        if not parts or len(parts) < 2:
            return None
        note_id, col_raw = parts[0], parts[1]
        if not note_id or not col_raw.isdigit():
            return None
        return note_id, int(col_raw)

    # --- Мастер заполнения новой заметки ---
    # ``note_id`` в callback_data — 8 символов, поэтому кнопки мастера
    # срабатывают даже после рестарта сервера (MemoryStorage пуст).

    @classmethod
    def wizard_next(cls, note_id: str) -> str:
        """«⏭ Next» — следующая колонка мастера для заметки ``note_id``."""
        return cls._join(cls.WIZARD_NEXT, note_id)

    @classmethod
    def wizard_done(cls, note_id: str) -> str:
        """«✅ Done» — закончить мастер заметки ``note_id``."""
        return cls._join(cls.WIZARD_DONE, note_id)

    @classmethod
    def wizard_another(cls, note_id: str) -> str:
        """«➕ Add another note» — ещё заметка в источник заметки ``note_id``."""
        return cls._join(cls.WIZARD_ANOTHER, note_id)

    @classmethod
    def parse_wizard_note_id(cls, data: str, prefix: str) -> str | None:
        """``note_id`` из callback ``prefix:<note_id>`` (None, если нет)."""
        parts = cls._split(data, prefix)
        if not parts or not parts[0]:
            return None
        return parts[0]

    # --- Колонки ---

    @classmethod
    def col_view(cls, position: int) -> str:
        """Меню колонки с номером ``position`` в списке (0-based)."""
        return cls._join(cls.COL_VIEW, position)

    @classmethod
    def col_del(cls, col_index: int) -> str:
        """Удалить пользовательскую колонку."""
        return cls._join(cls.COL_DEL, col_index)

    @classmethod
    def col_rename(cls, col_index: int) -> str:
        """Переименовать пользовательскую колонку."""
        return cls._join(cls.COL_RENAME, col_index)

    @classmethod
    def parse_col(cls, data: str, prefix: str) -> int | None:
        """Номер колонки из callback ``prefix:<col>``."""
        parts = cls._split(data, prefix)
        if not parts or not parts[0].isdigit():
            return None
        return int(parts[0])
