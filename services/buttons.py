"""Тексты кнопок, подписей и подсказок Big Note Bot."""

# --- Команды, зарегистрированные в Telegram (set_my_commands) ---
START = "📱 Open the main menu!"
HELP = "❓ Help"
ADD_SOURCE = "➕ Add source"
ADD_NOTE = "➕ Add note"
OPEN_TABLE = "🪎 Open Table"

# --- Кнопки главного меню ---
BY_SOURCE = "📂 Notes by source"
COLUMNS = "📊 Columns"
SOURCES = "🗂 Sources"

# --- Кнопки навигации и карточки ---
BACK = "⬅️ Back"
TO_MAIN = "🏠 Main menu"
CANCEL = "✖️ Cancel"
NEW_SOURCE = "➕ New source"
EDIT_FIELD = "✏️ Edit field"
PHOTO = "🖼 Add/Replace screenshot"
DELETE_NOTE = "🗑 Delete note"
DELETE_COLUMN = "🗑 Delete column"
RENAME_COLUMN = "✏️ Rename column"
ADD_COLUMN = "➕ Add column"
MOVE_LEFT = "⬅️"
MOVE_RIGHT = "➡️"
RENAME_SOURCE = "✏️ Rename"
DELETE_SOURCE = "🗑 Delete"
CONFIRM_DELETE = "✅ Yes, delete"

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
    "🗂 <b>New source</b>\n\nWrite the <b>SOURCE</b> name (e.g. <i>Instagram</i>):"
)

NEW_SOURCE_EXISTING = "Existing: {sources}"

NEW_SOURCE_PROMPT_WITH_EXISTING = (
    "🗂 <b>New source</b>\n"
    "{existing}\n\nWrite the <b>SOURCE</b> name (e.g. <i>Instagram</i>):"
)

NOTE_NAME_PROMPT = (
    "📝 <b>New note</b>\n\nSource: <b>{source}</b>\nWrite the note <b>NAME</b>:"
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
SOURCE_MANAGE_PROMPT = "🗂 <b>{source}</b>\n\nNotes: <b>{count}</b>\n\nChoose an action:"
SOURCE_RENAME_PROMPT = (
    "✏️ <b>Rename source</b> <b>{source}</b>\n\n"
    "Write the new source name (notes: <b>{count}</b>):"
)
SOURCE_RENAMED = "✅ Source renamed. Updated notes: <b>{count}</b>."
SOURCE_DELETE_CONFIRM = (
    "🗑 <b>Delete source</b> <b>{source}</b>?\n\n"
    "Notes that will be deleted: <b>{count}</b>. This cannot be undone."
)
SOURCE_DELETED = "🗑 Source deleted. Removed notes: <b>{count}</b>."
SOURCE_LOST = "⚠️ This source no longer exists. Open 🗂 Sources again."
NO_SOURCES = (
    "📂 <b>Notes by source</b>\n\nNo sources yet. Add one with <b>➕ Add note</b>."
)
NO_NOTES = "📭 There are no notes in <b>{source}</b> yet."
NOTES_PROMPT = "📂 <b>{source}</b> — {count} note(s):"

NOTE_DELETED = "🗑 Note deleted."
NOTE_SAVED = "✅ Saved."
PHOTO_SAVED = "✅ Screenshot saved to SCREEN."

COLUMNS_PROMPT = (
    "📋 <b>Columns</b>\n\n"
    "<code>ID</code>, <code>SOURCE</code>, <code>NAME</code> are fixed first, "
    "<code>COMMENT</code> is always last. New columns are inserted before it."
)
COLUMN_ADD_PROMPT = "➕ <b>New column</b>\n\nWrite the column name:"
COLUMN_RENAME_PROMPT = "✏️ <b>Rename column</b> <b>{field}</b>\n\nSend the new name:"

HELP_TEXT = (
    "❓ <b>How to use the bot</b>\n"
    "<i>Everything is done with buttons — no commands needed.</i>\n\n"
    "<b>➕ Add note</b> — pick an existing source or create a new one, "
    "then set the note name.\n"
    "<b>📂 Notes by source</b> — browse notes grouped by source.\n"
    "<b>📋 Columns</b> — add, rename, reorder or delete custom columns.\n\n"
    "In a note card you can edit any field via <b>✏️ Edit field</b>, "
    "attach a screenshot "
    "(stored in <b>SCREEN</b>) or delete the note."
)
