"""
AI-разбор сообщения в черновик заметки (✨ AI Note).

Модуль делает три вещи и ничего не знает про Telegram и Google Sheets:

* по ссылке достаёт заголовок и текст веб-страницы (``fetch_page``);
* собирает черновик ``AiNoteDraft`` строгим JSON-разбором ответа модели
  (``AiNoteParser``);
* готовит черновик к записи: ``draft_fields`` — колонка -> значение.

Клиент модели приходит снаружи (инъекция): тесты подставляют двойник и
не ходят в сеть.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Protocol, Sequence

logger = logging.getLogger(__name__)

__all__ = [
    "AI_PROMPT_HEADER",
    "PAGE_TEXT_LIMIT",
    "PAGE_TIMEOUT",
    "RAW_PREVIEW_LIMIT",
    "AiClient",
    "AiDraftError",
    "AiNoteDraft",
    "AiNoteParser",
    "AiVisionError",
    "SERVICE_HEADERS",
    "draft_fields",
    "extract_json",
    "fetch_page",
    "field_headers",
    "is_vision_error",
    "page_kind",
    "system_prompt",
]

#: Сколько символов ответа модели кладём в текст ошибки и в лог.
RAW_PREVIEW_LIMIT = 150

#: Заголовок секции кастомных правил в системном промпте.
AI_PROMPT_HEADER = "CUSTOM RULES FROM THE USER TABLE:"

#: Таймаут загрузки страницы по ссылке, секунды.
PAGE_TIMEOUT = 10.0
#: Сколько символов страницы отдаём модели: остальное всё равно не нужно.
PAGE_TEXT_LIMIT = 4000
#: Служебные колонки листа: их заполняет не модель, а сам сценарий.
SERVICE_HEADERS = {"ID", "SOURCE", "TITLE", "SCREEN", "COMMENT"}
#: Служебные ключи ответа модели — это не колонки листа.
_META_KEYS = {"source", "title", "fields", "extra_fields", "screen"}
#: Начало ссылки: по нему отличаем ссылку от пересланного текста.
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


# --- Двойники и протоколы ---


class _Message(Protocol):
    """Ответ модели: нужен только ``.content`` (текст или None)."""

    content: str | None


class _Choice(Protocol):
    """Один вариант ответа модели."""

    message: _Message


class _Completion(Protocol):
    """Ответ chat.completions.create."""

    choices: Sequence[_Choice]


class AiClient(Protocol):
    """Минимальный клиент модели: достаточно ``chat.completions.create``."""

    chat: Any


class AiDraftError(ValueError):
    """Модель вернула не тот ответ — черновик собрать нельзя."""


class AiVisionError(AiDraftError):
    """Модель не умеет читать картинки — нужна Vision-модель в ``AI_MODEL``."""


#: Признаки того, что модель отвергла изображение (текст ошибки провайдера).
_VISION_MARKERS = (
    "image_url",
    "image input",
    "does not support image",
    "doesn't support image",
    "no vision",
    "vision",
    "multimodal",
    "not a multimodal",
)


def is_vision_error(error: Exception) -> bool:
    """Ошибка провайдера именно из-за картинки (модель без поддержки vision)."""
    text = str(error).lower()
    return any(marker in text for marker in _VISION_MARKERS)


def field_headers(
    headers: Sequence[str], screen_position: int | None = None
) -> list[str]:
    """
    Заполняемые AI колонки листа — в порядке шапки.

    Убираем служебные ``ID``/``SOURCE``/``TITLE``/``COMMENT`` и колонку
    скриншотов: даже если её переименовали, она узнаётся по штатной позиции
    (``screen_position`` из ``screen_slot_index``). Новые колонки пользователя
    (``SUMMARY``, ``SALARY``, ...) попадают в схему без правок кода.
    """
    columns: list[str] = []
    for index, header in enumerate(headers, start=1):
        name = header.strip()
        if not name or name.upper() in SERVICE_HEADERS:
            continue
        if index == screen_position:
            continue
        columns.append(name)
    return columns


def system_prompt(
    sources: Sequence[str] = (),
    columns: Sequence[str] = (),
    ai_prompt: str = "",
) -> str:
    """
    Системный промпт: схема ответа, источники, колонки и кастомные правила.

    Схема строится из актуальных заголовков листа (``columns``), поэтому
    переименованная или добавленная колонка сразу видна модели. ``ai_prompt`` —
    текст служебной строки ``AI_PROMT``: его дописываем отдельной секцией.
    """
    schema = "{" + ", ".join(f'"{column}": "..."' for column in columns) + "}"
    lines = [
        "You turn one user message into a note draft.",
        "Answer with a strict JSON object and nothing else:",
        '{"source": "...", "title": "...", "fields": ' + schema + "}",
        "Never reply with apologies, explanations or questions.",
        "Never say that you cannot open the link or have no internet access.",
        "A message may be a bare URL without page text: still answer with JSON.",
        "Then put the link itself into the url field and a short title derived "
        "from the domain or the URL path into title.",
        "Omit any field you cannot infer from the message or the page text.",
        "Choose source from the existing list, otherwise invent a short one.",
        "Use fields only for these columns: "
        + (", ".join(columns) if columns else "(none)"),
        "Leave a field out when the message has no value for it.",
    ]
    if sources:
        lines.append("Existing sources: " + ", ".join(sources))
    if ai_prompt:
        lines.extend([AI_PROMPT_HEADER, ai_prompt])
    return "\n".join(lines)


@dataclass
class AiNoteDraft:
    """Черновик заметки: то, что предлагается пользователю на предпросмотре."""

    source: str = ""
    title: str = ""
    #: Заполненные колонки листа: имя из шапки -> значение (без служебных).
    fields: dict[str, str] = field(default_factory=dict)
    #: file_id фото Telegram для колонки SCREEN (в модель уходит сама картинка).
    screen: str = ""


# --- Ссылки ---


def page_kind(text: str) -> str:
    """'link' — если текст это одна ссылка ``http(s)://``, иначе 'text'."""
    return "link" if _URL_RE.match((text or "").strip()) else "text"


def _clean_html(raw: str) -> tuple[str, str]:
    """(заголовок <title>, текст страницы без скриптов и тегов)."""
    parser = _PageParser()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:  # битый HTML: берём то, что успели разобрать
        pass
    text = " ".join(parser.chunks)
    text = _TAG_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return " ".join(parser.title.split()), text


class _PageParser(HTMLParser):
    """Вытягивает <title> и видимый текст, молча пропуская script/style."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.chunks: list[str] = []
        self._in_title = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        elif tag in {"script", "style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip_depth:
            self.chunks.append(data)


def fetch_page(url: str) -> tuple[str, str]:
    """
    (заголовок, текст) веб-страницы по ссылке.

    При любой ошибке сети (403, таймаут, битый HTML) возвращает пустые
    строки: разбор продолжится по самой ссылке, бот не падает.
    """
    try:
        import requests

        response = requests.get(
            url,
            timeout=PAGE_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 (compatible; BigNoteBot/1.0)"},
        )
        response.raise_for_status()
        title, text = _clean_html(response.text or "")
    except Exception:
        return "", ""
    return title, text[:PAGE_TEXT_LIMIT]


# --- Разбор ответа модели ---


def extract_json(raw: str) -> dict[str, Any]:
    """
    Строгий JSON из ответа модели.

    Модель иногда «заворачивает» объект в ```json ... ``` или добавляет
    пояснения вокруг: срезаем обёртку и берём подстроку от первой ``{``
    до последней ``}``. Любая другая форма — ``AiDraftError``.
    """
    text = (raw or "").strip()
    text = _FENCE_RE.sub("", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise AiDraftError("AI returned no JSON object: " + text[:RAW_PREVIEW_LIMIT])
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as error:
        raise AiDraftError(f"AI returned broken JSON: {error}") from error
    if not isinstance(data, dict):
        raise AiDraftError("AI returned JSON that is not an object.")
    return data


def _text(value: Any) -> str:
    """Строковое значение поля из ответа модели (всё остальное — пусто)."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return ""


class AiNoteParser:
    """
    Разбор сообщения (текст/ссылка/фото) в ``AiNoteDraft``.

    Системный промпт собирается из кастомных правил пользователя
    (``ai_prompt``), списка существующих источников и пользовательских
    колонок листа: модель сразу отвечает в нужной схеме.
    """

    def __init__(self, client: AiClient, model: str) -> None:
        self._client = client
        self._model = model

    def parse(
        self,
        text: str = "",
        image: bytes | None = None,
        file_id: str = "",
        sources: Sequence[str] = (),
        columns: Sequence[str] = (),
        ai_prompt: str = "",
    ) -> AiNoteDraft:
        """Черновик заметки из сообщения (текст, ссылка или фото)."""
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": system_prompt(sources, columns, ai_prompt),
                    },
                    {"role": "user", "content": self._content(text, image)},
                ],
                temperature=0,
            )
        except Exception as error:
            # Фото + жалоба модели на изображение = нужна Vision-модель.
            if image is not None and is_vision_error(error):
                raise AiVisionError(self._model) from error
            raise
        raw = response.choices[0].message.content or ""
        try:
            return self._draft_from(raw, file_id=file_id, wanted=columns)
        except AiDraftError:
            if page_kind(text) != "link":
                raise
            # Блокирующие парсинг ссылки (lnkd.in, LinkedIn) часто приводят
            # к ответу «не могу открыть ссылку». Не роняем сценарий:
            # отдаём базовый черновик, который пользователь доделает сам.
            logger.warning(
                "AI returned no usable JSON for link %s: %r",
                text.strip(),
                (raw or "").strip()[:RAW_PREVIEW_LIMIT],
            )
            return self._link_draft(text, file_id=file_id, wanted=columns)

    @staticmethod
    def _link_draft(
        text: str, file_id: str = "", wanted: Sequence[str] = ()
    ) -> AiNoteDraft:
        """
        Базовый черновик для ссылки: URL в колонку URL, сам URL — в title.

        Поле ``url`` ищется по имени колонки (без учёта регистра), поэтому
        работает и для переименованных шапок; фото (``file_id``) не теряется.
        """
        link = (text or "").strip()
        url_header = next(
            (name.strip() for name in wanted if name.strip().upper() == "URL"), ""
        )
        fields = {url_header: link} if url_header else {}
        return AiNoteDraft(title=link, fields=fields, screen=file_id)

    # --- сообщение для модели ---

    def _content(self, text: str, image: bytes | None) -> Any:
        """Части user-сообщения; для фото добавляется ``image_url`` data-URL."""
        parts = self._parts(text)
        if image is None:
            return parts
        encoded = base64.b64encode(image).decode("ascii")
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
            }
        )
        return parts

    @staticmethod
    def _parts(text: str) -> list[dict[str, Any]]:
        """
        Части user-сообщения: текст или ссылка (с заголовком и текстом страницы).

        Загрузка страницы идёт только для ссылки: текст и фото в сеть не ходят.
        """
        value = (text or "").strip()
        if page_kind(value) != "link":
            return [{"type": "text", "text": value or "Describe this screenshot."}]
        title, page = fetch_page(value)
        body = f"URL: {value}"
        if title or page:
            body += f"\nPage title: {title}\nPage text: {page}"
        return [{"type": "text", "text": body}]

    # --- ответ модели ---

    def _draft_from(
        self, raw: str, file_id: str = "", wanted: Sequence[str] = ()
    ) -> AiNoteDraft:
        """
        Строгий JSON ответа -> черновик.

        Поля берём из ``fields`` (так просит системный промпт); для обратной
        совместимости понимаем и старый ``extra_fields``, и плоские ключи
        вида ``{"PRICE": "10$"}``. В ``fields`` остаются только колонки,
        которые реально есть в шапке пользователя.
        """
        data = extract_json(raw)
        allowed = {name.strip().upper(): name.strip() for name in wanted}
        fields: dict[str, str] = {}
        for section in (data.get("fields"), data.get("extra_fields")):
            if isinstance(section, dict):
                fields.update(self._known_fields(section, allowed))
        flat = {key: value for key, value in data.items() if key not in _META_KEYS}
        fields.update(self._known_fields(flat, allowed))
        return AiNoteDraft(
            source=_text(data.get("source")),
            title=_text(data.get("title")),
            fields=fields,
            screen=file_id,
        )

    @staticmethod
    def _known_fields(
        values: dict[str, Any], allowed: dict[str, str]
    ) -> dict[str, str]:
        """Только заполненные колонки из шапки листа (без служебных)."""
        known: dict[str, str] = {}
        for name, value in values.items():
            header = allowed.get(str(name).strip().upper())
            text = _text(value)
            if header and text and header.upper() not in SERVICE_HEADERS:
                known[header] = text
        return known


def draft_fields(draft: AiNoteDraft) -> dict[str, str]:
    """
    Заполненные поля черновика «колонка -> значение» для записи в лист.

    Это всё, что модель вернула по шапке пользователя (``SUMMARY``, ``PRICE``,
    ``URL``, ...), плюс ``SCREEN`` с file_id фото. Служебные ``ID``,
    ``SOURCE``, ``TITLE`` и ``COMMENT`` пишутся отдельно (``create``).
    """
    fields: dict[str, str] = {
        name.strip(): value
        for name, value in draft.fields.items()
        if name.strip() and name.strip().upper() not in SERVICE_HEADERS
    }
    if draft.screen:
        fields["SCREEN"] = draft.screen
    return fields
