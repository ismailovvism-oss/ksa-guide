"""Категории справочника.

Слаг — то, что лежит в БД и ходит в API; название — то, что видит человек.
Локации остаются одной из категорий, а не отдельной сущностью: карточка
локации — такое же вхождение из канала, просто с картой вместо цены.
"""

from __future__ import annotations

CATEGORIES: dict[str, str] = {
    "rent": "Аренда жилья",
    "sale": "Продажа недвижимости",
    "jobs": "Работа",
    "services": "Услуги",
    "goods": "Куплю/продам",
    "transport": "Транспорт и перевозки",
    "food": "Еда и доставка",
    "events": "События",
    "location": "Локации и места",
    "other": "Другое",
}

# Категории, где деньги — обязательный атрибут карточки. Для них расхождение
# цены между вхождениями считается сильным доводом «это разные объявления».
PRICED = {"rent", "sale", "goods", "services", "transport"}

# С чего начинаем: пока в проде только эти, остальное копится в raw_message
# и включается, когда качество разбора по аренде доведено.
MVP = ["rent", "location"]

DEFAULT = "other"


def is_valid(slug: str | None) -> bool:
    return slug in CATEGORIES


def normalize(slug: str | None) -> str:
    return slug if is_valid(slug) else DEFAULT


def title(slug: str | None) -> str:
    return CATEGORIES.get(slug or "", CATEGORIES[DEFAULT])
