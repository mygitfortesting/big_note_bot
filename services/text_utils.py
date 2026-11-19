"""
Чистые функции форматирования текста для Telegram.

Ничего не знают ни про сеть, ни про aiogram — поэтому их легко тестировать
(см. tests/test_notes.py). Всё, что связано с отправкой сообщений, —
в telegram_io.py.
"""

from __future__ import annotations

import html
import re
from typing import Sequence

#: Лимит текста одного сообщения Telegram.
MESSAGE_LIMIT = 4096
#: Служебные колонки, которые НЕ выводятся в карточке заметки.
_HIDDEN_CARD_HEADERS = {"ID", "SCREEN"}
_EMPTY_VALUES = {"", "—", "-"}

CARD_BLOCK_SEPARATOR = "\n\n"


def escape_html(text: str) -> str:
    """
    Экранирует пользовательские данные для parse_mode="HTML".
    Без этого символы < > & из таблицы ломают разметку Telegram
    (например, «<b>» без закрывающего тега -> Bad Request: can't parse entities).
    """
    return html.escape(str(text), quote=False)


def plain_text(text: str, limit: int = 3000) -> str:
    """Текст без HTML-разметки — запасной вариант, если HTML не принят Telegram."""
    return escape_html(text)[:limit]


def split_messages(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """
    Делит длинный текст на части не длиннее ``limit`` по границам строк.

    Telegram не примет сообщение длиннее 4096 символов, поэтому длинные
    списки заметок и многострочные карточки отправляются частями
    (``telegram_io.send_parts``). Строка, которая сама длиннее лимита,
    режется жёстко — иначе цикл не завершится.
    """
    text = text or ""
    if len(text) <= limit:
        return [text] if text else []
    parts: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit:
            parts.append(current)
            current = line
        else:
            current += line
    if current:
        parts.append(current)
    return parts


def _is_filled(value: str | None) -> bool:
    """Заполнена ли ячейка (не пустая строка, не прочерк, не пробелы)."""
    return (value or "").strip() not in _EMPTY_VALUES


#: «Пустая» строка фиксированной ширины: символы Брайля Telegram считает
#: шириной, поэтому пузырь не сжимается и ряд из двух кнопок не обрезается.
WIDTH_SPACER = "\u2800" * 26
#: Минимальная ширина пузыря с кнопками (длина WIDTH_SPACER).
MIN_BUBBLE_WIDTH = len(WIDTH_SPACER)
_TAG_RE = re.compile(r"<[^>]+>")


def _max_line_width(text: str) -> int:
    """Самая длинная строка текста без HTML-тегов (ширина пузыря)."""
    plain = _TAG_RE.sub("", text or "")
    lines = [line.rstrip() for line in plain.splitlines()]
    return max((len(line) for line in lines), default=0)


def with_min_width(text: str, min_width: int = MIN_BUBBLE_WIDTH) -> str:
    """
    Добивает короткий экран с кнопками до минимальной ширины пузыря.

    Если текст и так не уже распорки, он возвращается без изменений;
    перед распоркой всегда ровно один перевод строки (``\n``), без
    лишней пустой строки внизу сообщения.
    """
    target = (text or "").rstrip("\n")
    if target.endswith(WIDTH_SPACER):
        target = target[: -len(WIDTH_SPACER)].rstrip("\n")
    if _max_line_width(target) >= min_width:
        return target
    return f"{target}\n{WIDTH_SPACER}"


def note_card_lines(
    headers: Sequence[str],
    values: Sequence[str],
    title: str | None = None,
    spacer: bool = True,
) -> list[str]:
    """
    Строки карточки заметки: ТОЛЬКО заполненные колонки.

    Пустые ячейки пропускаются, поэтому у каждого источника в карточке видны
    лишь его релевантные поля. Служебные ``ID`` и ``SCREEN`` не выводятся
    (ID нужен для кнопок, а скриншот отправляется отдельным фото).

    Возвращает список блоков с HTML-разметкой; хендлер склеивает их через
    пустую строку (``"\n\n"``), чтобы поля карточки не слипались. Если
    ``spacer`` включён, последний элемент — отступ из символов Брайля:
    он держит ширину пузыря (в длинном списке заметок он не нужен).
    """
    lines: list[str] = []
    for index, header in enumerate(headers):
        if header.strip().upper() in _HIDDEN_CARD_HEADERS:
            continue
        value = values[index] if index < len(values) else ""
        if not _is_filled(value):
            continue
        label = escape_html(header)
        content = escape_html(value.strip())
        lines.append(f"<b>{label}:</b> {content}")
    if title:
        lines.insert(0, f"<b>{escape_html(title)}</b>")
    # Отступ отдельным блоком: не склеивается с последним полем карточки.
    if spacer:
        lines.append(WIDTH_SPACER)
    return lines


def note_card_text(
    headers: Sequence[str],
    values: Sequence[str],
    title: str | None = None,
) -> str:
    """Готовая карточка: блоки через пустую строку, в конце — отступ."""
    return CARD_BLOCK_SEPARATOR.join(note_card_lines(headers, values, title))


#: Визуальный разделитель между заметками в длинном списке.
NOTE_SEPARATOR = "\n\n━━━━━━━━━━\n\n"


def notes_digest_text(source: str, count: int, cards: Sequence[str]) -> str:
    """
    Полный текст всех заметок источника.

    ``cards`` — уже готовые карточки (только заполненные поля, без ID/SCREEN
    и без пустых распорок) в порядке вывода; они разделяются видимой чертой,
    перед каждой карточкой — её порядковый номер.
    """
    head = f"📂 <b>{escape_html(source)}</b> — {count} note(s):"
    if not cards:
        return with_min_width(head)
    numbered = [f"<b>{index}.</b>\n{card}" for index, card in enumerate(cards, start=1)]
    return head + NOTE_SEPARATOR + NOTE_SEPARATOR.join(numbered)
