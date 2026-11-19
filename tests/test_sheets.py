"""Тесты слоя данных: сортировка, источники, ID и защита служебных колонок."""

from __future__ import annotations

import pytest

from config import NOTES_HEADERS
from services.sheets import (
    COMMENT_HEADER,
    ID_HEADER,
    SCREEN_HEADER,
    SOURCE_HEADER,
    TITLE_HEADER,
    NoteRepository,
    column_letter,
    pad_row,
)
from tests.factories import columns_for, note_row, repo_for, rows_by_header

# --- Чистые функции ---


def test_column_letter() -> None:
    assert column_letter(1) == "A"
    assert column_letter(26) == "Z"
    assert column_letter(27) == "AA"


def test_pad_row_fills_short_rows() -> None:
    assert pad_row(["a"], 3) == ["a", "", ""]
    assert pad_row(["a", "b", "c"], 2) == ["a", "b", "c"]


# --- Сортировка ---


def test_sort_notes_by_source_then_title() -> None:
    """Сортировка: сначала SOURCE (без регистра), затем TITLE (без регистра)."""
    rows = [
        note_row("3", "twitter", "beta"),
        note_row("1", "Instagram", "Zulu"),
        note_row("2", "instagram", "alpha"),
        note_row("4", "Blog", "Gamma"),
    ]
    repo, sheet = repo_for(rows)

    repo.sort_notes()

    data = rows_by_header(sheet)
    assert [row[ID_HEADER] for row in data] == ["4", "2", "1", "3"]
    assert [row[SOURCE_HEADER] for row in data] == [
        "Blog",
        "instagram",
        "Instagram",
        "twitter",
    ]
    assert data[0][TITLE_HEADER] == "Gamma"


def test_sort_keeps_header_on_top() -> None:
    repo, sheet = repo_for([note_row("1", "B", "x"), note_row("2", "A", "y")])

    repo.sort_notes()

    assert sheet.rows[0] == NOTES_HEADERS


def test_create_adds_note_and_sorts() -> None:
    repo, sheet = repo_for([note_row("1", "YouTube", "Video")])

    note_id = repo.create("Blog", "Aaa")

    assert note_id
    data = rows_by_header(sheet)
    assert [row[SOURCE_HEADER] for row in data] == ["Blog", "YouTube"]
    created = repo.get(note_id)
    assert created is not None
    assert created.field(NOTES_HEADERS, TITLE_HEADER) == "Aaa"
    assert created.field(NOTES_HEADERS, SOURCE_HEADER) == "Blog"


def test_create_ids_are_unique() -> None:
    repo, _ = repo_for([])

    first = repo.create("Blog", "One")
    second = repo.create("Blog", "Two")

    assert first != second


# --- Источники ---


def test_list_sources_unique_sorted() -> None:
    rows = [
        note_row("1", "YouTube", "b"),
        note_row("2", "blog", "c"),
        note_row("3", "Blog", "a"),
        note_row("4", "", "no source"),
    ]
    repo, _ = repo_for(rows)

    assert repo.list_sources() == ["blog", "YouTube"]


def test_list_by_source_sorted_by_title() -> None:
    rows = [
        note_row("1", "Blog", "Zebra"),
        note_row("2", "Blog", "apple"),
        note_row("3", "YouTube", "Mango"),
    ]
    repo, _ = repo_for(rows)

    titles = [
        note.field(NOTES_HEADERS, TITLE_HEADER) for note in repo.list_by_source("blog")
    ]
    assert titles == ["apple", "Zebra"]


def test_list_by_source_is_case_insensitive() -> None:
    repo, _ = repo_for([note_row("1", "Instagram", "x")])
    assert len(repo.list_by_source("INSTAGRAM")) == 1


def test_list_sources_with_counts() -> None:
    """(источник, количество) по алфавиту, регистр не плодит дубликаты."""
    rows = [
        note_row("1", "YouTube", "b"),
        note_row("2", "blog", "a"),
        note_row("3", "Blog", "c"),
        note_row("4", "", "no source"),
    ]
    repo, _ = repo_for(rows)

    assert repo.list_sources_with_counts() == [("blog", 2), ("YouTube", 1)]


# --- Поиск по названию ---


def test_find_by_title_exact_wins_over_partial() -> None:
    """Точное совпадение TITLE приоритетнее частичного."""
    rows = [
        note_row("1", "Blog", "Yandex"),
        note_row("2", "Blog", "Yandex Zen"),
        note_row("3", "Blog", "FlexiTech"),
    ]
    repo, _ = repo_for(rows)

    found = repo.find_by_title("  yandex ")

    assert [note.note_id for note in found] == ["1"]


def test_find_by_title_partial_when_no_exact() -> None:
    """Нет точного — возвращаются все строки, где запрос — подстрока."""
    rows = [
        note_row("1", "Blog", "Yandex Zen"),
        note_row("2", "Blog", "Yandex Music"),
        note_row("3", "Blog", "FlexiTech"),
    ]
    repo, _ = repo_for(rows)

    found = repo.find_by_title("YANDEX")

    assert [note.note_id for note in found] == ["1", "2"]


def test_find_by_title_empty_query_returns_nothing() -> None:
    repo, _ = repo_for([note_row("1", "Blog", "Yandex")])

    assert repo.find_by_title("   ") == []


# --- Автозаполнение ID для ручных строк ---


def test_manual_row_without_id_gets_generated_id() -> None:
    """Строка, вписанная руками (SOURCE/TITLE без ID), получает ID в таблицу."""
    repo, sheet = repo_for([["", "Blog", "Manual", "", "", "", ""]])

    notes = repo.list_notes()

    assert len(notes) == 1
    generated = notes[0].note_id
    assert generated
    assert sheet.rows[1][0] == generated  # ID записан в колонку A
    assert repo.get(generated) is not None


def test_manual_row_without_id_is_found_when_listing_source() -> None:
    """Такая заметка видна и в списке источника (путь чтения для кнопок)."""
    repo, sheet = repo_for([["", "Blog", "Manual", "", "", "", ""]])

    notes = repo.list_by_source("blog")

    assert len(notes) == 1
    assert sheet.rows[1][0]
    assert notes[0].note_id == sheet.rows[1][0]


def test_empty_row_gets_no_id() -> None:
    """Полностью пустая строка так и остаётся без ID."""
    repo, sheet = repo_for([["", "", "", "", "", "", ""]])

    assert repo.list_notes() == []
    assert sheet.rows[1][0] == ""


# --- Пустой источник-болванка ---


def test_add_source_creates_blank_row_without_id() -> None:
    """add_source создаёт строку с одним SOURCE: 0 заметок и без ID."""
    repo, sheet = repo_for([])

    assert repo.add_source("Podcast") is True

    assert repo.list_sources_with_counts() == [("Podcast", 0)]
    assert repo.list_sources() == ["Podcast"]
    assert repo.list_notes() == []
    assert sheet.rows[1][0] == ""  # ID болванке не выдан
    assert sheet.rows[1][1] == "Podcast"
    assert sheet.rows[1][2] == ""


def test_add_source_is_case_insensitive_and_idempotent() -> None:
    """Повторное добавление (в другом регистре) ничего не меняет."""
    repo, sheet = repo_for([note_row("1", "Blog", "a")])

    assert repo.add_source("blog") is False

    assert len(sheet.rows) == 2
    assert repo.list_sources_with_counts() == [("Blog", 1)]


def test_add_source_rejects_empty_name() -> None:
    repo, _ = repo_for([])

    with pytest.raises(ValueError):
        repo.add_source("   ")


def test_create_fills_blank_source_row() -> None:
    """create заполняет строку-болванку вместо добавления дубликата."""
    repo, sheet = repo_for([])
    repo.add_source("Podcast")

    note_id = repo.create("podcast", "First")

    assert len(sheet.rows) == 2  # шапка + одна строка
    assert sheet.rows[1][0] == note_id
    assert sheet.rows[1][1] == "podcast"
    assert sheet.rows[1][2] == "First"
    assert repo.list_sources_with_counts() == [("podcast", 1)]
    assert [note.note_id for note in repo.list_by_source("Podcast")] == [note_id]


def test_blank_source_is_not_listed_as_note() -> None:
    """list_by_source пропускает болванку и видит только реальные заметки."""
    repo, _ = repo_for([note_row("1", "Blog", "a")])
    repo.add_source("Blog")

    notes = repo.list_by_source("Blog")

    assert [note.note_id for note in notes] == ["1"]


def test_rename_and_delete_source_include_blank_rows() -> None:
    """Болванка переименовывается и удаляется вместе с источником."""
    repo, _ = repo_for([])
    repo.add_source("Podcast")

    assert repo.rename_source("podcast", "Radio") == 0
    assert repo.list_sources() == ["Radio"]

    assert repo.delete_source("Radio") == 0
    assert repo.list_sources() == []


# --- Переименование и удаление источника ---


def test_rename_source_changes_all_rows_and_resorts() -> None:
    """rename_source меняет SOURCE во всех строках и пересортировывает лист."""
    rows = [
        note_row("1", "Blog", "Zebra"),
        note_row("2", "blog", "apple"),
        note_row("3", "YouTube", "a"),
    ]
    repo, sheet = repo_for(rows)

    updated = repo.rename_source("BLOG", "Podcast")

    assert updated == 2
    data = rows_by_header(sheet)
    assert [row[SOURCE_HEADER] for row in data] == ["Podcast", "Podcast", "YouTube"]
    assert [row[TITLE_HEADER] for row in data] == ["apple", "Zebra", "a"]
    assert repo.list_sources() == ["Podcast", "YouTube"]


def test_rename_source_rejects_empty_name() -> None:
    repo, _ = repo_for([note_row("1", "Blog", "a")])
    with pytest.raises(ValueError):
        repo.rename_source("Blog", "   ")


def test_rename_source_unknown_returns_zero() -> None:
    repo, sheet = repo_for([note_row("1", "Blog", "a")])

    assert repo.rename_source("Nowhere", "Else") == 0
    assert rows_by_header(sheet)[0][SOURCE_HEADER] == "Blog"


def test_delete_source_removes_only_its_rows() -> None:
    """delete_source удаляет все строки источника и не съезжает на чужие."""
    rows = [
        note_row("1", "Blog", "a"),
        note_row("2", "YouTube", "b"),
        note_row("3", "blog", "c"),
        note_row("4", "YouTube", "d"),
    ]
    repo, sheet = repo_for(rows)

    deleted = repo.delete_source("BLOG")

    assert deleted == 2
    data = rows_by_header(sheet)
    assert [row[ID_HEADER] for row in data] == ["2", "4"]
    assert repo.list_sources() == ["YouTube"]


def test_delete_source_unknown_returns_zero() -> None:
    repo, sheet = repo_for([note_row("1", "Blog", "a")])

    assert repo.delete_source("Nowhere") == 0
    assert len(sheet.rows) == 2


# --- Note ---


def test_update_cell_by_header() -> None:
    repo, _ = repo_for([note_row("1", "Blog", "Old")])

    repo.update_cell("1", COMMENT_HEADER, "new comment")

    note = repo.get("1")
    assert note is not None
    assert note.field(NOTES_HEADERS, COMMENT_HEADER) == "new comment"


def test_update_cell_unknown_header_raises() -> None:
    repo, _ = repo_for([note_row("1", "Blog", "Old")])
    with pytest.raises(ValueError):
        repo.update_cell("1", "NO_SUCH_COLUMN", "x")


def test_delete_removes_row() -> None:
    repo, sheet = repo_for([note_row("1", "Blog", "a"), note_row("2", "Blog", "b")])

    repo.delete("1")

    assert len(sheet.rows) == 2  # шапка + одна заметка
    assert repo.get("1") is None


def test_prompt_row_is_not_a_note() -> None:
    repo, _ = repo_for([note_row("1", "Blog", "a"), ["", "", "AI_PROMT", "rules"]])
    assert [note.note_id for note in repo.list_notes()] == ["1"]


# --- Защита служебных колонок ---


@pytest.mark.parametrize(
    "header", [ID_HEADER, SOURCE_HEADER, TITLE_HEADER, SCREEN_HEADER]
)
def test_protected_columns_cannot_be_deleted(header: str) -> None:
    service, sheet = columns_for([])
    col_index = NOTES_HEADERS.index(header) + 1

    with pytest.raises(ValueError):
        service.delete(col_index)

    assert sheet.rows[0] == NOTES_HEADERS


def test_comment_column_is_protected() -> None:
    """COMMENT — последняя обязательная колонка, удалить её нельзя."""
    service, sheet = columns_for([])
    comment_col = NOTES_HEADERS.index(COMMENT_HEADER) + 1

    assert comment_col in service.protected_columns()
    with pytest.raises(ValueError):
        service.delete(comment_col)
    assert sheet.rows[0][-1] == COMMENT_HEADER


def test_comment_stays_last_after_reorder() -> None:
    """COMMENT — всегда последняя, даже после перестановки пользовательских."""
    service, sheet = columns_for([])
    service.add("PRICE")
    width = len(sheet.rows[0])

    order = list(range(1, width + 1))
    price_col = sheet.rows[0].index("PRICE") + 1
    order.remove(price_col)
    order.insert(3, price_col)  # PRICE сразу после TITLE
    service.reorder(order)

    assert sheet.rows[0][-1] == COMMENT_HEADER
    assert sheet.rows[0][3] == "PRICE"


@pytest.mark.parametrize("header", [ID_HEADER, SOURCE_HEADER, TITLE_HEADER])
def test_leading_columns_cannot_be_renamed(header: str) -> None:
    service, _ = columns_for([])
    col_index = NOTES_HEADERS.index(header) + 1

    with pytest.raises(ValueError):
        service.rename(col_index, "Other")


def test_screen_can_be_renamed_but_not_deleted() -> None:
    """
    SCREEN переименовать можно, но удалить — нет.

    После переименования колонки по имени ``SCREEN`` уже не найти, поэтому
    защита должна опознать её по штатной позиции (``screen_slot_index``).
    """
    service, sheet = columns_for([])
    screen_col = NOTES_HEADERS.index(SCREEN_HEADER) + 1
    width_before = len(sheet.rows[0])

    service.rename(screen_col, "SCREENSHOT")
    assert sheet.rows[0][screen_col - 1] == "SCREENSHOT"
    assert screen_col in service.protected_columns()

    with pytest.raises(ValueError):
        service.delete(screen_col)
    assert len(sheet.rows[0]) == width_before
    assert sheet.rows[0][screen_col - 1] == "SCREENSHOT"


def test_user_column_is_inserted_before_comment() -> None:
    service, sheet = columns_for([])

    service.add("PRICE")

    assert sheet.rows[0] == [
        "ID",
        "SOURCE",
        "TITLE",
        "DESCRIPTION",
        "URL",
        "SCREEN",
        "PRICE",
        "COMMENT",
    ]


def test_user_column_can_be_renamed_and_deleted() -> None:
    service, sheet = columns_for([])
    service.add("PRICE")
    price_col = sheet.rows[0].index("PRICE") + 1

    service.rename(price_col, "COST")
    assert sheet.rows[0][price_col - 1] == "COST"

    service.delete(price_col)
    assert COMMENT_HEADER in sheet.rows[0]
    assert "COST" not in sheet.rows[0]
    assert sheet.rows[0][-1] == COMMENT_HEADER


def test_reorder_rejects_moving_leading_columns() -> None:
    """Полный порядок не может двигать ID, SOURCE, TITLE из начала."""
    service, _ = columns_for([])
    width = len(NOTES_HEADERS)

    order = list(range(1, width + 1))
    order[0], order[1] = order[1], order[0]  # SOURCE вперёд ID
    with pytest.raises(ValueError):
        service.reorder(order)


def test_reorder_keeps_data() -> None:
    """Перестановка полного порядка переносит данные ячеек вместе с шапкой."""
    service, sheet = columns_for([note_row("1", "Blog", "Note", "Desc")])
    width = len(NOTES_HEADERS)

    order = list(range(1, width + 1))
    order[3], order[4] = order[4], order[3]  # URL и DESCRIPTION
    header = service.reorder(order)

    assert header[:3] == [ID_HEADER, SOURCE_HEADER, TITLE_HEADER]
    assert header[-1] == COMMENT_HEADER
    row = dict(zip(sheet.rows[0], sheet.rows[1]))
    assert row[TITLE_HEADER] == "Note"
    assert row["DESCRIPTION"] == "Desc"


def test_reorder_middle_rejects_bad_permutation() -> None:
    """Повторы и чужие номера в порядке отклоняются."""
    service, _ = columns_for([])

    with pytest.raises(ValueError):
        service.reorder_middle([1, 1, 2])
    with pytest.raises(ValueError):
        service.reorder_middle([1, 2])


def test_reorder_middle_keeps_leading_and_comment() -> None:
    """Перестановка средних колонок сохраняет ID/SOURCE/TITLE и COMMENT."""
    service, sheet = columns_for(
        [note_row("1", "Blog", "Note", "Desc", "https://x", "fid", "c")]
    )

    header = service.reorder_middle([2, 1, 3])

    assert header[:3] == [ID_HEADER, SOURCE_HEADER, TITLE_HEADER]
    assert header[-1] == COMMENT_HEADER
    assert header[3:6] == ["URL", "DESCRIPTION", SCREEN_HEADER]
    row = dict(zip(sheet.rows[0], sheet.rows[1]))
    assert row["DESCRIPTION"] == "Desc"
    assert row["URL"] == "https://x"
    assert row[SCREEN_HEADER] == "fid"
    assert row[COMMENT_HEADER] == "c"
    assert row[TITLE_HEADER] == "Note"


def test_short_rows_are_padded_to_header_width() -> None:
    """Рваная строка (нет правых ячеек) не ломает карточку и сортировку."""
    repo, sheet = repo_for([["1", "Blog", "Only name"]])
    sheet.rows.append(["2", "Blog", "Another"])

    repo.sort_notes()

    note = repo.get("2")
    assert note is not None
    assert isinstance(repo, NoteRepository)
    assert note.values[-1] == ""


# --- Кэш: одиночные записи не заставляют читать лист заново ---


class _CountingSheet:
    """Обёртка над FakeSheet: считает широкие чтения листа."""

    def __init__(self, sheet: object) -> None:
        self._sheet = sheet
        self.reads = 0

    def __getattr__(self, name: str) -> object:
        return getattr(self._sheet, name)

    def get(self, range_name: str, value_render_option: str = "") -> list[list[str]]:
        self.reads += 1
        return self._sheet.get(range_name)

    def get_all_values(self) -> list[list[str]]:
        self.reads += 1
        return self._sheet.get_all_values()


def test_create_and_update_reuse_cached_snapshot() -> None:
    """``create`` и ``update_cell_at`` обновляют кэш без повторного чтения."""
    repo, sheet = repo_for([note_row("1", "Blog", "Post")])
    counting = _CountingSheet(sheet)
    repo._sheet = counting

    note_id = repo.create("Blog", "Fresh")
    reads_after_create = counting.reads

    note = repo.get(note_id)
    assert note is not None
    assert note.value(3) == "Fresh"
    assert repo.headers() == list(NOTES_HEADERS)
    assert counting.reads == reads_after_create

    row_index = repo.find_row(note_id)
    assert row_index is not None
    comment_col = NOTES_HEADERS.index(COMMENT_HEADER) + 1
    repo.update_cell_at(row_index, comment_col, "After edit")

    updated = repo.get(note_id)
    assert updated is not None
    assert updated.value(comment_col) == "After edit"
    assert repo.headers() == list(NOTES_HEADERS)
    assert counting.reads == reads_after_create
