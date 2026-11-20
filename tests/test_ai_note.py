"""✨ AI Note: разбор текста/ссылки/фото, предпросмотр черновика и сохранение."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from config import (
    DEFAULT_AI_BASE_URL,
    DEFAULT_AI_MODEL,
    NOTES_HEADERS,
    ai_api_key,
    ai_base_url,
    ai_model,
)
from handlers import ai_note_handler
from services import ai_parser, buttons
from services.ai_parser import (
    AI_PROMPT_HEADER,
    AiDraftError,
    AiNoteDraft,
    AiNoteParser,
)
from services.callback import Cb
from services.sheets import NoteRepository
from tests.conftest import callback, flat, message
from tests.factories import note_row, repo_for, rows_by_header
from tests.fakes import FakeSheet, FakeState

#: Фикстура ``use_repo`` из ``tests.conftest``.
UseRepo = Callable[[NoteRepository], NoteRepository]
#: Фикстура ``ai_client`` — двойник клиента модели с журналом запросов.
UseAi = dict[str, Any]

#: Ответ модели в «человеческой» обёртке ```json ... ```.
AI_REPLY = (
    "```json\n"
    '{"source": "Blog", "title": "AI title", '
    '"fields": {"DESCRIPTION": "AI text", "PRICE": "10$", '
    '"UNKNOWN": "x"}}\n'
    "```"
)


@pytest.fixture()
def ai_client() -> dict[str, Any]:
    """Двойник клиента модели: журнал запросов и подменяемый ответ."""
    state: dict[str, Any] = {"calls": [], "reply": AI_REPLY}

    def create(**kwargs: Any) -> Any:
        state["calls"].append(kwargs)
        if state.get("error"):
            raise RuntimeError(state["error"])
        reply = SimpleNamespace(content=state["reply"])
        return SimpleNamespace(choices=[SimpleNamespace(message=reply)])

    completions = SimpleNamespace(create=create)
    state["chat"] = SimpleNamespace(completions=completions)
    return state


class _Photo:
    """Один размер фото: Telegram отдаёт список, бот берёт последний."""

    def __init__(self, file_id: str) -> None:
        self.file_id = file_id


def _client(ai_client: UseAi) -> Any:
    """Клиент для ``AiNoteParser``: он обращается к ``client.chat.completions``."""
    return SimpleNamespace(chat=ai_client["chat"])


def _use_ai_key(monkeypatch: pytest.MonkeyPatch, ai_client: UseAi) -> None:
    """Ключ модели для хендлера: настоящий клиент провайдера не создаётся."""
    monkeypatch.setattr(ai_note_handler, "ai_api_key", lambda: "test-key")
    monkeypatch.setattr(
        ai_note_handler,
        "ai_service",
        lambda: AiNoteParser(_client(ai_client), "test-model"),
    )


def _repo_with_price(use_repo: UseRepo) -> tuple[NoteRepository, FakeSheet]:
    """Лист с пользовательской колонкой PRICE — её заполняет модель."""
    headers = [*NOTES_HEADERS[:-1], "PRICE", NOTES_HEADERS[-1]]
    repo, sheet = repo_for([note_row("1", "Blog", "a")], headers=headers)
    use_repo(repo)
    return repo, sheet


def _open_ai(state: FakeState) -> None:
    """Открывает экран ✨ AI Note у уже подменённого репозитория."""
    asyncio.run(ai_note_handler.cb_ai_note(callback(Cb.AI_NOTE), state))


def _draft(state: FakeState, text: str = "Forwarded text") -> Any:
    """Сообщение с текстом ➔ черновик; возвращает само сообщение с ответами."""
    draft_message = message(text, user_id=1)
    asyncio.run(ai_note_handler.got_ai_message(draft_message, state))
    return draft_message


def test_without_key_main_menu_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Главное меню содержит ✨ AI Note, пустой ключ его не ломает."""
    from services import keyboards

    texts = [button.text for button in flat(keyboards.main_menu())]

    assert buttons.AI_NOTE in texts
    for name in ("AI_API_KEY", "AI_BASE_URL", "AI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert ai_api_key() == ""
    assert ai_base_url() == DEFAULT_AI_BASE_URL
    assert ai_model() == DEFAULT_AI_MODEL


# --- Сервис разбора ---


def test_ai_client_returns_sync_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    ai_client() берёт синхронный ``OpenAI``, а не ``AsyncOpenAI``.

    ``AiNoteParser.parse`` читает ``response.choices`` сразу после вызова
    (он и так идёт в потоке): async-клиент вернул бы корутину —
    ``AttributeError: 'coroutine' object has no attribute 'choices'``.
    """
    created: dict[str, Any] = {}

    class _SyncClient:
        """Подмена openai.OpenAI: запоминает аргументы конструктора."""

        def __init__(self, **kwargs: Any) -> None:
            created.update(kwargs)

    import openai

    monkeypatch.setattr(openai, "OpenAI", _SyncClient)
    monkeypatch.setattr(ai_note_handler, "ai_api_key", lambda: "test-key")
    monkeypatch.setattr(ai_note_handler, "ai_base_url", lambda: "https://ai")

    client = ai_note_handler.ai_client()

    assert isinstance(client, _SyncClient)
    assert created == {"api_key": "test-key", "base_url": "https://ai"}


def test_ai_client_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без AI_API_KEY клиент не создаётся — RuntimeError с понятным текстом."""
    monkeypatch.setattr(ai_note_handler, "ai_api_key", lambda: "")

    with pytest.raises(RuntimeError, match="AI_API_KEY"):
        ai_note_handler.ai_client()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(
            '{"source": "Blog", "title": "T"}', {"source": "Blog"}, id="plain"
        ),
        pytest.param('```json\n{"title": "T"}\n```', {"title": "T"}, id="fenced"),
        pytest.param(
            'Here you go:\n{"title": "T"}\nDone', {"title": "T"}, id="prose-around"
        ),
    ],
)
def test_extract_json_accepts_wrapped_objects(
    raw: str, expected: dict[str, Any]
) -> None:
    """Строгий JSON: модель может обернуть объект в ```json ... ``` или в текст."""
    parsed = ai_parser.extract_json(raw)

    assert all(parsed[key] == value for key, value in expected.items())


def test_extract_json_rejects_garbage() -> None:
    """Без JSON-объекта разбор невозможен — AiDraftError."""
    with pytest.raises(AiDraftError):
        ai_parser.extract_json("No JSON here")


def test_page_kind_tells_links_from_text() -> None:
    """Ссылка http(s):// разбирается как страница, остальной текст — как текст."""
    kinds = [
        ai_parser.page_kind("https://example.com/post"),
        ai_parser.page_kind("http://example.com"),
        ai_parser.page_kind("just forwarded text"),
    ]

    assert kinds == ["link", "link", "text"]


@pytest.mark.parametrize("mode", ["error", "page"])
def test_link_page_falls_back_to_url(
    monkeypatch: pytest.MonkeyPatch, ai_client: UseAi, mode: str
) -> None:
    """Ссылка: заголовок и текст страницы уходят в модель, а при 403 — сама ссылка."""
    monkeypatch.setattr(
        ai_parser,
        "fetch_page",
        (lambda url: ("Page title", "Page body") if mode == "page" else ("", "")),
    )

    draft = AiNoteParser(_client(ai_client), "test-model").parse(
        "https://example.com", sources=["Blog"], columns=["PRICE"]
    )

    user_text = ai_client["calls"][-1]["messages"][1]["content"][0]["text"]

    assert user_text.startswith("URL: https://example.com")
    assert ("Page title" in user_text) is (mode == "page")
    assert draft.title == "AI title"


def test_parse_keeps_only_known_columns_and_file_id(ai_client: UseAi) -> None:
    """fields фильтруются по шапке, file_id фото уходит в SCREEN."""
    draft = AiNoteParser(_client(ai_client), "test-model").parse(
        "Forwarded text", file_id="photo-1", sources=["Blog"], columns=["PRICE"]
    )

    # UNKNOWN из ответа модели в черновик не попадает: такой колонки нет.
    assert draft.fields == {"PRICE": "10$"} and draft.screen == "photo-1"


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(
            '{"source": "Blog", "title": "T", "extra_fields": {"PRICE": "10$"}}',
            id="legacy-extra-fields",
        ),
        pytest.param(
            '{"source": "Blog", "title": "T", "PRICE": "10$"}',
            id="flat-keys",
        ),
    ],
)
def test_parse_accepts_legacy_reply_shapes(ai_client: UseAi, reply: str) -> None:
    """Старые форматы ответа (extra_fields / плоские ключи) по-прежнему читаются."""
    ai_client["reply"] = reply

    draft = AiNoteParser(_client(ai_client), "test-model").parse(
        "Forwarded text", sources=["Blog"], columns=["PRICE"]
    )

    assert draft.fields == {"PRICE": "10$"}


def test_parse_sends_photo_as_image_url(ai_client: UseAi) -> None:
    """Фото уходит в модель как ``image_url`` data-URL, а не текстом."""
    AiNoteParser(_client(ai_client), "test-model").parse(
        "", image=b"\xff\xd8jpeg", file_id="big"
    )

    image = ai_client["calls"][-1]["messages"][1]["content"][-1]

    assert image["type"] == "image_url"
    assert image["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_system_prompt_contains_columns_schema(ai_client: UseAi) -> None:
    """Промпт: правила из таблицы, источники и схема ответа по колонкам листа."""
    columns = ["SUMMARY", "URL", "SALARY"]
    AiNoteParser(_client(ai_client), "test-model").parse(
        "text", sources=["Blog"], columns=columns, ai_prompt="Always use $"
    )

    system = ai_client["calls"][-1]["messages"][0]["content"]
    schema = '{"SUMMARY": "...", "URL": "...", "SALARY": "..."}'

    assert AI_PROMPT_HEADER in system and schema in system
    assert all(part in system for part in ("Always use $", "Blog", "SALARY"))
    assert "extra_fields" not in system


def test_field_headers_follow_current_table_columns() -> None:
    """field_headers: только заполняемые колонки, скриншот — по позиции."""
    headers = ["ID", "SOURCE", "TITLE", "SUMMARY", "URL", "SHOT", "SALARY"]
    headers.append("COMMENT")

    assert ai_parser.field_headers(headers, screen_position=6) == [
        "SUMMARY",
        "URL",
        "SALARY",
    ]


def test_draft_fields_skip_service_columns() -> None:
    """draft_fields: поля черновика по шапке + SCREEN, без служебных."""
    draft = AiNoteDraft(
        fields={"SUMMARY": "Text", "URL": "https://x", "COMMENT": "ignored"},
        screen="fid",
    )

    assert ai_parser.draft_fields(draft) == {
        "SUMMARY": "Text",
        "URL": "https://x",
        "SCREEN": "fid",
    }


# --- Репозиторий: правила разбора из таблицы ---


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        pytest.param(["", "", "AI_PROMT", "Use $", "", "", ""], "Use $", id="by-title"),
        pytest.param(["", "AI_PROMT", "Rules", "", "5$", "", ""], "5$", id="by-source"),
        pytest.param(["", "Blog", "Note", "", "", "", ""], "", id="no-prompt-row"),
    ],
)
def test_get_ai_prompt_reads_service_row(row: list[str], expected: str) -> None:
    """get_ai_prompt: строка AI_PROMT по TITLE/SOURCE, текст из DESCRIPTION."""
    repo, _ = repo_for([row])

    assert repo.get_ai_prompt() == expected


# --- Сценарий ---


def test_ai_note_without_key_explains_setup(
    use_repo: UseRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без AI_API_KEY бот работает, а ✨ AI Note объясняет, что ключа нет."""
    monkeypatch.setattr(ai_note_handler, "ai_api_key", lambda: "")
    repo, _ = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    state = FakeState()
    trigger = callback(Cb.AI_NOTE)

    asyncio.run(ai_note_handler.cb_ai_note(trigger, state))

    assert trigger.message.sent[-1]["text"] == buttons.AI_NO_KEY
    assert state.state is None


def test_ai_note_draft_from_text_then_save(
    use_repo: UseRepo, monkeypatch: pytest.MonkeyPatch, ai_client: UseAi
) -> None:
    """Текст ➔ «✨ AI Draft» с кнопками ➔ «✅ Save» пишет поля и карточку."""
    _use_ai_key(monkeypatch, ai_client)
    _, sheet = _repo_with_price(use_repo)
    state = FakeState()
    _open_ai(state)
    draft_message = _draft(state, "Some forwarded text")

    analyzing, draft = draft_message.sent[0], draft_message.sent[-1]
    assert analyzing["text"] == buttons.AI_ANALYZING
    # Пользовательская колонка (PRICE) видна в предпросмотре до сохранения.
    assert all(
        part in draft["text"] for part in ("AI title", "AI text", "<b>PRICE:</b> 10$")
    )
    assert [button.text for button in flat(draft["reply_markup"])] == [
        buttons.AI_SAVE,
        buttons.AI_CHANGE_SOURCE,
        buttons.AI_EDIT_TITLE,
        buttons.CANCEL,
    ]

    save = callback(Cb.AI_SAVE)
    asyncio.run(ai_note_handler.cb_ai_save(save, state))

    # Новая заметка отсортирована в конец: первой едет исходная «a».
    row = rows_by_header(sheet)[-1]
    assert row["SOURCE"] == "Blog" and row["TITLE"] == "AI title"
    assert row["DESCRIPTION"] == "AI text" and row["PRICE"] == "10$"
    assert "AI title" in save.message.sent[-1]["text"]
    assert state.state is None


def test_ai_note_follows_renamed_and_new_columns(
    use_repo: UseRepo, monkeypatch: pytest.MonkeyPatch, ai_client: UseAi
) -> None:
    """DESCRIPTION ➔ SUMMARY + новая SALARY: промпт, предпросмотр и запись."""
    _use_ai_key(monkeypatch, ai_client)
    ai_client["reply"] = (
        '{"source": "Blog", "title": "Raise", '
        '"fields": {"SUMMARY": "New role", "SALARY": "100k"}}'
    )
    headers = [
        "ID",
        "SOURCE",
        "TITLE",
        "SUMMARY",
        "URL",
        "SCREEN",
        "SALARY",
        "COMMENT",
    ]
    repo, sheet = repo_for([note_row("1", "Blog", "a")], headers=headers)
    use_repo(repo)
    state = FakeState()
    _open_ai(state)
    draft_message = _draft(state, "I got a raise")

    system = ai_client["calls"][-1]["messages"][0]["content"]
    assert '"SUMMARY": "..."' in system and '"SALARY": "..."' in system
    text = draft_message.sent[-1]["text"]
    assert all(
        part in text for part in ("<b>SUMMARY:</b> New role", "<b>SALARY:</b> 100k")
    )
    assert text.index("<b>SUMMARY:</b>") < text.index("<b>SALARY:</b>")

    asyncio.run(ai_note_handler.cb_ai_save(callback(Cb.AI_SAVE), state))

    row = rows_by_header(sheet)[-1]
    assert row["SUMMARY"] == "New role" and row["SALARY"] == "100k"
    assert row["TITLE"] == "Raise"


def test_ai_note_photo_saves_file_id_into_screen(
    use_repo: UseRepo, monkeypatch: pytest.MonkeyPatch, ai_client: UseAi
) -> None:
    """Фото: file_id самого большого размера пишется в SCREEN при сохранении."""
    _use_ai_key(monkeypatch, ai_client)
    repo, sheet = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    state = FakeState()
    _open_ai(state)
    photo_message = message("", user_id=1)
    photo_message.photo = [_Photo("small"), _Photo("big_file_id")]

    asyncio.run(ai_note_handler.got_ai_message(photo_message, state))
    asyncio.run(ai_note_handler.cb_ai_save(callback(Cb.AI_SAVE), state))

    assert rows_by_header(sheet)[-1]["SCREEN"] == "big_file_id"
    assert photo_message.bot.downloaded[-1].file_id == "big_file_id"


def test_ai_note_link_uses_page_title(
    use_repo: UseRepo, monkeypatch: pytest.MonkeyPatch, ai_client: UseAi
) -> None:
    """Ссылка: бот загружает страницу (через ``fetch_page``) и разбирает её."""
    _use_ai_key(monkeypatch, ai_client)
    seen: list[str] = []
    monkeypatch.setattr(
        ai_parser,
        "fetch_page",
        lambda url: (seen.append(url) or "Page title", "Page body"),
    )
    repo, _ = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    state = FakeState()
    _open_ai(state)

    link_message = _draft(state, "https://example.com/post")

    assert seen == ["https://example.com/post"]
    assert all(
        part in link_message.sent[-1]["text"] for part in ("AI Draft", "AI title")
    )


def test_ai_note_change_source_updates_draft(
    use_repo: UseRepo, monkeypatch: pytest.MonkeyPatch, ai_client: UseAi
) -> None:
    """«🗂 Change source»: выбор из списка меняет SOURCE черновика."""
    _use_ai_key(monkeypatch, ai_client)
    repo, sheet = repo_for([note_row("1", "Blog", "a"), note_row("2", "YouTube", "b")])
    use_repo(repo)
    state = FakeState()
    _open_ai(state)
    _draft(state)

    switch = callback(Cb.AI_SOURCE)
    asyncio.run(ai_note_handler.cb_ai_source(switch, state))
    texts = [button.text for button in flat(switch.message.sent[-1]["reply_markup"])]

    assert texts[:2] == ["🗂 Blog", "🗂 YouTube"]
    asyncio.run(ai_note_handler.cb_ai_pick_source(callback(Cb.ai_source(1)), state))
    asyncio.run(ai_note_handler.cb_ai_save(callback(Cb.AI_SAVE), state))
    assert rows_by_header(sheet)[-1]["SOURCE"] == "YouTube"


def test_ai_note_new_source_from_draft(
    use_repo: UseRepo, monkeypatch: pytest.MonkeyPatch, ai_client: UseAi
) -> None:
    """Кнопка «➕ New source» в черновике добавляет источник и меняет SOURCE."""
    _use_ai_key(monkeypatch, ai_client)
    repo, sheet = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    state = FakeState()
    _open_ai(state)
    _draft(state)
    asyncio.run(ai_note_handler.cb_ai_source(callback(Cb.AI_SOURCE), state))
    asyncio.run(ai_note_handler.cb_add_ai_source(callback(Cb.SRC_ADD), state))

    name_message = message("Podcast", user_id=1)
    asyncio.run(ai_note_handler.got_ai_message(name_message, state))

    assert name_message.sent[0]["text"] == buttons.SOURCE_ADDED.format(source="Podcast")
    assert "<b>AI Draft</b>" in name_message.sent[-1]["text"]
    asyncio.run(ai_note_handler.cb_ai_save(callback(Cb.AI_SAVE), state))
    assert rows_by_header(sheet)[-1]["SOURCE"] == "Podcast"


def test_ai_note_edit_title_before_save(
    use_repo: UseRepo, monkeypatch: pytest.MonkeyPatch, ai_client: UseAi
) -> None:
    """«✏️ Edit title»: свой заголовок попадает в предпросмотр и в таблицу."""
    _use_ai_key(monkeypatch, ai_client)
    repo, sheet = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    state = FakeState()
    _open_ai(state)
    _draft(state)
    asyncio.run(ai_note_handler.cb_ai_title(callback(Cb.AI_TITLE), state))

    title_message = message("My title", user_id=1)
    asyncio.run(ai_note_handler.got_ai_message(title_message, state))
    asyncio.run(ai_note_handler.cb_ai_save(callback(Cb.AI_SAVE), state))

    assert "<b>TITLE:</b> My title" in title_message.sent[-1]["text"]
    assert rows_by_header(sheet)[-1]["TITLE"] == "My title"


def test_ai_note_broken_model_answer_is_reported(
    use_repo: UseRepo, monkeypatch: pytest.MonkeyPatch, ai_client: UseAi
) -> None:
    """Модель ответила без JSON: пользователь видит AI_FAILED, лист не тронут."""
    _use_ai_key(monkeypatch, ai_client)
    ai_client["reply"] = "Sorry, no JSON"
    repo, sheet = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    state = FakeState()
    _open_ai(state)
    broken_message = _draft(state)

    assert broken_message.sent[-1]["text"] == buttons.AI_FAILED
    assert len(sheet.rows) == 2


def test_ai_note_photo_on_text_model_hints_vision(
    use_repo: UseRepo, monkeypatch: pytest.MonkeyPatch, ai_client: UseAi
) -> None:
    """Модель без vision отвергла фото — подсказка про Vision в AI_MODEL."""
    _use_ai_key(monkeypatch, ai_client)
    ai_client["error"] = "400: This model does not support image_url input"
    repo, sheet = repo_for([note_row("1", "Blog", "a")])
    use_repo(repo)
    state = FakeState()
    _open_ai(state)
    photo_message = message("", user_id=1)
    photo_message.photo = [_Photo("big_file_id")]

    asyncio.run(ai_note_handler.got_ai_message(photo_message, state))

    assert photo_message.sent[-1]["text"] == buttons.AI_NO_VISION
    assert "AI_MODEL" in buttons.AI_NO_VISION
    assert len(sheet.rows) == 2


def test_vision_error_detection() -> None:
    """is_vision_error узнаёт жалобы модели на картинки."""
    flags = [
        ai_parser.is_vision_error(Exception("unsupported image_url")),
        ai_parser.is_vision_error(Exception("model has no vision support")),
        ai_parser.is_vision_error(Exception("429 quota exceeded")),
    ]

    assert flags == [True, True, False]
