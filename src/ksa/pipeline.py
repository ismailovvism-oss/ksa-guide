"""Конвейер: сырое сообщение → разобранное вхождение → карточка справочника.

Каждый шаг идемпотентен и берёт работу из БД, поэтому его можно прервать
и запустить снова — ничего не задвоится.
"""

from __future__ import annotations

import sqlite3

from .db import dumps, insert, rows, utcnow
from .dedup.cluster import assign_pending
from .dedup.normalize import extract_contacts, normalize_text
from .parse.extract import Parsed, extract_one, fallback


def unparsed(conn: sqlite3.Connection, limit: int = 200) -> list[sqlite3.Row]:
    """Сообщения, для которых ещё нет вхождения."""
    return rows(
        conn,
        """SELECT r.* FROM raw_message r
             LEFT JOIN listing_occurrence o ON o.raw_message_id = r.id
            WHERE o.id IS NULL
            ORDER BY r.posted_at
            LIMIT ?""",
        (limit,),
    )


def store_occurrence(conn: sqlite3.Connection, raw: sqlite3.Row, parsed: Parsed) -> int:
    """Кладёт разбор в listing_occurrence, добавляя контакты и нормализованный текст."""
    row = parsed.as_row()
    row.update(
        {
            "raw_message_id": raw["id"],
            "contacts": dumps(extract_contacts(raw["text"])),
            "norm_text": normalize_text(raw["text"]),
            "parsed_at": utcnow(),
        }
    )
    return insert(conn, "listing_occurrence", row)


def parse_pending(conn: sqlite3.Connection, limit: int = 200, use_model: bool = True) -> int:
    """Разбирает сообщения по одному. Для архива дешевле parse.extract.submit_batch."""
    processed = 0
    for raw in unparsed(conn, limit):
        parsed = extract_one(raw["text"]) if use_model else fallback(raw["text"])
        store_occurrence(conn, raw, parsed)
        processed += 1
    return processed


def run(conn: sqlite3.Connection, limit: int = 200, use_model: bool = True) -> dict[str, int]:
    """Разбор + склейка одним вызовом."""
    parsed = parse_pending(conn, limit, use_model)
    stats = assign_pending(conn)
    return {"parsed": parsed, **stats}


def publish_ready(conn: sqlite3.Connection) -> int:
    """Публикует карточки, по которым не осталось вопросов у дедупа.

    Карточка с открытым спорным совпадением ждёт модератора: лучше
    задержать одно объявление, чем показать людям дубль.
    """
    result = conn.execute(
        """UPDATE listing SET status = 'published', updated_at = ?
            WHERE status = 'pending'
              AND id NOT IN (
                  SELECT l.id FROM listing l
                    JOIN listing_occurrence o ON o.listing_id = l.id
                    JOIN dedup_suggestion s ON s.occurrence_id = o.id
                   WHERE s.resolved_at IS NULL
              )""",
        (utcnow(),),
    )
    return result.rowcount
