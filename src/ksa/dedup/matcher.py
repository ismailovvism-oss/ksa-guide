"""Сравнение двух объявлений: одно это и то же или разные.

Ни один сигнал по отдельности не надёжен — телефон повторяется у агента с
десятком квартир, текст переписывают, фото берут из интернета. Поэтому
считается взвешенная сумма с явными штрафами за расхождения, а не «или-или».
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz

from .. import categories
from . import phash as phash_mod

MERGE_THRESHOLD = 0.70   # выше — склеиваем в одну карточку
REVIEW_THRESHOLD = 0.55  # между порогами — на глаз модератору


@dataclass
class Item:
    """Одно объявление в виде, пригодном для сравнения."""

    norm_text: str = ""
    contacts: list[dict[str, str]] = field(default_factory=list)
    media_phash: str | None = None
    category: str | None = None
    city: str | None = None
    district: str | None = None
    price_amount: float | None = None
    price_currency: str | None = None
    rooms: float | None = None
    area_sqm: float | None = None

    def contact_keys(self) -> set[str]:
        return {f"{c['type']}:{c['value']}" for c in self.contacts if c.get("value")}


@dataclass
class Match:
    score: float
    reasons: list[str]

    @property
    def merge(self) -> bool:
        return self.score >= MERGE_THRESHOLD

    @property
    def needs_review(self) -> bool:
        return REVIEW_THRESHOLD <= self.score < MERGE_THRESHOLD


def _price_verdict(left: Item, right: Item) -> tuple[float, str] | None:
    """Цена — сильный различитель, но только внутри одной валюты."""
    a, b = left.price_amount, right.price_amount
    if not a or not b:
        return None
    if left.price_currency and right.price_currency and left.price_currency != right.price_currency:
        return None
    diff = abs(a - b) / max(a, b)
    if diff <= 0.05:
        return 0.15, "цена совпадает"
    if diff >= 0.25 and categories.normalize(left.category) in categories.PRICED:
        return -0.35, f"цена расходится на {diff:.0%}"
    return None


def compare(left: Item, right: Item, common_contacts: set[str] | None = None) -> Match:
    """Оценка «это одно и то же» в диапазоне примерно 0..1.

    common_contacts — контакты, которые встречаются в канале настолько часто,
    что это подпись канала или телефон агентства, а не признак конкретного
    объявления. Они учитываются со сниженным весом.
    """
    common = common_contacts or set()
    score = 0.0
    reasons: list[str] = []

    # Разные категории или города — почти наверняка разные объявления.
    if left.category and right.category and left.category != right.category:
        return Match(0.0, ["разные категории"])
    if left.city and right.city and left.city != right.city:
        score -= 0.25
        reasons.append("разные города")

    # Полное совпадение очищенного текста — дальше можно не считать.
    if left.norm_text and left.norm_text == right.norm_text:
        return Match(1.0, ["текст совпадает дословно"])

    shared = left.contact_keys() & right.contact_keys()
    same_contact = False
    if shared:
        personal = shared - common
        if personal:
            phones = [k for k in personal if k.startswith("phone:")]
            score += 0.60 if phones else 0.40
            reasons.append("тот же телефон" if phones else "тот же telegram-контакт")
            same_contact = True
        else:
            score += 0.10
            reasons.append("контакт совпадает, но он общий для канала")

    distance = phash_mod.hamming(left.media_phash, right.media_phash)
    same_photo = distance <= phash_mod.SAME_IMAGE
    if same_photo:
        score += 0.40
        reasons.append(f"то же фото (расстояние {distance})")

    if left.norm_text and right.norm_text:
        ratio = fuzz.token_set_ratio(left.norm_text, right.norm_text)
        if ratio >= 95:
            score += 0.50
            reasons.append(f"текст почти идентичен ({ratio:.0f}%)")
        elif ratio >= 88:
            score += 0.30
            reasons.append(f"текст очень похож ({ratio:.0f}%)")
        elif ratio >= 78:
            score += 0.15
            reasons.append(f"текст похож ({ratio:.0f}%)")
        elif ratio < 55 and not (same_photo or same_contact):
            # Штрафуем за непохожий текст, только если больше опереться не на
            # что. Когда сошлись фото или личный контакт, переписанный текст —
            # ожидаемое поведение перепоста, а не довод против.
            score -= 0.10
            reasons.append(f"тексты разные ({ratio:.0f}%)")

    verdict = _price_verdict(left, right)
    if verdict:
        delta, reason = verdict
        score += delta
        reasons.append(reason)

    if left.rooms and right.rooms and left.rooms != right.rooms:
        score -= 0.20
        reasons.append("разное число комнат")

    if left.area_sqm and right.area_sqm:
        area_diff = abs(left.area_sqm - right.area_sqm) / max(left.area_sqm, right.area_sqm)
        if area_diff >= 0.15:
            score -= 0.15
            reasons.append("разная площадь")

    if left.district and right.district and left.district != right.district:
        score -= 0.10
        reasons.append("разные районы")

    return Match(max(0.0, min(1.0, score)), reasons)


def identity_key(item: Item) -> str | None:
    """Грубый ключ для быстрого отбора кандидатов до полного сравнения.

    Контакт + категория + город + ценовая корзина. Без контакта ключа нет:
    склеивать объявления по одной лишь категории и цене нельзя.
    """
    from .normalize import price_bucket

    phones = sorted(k for k in item.contact_keys() if k.startswith("phone:"))
    contact = phones[0] if phones else next(iter(sorted(item.contact_keys())), None)
    if not contact:
        return None
    return "|".join(
        [
            contact,
            categories.normalize(item.category),
            (item.city or "?").lower(),
            price_bucket(item.price_amount),
        ]
    )
