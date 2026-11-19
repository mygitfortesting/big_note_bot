"""ColumnService: защита служебных колонок, добавление, переименование, порядок."""

from __future__ import annotations

import pytest

from config import NOTES_HEADERS
from services.sheets import (
    COMMENT_HEADER,
    ID_HEADER,
    SCREEN_HEADER,
    SOURCE_HEADER,
    TITLE_HEADER,
    screen_slot_index,
)
from tests.factories import columns_for, note_row


@pytest.mark.parametrize(
    "header", [ID_HEADER, SOURCE_HEADER, TITLE_HEADER, SCREEN_HEADER]
)
def test_protected_columns_cannot_be_deleted(header: str) -> None:
    """ID, SOURCE, TITLE и SCREEN удалить нельзя — ValueError."""
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
    """Служебные первые колонки нельзя переименовать."""
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
    """Новая колонка вставляется перед COMMENT, а не в конец."""
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
    """Пользовательскую колонку можно переименовать и удалить."""
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


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        pytest.param(
            ["ID", "SOURCE", "TITLE", "DESC", "URL", "SCREEN", "COMMENT"],
            6,
            id="by-name",
        ),
        pytest.param(
            ["ID", "SOURCE", "TITLE", "DESC", "URL", "SCREENSHOT", "COMMENT"],
            6,
            id="before-comment",
        ),
        pytest.param(
            ["ID", "SOURCE", "TITLE", "DESC", "URL", "SCREENSHOT", "EXTRA", "COMMENT"],
            7,
            id="before-comment-with-extra",
        ),
        pytest.param(
            ["ID", "SOURCE", "TITLE", "DESC", "URL", "SCREENSHOT"],
            6,
            id="fallback-base-position",
        ),
    ],
)
def test_screen_slot_index_resolution(headers: list[str], expected: int) -> None:
    """screen_slot_index: имя SCREEN -> позиция перед COMMENT -> базовая позиция."""
    assert screen_slot_index(headers) == expected


def test_protected_columns_accepts_cached_headers() -> None:
    """protected_columns может принимать уже прочитанные headers."""
    service, _ = columns_for([])
    headers = service.headers()
    assert service.protected_columns(headers) == service.protected_columns()


test_protected_columns_accepts_preloaded_headers = (
    test_protected_columns_accepts_cached_headers
)
