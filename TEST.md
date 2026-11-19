# TEST.md — реестр автотестов Big Note Bot

Все тесты работают офлайн: Google Sheets и Telegram заменены двойниками
(`tests/fakes.py`), данные создаются фабриками (`tests/factories.py`),
общие фикстуры (`use_repo`, `use_service`) и хелперы (`callback`, `flat`)
лежат в `tests/conftest.py`.

Запуск:

```powershell
pytest tests -m "not integration" -q
```

---

## `tests/test_sheets_notes.py` — слой данных заметок и кэш

### Чистые функции

| Тест | Проверяемое правило |
| --- | --- |
| `test_column_letter` | Номер колонки переводится в букву Sheets: 1 → A, 27 → AA. |
| `test_pad_row_fills_short_rows` | Рваная строка добивается пустыми ячейками до ширины шапки. |

### Сортировка

| Тест | Проверяемое правило |
| --- | --- |
| `test_sort_notes_by_source_then_title` | Лист сортируется по SOURCE, затем по TITLE, без учёта регистра. |
| `test_sort_keeps_header_on_top` | Шапка всегда остаётся первой строкой листа. |
| `test_short_rows_are_padded_to_header_width` | Короткая строка не ломает сортировку и карточку. |

### Создание заметок

| Тест | Проверяемое правило |
| --- | --- |
| `test_create_adds_note_and_sorts` | `create` пишет заметку и пересортировывает лист. |
| `test_create_ids_are_unique` | Две заметки получают разные ID. |

### Источники

| Тест | Проверяемое правило |
| --- | --- |
| `test_list_sources_unique_sorted` | Источники уникальны и отсортированы без учёта регистра. |
| `test_list_by_source_sorted_by_title` | Заметки источника идут по TITLE без учёта регистра. |
| `test_list_by_source_is_case_insensitive` | Регистр имени источника не мешает выборке. |
| `test_list_sources_with_counts` | Счётчик заметок по источникам не плодит дубликатов из-за регистра. |

### Поиск по названию

| Тест | Проверяемое правило |
| --- | --- |
| `test_find_by_title` | Поиск по TITLE: точное совпадение приоритетнее частичного; пустой запрос — ничего. |

### Автозаполнение ID

| Тест | Проверяемое правило |
| --- | --- |
| `test_manual_row_without_id_gets_generated_id` | Ручная строка (SOURCE+TITLE без ID) получает сгенерированный ID в таблицу. |
| `test_manual_row_without_id_is_found_when_listing_source` | Такая заметка видна в списке источника (кнопки работают). |
| `test_empty_row_gets_no_id` | Полностью пустая строка остаётся без ID. |

### Источник-болванка

| Тест | Проверяемое правило |
| --- | --- |
| `test_add_source_creates_blank_row_without_id` | Источник добавляется строкой с одним SOURCE и 0 заметок. |
| `test_add_source_is_case_insensitive_and_idempotent` | Повторное добавление в другом регистре ничего не меняет. |
| `test_source_name_validation_rejects_empty` | Пустое имя источника — `ValueError` при добавлении и переименовании. |
| `test_create_fills_blank_source_row` | Первая заметка заполняет болванку вместо дубликата. |
| `test_blank_source_is_not_listed_as_note` | Болванка не считается заметкой в списке источника. |
| `test_rename_and_delete_source_include_blank_rows` | Болванка переименовывается и удаляется вместе с источником. |

### Переименование и удаление источника

| Тест | Проверяемое правило |
| --- | --- |
| `test_rename_source_changes_all_rows_and_resorts` | `rename_source` меняет SOURCE во всех строках и пересортировывает лист. |
| `test_unknown_source_returns_zero` | Неизвестный источник: 0 переименованных заметок и 0 удалённых строк. |
| `test_delete_source_removes_only_its_rows` | `delete_source` удаляет строки только указанного источника. |

### Прочее и кэш

| Тест | Проверяемое правило |
| --- | --- |
| `test_delete_removes_row` | `delete` удаляет строку заметки, остальные остаются. |
| `test_prompt_row_is_not_a_note` | Служебная строка AI_PROMT не является заметкой. |
| `test_create_and_update_reuse_cached_snapshot` | `create`/`update_cell_at` правят кэш без повторного чтения листа. |
| `test_locate_rereads_sheet_when_cached_snapshot_is_stale` | ID нет в кэше, но есть в листе — `locate`/`get` перечитывают лист и находят заметку. |
| `test_locate_rereads_sheet_when_id_appears_in_new_sheet_object` | Ключ кэша стабилен: новый объект листа с тем же `title` видит те же данные. |

---

## `tests/test_sheets_columns.py` — `ColumnService` и защита колонок

| Тест | Проверяемое правило |
| --- | --- |
| `test_protected_columns_cannot_be_deleted` | ID, SOURCE, TITLE и SCREEN удалить нельзя. |
| `test_comment_column_is_protected` | COMMENT — последняя защищённая колонка. |
| `test_comment_stays_last_after_reorder` | COMMENT остаётся последней после перестановки. |
| `test_leading_columns_cannot_be_renamed` | Служебные первые колонки нельзя переименовать. |
| `test_screen_can_be_renamed_but_not_deleted` | SCREEN переименовывается, но не удаляется (защита по позиции). |
| `test_user_column_is_inserted_before_comment` | Новая колонка вставляется перед COMMENT. |
| `test_user_column_can_be_renamed_and_deleted` | Пользовательскую колонку можно переименовать и удалить. |
| `test_reorder_rejects_moving_leading_columns` | Полный порядок не двигает ID, SOURCE, TITLE. |
| `test_reorder_keeps_data` | Перестановка колонок переносит данные вместе с шапкой. |
| `test_reorder_middle_rejects_bad_permutation` | Повторы и чужие номера отклоняются. |
| `test_reorder_middle_keeps_leading_and_comment` | Средние колонки меняются местами, служебные остаются на местах. |

---

## `tests/test_notes_card.py` — карточка, скриншот, поиск

### Карточка заметки

| Тест | Проверяемое правило |
| --- | --- |
| `test_card_lines_formatting` | Карточка: только заполненные поля, без прочерков, с экранированием HTML и пользовательскими колонками. |
| `test_card_fields_separated_by_blank_line` | Поля карточки разделены пустой строкой. |
| `test_note_context_editable_columns_exclude_id_and_screen` | Текстовая правка недоступна для ID и SCREEN. |
| `test_note_context_reads_screen_file_id` | file_id скриншота читается из SCREEN. |
| `test_note_card_buttons` | В карточке 5 кнопок; «✏️ Edit field» открывает меню колонок, а не ячейку. |
| `test_edit_field_accepts_any_column_except_id_and_screen` | Меню правки показывает все колонки, кроме ID и SCREEN. |

### Скриншот и поиск

| Тест | Проверяемое правило |
| --- | --- |
| `test_screenshot_saves_file_id_into_screen` | В SCREEN пишется `photo[-1].file_id` (самый большой размер). |
| `test_screenshot_uses_screen_column_not_comment` | SCREEN ищется по имени, даже с пользовательскими колонками. |
| `test_screenshot_without_photo_sends_warning` | Пустой список фото: предупреждение без записи в таблицу. |
| `test_screenshot_without_screen_column_reports_error` | Нет колонки SCREEN: `ValueError` перехвачен, бот не падает. |
| `test_search_by_title_opens_single_card` | Уникальное совпадение TITLE сразу открывает карточку. |
| `test_search_by_title_lists_several_matches` | Несколько совпадений — нумерованный список с источником. |
| `test_search_by_title_nothing_found` | Ничего не найдено — сообщение с экранированным запросом. |
| `test_search_ignores_commands` | Слэш-команды не перехватываются поиском. |

### Навигация и текст

| Тест | Проверяемое правило |
| --- | --- |
| `test_source_navigation_buttons` | «Notes by source» без Cancel, ввод TITLE — с Cancel. |
| `test_split_messages_respects_limit` | Длинный текст режется на части не длиннее лимита. |
| `test_split_messages_short_text_single_part` | Короткий текст — одна часть, пустой — ни одной. |
| `test_with_min_width` | Распорка добавляется только короткому тексту (по видимому тексту без тегов), без `\n\n`; длинному — не добавляется. |
| `test_with_min_width_pads_short_text` | Короткий текст: распорка через один `\n`, без пустой строки. |
| `test_with_min_width_short_text_padding` | Идемпотентность: повторная распорка не добавляется ни к короткому, ни к длинному тексту. |

---

## `tests/test_notes_wizard.py` — мастер новой заметки

### Выбор источника

| Тест | Проверяемое правило |
| --- | --- |
| `test_add_note_shows_sources_and_new_source_button` | «➕ Add note» показывает источники и «➕ Новый источник». |
| `test_add_note_without_sources_asks_for_new_source` | Без источников сразу просит имя нового. |
| `test_add_source_lists_existing_sources_and_cancel` | «➕ Add source» и кнопка «➕ New source»: уникальный список источников, Cancel и Main menu. |
| `test_add_source_prompt_formatting` | Промпт нового источника: список уникален, имя экранируется, без источников нет «Existing:». |
| `test_select_source_then_name_creates_note` | Источник → TITLE → заметка создана и отсортирована. |

### Шаги мастера

| Тест | Проверяемое правило |
| --- | --- |
| `test_wizard_starts_after_title` | Мастер спрашивает DESCRIPTION, показывает TITLE и кнопки шага. |
| `test_wizard_full_walkthrough` | Текст → Next → фото SCREEN → последнее поле → открывается карточка. |
| `test_wizard_done_opens_card` | «✅ Done» открывает карточку заметки. |
| `test_wizard_another_note_same_source` | «➕ Add another note» снова просит TITLE того же источника. |
| `test_wizard_buttons_work_after_fsm_reset` | Кнопки мастера работают с пустым FSM (рестарт сервера). |
| `test_wizard_photo_uses_next_then_saves_file_id` | «⏭ Next» доводит до SCREEN, фото пишет file_id в SCREEN. |
| `test_wizard_columns_skip_service_headers` | Мастер не спрашивает ID, SOURCE и TITLE. |
| `test_wizard_columns_start_at_description` | Первая колонка мастера — DESCRIPTION (4-я колонка листа). |

---

## `tests/test_sources.py` — источники и их заметки

### Список источников

| Тест | Проверяемое правило |
| --- | --- |
| `test_new_source_is_added_as_blank_source` | Источник сохраняется сразу болванкой без ID и TITLE. |
| `test_sources_menu_numbers_and_opens_actions` | «🗂 Sources» нумерует источники и открывает действия. |
| `test_source_actions_legacy_show_notes` | Старый callback «📂 Show notes» ведёт к списку заметок. |
| `test_empty_source_manage_screen` | Экран источника без заметок: заголовок и действия с подписями. |
| `test_source_actions_show_all_notes` | «📖 Show all notes» выводит полные карточки источника. |
| `test_notes_list_is_numbered_with_code_names` | Список заметок нумерован и даёт кнопки-номера. |
| `test_notes_list_edit_source_button_starts_rename` | «✏️ Edit Source» запускает переименование. |
| `test_source_actions_add_note_skips_source_choice` | «➕ Add note» пропускает выбор источника и сразу просит TITLE. |

### Устаревший индекс

| Тест | Проверяемое правило |
| --- | --- |
| `test_saved_source_alerts_on_invalid_index` | Индекс вне сохранённого списка (`[]`, `["Blog"]`, `["Blog", "News"]`) — alert SOURCE_LOST без падения. |

### Переименование и удаление

| Тест | Проверяемое правило |
| --- | --- |
| `test_sources_menu_rename_flow` | Rename → новое имя → отчёт и список заметок (2 сообщения). |
| `test_sources_menu_delete_flow` | Delete → подтверждение → Yes, delete → список источников. |

---

## `tests/test_columns.py` — экран «📋 Columns»

| Тест | Проверяемое правило |
| --- | --- |
| `test_columns_menu_lists_only_custom_columns` | В списке только пользовательские колонки, индексы — в FSM. |
| `test_column_number_opens_actions` | Номер открывает меню действий колонки с короткими подписями. |
| `test_change_order_full_flow` | Изменение порядка «2 1 3» переставляет средние колонки. |
| `test_order_cancel_returns_to_columns` | Cancel возвращает к списку колонок без изменений. |
| `test_bad_order_keeps_user_in_order_state` | Неверный ввод — подсказка и ожидание повтора. |
| `test_screen_column_cannot_be_deleted` | У SCREEN есть переименование, но нет удаления. |
| `test_columns_menu_add_column_prompt` | «➕ Add column» спрашивает имя колонки. |
| `test_new_column_gets_into_numbered_list` | Новая колонка появляется в нумерованном списке. |
