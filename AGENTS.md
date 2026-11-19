# AGENTS.md — Правила проекта `Big Note Bot` (`@big_note_bot`)

Telegram-бот заметок (`Python 3.13`, `aiogram 3.x`, `gspread`, `OpenAI API`).

## 1. STRICT EXECUTION RULES
- **НИКОГДА не запускать команды в терминале** (`pytest`, `ruff`, `mypy`, `pip`, `git`, `pre-commit`). Все проверки запускает только пользователь.
- Не менять структуру папок, не трогать `.venv` и `.git`, не создавать временные скрипты.
- Работать **только с запрошенными файлами**, вносить правки точечно (in-place).
- После правки тестов указывать команду запуска (`pytest tests -m "not integration" -q`), не выполняя её.

## 2. ARCHITECTURE & DATA LAYER
- `bot.py` — `/start`, `/help`, `/table`, возврат в меню, роутеры, polling.
- `config.py` — `.env`, `Dispatcher`, доступ к Google Sheets, `notes_sheet_name(user_id)` (`MY_NOTES` для админа, `Notes_{user_id}` для остальных), `setup_user_sheets` (создаёт вкладку и строку в `Users`).
- `services/sheets.py` — `SheetCache` (TTL 300 с, stale-фоллбэк при `429`, ключ кэша — стабильный `sheet.id` → `sheet.title`, НЕ `id(sheet)`), `NoteRepository`, `ColumnService`, хелперы `headers_of`, `cell`, `pad_row`. Никаких вызовов `gspread` вне `sheets.py` и `config.py`.
- **Кэш и запись:** `NoteRepository.create`/`sort_notes`/`add_source` всегда кладут свежий отсортированный снимок в `SheetCache` (даже если кэш был пуст), а `locate` при промахе по ID один раз перечитывает лист с сервера — защита от задержки репликации Google Sheets. Тесты, работающие с разными `FakeSheet` без `id`/`title`, сбрасывают кэш через `tests.factories.reset_sheet_cache()`.
- `services/callback.py` — единый реестр `Cmd` (команды) и `Cb` (`callback_data`).
- `services/buttons.py` / `services/keyboards.py` — все тексты и `InlineKeyboardMarkup`.
- `services/telegram_io.py` — единый выход в Telegram (`ack_callback`, `alert`, `hide_callback_message`, `show_main_menu`, `send_parts`, `status_message`, `send_error_message`).
- `services/text_utils.py` — `escape_html`, `split_messages`, `note_card_lines`, `with_min_width`. Ширина пузыря: `with_min_width` считает самую длинную строку без HTML-тегов (`re.sub(r"<[^>]+>", "")`); если она не короче `WIDTH_SPACER` (`MIN_BUBBLE_WIDTH`), текст возвращается без распорки, иначе распорка добавляется через ОДИН `\n` (никаких `\n\n` внизу). Вызов идемпотентен: повторная распорка не добавляется. Шаблоны кнопок в `services/buttons.py` не заканчиваются переводом строки.
- **Сводка источника:** `notes_digest_text` ставит черту `━━━━━━━━━━` через одиночные `\n` (`NOTE_SEPARATOR = "\n━━━━━━━━━━\n"`), а `_notes_digest` склеивает поля карточки в сводке через `"\n".join(...)` — компактный блок без пустых строк. В одиночной карточке (`NoteContext.card_text`) поля по-прежнему разделяются пустой строкой (`\n\n`).
- `handlers/notes_handler.py` / `handlers/columns_handler.py` — диалоги, FSM и вызовы репозиториев через `asyncio.to_thread`.

## 3. BUSINESS & SAFETY RULES
- **Структура колонок (`BASE_HEADERS`):** `ID | SOURCE | TITLE | DESCRIPTION | URL | SCREEN | [пользовательские...] | COMMENT`.
- **Защита колонок (`ColumnService`):** `ID`, `SOURCE`, `TITLE` (первые 3) и `COMMENT` (последняя) нельзя удалить, переименовать или сдвинуть. `SCREEN` нельзя удалить (но можно переименовать). Новые колонки вставляются перед `COMMENT` (`insert_cols`).
- **Сортировка и чистота данных:** `NoteRepository` всегда держит лист отсортированным по `SOURCE` ➔ `TITLE` (без учёта регистра). Пустых заметок без `TITLE` не создаём; удаление — только через `delete_rows`.
- **Карточка и скриншот:** `note_card_lines` выводит только заполненные поля (пропуская пустые, `-`, `—`, `ID` и `SCREEN`). В `SCREEN` хранится только `message.photo[-1].file_id`, показывается через `answer_photo` (при ошибке — текстовая карточка + лог).
- **Изоляция:** `user_id` берётся строго из `event.from_user.id`, кэш сбрасывается точечно через `invalidate_cache(user_id)`.

## 4. CODE & TESTING STANDARDS
- `ruff` (`line-length = 88`, `E,F,I,W`), `ruff format`, `mypy` без ошибок (`tests/` исключён).
- Весь пользовательский ввод в HTML экранируется через `escape_html`; списки делятся `split_messages` / `send_parts`.
- Юнит-тесты (`tests/fakes.py`, `tests/factories.py`, `tests/test_sheets.py`, `tests/test_sheets_notes.py`, `tests/test_notes_card.py`) работают полностью офлайн без сети.
- Полные имена при импорте: никаких коротких псевдонимов (`from services import buttons`, а не `as btn`) и дублирующих префиксов в константах (кнопки без `BTN_` — просто `buttons.ADD_NOTE`, `buttons.COLUMNS` и т.п.).
