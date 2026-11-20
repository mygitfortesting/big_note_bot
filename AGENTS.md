# AGENTS.md — Правила проекта `Big Note Bot` (`@big_note_bot`)

Telegram-бот заметок (`Python 3.13`, `aiogram 3.x`, `gspread`).

## 1. STRICT EXECUTION RULES
- **НИКОГДА не запускать команды в терминале** (`pytest`, `ruff`, `mypy`, `pip`, `git`, `pre-commit`). Проверки запускает только пользователь.
- Не менять структуру папок, не трогать `.venv` и `.git`, не создавать временные скрипты.
- Работать **только с запрошенными файлами**, вносить правки точечно (in-place).
- После правки тестов указывать команду запуска (`pytest tests -m "not integration" -q`), не выполняя её.

## 2. ARCHITECTURE & MODULES
- `bot.py` — команды `/start`, `/help`, `/table`, `/refresh` и кнопка «🔄 Sync» (сброс кэша + перечитывание листа, общий `sync_table`), сборка роутеров (порядок: общие команды ➔ ✨ AI Note ➔ заметки ➔ колонки) и polling.
- `config.py` — `.env`, `Dispatcher`, доступ к Google Sheets, `notes_sheet_name(user_id)` (`MY_NOTES` для админа, `Notes_{user_id}` для остальных), `setup_user_sheets`, настройки AI (`ai_api_key`, `ai_base_url` — по умолчанию `https://openrouter.ai`, `ai_model` — по умолчанию `openrouter/free`).
- `services/sheets.py` — единственный модуль работы с `gspread` (кроме `config.py`): потокобезопасный `SheetCache` (TTL 300 с, ключ по `sheet.id`/`sheet.title`, фоллбэк на stale при `429`), `NoteRepository` (включая `get_ai_prompt` — правила из строки `AI_PROMT`), `ColumnService`. При промахе `locate` один раз перечитывает лист с сервера.
- `services/ai_parser.py` — разбор сообщения в `AiNoteDraft` (`source`, `title`, `fields` — колонка → значение по актуальной шкале листа, `screen`): загрузка страницы по ссылке (`fetch_page`, таймаут 10 с, фоллбэк на URL), строгий JSON от модели (`extract_json`), схема полей в промпте из `field_headers`/`system_prompt` (без захардкоженных `DESCRIPTION`/`URL`, читает и старые `extra_fields`/плоские ключи), `draft_fields` — все заполненные поля + `SCREEN`, `is_vision_error`/`AiVisionError` — модель не умеет смотреть картинки. Клиент модели приходит извне (инъекция).
- `services/callback.py` — единый реестр `Cmd` и `Cb` (лимит `callback_data` — 64 байта, поэтому в списках используются индексы или короткие `note_id`).
- `services/buttons.py` / `services/keyboards.py` — тексты и сборка `InlineKeyboardMarkup` (`_rows` валидирует `per_row >= 1`).
- `services/telegram_io.py` — вывод в Telegram (`ack_callback`, `alert`, `show_main_menu`, `send_parts`, `status_message`, `send_error_message`) и `user_lock(user_id)` для сериализации записи.
- `services/text_utils.py` — `escape_html`, `plain_text`, `split_messages`, `note_card_lines`, идемпотентный `with_min_width` (распорка ставится через один `\n` только для узких текстов).
- `handlers/` — `notes_handler.py` (публичный API, роутер пакета, карточка, поиск), `wizard_handler.py` (мастер создания заметки), `sources_handler.py` (источники), `ai_note_handler.py` (✨ AI Note: состояние `AiStates`, разбор и предпросмотр черновика по актуальной шапке листа, сохранение под `user_lock`), `columns_handler.py` (колонки: отмена ввода на любом шаге, `alert` чистым текстом, `column_total` в FSMContext), `notes_common.py` (общие состояния и модели).

## 3. BUSINESS & SAFETY RULES
- **Схема колонок:** `ID | SOURCE | TITLE | DESCRIPTION | URL | SCREEN | [пользовательские...] | COMMENT`.
- **Защита колонок:** `ID`, `SOURCE`, `TITLE` (первые 3) и `COMMENT` (последняя) нельзя удалить, переименовать или сдвинуть. `SCREEN` нельзя удалить (можно переименовать). Новые колонки вставляются перед `COMMENT`.
- **Данные:** лист всегда отсортирован по `SOURCE` ➔ `TITLE` (без учёта регистра). `add_source` делает не более 1 чтения (0 при тёплом кэше). После ручных правок в таблице кэш сбрасывается вручную: «🔄 Sync» / `/refresh` (`invalidate_cache` + прогрев `repo.all_values`). Удаление заметок — с подтверждением (`Cb.NOTE_DEL_ASK` ➔ `Cb.NOTE_DEL`) через `delete_rows`. В именах источников запрещён символ `:`.
- **Карточка:** выводятся только заполненные поля (без пустых, `-`, `—`, `ID`, `SCREEN`). В `SCREEN` хранится только `file_id` фото Telegram.
- **✨ AI Note:** `AI_API_KEY` пуст — бот работает как обычно, а кнопка объясняет, что AI-ключ не настроен. Схема полей (промпт, предпросмотр, запись) строится динамически из `repo.headers()`: все колонки кроме `ID`, `SOURCE`, `TITLE`, `SCREEN`, `COMMENT` (переименованная SCREEN узнаётся через `screen_slot_index`); служебные имена в коде не зашиты. Черновик показывает `✨ AI Draft` с «✅ Save», «🗂 Change source», «✏️ Edit title», «✖️ Cancel»; сохранение — через `NoteRepository.create` под `user_lock(user_id)`. Кастомные правила — строка `AI_PROMT` на листе (`get_ai_prompt`). Если модель из `AI_MODEL` отвергла фото (нет vision), пользователь получает подсказку указать Vision-модель.
- **Изоляция:** `user_id` берётся строго из `event.from_user.id`, запись сериализуется через `user_lock(user_id)`.

## 4. CODE & TESTING STANDARDS
- `ruff` (`line-length = 88`, `E,F,I,W`), `ruff format`, `mypy` без ошибок (`tests/` исключён).
- Весь пользовательский ввод в HTML экранируется через `escape_html`; длинные сообщения делятся через `send_parts`.
- Полные имена при импорте: без коротких псевдонимов (`from services import buttons`, а не `as btn`) и без дублирующих префиксов в константах (`buttons.ADD_NOTE`, без `BTN_`).
- Тесты (`tests/`) работают офлайн на `FakeSheet` и `FakeMessage` (`tests/fakes.py`, `tests/factories.py`). Фикстуры и хелперы (`use_repo`, `use_service`, `callback`, `flat`, `message`) берутся только из `tests.conftest`. Повторяющиеся кейсы объединяются через `@pytest.mark.parametrize`, а `TEST.md` обновляется вместе с тестами.
- Без дублей: похожие проверки схлопываются в одну строку (срез `rows[1][:3]`, словарь `answers[-1]`, `all(...)`/`not any(...)` для текста, сравнение списков кнопок), а полные дубли тестов удаляются и вычищаются из `TEST.md`. Фикстура `use_repo` в тестах мастера аннотируется как `use_repo: UseRepo`.
