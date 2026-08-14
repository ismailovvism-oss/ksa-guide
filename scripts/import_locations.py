"""Перенос data/locations.json в базу справочника.

Локации собраны раньше и отдельно, у них нет ни сырых сообщений, ни дат
публикации — поэтому они кладутся сразу каноническими карточками, минуя
разбор и дедуп. Повторный запуск ничего не задваивает: ключ — source.

    uv run python scripts/import_locations.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ksa import config, db  # noqa: E402

SOURCE_FILE = config.DATA_DIR / "locations.json"


def title_of(raw: dict) -> str:
    """Заголовок карточки: у части записей в title лежит адрес из Google Maps."""
    title = (raw.get("title") or "").strip()
    # Адреса из Maps узнаются по plus-code и хвосту с индексом: берём первую
    # часть до запятой, она читается как название места.
    if "،" in title or len(title) > 70:
        title = title.split("،")[0].split(",")[0].strip()
    return title[:120] or "Без названия"


def summary_of(raw: dict) -> str | None:
    body = (raw.get("body") or "").strip()
    if not body:
        return None
    # Хвост поста — служебные строки «📍 адрес» и «Источник: …», они уже
    # разложены по отдельным полям карточки.
    lines = [
        line for line in body.splitlines()
        if not line.strip().startswith(("📍", "Источник:"))
    ]
    return "\n".join(lines).strip()[:1500] or None


def main() -> int:
    if not SOURCE_FILE.exists():
        print(f"Нет файла {SOURCE_FILE}")
        return 1

    records = json.loads(SOURCE_FILE.read_text(encoding="utf-8"))
    added = updated = 0

    with db.session() as conn, db.transaction(conn):
        for raw in records:
            external_id = raw["id"]
            existing = db.one(
                conn, "SELECT id FROM listing WHERE external_id = ?", (external_id,)
            )

            now = db.utcnow()
            values = {
                "category": "location",
                "external_id": external_id,
                "subcategory": (raw.get("category") or "").strip() or None,
                "city": (raw.get("city") or "").strip() or None,
                "title": title_of(raw),
                "summary": summary_of(raw),
                "map_url": raw.get("mapUrl"),
                "photo": raw.get("photo"),
                "source_url": raw.get("source"),
                "status": "published",
                "updated_at": now,
            }

            if existing:
                db.update(conn, "listing", existing["id"], values)
                updated += 1
            else:
                db.insert(
                    conn,
                    "listing",
                    {**values, "first_seen_at": now, "last_seen_at": now, "repost_count": 1},
                )
                added += 1

    print(f"Локации: добавлено {added}, обновлено {updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
