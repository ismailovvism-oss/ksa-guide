"""Приведение текста, телефонов и цен к сравнимому виду.

Всё сравнение объявлений опирается на этот модуль: пока телефон из двух
разных постов не сходится посимвольно, дедуп по контакту не работает.
"""

from __future__ import annotations

import re
import unicodedata

# --- телефоны ---------------------------------------------------------------

# Саудовские мобильные: 9 цифр после кода страны, начинаются на 5.
KSA_CODE = "966"

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

# Три записи, которые встречаются в объявлениях, с любыми разделителями внутри.
_PHONE_RE = re.compile(
    r"(?:(?:\+|00)\d[\d\s()\-.]{6,}\d)"     # +966…, 00966…
    r"|(?:\b966[\d\s()\-.]{7,}\d)"          # 966… — код страны без плюса
    r"|(?:\b0?5[\d\s()\-.]{7,}\d)"          # 05…, 5… — местная запись
)


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value.translate(_ARABIC_DIGITS))


def normalize_phone(value: str) -> str | None:
    """Возвращает номер в E.164 либо None, если это не похоже на телефон.

    Понимает саудовские записи: 05XXXXXXXX, 5XXXXXXXX, 9665XXXXXXXX,
    00966..., +966... — всё сводится к +9665XXXXXXXX.
    """
    digits = _digits(value)
    if not digits:
        return None

    if digits.startswith("00"):
        digits = digits[2:]

    # Саудовский мобильный в любой из локальных записей.
    if digits.startswith(KSA_CODE):
        rest = digits[len(KSA_CODE) :].lstrip("0")
        if len(rest) == 9 and rest.startswith("5"):
            return f"+{KSA_CODE}{rest}"
    if len(digits) == 10 and digits.startswith("05"):
        return f"+{KSA_CODE}{digits[1:]}"
    if len(digits) == 9 and digits.startswith("5"):
        return f"+{KSA_CODE}{digits}"

    # Иностранный номер: оставляем как есть, если длина правдоподобна.
    if 10 <= len(digits) <= 15:
        return f"+{digits}"
    return None


def extract_phones(text: str) -> list[str]:
    """Все телефоны из текста, в E.164, без повторов, в порядке появления."""
    seen: dict[str, None] = {}
    for match in _PHONE_RE.finditer(text.translate(_ARABIC_DIGITS)):
        phone = normalize_phone(match.group())
        if phone:
            seen.setdefault(phone, None)
    return list(seen)


# --- telegram-контакты ------------------------------------------------------

_USERNAME_RE = re.compile(r"(?:@|(?:https?://)?t\.me/)([A-Za-z][A-Za-z0-9_]{3,31})\b")

# Служебные имена, которые встречаются в подписях каналов и контактом не являются.
_USERNAME_STOPWORDS = {"joinchat", "share", "addstickers", "proxy", "iv"}


def extract_usernames(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for match in _USERNAME_RE.finditer(text):
        name = match.group(1).lower()
        if name not in _USERNAME_STOPWORDS:
            seen.setdefault(name, None)
    return list(seen)


def extract_contacts(text: str) -> list[dict[str, str]]:
    """Контакты одного поста: телефоны сначала, они надёжнее как ключ."""
    contacts = [{"type": "phone", "value": p} for p in extract_phones(text)]
    contacts += [{"type": "telegram", "value": u} for u in extract_usernames(text)]
    return contacts


# --- текст ------------------------------------------------------------------

_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_HASHTAG_RE = re.compile(r"#\w+")

# Хвосты-подписи каналов: не относятся к объявлению и мешают сравнению.
_FOOTER_RE = re.compile(
    r"^\s*(?:подпис\w*|наш\s+канал|канал\s*:|реклам\w*|сотрудничеств\w*|"
    r"по\s+вопросам\s+рекламы|прислать\s+объявлен\w*|бот\s+для\s+объявлен\w*|"
    r"subscribe|join\s+us)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)


# Подписи к контактам: в перепостах «Телефон:» превращается в «тел», а смысла
# в них нет — само значение контакта к этому моменту уже вырезано.
_CONTACT_LABEL_RE = re.compile(
    r"\b(?:тел(?:ефон)?|номер|звонить|звоните|писать|пишите|связь|контакт\w*|"
    r"whats?app|ватсап|вотсап|вацап|phone|tel|contact|call|mob(?:ile)?)\b",
    re.IGNORECASE,
)


def _strip_emoji(text: str) -> str:
    """Убирает эмодзи и прочие пиктограммы: в перепостах их часто меняют."""
    return "".join(
        ch for ch in text if unicodedata.category(ch) not in {"So", "Sk", "Cf", "Cs"}
    )


def normalize_text(text: str) -> str:
    """Текст для сравнения: без ссылок, контактов, эмодзи и подписей канала.

    Цифры сохраняются — цена и площадь как раз и отличают похожие объявления
    друг от друга.
    """
    text = unicodedata.normalize("NFKC", text).translate(_ARABIC_DIGITS)
    text = _FOOTER_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    text = _USERNAME_RE.sub(" ", text)
    text = _PHONE_RE.sub(" ", text)
    text = _HASHTAG_RE.sub(" ", text)
    text = _CONTACT_LABEL_RE.sub(" ", text)
    text = _strip_emoji(text).lower()
    text = text.replace("ё", "е")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# --- цены -------------------------------------------------------------------

_CURRENCY_WORDS = {
    "sar": "SAR", "ريال": "SAR", "риал": "SAR", "риалов": "SAR", "риала": "SAR",
    "sr": "SAR", "﷼": "SAR", "usd": "USD", "$": "USD", "долл": "USD",
    "rub": "RUB", "руб": "RUB", "₽": "RUB", "eur": "EUR", "€": "EUR",
}

_PRICE_RE = re.compile(
    r"(\d[\d\s.,]{0,12}\d|\d)\s*(sar|sr|ريال|﷼|риал\w*|руб\w*|₽|\$|usd|долл\w*|eur|€)",
    re.IGNORECASE,
)


def parse_price(text: str) -> tuple[float, str] | None:
    """Первая цена с явной валютой. Без валюты не угадываем — чаще навредит."""
    match = _PRICE_RE.search(text.translate(_ARABIC_DIGITS))
    if not match:
        return None
    number = match.group(1).replace(" ", "").replace(",", "").rstrip(".")
    if number.count(".") > 1:
        number = number.replace(".", "")
    try:
        amount = float(number)
    except ValueError:
        return None
    if amount <= 0:
        return None

    token = match.group(2).lower()
    currency = next(
        (code for word, code in _CURRENCY_WORDS.items() if token.startswith(word)),
        "SAR",
    )
    return amount, currency


def price_bucket(amount: float | None) -> str:
    """Логарифмическая корзина цены для ключа идентичности.

    Смысл: 2500 и 2600 риалов за ту же квартиру должны попасть в одну корзину
    (в перепостах цену часто слегка правят), а 2500 и 9000 — в разные.
    """
    if not amount or amount <= 0:
        return "na"
    step = 0
    value = amount
    while value >= 1.15:
        value /= 1.15
        step += 1
    return f"b{step // 3}"
