"""Разбор поста в структуру: категория, город, цена, комнаты.

Контакты моделью не извлекаются — регулярки из dedup.normalize делают это
надёжнее и бесплатно, а от контактов зависит вся склейка дублей.

Два режима:
  extract_one   — один пост, для потока новых сообщений;
  extract_batch — Batch API, вдвое дешевле, для разбора накопленного архива.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from .. import categories, config

# По умолчанию — самая сильная модель: на разборе живых объявлений с опечатками,
# смесью языков и рекламным мусором качество важнее цены. Для разбора архива
# в десятки тысяч постов имеет смысл поставить в .env модель подешевле:
#   KSA_PARSE_MODEL=claude-haiku-4-5
MODEL = config.env("KSA_PARSE_MODEL", "claude-opus-5")

# Извлечение — не рассуждательная задача, низкое усилие экономит токены.
EFFORT = config.env("KSA_PARSE_EFFORT", "low")

MAX_TOKENS = 1024

SYSTEM = """Ты разбираешь объявления из русскоязычных Telegram-каналов о Саудовской Аравии.

Верни строго те факты, которые есть в тексте. Ничего не додумывай:
если поля нет — оставь null. Пустое поле лучше выдуманного.

is_listing = false для постов, которые объявлением не являются: новости,
приветствия, опросы, реклама самого канала, обсуждения.

Города пиши по-русски в именительном падеже: Мекка, Медина, Джидда, Эр-Рияд,
Даммам, Таиф, Абха, Янбу, Аль-Ула. Район (district) — только если он назван
явно; не выводи район из описания вроде «рядом с Харамом».

Цена: price_amount — только число. price_currency — код валюты (SAR для риалов).
price_period — month, year, day или once. Если валюта не указана явно,
но текст на русском про Саудию — считай, что это SAR."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_listing": {
            "type": "boolean",
            "description": "true, если это объявление, а не новость или болтовня",
        },
        "category": {
            "type": "string",
            "enum": list(categories.CATEGORIES),
            "description": "категория объявления",
        },
        "title": {
            "type": "string",
            "description": "короткий заголовок карточки, до 70 знаков, без эмодзи",
        },
        "summary": {
            "type": "string",
            "description": "суть объявления в 1-2 предложениях, без контактов и призывов",
        },
        "city": {"type": ["string", "null"]},
        "district": {"type": ["string", "null"]},
        "price_amount": {"type": ["number", "null"]},
        "price_currency": {"type": ["string", "null"]},
        "price_period": {
            "type": ["string", "null"],
            "enum": ["month", "year", "day", "once", None],
        },
        "rooms": {"type": ["number", "null"]},
        "area_sqm": {"type": ["number", "null"]},
    },
    "required": [
        "is_listing", "category", "title", "summary", "city", "district",
        "price_amount", "price_currency", "price_period", "rooms", "area_sqm",
    ],
    "additionalProperties": False,
}

OUTPUT_CONFIG = {"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}}


@dataclass
class Parsed:
    is_listing: bool = True
    category: str = categories.DEFAULT
    title: str = ""
    summary: str | None = None
    city: str | None = None
    district: str | None = None
    price_amount: float | None = None
    price_currency: str | None = None
    price_period: str | None = None
    rooms: float | None = None
    area_sqm: float | None = None
    parse_model: str = "rules"

    def as_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["is_listing"] = int(row["is_listing"])
        row["category"] = categories.normalize(row["category"])
        return row


def _client():
    import anthropic

    if not config.ANTHROPIC_API_KEY:
        # Ключ может лежать не в .env, а в профиле `ant auth login` —
        # тогда пустой конструктор сам его найдёт.
        return anthropic.Anthropic()
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _prompt(text: str) -> str:
    return f"Разбери этот пост:\n\n<post>\n{text.strip()}\n</post>"


def _from_json(payload: dict[str, Any], model: str) -> Parsed:
    fields = {k: payload.get(k) for k in Parsed.__dataclass_fields__ if k != "parse_model"}
    fields["is_listing"] = bool(fields.get("is_listing", True))
    fields["category"] = categories.normalize(fields.get("category"))
    fields["title"] = (fields.get("title") or "").strip()[:120] or "Без названия"
    return Parsed(**fields, parse_model=model)


def fallback(text: str) -> Parsed:
    """Разбор без модели: цена регуляркой, остальное пусто.

    Нужен, когда ключа нет или запрос не прошёл, — вхождение всё равно
    попадёт в базу и будет разобрано позже.
    """
    from ..dedup.normalize import parse_price

    price = parse_price(text)
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return Parsed(
        title=first_line[:120] or "Без названия",
        summary=text.strip()[:400] or None,
        price_amount=price[0] if price else None,
        price_currency=price[1] if price else None,
    )


def extract_one(text: str) -> Parsed:
    if not text.strip():
        return Parsed(is_listing=False, title="Пустой пост")
    try:
        response = _client().messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            output_config=OUTPUT_CONFIG,
            messages=[{"role": "user", "content": _prompt(text)}],
        )
    except Exception:
        return fallback(text)

    if response.stop_reason == "refusal":
        return fallback(text)
    payload = next((b.text for b in response.content if b.type == "text"), None)
    if not payload:
        return fallback(text)
    try:
        return _from_json(json.loads(payload), MODEL)
    except (json.JSONDecodeError, TypeError, ValueError):
        return fallback(text)


def submit_batch(items: list[tuple[str, str]]) -> str:
    """Отправляет пачку постов на разбор. items — список (custom_id, текст).

    Batch API дешевле обычного вдвое и обрабатывает до 100 000 запросов;
    это основной способ разобрать накопленный архив канала.
    """
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    batch = _client().messages.batches.create(
        requests=[
            Request(
                custom_id=custom_id,
                params=MessageCreateParamsNonStreaming(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=SYSTEM,
                    output_config=OUTPUT_CONFIG,
                    messages=[{"role": "user", "content": _prompt(text)}],
                ),
            )
            for custom_id, text in items
        ]
    )
    return batch.id


def batch_ready(batch_id: str) -> bool:
    return _client().messages.batches.retrieve(batch_id).processing_status == "ended"


def collect_batch(batch_id: str) -> dict[str, Parsed]:
    """Результаты пачки по custom_id. Порядок ответов произвольный."""
    out: dict[str, Parsed] = {}
    for result in _client().messages.batches.results(batch_id):
        if result.result.type != "succeeded":
            continue
        message = result.result.message
        if message.stop_reason == "refusal":
            continue
        payload = next((b.text for b in message.content if b.type == "text"), None)
        if not payload:
            continue
        try:
            out[result.custom_id] = _from_json(json.loads(payload), MODEL)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return out
