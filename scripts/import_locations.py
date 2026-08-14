"""Перенос data/locations.json в базу справочника.

Локации собраны раньше и отдельно, у них нет ни сырых сообщений, ни дат
публикации — поэтому они кладутся сразу каноническими карточками, минуя
разбор и дедуп. Повторный запуск ничего не задваивает: ключ — source.

    uv run python scripts/import_locations.py
"""

from __future__ import annotations

import collections
import json
import re
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


# Начало перечисления мест: маркер списка, ссылка на карту или значок места
# посреди строки — так каналы помечают очередной пункт подборки.
ENUMERATION = re.compile(r"^\s*(?:[•\-–—*]|\d+[.)])\s|maps\.app\.goo\.gl|goo\.gl/maps")


def summary_of(raw: dict, shared_post: bool) -> str | None:
    """Описание карточки.

    Один пост канала описывает до 11 мест сразу, и в выгрузке тело поста
    целиком попало в каждую запись — карточка выглядела списком других мест.
    Для таких постов оставляем только вступление: оно про подборку в целом
    и читается как описание, а перечисление отрезаем.

    Пост про одно место режем только по служебным хвостам — там весь текст
    и есть описание.
    """
    body = (raw.get("body") or "").strip()
    if not body:
        return None

    kept: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        # Служебные хвосты: адрес и ссылка на источник уже лежат в своих полях.
        if stripped.startswith(("📍", "Источник:")):
            continue
        if shared_post and (ENUMERATION.search(line) or ("📍" in line and kept)):
            break
        kept.append(line)

    return "\n".join(kept).strip()[:1500] or None


KNOWN_CITIES = {
    "Мекка": r"мекк", "Медина": r"медин", "Джидда": r"джидд",
    "Эр-Рияд": r"эр-рияд|эррияд", "Даммам": r"даммам", "Таиф": r"таиф",
    "Янбу": r"янбу", "Абха": r"абха", "Аль-Ула": r"аль-ул",
}


def city_of(raw: dict) -> str | None:
    """Город записи.

    Основной источник — адрес из Google Maps, он разобран при выгрузке.
    Если там не разобралось, пробуем заголовок самой записи: в нём город
    часто назван прямо («Мечеть на воде в Джидде»). Текст поста для этого
    не годится — в подборке он перечисляет города чужих мест.
    """
    city = (raw.get("city") or "").strip()
    if city:
        return city

    title = (raw.get("title") or "").lower()
    found = [name for name, pattern in KNOWN_CITIES.items() if re.search(pattern, title)]
    return found[0] if len(found) == 1 else None


def load_refinements() -> dict[str, dict]:
    """Правки от scripts/refine_locations.py, если их уже сделали.

    Хранятся отдельно от locations.json: выгрузка из канала — сырьё, её
    не переписываем, как и raw_message в основном конвейере.
    """
    path = config.DATA_DIR / "locations_refined.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    if not SOURCE_FILE.exists():
        print(f"Нет файла {SOURCE_FILE}")
        return 1

    records = json.loads(SOURCE_FILE.read_text(encoding="utf-8"))
    refinements = load_refinements()

    # Сколько мест описано в одном посте: от этого зависит, резать ли
    # перечисление из описания.
    per_post = collections.Counter(raw.get("source") for raw in records)

    added = updated = refined = 0

    with db.session() as conn, db.transaction(conn):
        for raw in records:
            external_id = raw["id"]
            shared_post = per_post[raw.get("source")] > 1
            fix = refinements.get(external_id, {})
            if fix:
                refined += 1
            existing = db.one(
                conn, "SELECT id FROM listing WHERE external_id = ?", (external_id,)
            )

            now = db.utcnow()
            values = {
                "category": "location",
                "external_id": external_id,
                # Правка от модели важнее исходной выгрузки: там человеческое
                # название вместо арабского адреса и описание именно этого
                # места, а не всей подборки.
                "subcategory": (fix.get("subcategory")
                                or (raw.get("category") or "").strip() or None),
                # Город: модель видела и адрес места, и текст поста, поэтому
                # её ответ важнее — она разводит подборки, где адрес одного
                # места соседствует с упоминанием города другого.
                "city": fix.get("city") or city_of(raw),
                "title": fix.get("title") or title_of(raw),
                "summary": fix.get("summary") or summary_of(raw, shared_post),
                "details": db.dumps(fix.get("details")) if fix.get("details") else None,
                "map_url": raw.get("mapUrl"),
                # no_photo — на снимке автор канала или другие узнаваемые люди.
                # Файл остаётся в data/photos, но в справочник не попадает:
                # публиковать чужое лицо мы права не имеем.
                "photo": None if fix.get("no_photo") else raw.get("photo"),
                # У локации из выгрузки снимок один; набор пополняется, когда
                # то же место придёт из другого канала.
                "photos": (None if fix.get("no_photo") or not raw.get("photo")
                           else db.dumps([raw["photo"]])),
                "source_url": raw.get("source"),
                # skip — запись не является местом: в выгрузку попала первая
                # строка поста или обрывок адреса. Не удаляем, а снимаем
                # с публикации: исходная запись остаётся на месте.
                "status": "rejected" if fix.get("skip") else "published",
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

    note = f", с правками модели {refined}" if refined else ""
    print(f"Локации: добавлено {added}, обновлено {updated}{note}")
    if not refinements:
        print("Правок нет — заголовки и описания как в выгрузке канала.")
        print("Разобрать по местам: uv run python scripts/refine_locations.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
