# Big Note Bot

Telegram-бот (`@big_note_bot`) для хранения заметок, ссылок и скриншотов
в Google Sheets. Каждая заметка привязана к источнику (`SOURCE`), например
`YouTube` или `Blog`, и хранит заголовок (`TITLE`), описание, ссылку,
скриншот и комментарий.

Администратор (`ADMIN_ID`) работает с основным листом `MY_NOTES`. Остальным
пользователям при первом `/start` автоматически создаются личные вкладки
`Notes_{user_id}` и строка в служебном листе `Users` — данные разных
пользователей не пересекаются.

## Возможности

- **➕ Add note** — мастер создания заметки: выбор или создание источника,
  ввод `TITLE`, пошаговое заполнение остальных колонок
  (`⏭ Next`, `✅ Done`, `➕ Add another note`).
- **✨ AI Note** — умный разбор скриншота, ссылки или пересланного текста:
  бот предлагает черновик (`✅ Save`, `🗂 Change source`, `✏️ Edit title`)
  и пишет его в лист только по подтверждению.
- **🗂 Sources** — нумерованный список источников: открыть заметки, показать
  все заметки сразу, добавить, переименовать или удалить источник.
- **📊 Columns** — управление пользовательскими колонками: добавление,
  переименование, удаление и изменение порядка.
- **Быстрый поиск** — название заметки текстом в чат открывает карточку
  или список совпадений.
- **🗑 Удаление с подтверждением** — заметка удаляется только после явного
  «✅ Yes, delete».
- **🔄 Sync / `/refresh`** — мгновенная синхронизация после ручных правок
  в Google Таблице: кэш сбрасывается и лист перечитывается сразу.

### ✨ AI Note

Разбор сообщения в черновик собирается динамически из актуальной шапки листа:
все колонки, кроме `ID`, `SOURCE`, `TITLE`, `SCREEN` и `COMMENT`, модель
заполняет по смыслу (переименованная `SCREEN` узнаётся по штатной позиции).
Свои правила разбора можно задать прямо в таблице — в служебной строке
`AI_PROMT` (см. [USER_GUIDE.md](USER_GUIDE.md)); бот подмешивает этот текст
в системный промпт.

## Стек

- Python 3.13
- `aiogram 3` — Telegram Bot API
- `gspread` — Google Sheets
- `openai` + `requests` — ✨ AI Note (разбор модели и загрузка страниц по ссылке)
- `pytest` — тесты, `Ruff` — линт и форматирование, `mypy` — типы

## Структура проекта

```text
bot.py                    точка входа: общие команды, sync_table, сборка роутеров, polling
config.py                 .env, Bot/Dispatcher, доступ к Google Sheets, настройки AI
handlers/
  notes_handler.py        публичный API пакета, карточка заметки, поиск, удаление
  wizard_handler.py       мастер создания заметки
  sources_handler.py      источники: список, заметки, переименование, удаление
  ai_note_handler.py      ✨ AI Note: приём сообщения, черновик, сохранение
  columns_handler.py      управление колонками
  notes_common.py         общие состояния (NoteStates), NoteContext и хелперы
services/
  sheets.py               единственный слой gspread: SheetCache, NoteRepository, ColumnService
  ai_parser.py            разбор сообщения в AiNoteDraft, промпт, загрузка страницы
  callback.py             реестр Cmd и Cb (callback_data)
  buttons.py              тексты кнопок и сообщений
  keyboards.py            сборка InlineKeyboardMarkup
  telegram_io.py          вывод в Telegram и user_lock(user_id)
  text_utils.py           экранирование, разбиение длинных сообщений, карточка
  healthcheck.py          HTTP-сервер здоровья для Render
tests/                    офлайн-тесты на FakeSheet и FakeMessage (см. TEST.md)
```

Порядок роутеров важен: общие команды ➔ ✨ AI Note ➔ заметки ➔ колонки.
`ai_router` подключается только в `bot.register_routers`, иначе состояния
`AiStates` не перехватили бы сообщения раньше текстового поиска.

## Локальный запуск

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

Перед запуском:

1. Создайте `.env` в корне и заполните обязательные переменные:
   - `BOT_TOKEN` — токен бота от [@BotFather](https://t.me/BotFather);
   - `SPREADSHEET_ID` — ID Google-таблицы;
   - `ADMIN_ID` — Telegram ID администратора.
2. Положите `credentials.json` сервисного аккаунта в корень репозитория
   и выдайте этому аккаунту доступ к таблице (путь можно переопределить
   через `GOOGLE_CREDENTIALS_FILE`).

## Конфигурация / переменные окружения (`.env`)

| Переменная | Обязательна | По умолчанию | Назначение |
| --- | --- | --- | --- |
| `BOT_TOKEN` | да | — | токен Telegram-бота |
| `SPREADSHEET_ID` | да | — | ID Google-таблицы с заметками |
| `ADMIN_ID` | да | `0` | Telegram ID администратора (`MY_NOTES`) |
| `GOOGLE_CREDENTIALS_FILE` | нет | `credentials.json` | путь к ключу сервисного аккаунта |
| `AI_API_KEY` | нет | пусто | ключ AI-провайдера (OpenAI-совместимый API) |
| `AI_BASE_URL` | нет | `https://openrouter.ai` | эндпоинт AI-провайдера |
| `AI_MODEL` | нет | `openrouter/free` | модель для разбора заметок |
| `PORT` | нет | `10000` | порт health-check сервера (Render) |

**AI-режим опционален.** Без `AI_API_KEY` бот работает в штатном режиме,
а кнопка `✨ AI Note` объясняет, что ключ не настроен. С ключом становится
доступен умный разбор скриншотов, ссылок и текста; для скриншотов нужна
модель с поддержкой изображений (Vision) — иначе бот подскажет заменить
`AI_MODEL`.

## Проверка качества

```powershell
pytest tests -m "not integration" -q
pre-commit run --all-files
```

Инструкция для пользователя — в [USER_GUIDE.md](USER_GUIDE.md),
реестр автотестов — в [TEST.md](TEST.md).
