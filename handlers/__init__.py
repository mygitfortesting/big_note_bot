"""Роутеры бота: по одному модулю на раздел (заметки, колонки)."""

from handlers import columns_handler, notes_handler

__all__ = ["columns_handler", "notes_handler"]
