"""Раскладка разобранных сообщений по каноническим карточкам.

Работает инкрементально: каждое новое вхождение либо прилипает к уже
существующей карточке (это перепост), либо заводит новую.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from .. import categories
from ..db import dumps, insert, loads, one, rows, update, utcnow
from .matcher import Item, Match, compare, identity_key

# Насколько назад ищем кандидатов. Объявление, не всплывавшее два месяца,
# считаем протухшим — новый пост с тем же телефоном будет новой карточкой.
CANDIDATE_WINDOW_DAYS = 60

# Контакт, встретившийся в стольких вхождениях одного канала, — это подпись
# канала или телефон агентства, а не признак конкретного объявления.
COMMON_CONTACT_MIN = 15


def _cutoff(days: int = CANDIDATE_WINDOW_DAYS) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).replace(microsecond=0).isoformat()


def common_contacts(conn: sqlite3.Connection, channel_id: int) -> set[str]:
    """Контакты, которые в этом канале мелькают слишком часто, чтобы что-то значить."""
    counter: dict[str, int] = {}
    query = """
        SELECT o.contacts
          FROM listing_occurrence o
          JOIN raw_message r ON r.id = o.raw_message_id
         WHERE r.channel_id = ? AND o.contacts IS NOT NULL
    """
    for row in rows(conn, query, (channel_id,)):
        for contact in loads(row["contacts"], []) or []:
            if contact.get("value"):
                key = f"{contact['type']}:{contact['value']}"
                counter[key] = counter.get(key, 0) + 1
    return {key for key, count in counter.items() if count >= COMMON_CONTACT_MIN}


def _item_from_row(row: sqlite3.Row, norm_text: str = "", media_phash: str | None = None) -> Item:
    keys = row.keys()
    return Item(
        norm_text=norm_text or (row["norm_text"] if "norm_text" in keys else "") or "",
        contacts=loads(row["contacts"], []) or [],
        media_phash=media_phash if media_phash is not None else (
            row["media_phash"] if "media_phash" in keys else None
        ),
        category=row["category"],
        city=row["city"],
        district=row["district"],
        price_amount=row["price_amount"],
        price_currency=row["price_currency"],
        rooms=row["rooms"],
        area_sqm=row["area_sqm"],
    )


def candidates(conn: sqlite3.Connection, item: Item) -> list[sqlite3.Row]:
    """Карточки, с которыми имеет смысл сравниваться.

    Отбор широкий (категория + окно свежести), потому что отсев ошибочно
    отброшенного кандидата стоит дороже, чем лишнее сравнение: объявлений
    в окне — сотни, а не миллионы.
    """
    query = """
        SELECT l.*, o.norm_text AS norm_text, r.media_phash AS media_phash
          FROM listing l
          LEFT JOIN listing_occurrence o ON o.id = l.canonical_occurrence_id
          LEFT JOIN raw_message r ON r.id = o.raw_message_id
         WHERE l.status != 'rejected'
           AND l.category = ?
           AND l.last_seen_at >= ?
    """
    return rows(conn, query, (categories.normalize(item.category), _cutoff()))


def best_match(
    conn: sqlite3.Connection, item: Item, common: set[str] | None = None
) -> tuple[sqlite3.Row | None, Match | None]:
    best_row: sqlite3.Row | None = None
    best: Match | None = None
    for row in candidates(conn, item):
        result = compare(item, _item_from_row(row), common)
        if best is None or result.score > best.score:
            best_row, best = row, result
    return best_row, best


def _merge_contacts(existing: list[dict], incoming: list[dict]) -> list[dict]:
    seen = {f"{c['type']}:{c['value']}" for c in existing}
    return existing + [
        c for c in incoming if f"{c['type']}:{c['value']}" not in seen
    ]


def attach(
    conn: sqlite3.Connection,
    occurrence_id: int,
    listing_id: int,
    posted_at: str,
    channel_trust: int = 50,
) -> None:
    """Присоединяет вхождение к существующей карточке.

    Карточка «поднимается» по свежести и, если источник авторитетнее
    нынешнего, забирает у него текст и фото.
    """
    listing = one(conn, "SELECT * FROM listing WHERE id = ?", (listing_id,))
    occurrence = one(conn, "SELECT * FROM listing_occurrence WHERE id = ?", (occurrence_id,))
    if listing is None or occurrence is None:
        return

    update(conn, "listing_occurrence", occurrence_id, {"listing_id": listing_id})

    changes: dict[str, object] = {
        "repost_count": listing["repost_count"] + 1,
        "last_seen_at": max(listing["last_seen_at"], posted_at),
        "first_seen_at": min(listing["first_seen_at"], posted_at),
        "updated_at": utcnow(),
        "contacts": dumps(
            _merge_contacts(
                loads(listing["contacts"], []) or [],
                loads(occurrence["contacts"], []) or [],
            )
        ),
    }

    # Фотография перепоста — не мусор, а ещё один ракурс того же места.
    # Раньше она терялась; теперь карточка собирает снимки из всех каналов
    # и оказывается полнее любого исходного поста.
    media = one(
        conn,
        """SELECT r.media_path AS media_path FROM listing_occurrence o
             JOIN raw_message r ON r.id = o.raw_message_id
            WHERE o.id = ?""",
        (occurrence_id,),
    )
    if media and media["media_path"]:
        gallery = loads(listing["photos"], []) or []
        if media["media_path"] not in gallery:
            gallery.append(media["media_path"])
            changes["photos"] = dumps(gallery)
            if not listing["photo"]:
                changes["photo"] = media["media_path"]

    # Недостающие поля добираем из любого вхождения, где они есть.
    for field in ("city", "district", "price_amount", "price_currency",
                  "price_period", "rooms", "area_sqm"):
        if listing[field] is None and occurrence[field] is not None:
            changes[field] = occurrence[field]

    current_trust = one(
        conn,
        """SELECT c.trust AS trust FROM listing_occurrence o
             JOIN raw_message r ON r.id = o.raw_message_id
             JOIN channel c ON c.id = r.channel_id
            WHERE o.id = ?""",
        (listing["canonical_occurrence_id"],),
    )
    if channel_trust > (current_trust["trust"] if current_trust else 0):
        changes["canonical_occurrence_id"] = occurrence_id
        changes["title"] = occurrence["title"] or listing["title"]
        changes["summary"] = occurrence["summary"] or listing["summary"]

    update(conn, "listing", listing_id, changes)


def create(
    conn: sqlite3.Connection, occurrence_id: int, item: Item, posted_at: str, photo: str | None
) -> int:
    occurrence = one(conn, "SELECT * FROM listing_occurrence WHERE id = ?", (occurrence_id,))
    now = utcnow()
    listing_id = insert(
        conn,
        "listing",
        {
            "category": categories.normalize(item.category),
            "city": item.city,
            "district": item.district,
            "title": (occurrence["title"] if occurrence else None) or "Без названия",
            "summary": occurrence["summary"] if occurrence else None,
            "price_amount": item.price_amount,
            "price_currency": item.price_currency,
            "price_period": occurrence["price_period"] if occurrence else None,
            "rooms": item.rooms,
            "area_sqm": item.area_sqm,
            "contacts": dumps(item.contacts),
            "identity_key": identity_key(item),
            "photo": photo,
            "photos": dumps([photo]) if photo else None,
            "first_seen_at": posted_at,
            "last_seen_at": posted_at,
            "repost_count": 1,
            "status": "pending",
            "canonical_occurrence_id": occurrence_id,
            "updated_at": now,
        },
    )
    update(conn, "listing_occurrence", occurrence_id, {"listing_id": listing_id})
    return listing_id


def assign(conn: sqlite3.Connection, occurrence_id: int) -> tuple[int, Match | None]:
    """Определяет судьбу одного вхождения. Возвращает (listing_id, решение).

    Ручное решение модератора из dedup_override всегда важнее автоматики.
    """
    row = one(
        conn,
        """SELECT o.*, r.posted_at AS posted_at, r.media_phash AS media_phash,
                  r.media_path AS media_path, r.channel_id AS channel_id,
                  c.trust AS trust
             FROM listing_occurrence o
             JOIN raw_message r ON r.id = o.raw_message_id
             JOIN channel c ON c.id = r.channel_id
            WHERE o.id = ?""",
        (occurrence_id,),
    )
    if row is None:
        raise ValueError(f"вхождение {occurrence_id} не найдено")

    override = one(
        conn, "SELECT * FROM dedup_override WHERE occurrence_id = ?", (occurrence_id,)
    )
    if override is not None and override["listing_id"]:
        attach(conn, occurrence_id, override["listing_id"], row["posted_at"], row["trust"])
        return override["listing_id"], None

    item = _item_from_row(row)
    best_row: sqlite3.Row | None = None
    match: Match | None = None
    if override is None:
        best_row, match = best_match(conn, item, common_contacts(conn, row["channel_id"]))
        if best_row is not None and match is not None and match.merge:
            attach(conn, occurrence_id, best_row["id"], row["posted_at"], row["trust"])
            return best_row["id"], match

    listing_id = create(conn, occurrence_id, item, row["posted_at"], row["media_path"])

    # Похоже, но не настолько, чтобы решать без человека: карточку всё же
    # заводим (иначе объявление пропадёт из выдачи), но помечаем на проверку.
    if best_row is not None and match is not None and match.needs_review:
        insert(
            conn,
            "dedup_suggestion",
            {
                "occurrence_id": occurrence_id,
                "listing_id": best_row["id"],
                "score": match.score,
                "reasons": dumps(match.reasons),
                "created_at": utcnow(),
            },
        )
    return listing_id, match


def assign_pending(conn: sqlite3.Connection, limit: int = 1000) -> dict[str, int]:
    """Разбирает все ещё не кластеризованные вхождения. Порядок — по дате поста.

    Хронологический порядок важен: первым в карточку должен попасть самый
    ранний пост, тогда first_seen_at честный.
    """
    pending = rows(
        conn,
        """SELECT o.id AS id FROM listing_occurrence o
             JOIN raw_message r ON r.id = o.raw_message_id
            WHERE o.listing_id IS NULL AND COALESCE(o.is_listing, 1) = 1
            ORDER BY r.posted_at
            LIMIT ?""",
        (limit,),
    )
    stats = {"merged": 0, "created": 0}
    for row in pending:
        _, match = assign(conn, row["id"])
        stats["merged" if match and match.merge else "created"] += 1
    return stats
