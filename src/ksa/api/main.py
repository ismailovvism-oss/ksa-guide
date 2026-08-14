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


def _category_clause(category: str | None) -> tuple[str, dict[str, Any]]:
    """Условие по разделу.

    Без раздела показываем объявления и НЕ показываем локации: справочник
    в первую очередь про объявления, а места — отдельный раздел.
    """
    if category == categories.LOCATION:
        return "l.category = :category", {"category": categories.LOCATION}
    if category:
        return "l.category = :category", {"category": category}
    return "l.category != :location", {"location": categories.LOCATION}


@app.get("/api/facets")
def facets(city: str | None = None, category: str | None = None) -> dict[str, Any]:
    """Счётчики для навигации, посчитанные в рамках выбранного города.

    Город — не фильтр наравне с прочими, а контекст: человек ищет жильё
    в своём городе, а не сравнивает районы Джидды с районами Медины.
    Поэтому всё, кроме списка самих городов, считается уже внутри него.
    """
    conn = db()
    try:
        city_clause = "AND l.city = :city" if city else ""
        city_param: dict[str, Any] = {"city": city} if city else {}

        # Разделы — в рамках города, чтобы во вкладках стояли честные числа.
        by_category = {
            row["key"]: row["count"]
            for row in rows(
                conn,
                f"""SELECT l.category AS key, COUNT(*) AS count FROM listing l
                     WHERE l.status = 'published' {city_clause}
                     GROUP BY l.category""",
                city_param,
            )
        }

        # Города перечисляем ВСЕ, какие есть в справочнике, а считаем — в рамках
        # раздела. Иначе на пустом разделе переключатель окажется пустым и город
        # станет невозможно выбрать, хотя он — глобальный контекст, а не фильтр.
        clause, params = _category_clause(category)
        by_city = rows(
            conn,
            f"""SELECT known.city AS key,
                       (SELECT COUNT(*) FROM listing l
                         WHERE l.status = 'published' AND l.city = known.city
                           AND {clause}) AS count
                  FROM (SELECT DISTINCT city FROM listing
                         WHERE status = 'published' AND city IS NOT NULL) known
                 ORDER BY count DESC, known.city""",
            params,
        )

        # Типы и районы — самый узкий уровень: и город, и раздел.
        narrow = {**params, **city_param}
        by_subcategory = rows(
            conn,
            f"""SELECT l.subcategory AS key, COUNT(*) AS count FROM listing l
                 WHERE l.status = 'published' AND l.subcategory IS NOT NULL
                   AND {clause} {city_clause}
                 GROUP BY l.subcategory ORDER BY count DESC""",
            narrow,
        )
        by_district = rows(
            conn,
            f"""SELECT l.district AS key, COUNT(*) AS count FROM listing l
                 WHERE l.status = 'published' AND l.district IS NOT NULL
                   AND {clause} {city_clause}
                 GROUP BY l.district ORDER BY count DESC""",
            narrow,
        )

        ads_total = sum(count for slug, count in by_category.items() if slug != categories.LOCATION)
        return {
            # Вкладки: только непустые разделы, в осмысленном порядке.
            "categories": [
                {"slug": slug, "title": categories.title(slug), "count": by_category[slug]}
                for slug in categories.ADS
                if by_category.get(slug)
            ],
            "cities": [dict(row) for row in by_city],
            "subcategories": [dict(row) for row in by_subcategory],
            "districts": [dict(row) for row in by_district],
            "adsTotal": ads_total,
            "locationsTotal": by_category.get(categories.LOCATION, 0),
        }
    finally:
        conn.close()


@app.get("/api/listings")
def listings(
    category: str | None = None,
    city: str | None = None,
    subcategory: str | None = None,
    district: str | None = None,
    q: str | None = Query(None, description="поиск по названию и описанию"),
    limit: int = Query(48, le=200),
    offset: int = 0,
) -> dict[str, Any]:
    conn = db()
    try:
        clause, category_params = _category_clause(category)
        where = ["l.status = 'published'", clause]
        params: dict[str, Any] = {
            "now": utcnow(), "limit": limit, "offset": offset, **category_params
        }

        if district:
            where.append("l.district = :district")
            params["district"] = district
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

        sql_where = " AND ".join(where)
        total = conn.execute(
            f"SELECT COUNT(*) FROM listing l WHERE {sql_where}",
            {k: v for k, v in params.items() if k not in {"limit", "offset", "now"}},
        ).fetchone()[0]

        found = rows(
            conn,
            f"""SELECT {LISTING_FIELDS}, {PROMOTION_RANK} AS promotion_rank
                  FROM listing l
                 WHERE {sql_where}
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
