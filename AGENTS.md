# AGENTS.md — правила проекта `Big Note Bot` (`@big_note_bot`)

Telegram-бот заметок: `Python 3.13`, `aiogram 3.x`, `gspread`, `openai`.

## 1. Правила работы
- **Не запускать команды в терминале** (`pytest`, `ruff`, `mypy`, `pip`, `git`, `pre-commit`) — проверки запускает пользователь. После правки тестов указать команду: `pytest tests -m "not integration" -q`.
- Правки точечные (in-place), только в запрошенных файлах. Не менять структуру папок, не трогать `.venv` и `.git`, не создавать временные скрипты.

## 2. Карта модулей
- `bot.py` — общие команды (`/start`, `/help`, `/table`, `/refresh`), кнопка `🔄 Sync` (общий `sync_table`: `invalidate_cache(user_id_of(event))` + прогрев, одно сообщение `SYNCED`), хелперы `_start_session` / `_ack_and_hide` / `_require_admin`, сборка роутеров (`register_routers`) и запуск (`_start_health_server` ➔ `_setup_bot` ➔ `_run_polling`).
- `config.py` — `.env`, `Bot`/`Dispatcher`, доступ к Sheets (`notes_sheet_name`, `setup_user_sheets`), настройки AI (`ai_api_key`, `ai_base_url` → `https://openrouter.ai`, `ai_model` → `openrouter/free`).
- `services/sheets.py` — единственный слой `gspread`: `SheetCache` (TTL 300 с, фоллбэк на stale при 429), `NoteRepository` (в т.ч. `get_ai_prompt`), `ColumnService`.
- `services/ai_parser.py` — разбор сообщения в `AiNoteDraft`: `fetch_page` (ссылка, таймаут 10 с), `extract_json`, `system_prompt` из шапки листа + `AI_PROMT`, `field_headers`, `draft_fields`, `AiVisionError`. Клиент модели инъектируется. Ссылка без JSON от модели -> `_link_draft` (URL в `title` и в колонку `URL`), для текста — `AiDraftError` с началом ответа (`RAW_PREVIEW_LIMIT`).
- `services/callback.py` — реестр `Cmd`/`Cb` (в списках индексы или короткие `note_id`: лимит 64 байта). Имя источника всегда едет в хвосте `prefix:<note_id>:<source>` и разбирается через `split(":", maxsplit)` — двоеточия внутри не режутся.
- `services/buttons.py`, `services/keyboards.py` — тексты и `InlineKeyboardMarkup` (`_rows` проверяет `per_row >= 1`).
- `services/telegram_io.py` — вывод в Telegram (`show_main_menu`, `status_message`, `send_parts`, `send_error_message`, `alert`) и `user_lock(user_id)`.
- `services/text_utils.py` — `escape_html`, `split_messages`, `note_card_text`, `with_min_width`.
- `handlers/` — `notes_handler.py` (публичный API пакета, карточка, поиск, удаление), `wizard_handler.py` (мастер заметки), `sources_handler.py` (источники), `ai_note_handler.py` (✨ AI Note: `AiStates`, черновик, сохранение), `columns_handler.py` (колонки), `notes_common.py` (`NoteStates`, `NoteContext`, хелперы).
- Порядок роутеров: общие команды ➔ ✨ AI Note ➔ заметки ➔ колонки. `ai_router` подключается только в `bot.register_routers`.

## 3. Инварианты
- **Колонки:** `ID | SOURCE | TITLE | DESCRIPTION | URL | SCREEN | [пользовательские...] | COMMENT`.
- `ID`, `SOURCE`, `TITLE` (первые 3) и `COMMENT` (последняя) нельзя удалить, переименовать или сдвинуть; `SCREEN` нельзя удалить. Новые колонки — перед `COMMENT`.
- Лист отсортирован по `SOURCE` ➔ `TITLE` (без учёта регистра). В именах источников двоеточие `:` **разрешено**: запрещено только пустое имя (`add_source`/`rename_source`).
- Правка `SOURCE` в карточке: `buttons.EDIT_SOURCE_PROMPT` + список существующих источников в `<code>` (`repo.list_sources`), `-` не очищает SOURCE (только `buttons.EDIT_SOURCE_EMPTY`).
- В `SCREEN` хранится только `file_id` фото; карточка показывает лишь заполненные поля (без пустых, `-`, `—`, `ID`, `SCREEN`).
- Удаление заметки — через подтверждение (`Cb.NOTE_DEL_ASK` ➔ `Cb.NOTE_DEL`).
- Кэш: любая запись через бота его инвалидирует; ручные правки видны только после `🔄 Sync` / `/refresh` (`invalidate_cache` + прогрев).

## 4. ✨ AI Note
- `AI_API_KEY` пуст — бот работает штатно, кнопка объясняет, что ключ не настроен.
- Схема полей (промпт, предпросмотр, запись) строится из `repo.headers()`: всё кроме `ID`, `SOURCE`, `TITLE`, `SCREEN`, `COMMENT`; переименованная `SCREEN` узнаётся через `screen_slot_index`. Служебные имена в коде не зашивать.
- Кастомные правила — строка `AI_PROMT` на листе (текст из первой непустой колонки после `TITLE`), читается через `get_ai_prompt`.
- Черновик: `✅ Save`, `🗂 Change source`, `✏️ Edit title`, `✖️ Cancel`; запись — `NoteRepository.create` под `user_lock(user_id)`.
- Нет vision у модели — `AiVisionError`/`is_vision_error` ➔ подсказка сменить `AI_MODEL`.
- Промпт запрещает извинения и «нет доступа к ссылке»: даже голый URL без текста страницы модель обязана вернуть JSON (`url`, заголовок из домена/пути). Блокирующие парсинг ссылки (`lnkd.in`, LinkedIn) не валят сценарий: `parse` логирует ответ и возвращает базовый черновик ссылки.

## 5. Стандарты кода и тестов
- `ruff` (`line-length = 88`, `E,F,I,W`), `ruff format`, `mypy` без ошибок (`tests/` исключён).
- Ввод экранируется через `escape_html`; длинные сообщения — `send_parts`.
- Импорты полными именами (`from services import buttons`), константы без префиксов (`buttons.ADD_NOTE`).
- Тесты офлайн на `FakeSheet`/`FakeMessage`; фикстуры и хелперы (`use_repo`, `use_service`, `callback`, `flat`, `message`) — только из `tests.conftest`; повторяющиеся кейсы — `@pytest.mark.parametrize`; `TEST.md` обновляется вместе с тестами.
