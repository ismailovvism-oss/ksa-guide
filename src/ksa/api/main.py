"""HTTP-API справочника и раздача фронтенда.

Один и тот же API обслуживает и обычный сайт, и Telegram Mini App —
это просто JSON по HTTP.

    uv run uvicorn ksa.api.main:app --reload
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import categories, config
from ..db import connect, loads, migrate, rows, utcnow

app = FastAPI(title="Справочник по Саудии", docs_url="/api/docs", openapi_url="/api/openapi.json")

WEB_DIR = config.ROOT / "web"


def db() -> sqlite3.Connection:
    conn = connect()
    migrate(conn)
    return conn


# --- порядок выдачи ---------------------------------------------------------

# Оплаченный подъём — первое слагаемое сортировки, свежесть — второе.
# Ровно здесь и живёт монетизация: всё остальное про неё ничего не знает.
PROMOTION_RANK = """
    COALESCE((SELECT MAX(p.tier) FROM promotion p
               WHERE p.listing_id = l.id
                 AND p.starts_at <= :now AND p.ends_at >= :now), 0)
"""

LISTING_FIELDS = """
    l.id, l.category, l.subcategory, l.city, l.district, l.title, l.summary,
    l.price_amount, l.price_currency, l.price_period, l.rooms, l.area_sqm,
    l.contacts, l.photo, l.map_url, l.source_url, l.last_seen_at,
    l.first_seen_at, l.repost_count
"""


def serialize(row: sqlite3.Row) -> dict[str, Any]:
    item = {key: row[key] for key in row.keys()}
    item["contacts"] = loads(item.get("contacts"), []) or []
    item["categoryTitle"] = categories.title(item["category"])
    item["promoted"] = bool(item.pop("promotion_rank", 0))
    if item.get("photo"):
        item["photo"] = config.media_url(item["photo"])
    return item


@app.get("/api/facets")
def facets(category: str | None = None) -> dict[str, Any]:
    """Счётчики для навигации: сколько всего в каждой категории, городе, типе."""
    conn = db()
    try:
        by_category = rows(
            conn,
            """SELECT category AS key, COUNT(*) AS count FROM listing
                WHERE status = 'published' GROUP BY category""",
        )
        scope = "AND category = ?" if category else ""
        params = (category,) if category else ()
        by_city = rows(
            conn,
            f"""SELECT city AS key, COUNT(*) AS count FROM listing
                 WHERE status = 'published' AND city IS NOT NULL {scope}
                 GROUP BY city ORDER BY count DESC""",
            params,
        )
        by_subcategory = rows(
            conn,
            f"""SELECT subcategory AS key, COUNT(*) AS count FROM listing
                 WHERE status = 'published' AND subcategory IS NOT NULL {scope}
                 GROUP BY subcategory ORDER BY count DESC""",
            params,
        )
        return {
            "categories": [
                {
                    "slug": row["key"],
                    "title": categories.title(row["key"]),
                    "count": row["count"],
                }
                for row in by_category
            ],
            "cities": [dict(row) for row in by_city],
            "subcategories": [dict(row) for row in by_subcategory],
            "total": sum(row["count"] for row in by_category),
        }
    finally:
        conn.close()


@app.get("/api/listings")
def listings(
    category: str | None = None,
    city: str | None = None,
    subcategory: str | None = None,
    q: str | None = Query(None, description="поиск по названию и описанию"),
    limit: int = Query(48, le=200),
    offset: int = 0,
) -> dict[str, Any]:
    conn = db()
    try:
        where = ["l.status = 'published'"]
        params: dict[str, Any] = {"now": utcnow(), "limit": limit, "offset": offset}

        if category:
            where.append("l.category = :category")
            params["category"] = category
        if city:
            where.append("l.city = :city")
            params["city"] = city
        if subcategory:
            where.append("l.subcategory = :subcategory")
            params["subcategory"] = subcategory
        if q and q.strip():
            where.append(
                "(l.title LIKE :q OR l.summary LIKE :q OR l.city LIKE :q"
                " OR l.subcategory LIKE :q)"
            )
            params["q"] = f"%{q.strip()}%"

        clause = " AND ".join(where)
        total = conn.execute(
            f"SELECT COUNT(*) FROM listing l WHERE {clause}",
            {k: v for k, v in params.items() if k not in {"limit", "offset", "now"}},
        ).fetchone()[0]

        found = rows(
            conn,
            f"""SELECT {LISTING_FIELDS}, {PROMOTION_RANK} AS promotion_rank
                  FROM listing l
                 WHERE {clause}
                 ORDER BY promotion_rank DESC,
                          l.last_seen_at DESC,
                          (l.photo IS NULL),
                          l.id
                 LIMIT :limit OFFSET :offset""",
            params,
        )
        return {
            "total": total,
            "items": [serialize(row) for row in found],
            "hasMore": offset + len(found) < total,
        }
    finally:
        conn.close()


@app.get("/api/listings/{listing_id}")
def listing(listing_id: int) -> dict[str, Any]:
    conn = db()
    try:
        found = rows(
            conn,
            f"""SELECT {LISTING_FIELDS}, {PROMOTION_RANK} AS promotion_rank
                  FROM listing l WHERE l.id = :id AND l.status = 'published'""",
            {"id": listing_id, "now": utcnow()},
        )
        if not found:
            raise HTTPException(404, "Карточка не найдена")
        item = serialize(found[0])

        # Все посты, из которых склеена карточка: видно, откуда она собрана.
        item["sources"] = [
            {
                "channel": row["username"],
                "url": row["source"],
                "postedAt": row["posted_at"],
            }
            for row in rows(
                conn,
                """SELECT c.username AS username, r.posted_at AS posted_at,
                          'https://t.me/' || c.username || '/' || r.tg_msg_id AS source
                     FROM listing_occurrence o
                     JOIN raw_message r ON r.id = o.raw_message_id
                     JOIN channel c ON c.id = r.channel_id
                    WHERE o.listing_id = ?
                    ORDER BY r.posted_at DESC""",
                (listing_id,),
            )
        ]
        return item
    finally:
        conn.close()


# Фото: и старая выгрузка локаций (data/photos), и новые скачанные (data/media).
@app.get("/media/{filename}")
def media(filename: str) -> FileResponse:
    name = filename.replace("\\", "/").split("/")[-1]  # без выхода за каталог
    for folder in (config.DATA_DIR / "photos", config.MEDIA_DIR):
        candidate = folder / name
        if candidate.is_file():
            return FileResponse(candidate)
    raise HTTPException(404, "Файл не найден")


if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
