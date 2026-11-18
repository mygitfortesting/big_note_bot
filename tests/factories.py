"""Фабрики тестовых данных: заметки, репозитории на FakeSheet, карточки."""

from __future__ import annotations

from typing import Sequence

from services.sheets import ColumnService, NoteRepository, invalidate_cache
from tests.fakes import FakeSheet, make_sheet


def note_row(
    note_id: str,
    source: str,
    name: str,
    description: str = "",
    url: str = "",
    screen: str = "",
    comment: str = "",
) -> list[str]:
    """Строка заметки в порядке базовой шапки (ID..COMMENT)."""
    return [note_id, source, name, description, url, screen, comment]


def sheet_with_notes(
    rows: Sequence[Sequence[str]],
    headers: Sequence[str] | None = None,
) -> FakeSheet:
    """FakeSheet с базовой (или переданной) шапкой и строками заметок."""
    if headers is not None:
        return FakeSheet(headers=list(headers), rows=[list(r) for r in rows])
    return make_sheet([list(row) for row in rows])


def repo_for(
    rows: Sequence[Sequence[str]] = (),
    user_id: int = 1,
    headers: Sequence[str] | None = None,
) -> tuple[NoteRepository, FakeSheet]:
    """
    Репозиторий поверх готового FakeSheet (dependency injection вместо сети).

    Кэш сбрасывается, чтобы тесты не видели данные друг друга.
    """
    sheet = sheet_with_notes(rows, headers)
    invalidate_cache()
    return NoteRepository(user_id=user_id, sheet=sheet), sheet


def columns_for(
    rows: Sequence[Sequence[str]] = (),
    user_id: int = 1,
    headers: Sequence[str] | None = None,
) -> tuple[ColumnService, FakeSheet]:
    """Сервис колонок поверх FakeSheet (шапка в первой строке)."""
    sheet = sheet_with_notes(rows, headers)
    invalidate_cache()
    return ColumnService(user_id=user_id, sheet=sheet), sheet


def rows_by_header(sheet: FakeSheet) -> list[dict[str, str]]:
    """Строки данных в виде словарей «колонка -> значение» (удобно сравнивать)."""
    if not sheet.rows:
        return []
    headers = sheet.rows[0]
    return [
        {
            header: (row[index] if index < len(row) else "")
            for index, header in enumerate(headers)
        }
        for row in sheet.rows[1:]
    ]
