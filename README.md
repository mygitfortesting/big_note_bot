# Big Note Bot

Telegram-бот (`@big_note_bot`) для хранения заметок, ссылок и скриншотов
в Google Sheets. Каждая заметка привязана к источнику (`SOURCE`), например
`YouTube` или `Blog`, и хранит заголовок (`TITLE`), описание, ссылку,
скриншот и комментарий.

Администратор (`ADMIN_ID`) работает с основным листом `MY_NOTES`. Остальным
пользователям при первом `/start` автоматически создаются личные вкладки
`Notes_{user_id}` и строка в служебном листе `Users` — данные разных
пользователей не пересекаются.

## Стек

- Python 3.13
- `aiogram 3` — Telegram Bot API
- `gspread` — Google Sheets
- `pytest` — тесты, `Ruff` — линт и форматирование, `mypy` — типы

## Локальный запуск

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

Перед запуском:

1. Создайте `.env` в корне и заполните:
   - `BOT_TOKEN` — токен бота от [@BotFather](https://t.me/BotFather);
   - `SPREADSHEET_ID` — ID Google-таблицы;
   - `ADMIN_ID` — Telegram ID администратора.
2. Положите `credentials.json` сервисного аккаунта в корень репозитория
   и выдайте этому аккаунту доступ к таблице.

## Проверка качества

```powershell
pytest tests -m "not integration" -q
pre-commit run --all-files
```

Инструкция для пользователя — в [USER_GUIDE.md](USER_GUIDE.md).
