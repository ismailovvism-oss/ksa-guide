"""Сборка статической версии справочника для GitHub Pages.

Складывает в docs/ сайт, который работает без бэкенда: весь справочник
лежит в одном data/listings.json, фильтрация считается в браузере.
Папка называется docs, потому что GitHub Pages умеет раздавать её прямо
из основной ветки — не нужны ни отдельная ветка, ни сборка.

Годится, пока карточек тысячи, а не сотни тысяч: файл целиком грузится
в память телефона. Дальше понадобится настоящий хостинг с бэкендом.

    uv run python scripts/export_static.py
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ksa import categories, config, db  # noqa: E402
from ksa.api.main import LISTING_FIELDS, PROMOTION_RANK, serialize  # noqa: E402

DIST = ROOT / "docs"
WEB = ROOT / "web"


def collect() -> list[dict]:
    with db.session() as conn:
        rows = db.rows(
            conn,
            f"""SELECT {LISTING_FIELDS}, {PROMOTION_RANK} AS promotion_rank
                  FROM listing l
                 WHERE l.status = 'published'
                 ORDER BY l.id""",
            {"now": db.utcnow()},
        )
    items = []
    for row in rows:
        item = serialize(row)
        # Когда картинки на R2, serialize уже вернул абсолютный адрес — трогать
        # его нельзя. Локальный путь делаем относительным: на GitHub Pages сайт
        # живёт не в корне домена, а в /имя-репозитория/.
        if item.get("photo") and not item["photo"].startswith("http"):
            item["photo"] = item["photo"].lstrip("/")
        items.append(item)
    return items


def copy_photos(items: list[dict]) -> int:
    """Кладёт фото рядом с сайтом. Не нужно, когда они уже лежат на R2."""
    if config.MEDIA_BASE_URL:
        return 0

    target = DIST / "media"
    target.mkdir(parents=True, exist_ok=True)
    sources = (config.DATA_DIR / "photos", config.MEDIA_DIR)

    copied = 0
    for item in items:
        if not item.get("photo"):
            continue
        name = item["photo"].split("/")[-1]
        for folder in sources:
            candidate = folder / name
            if candidate.is_file():
                shutil.copy2(candidate, target / name)
                copied += 1
                break
    return copied


def write_index() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    # Абсолютные пути заменяем относительными и включаем статический режим.
    html = (
        html.replace('href="/styles.css"', 'href="styles.css"')
        .replace('src="/app.js"', 'src="config.js"></script>\n<script src="app.js"')
        .replace('<a class="brand" href="/"', '<a class="brand" href="."')
    )
    (DIST / "index.html").write_text(html, encoding="utf-8")
    (DIST / "config.js").write_text("window.KSA_STATIC = true;\n", encoding="utf-8")


def main() -> int:
    items = collect()
    if not items:
        print("В базе нет опубликованных карточек — нечего выгружать.")
        return 1

    if DIST.exists():
        shutil.rmtree(DIST)
    (DIST / "data").mkdir(parents=True)

    # Порядок разделов кладём рядом с данными, а не дублируем в JS:
    # иначе он разъедется, как только в categories.py поменяется список.
    bundle = {"ads": categories.ADS, "items": items}
    (DIST / "data" / "listings.json").write_text(
        json.dumps(bundle, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    shutil.copy2(WEB / "styles.css", DIST / "styles.css")
    shutil.copy2(WEB / "app.js", DIST / "app.js")
    write_index()
    # Иначе GitHub Pages прогоняет файлы через Jekyll и прячет всё,
    # что начинается с подчёркивания.
    (DIST / ".nojekyll").write_text("", encoding="utf-8")

    photos = copy_photos(items)
    size = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file())
    where = f"{photos} фото рядом" if photos else f"фото с {config.MEDIA_BASE_URL}"
    print(f"docs/ готов: {len(items)} карточек, {where}, {size / 1_048_576:.1f} МБ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
