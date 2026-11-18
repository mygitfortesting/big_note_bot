"""Тесты слоя данных: сортировка, источники, ID и защита служебных колонок."""

from __future__ import annotations

import pytest

from config import NOTES_HEADERS
from services.sheets import (
    COMMENT_HEADER,
    ID_HEADER,
    NAME_HEADER,
    SCREEN_HEADER,
    SOURCE_HEADER,
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


def test_sort_notes_by_source_then_name() -> None:
    """Сортировка: сначала SOURCE (без регистра), затем NAME (без регистра)."""
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
    assert data[0][NAME_HEADER] == "Gamma"


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
    assert created.field(NOTES_HEADERS, NAME_HEADER) == "Aaa"
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


def test_list_by_source_sorted_by_name() -> None:
    rows = [
        note_row("1", "Blog", "Zebra"),
        note_row("2", "Blog", "apple"),
        note_row("3", "YouTube", "Mango"),
    ]
    repo, _ = repo_for(rows)

    names = [
        note.field(NOTES_HEADERS, NAME_HEADER) for note in repo.list_by_source("blog")
    ]
    assert names == ["apple", "Zebra"]


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
    assert [row[NAME_HEADER] for row in data] == ["apple", "Zebra", "a"]
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
    "header", [ID_HEADER, SOURCE_HEADER, NAME_HEADER, SCREEN_HEADER]
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
    order.insert(3, price_col)  # PRICE сразу после NAME
    service.reorder(order)

    assert sheet.rows[0][-1] == COMMENT_HEADER
    assert sheet.rows[0][3] == "PRICE"


@pytest.mark.parametrize("header", [ID_HEADER, SOURCE_HEADER, NAME_HEADER])
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
        "NAME",
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
    service, _ = columns_for([])
    width = len(NOTES_HEADERS)

    order = list(range(1, width + 1))
    order[0], order[1] = order[1], order[0]  # SOURCE вперёд ID
    with pytest.raises(ValueError):
        service.reorder(order)


def test_reorder_rejects_comment_not_last() -> None:
    service, _ = columns_for([])
    width = len(NOTES_HEADERS)
    comment_col = NOTES_HEADERS.index(COMMENT_HEADER) + 1

    order = [i for i in range(1, width + 1) if i != comment_col]
    order.insert(3, comment_col)
    with pytest.raises(ValueError):
        service.reorder(order)


def test_reorder_keeps_data() -> None:
    service, sheet = columns_for([note_row("1", "Blog", "Note", "Desc")])

    width = len(NOTES_HEADERS)
    order = list(range(1, width + 1))
    # DESCRIPTION (4) и URL (5) меняются местами
    order[3], order[4] = order[4], order[3]

    service.reorder(order)

    headers = sheet.rows[0]
    assert headers[:3] == [ID_HEADER, SOURCE_HEADER, NAME_HEADER]
    assert headers[-1] == COMMENT_HEADER
    row = dict(zip(headers, sheet.rows[1]))
    assert row[NAME_HEADER] == "Note"
    assert row["DESCRIPTION"] == "Desc"


def test_short_rows_are_padded_to_header_width() -> None:
    """Рваная строка (нет правых ячеек) не ломает карточку и сортировку."""
    repo, sheet = repo_for([["1", "Blog", "Only name"]])
    sheet.rows.append(["2", "Blog", "Another"])

    repo.sort_notes()

    note = repo.get("2")
    assert note is not None
    assert isinstance(repo, NoteRepository)
    assert note.values[-1] == ""
