"""Тексты кнопок, подписей и подсказок Big Note Bot."""

# --- Команды, зарегистрированные в Telegram (set_my_commands) ---
START = "📱 Open the main menu!"
HELP = "❓ Help"
ADD_SOURCE = "➕ Add source"
ADD_NOTE = "➕ Add note"
OPEN_TABLE = "🪎 Open Table"

# --- Кнопки главного меню ---
COLUMNS = "📊 Columns"
SOURCES = "🗂 Sources"
AI_NOTE = "✨ AI Note"
#: «🔄 Sync» — принудительно перечитать таблицу (после ручных правок).
SYNC = "🔄 Sync"

# --- Кнопки навигации и карточки ---
BACK = "⬅️ Back"
TO_MAIN = "🏠 Main menu"
CANCEL = "✖️ Cancel"
NEW_SOURCE = "➕ New source"
EDIT_FIELD = "✏️ Edit field"
PHOTO = "🖼 Add/Replace screenshot"
DELETE_NOTE = "🗑 Delete"
ADD_COLUMN = "➕ Add column"
RENAME_SOURCE = "✏️ Edit source"
DELETE_SOURCE = "🗑 Delete source"
CONFIRM_DELETE = "✅ Yes, delete"
#: Подписи действий над колонками — короче, чтобы влезали в ряд с меню.
RENAME_COLUMN_SHORT = "✏️ Rename"
DELETE_COLUMN_SHORT = "🗑 Delete"
#: Кнопка в списке заметок источника — ярлык именно этого экрана.
ADD_NOTE_ROW = "➕ Add Note"
EDIT_SOURCE = "✏️ Edit Source"
#: Экран источника: все заметки сразу с полным содержанием.
SHOW_ALL_NOTES = "📖 Show all notes"

# --- Мастер заполнения новой заметки ---
#: «⏭ Next» — следующая колонка, «✅ Done» — оставить как есть.
NEXT_FIELD = "⏭ Next"
DONE_NOTE = "✅ Done"
#: «➕ Add another note» — снова TITLE для того же источника.
ADD_ANOTHER_NOTE = "➕ Add another note"

# --- ✨ AI Note: черновик из скриншота, ссылки или текста ---
#: Кнопки предпросмотра черновика.
AI_SAVE = "✅ Save"
AI_CHANGE_SOURCE = "🗂 Change source"
AI_EDIT_TITLE = "✏️ Edit title"

AI_NOTE_PROMPT = (
    "✨ <b>AI Note</b>\n\n"
    "Send a screenshot, a link or forwarded text — "
    "I will turn it into a note draft:"
)
AI_ANALYZING = "✨ Analyzing..."
AI_DRAFT_PROMPT = "✨ <b>AI Draft</b>\n\nCheck the fields and save the note:"
AI_DRAFT_EMPTY = "⚠️ Could not build a draft. Send another message."
AI_NO_KEY = (
    "🔑 AI is not configured: <code>AI_API_KEY</code> is missing.\n"
    "Ask the bot owner to set it, or use <b>➕ Add note</b>."
)
AI_FAILED = "⚠️ AI could not parse this message. Please try again."
#: Модель из AI_MODEL не умеет смотреть картинки — просим Vision-модель.
AI_NO_VISION = (
    "🖼 This model cannot read images.\n"
    "Please set a <b>Vision</b> model in <code>AI_MODEL</code> "
    "(for example a vision model from OpenRouter), "
    "or send the note as text or a link."
)
AI_TITLE_PROMPT = (
    "✏️ <b>AI draft title</b>\n\n"
    "Current: <b>{title}</b>\nWrite the new note <b>TITLE</b>:"
)
AI_TITLE_EMPTY = "⚠️ Title must not be empty."

# --- Тексты ---
TABLE_LINK_PROMPT = (
    "📊 <b>Google Sheets database</b>\n"
    "Press the button below to open it in your browser or app:"
)

ADMIN_ONLY = "🔒 This section is available to the bot owner only."

MAIN_MENU_PROMPT = (
    "🏠 <b>Big Note Bot</b>\n\n"
    "Notes are stored by <b>source</b>. Choose an action below:"
)

NEW_NOTE_PROMPT = (
    "➕ <b>New note</b>\n\nChoose an existing <b>SOURCE</b> or add a new one:"
)

NEW_SOURCE_PROMPT = (
    "🗂 <b>Add a new source</b>\n{existing}Write a <b>NEW NAME</b> for the source:"
)

NOTE_TITLE_PROMPT = (
    "📝 <b>New note</b>\n\nSource: <b>{source}</b>\nWrite the new note <b>TITLE</b>:"
)

#: Шаг мастера: обычная текстовая колонка заметки.
WIZARD_FIELD_PROMPT = (
    "📝 <b>{name}</b>\n\n"
    "Field <b>{field}</b> — send the value (or <code>-</code> to skip it):"
)

#: Шаг мастера: колонка SCREEN — ждём фото.
WIZARD_SCREEN_PROMPT = (
    "🖼 <b>{name}</b>\n\n"
    "Field <b>SCREEN</b> — send a screenshot as a photo, "
    "or press <b>⏭ Next</b>:"
)

#: Прислали текст там, где ждали фото для SCREEN.
WIZARD_SCREEN_WRONG_TYPE = (
    "⚠️ Please send a photo, or press <b>⏭ Next</b> to skip <b>SCREEN</b>."
)

#: Прислали фото там, где ждали текст для обычной колонки.
WIZARD_FIELD_WRONG_TYPE = (
    "⚠️ Please send text for <b>{field}</b>, or press <b>⏭ Next</b> to skip it."
)

EDIT_FIELD_PROMPT = (
    "✏️ <b>{name}</b>\n\n"
    "Column: <b>{field}</b>\n"
    "Send the new value (or <code>-</code> to clear it):"
)

PHOTO_PROMPT = (
    "🖼 <b>{name}</b>\n\n"
    "Send a screenshot as a photo — it will be stored in <b>SCREEN</b>:"
)

SOURCES_PROMPT = "📂 <b>Notes by source</b>\n\nChoose a source:"

SOURCES_MANAGE_PROMPT = (
    "🗂 <b>Sources</b>\n\nTap a number to rename or delete that source:"
)
SOURCE_MANAGE_PROMPT = "🗂 <b>{source}</b>\n\nChoose an action:"
SOURCE_RENAME_PROMPT = (
    "✏️ <b>Rename source</b> <code>{source}</code>\n\n"
    "Write the new source name (notes: <b>{count}</b>):"
)
SOURCE_RENAMED = "✅ Source renamed. Updated notes: <b>{count}</b>."
SOURCE_DELETE_CONFIRM = (
    "🗑 <b>Delete source</b> <b>{source}</b>?\n\n"
    "Notes that will be deleted: <b>{count}</b>. This cannot be undone."
)
SOURCE_DELETED = "🗑 Source deleted. Removed notes: <b>{count}</b>."
SOURCE_LOST = "⚠️ This source no longer exists. Open 🗂 Sources again."
SOURCE_ADDED = "✅ Source added: <code>{source}</code>"
SOURCE_EXISTS = "✅ Source already exists: <code>{source}</code>"
NO_SOURCES = (
    "📂 <b>Notes by source</b>\n\nNo sources yet. Add one with <b>➕ Add note</b>."
)
NO_NOTES = "📭 There are no notes in <b>{source}</b> yet."
NOTES_PROMPT = "📂 <b>{source}</b> — {count} note(s):"

NOTE_DELETED = "🗑 Note deleted."
NOTHING_FOUND = "🤷‍♂️ Nothing found for '{query}'. Try another name."
SEARCH_RESULTS = "🔎 <b>Found</b> — {count} note(s):"
NOTE_SAVED = "✅ Saved."
PHOTO_SAVED = "✅ Screenshot saved to SCREEN."
SYNCED = "🔄 Synced with Google Sheets."

COLUMNS_PROMPT = (
    "📋 <b>Columns</b>\n\n"
    "<code>ID</code>, <code>SOURCE</code>, <code>TITLE</code> are fixed first, "
    "<code>COMMENT</code> is always last. New columns are inserted before it."
)
COLUMN_ADD_PROMPT = "➕ <b>New column</b>\n\nWrite the column name:"
COLUMN_RENAME_PROMPT = "✏️ <b>Rename column</b> <b>{field}</b>\n\nSend the new name:"
COLUMN_VIEW_PROMPT = "📋 <b>Column: {field}</b>\n\nChoose an action:"

CHANGE_ORDER = "🔢 Change order"
COLUMN_ORDER_PROMPT = (
    "🔢 <b>Change column order</b>\n\n"
    "<b>ID</b>, <b>SOURCE</b>, <b>TITLE</b> stay first, <b>COMMENT</b> stays last."
)
COLUMN_ORDER_BAD_INPUT = (
    "⚠️ Send each number from {low} to {high} exactly once, "
    "separated by spaces (e.g. <code>{example}</code>)."
)
COLUMN_ORDER_SAVED = "✅ Column order updated."

HELP_TEXT = (
    "❓ <b>How to use the bot</b>\n"
    "<i>Everything is done with buttons — commands are optional.</i>\n\n"
    "<b>📝 Creating notes</b>\n"
    "<b>➕ Add note</b> — pick a source or create a new one, set <b>TITLE</b> "
    "and fill the columns step by step (<b>⏭ Next</b>, <b>✅ Done</b>, "
    "<b>➕ Add another note</b>).\n"
    f"<b>{AI_NOTE}</b> — send a screenshot, a link or forwarded text: "
    "the bot fills a draft, then <b>✅ Save</b>, change the source or edit "
    "the title.\n"
    "<b>➕ Add source</b> — create a source in advance: it appears with "
    "0 notes and is filled by its first note.\n\n"
    "<b>🗂 Browsing &amp; Editing</b>\n"
    "<b>🗂 Sources</b> — numbered source list: open notes by number, "
    "<b>📖 Show all notes</b>, add, rename or delete a source.\n"
    "<b>🔎 Search</b> — send a note <b>TITLE</b> as a chat message: "
    "the matching card opens right away.\n"
    "In a note card: <b>✏️ Edit field</b> changes any column "
    "(<code>-</code> clears a cell), <b>🖼 Add/Replace screenshot</b> stores "
    "a photo in <b>SCREEN</b>, <b>🗑 Delete</b> removes the note.\n\n"
    "<b>⚙️ Table &amp; Commands</b>\n"
    f"<b>{COLUMNS}</b> — add, rename, reorder or delete custom columns "
    "(<code>ID</code>, <code>SOURCE</code>, <code>TITLE</code> stay first, "
    "<code>COMMENT</code> stays last).\n"
    f"<b>{SYNC}</b> / <code>/refresh</code> — drop the cache and re-read the "
    "table right away (use it after editing cells in Google Sheets).\n"
    "<code>/table</code> — open the Google Sheets database.\n"
    "<code>/start</code> — main menu, <code>/help</code> — this guide."
)
