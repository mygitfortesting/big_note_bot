"""Слой данных заметок: сортировка, источники, ID, поиск, удаление и кэш."""

from __future__ import annotations

import pytest

from config import NOTES_HEADERS
from services.sheets import (
    COMMENT_HEADER,
    ID_HEADER,
    SOURCE_HEADER,
    TITLE_HEADER,
    NoteRepository,
    column_letter,
    pad_row,
)
from tests.factories import note_row, repo_for, reset_sheet_cache, rows_by_header
from tests.fakes import FakeSheet

# --- Чистые функции ---


def test_column_letter() -> None:
    """Номер колонки -> буква Sheets (1 -> A, 27 -> AA)."""
    assert column_letter(1) == "A"
    assert column_letter(26) == "Z"
    assert column_letter(27) == "AA"


def test_pad_row_fills_short_rows() -> None:
    """Рваная строка добивается пустыми ячейками до ширины шапки."""
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
    """Шапка всегда остаётся первой строкой."""
    repo, sheet = repo_for([note_row("1", "B", "x"), note_row("2", "A", "y")])

    repo.sort_notes()

    assert sheet.rows[0] == NOTES_HEADERS


def test_short_rows_are_padded_to_header_width() -> None:
    """Рваная строка (нет правых ячеек) не ломает карточку и сортировку."""
    repo, sheet = repo_for([["1", "Blog", "Only name"]])
    sheet.rows.append(["2", "Blog", "Another"])

    repo.sort_notes()

    note = repo.get("2")
    assert note is not None
    assert isinstance(repo, NoteRepository)
    assert note.values[-1] == ""


# --- Создание заметок ---


def test_create_adds_note_and_sorts() -> None:
    """Созданная заметка попадает в лист и встаёт по SOURCE/TITLE."""
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
    """Две подряд созданные заметки получают разные ID."""
    repo, _ = repo_for([])

    first = repo.create("Blog", "One")
    second = repo.create("Blog", "Two")

    assert first != second


# --- Источники ---


def test_list_sources_unique_sorted() -> None:
    """Источники уникальны и отсортированы без учёта регистра."""
    rows = [
        note_row("1", "YouTube", "b"),
        note_row("2", "blog", "c"),
        note_row("3", "Blog", "a"),
        note_row("4", "", "no source"),
    ]
    repo, _ = repo_for(rows)

    assert repo.list_sources() == ["blog", "YouTube"]


def test_list_by_source_sorted_by_title() -> None:
    """Заметки источника идут по TITLE без учёта регистра."""
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
    """Регистр имени источника не мешает выборке."""
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


@pytest.mark.parametrize(
    ("query", "expected_ids"),
    [
        pytest.param("  yandex ", ["1"], id="exact-wins"),
        pytest.param("YAND", ["1", "2"], id="partial-fallback"),
        pytest.param("   ", [], id="empty-query"),
    ],
)
def test_find_by_title(query: str, expected_ids: list[str]) -> None:
    """Поиск по TITLE: точное совпадение приоритетнее частичного."""
    rows = [
        note_row("1", "Blog", "Yandex"),
        note_row("2", "Blog", "Yandex Zen"),
        note_row("3", "Blog", "FlexiTech"),
    ]
    repo, _ = repo_for(rows)

    found = repo.find_by_title(query)

    assert [note.note_id for note in found] == expected_ids


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


def test_source_name_validation_rejects_empty() -> None:
    """Пустое имя источника не принимается ни при добавлении, ни при переименовании."""
    repo, _ = repo_for([note_row("1", "Blog", "a")])

    with pytest.raises(ValueError):
        repo.add_source("   ")
    with pytest.raises(ValueError):
        repo.rename_source("Blog", "   ")
    assert repo.list_sources() == ["Blog"]


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
    assert [row[SOURCE_HEADER] for row in data] == [
        "Podcast",
        "Podcast",
        "YouTube",
    ]
    assert [row[TITLE_HEADER] for row in data] == ["apple", "Zebra", "a"]
    assert repo.list_sources() == ["Podcast", "YouTube"]


def test_unknown_source_returns_zero() -> None:
    """Неизвестный источник: 0 изменённых заметок и 0 удалённых строк."""
    repo, sheet = repo_for([note_row("1", "Blog", "a")])

    assert repo.rename_source("Nowhere", "Else") == 0
    assert repo.delete_source("Nowhere") == 0
    assert len(sheet.rows) == 2
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


# --- Заметки: удаление и служебные строки ---


def test_delete_removes_row() -> None:
    """delete удаляет строку заметки, остальные остаются."""
    repo, sheet = repo_for([note_row("1", "Blog", "a"), note_row("2", "Blog", "b")])

    repo.delete("1")

    assert len(sheet.rows) == 2  # шапка + одна заметка
    assert repo.get("1") is None


def test_prompt_row_is_not_a_note() -> None:
    """Служебная строка AI_PROMT не считается заметкой."""
    repo, _ = repo_for([note_row("1", "Blog", "a"), ["", "", "AI_PROMT", "rules"]])
    assert [note.note_id for note in repo.list_notes()] == ["1"]


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


# --- Устаревший снимок в кэше: locate дочитывает лист ---


def test_locate_rereads_sheet_when_cached_snapshot_is_stale() -> None:
    """ID нет в кэше, но есть в листе — locate сбрасывает кэш и находит его."""
    repo, sheet = repo_for([note_row("1", "Blog", "Post")])
    # Кэш уже заполнен снимком без новой строки (имитация задержки репликации)
    assert repo.headers() == list(NOTES_HEADERS)
    sheet.rows.append(note_row("2", "Blog", "Fresh"))

    found = repo.locate("2")

    assert found is not None
    row_index, note = found
    assert note.field(NOTES_HEADERS, TITLE_HEADER) == "Fresh"
    assert sheet.rows[row_index - 1][0] == "2"
    # Повторное чтение уже с кэшем: второго запроса к листу нет
    assert repo.get("2") is not None


def test_locate_rereads_sheet_when_id_appears_in_new_sheet_object() -> None:
    """
    gspread пересоздал объект листа: ключ кэша стабилен (по title).

    Снимок старого объекта листа не должен прятать заметку, созданную
    через новый объект того же листа.
    """
    reset_sheet_cache()
    sheet = FakeSheet(headers=list(NOTES_HEADERS), rows=[note_row("1", "Blog", "a")])
    setattr(sheet, "title", "Notes_1")
    repo, _ = repo_for([], user_id=1)
    repo._sheet = sheet
    assert [note.note_id for note in repo.list_notes()] == ["1"]

    recreated = FakeSheet(headers=list(NOTES_HEADERS), rows=sheet.rows)
    setattr(recreated, "title", "Notes_1")
    fresh = NoteRepository(user_id=1, sheet=recreated)

    assert [note.note_id for note in fresh.list_notes()] == ["1"]
