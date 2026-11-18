"""
Чистые функции форматирования текста для Telegram.

Ничего не знают ни про сеть, ни про aiogram — поэтому их легко тестировать
(см. tests/test_notes.py). Всё, что связано с отправкой сообщений, —
в telegram_io.py.
"""

from __future__ import annotations

import html
from typing import Sequence

#: Лимит текста одного сообщения Telegram.
MESSAGE_LIMIT = 4096
#: Служебные колонки, которые НЕ выводятся в карточке заметки.
_HIDDEN_CARD_HEADERS = {"ID", "SCREEN"}
_EMPTY_VALUES = {"", "—", "-"}


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


def note_card_lines(
    headers: Sequence[str],
    values: Sequence[str],
    title: str | None = None,
) -> list[str]:
    """
    Строки карточки заметки: ТОЛЬКО заполненные колонки.

    Пустые ячейки пропускаются, поэтому у каждого источника в карточке видны
    лишь его релевантные поля. Служебные ``ID`` и ``SCREEN`` не выводятся
    (ID нужен для кнопок, а скриншот отправляется отдельным фото).

    Возвращает список блоков с HTML-разметкой; хендлер склеивает их через
    пустую строку (``"\n\n"``), чтобы поля карточки не слипались.
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
    return lines


#: Разделитель блоков карточки: между заполненными полями — пустая строка.
CARD_BLOCK_SEPARATOR = "\n\n"


def note_card_text(
    headers: Sequence[str],
    values: Sequence[str],
    title: str | None = None,
) -> str:
    """Готовая карточка заметки: блоки разделены пустой строкой."""
    return CARD_BLOCK_SEPARATOR.join(note_card_lines(headers, values, title))
