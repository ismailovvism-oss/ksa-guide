"""Разбор постов-подборок на отдельные места.

Один пост канала описывает до 11 мест сразу, и в выгрузке в каждую запись
попал текст поста целиком. Записи при этом уже раздельные — у каждой свой
mapUrl, — но название взято из Google Maps (часто арабский адрес), а
описание относится ко всей подборке.

Скрипт проходит по таким записям и просит модель выдать для каждой:
название по-человечески, описание именно этого места и уточнённый тип.

Результат ложится в data/locations_refined.json, а не в locations.json:
выгрузка из канала — сырьё, её не переписываем (тот же принцип, что и с
raw_message в основном конвейере). Скрипт можно прервать и запустить
снова — уже разобранное пропускается.

    uv run python scripts/refine_locations.py            # всё, что нужно
    uv run python scripts/refine_locations.py --limit 10 # попробовать на десятке
    uv run python scripts/refine_locations.py --force    # пересчитать заново
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ksa import config  # noqa: E402
from ksa.parse.extract import MAX_TOKENS, MODEL, OUTPUT_CONFIG, _client  # noqa: E402

TARGET = config.DATA_DIR / "locations_refined.json"
SOURCE = config.DATA_DIR / "locations.json"

ARABIC = re.compile(r"[؀-ۿ]")

SYSTEM = """Ты приводишь в порядок карточки мест для справочника по Саудовской Аравии.

Тебе дают пост из телеграм-канала, в котором описано несколько мест сразу,
и одно конкретное место из этого поста — его адрес в Google Maps и ссылку.

Верни данные ТОЛЬКО про это место, а не про всю подборку.

name — короткое человеческое название, по-русски. Если у места есть
собственное имя (Нур Молл, Самарканд кебаб, парковка Аль-Захир) — используй
его. Арабский адрес из карт названием не является: переведи или подбери
понятное имя по смыслу поста. До 60 знаков, без эмодзи и без адреса.

summary — 1-3 предложения об этом месте, по тексту поста. Не пересказывай
соседние места из подборки. Если про само место в посте почти ничего нет,
опиши, чем полезна подборка в целом, но без перечисления других мест.
Не вставляй ссылки и телефоны.

subcategory — тип места одним словом с большой буквы: Ресторан, Кафе, Парк,
Мечеть, Рынок, Магазин, Парковка, Пляж, Отель, Больница, Музей, Аквапарк,
Транспорт, Учёба, Спорт, Зиярат, Развлечения, Природа, Продукты, Сервис,
Достопримечательность. Если ничего не подходит — Другое."""

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "человеческое название места"},
        "summary": {"type": "string", "description": "1-3 предложения об этом месте"},
        "subcategory": {"type": "string", "description": "тип места одним словом"},
    },
    "required": ["name", "summary", "subcategory"],
    "additionalProperties": False,
}

CONFIG = {**OUTPUT_CONFIG, "format": {"type": "json_schema", "schema": SCHEMA}}


def needs_refining(raw: dict, shared_post: bool) -> bool:
    """Кого разбираем: места из подборок и те, у кого вместо названия адрес."""
    return shared_post or bool(ARABIC.search(raw.get("title") or ""))


def prompt(raw: dict) -> str:
    return (
        f"<пост>\n{(raw.get('body') or '').strip()}\n</пост>\n\n"
        f"<место>\nАдрес в Google Maps: {raw.get('title')}\n"
        f"Ссылка: {raw.get('mapUrl')}\n</место>"
    )


def refine(client, raw: dict) -> dict | None:
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM,
        output_config=CONFIG,
        messages=[{"role": "user", "content": prompt(raw)}],
    )
    if response.stop_reason == "refusal":
        return None
    payload = next((b.text for b in response.content if b.type == "text"), None)
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None

    title = (data.get("name") or "").strip()[:120]
    if not title:
        return None
    return {
        "title": title,
        "summary": (data.get("summary") or "").strip()[:1500] or None,
        "subcategory": (data.get("subcategory") or "").strip() or None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="сколько записей разобрать")
    parser.add_argument("--force", action="store_true", help="пересчитать уже разобранное")
    args = parser.parse_args()

    if not SOURCE.exists():
        print(f"Нет файла {SOURCE}")
        return 1

    records = json.loads(SOURCE.read_text(encoding="utf-8"))
    per_post = collections.Counter(raw.get("source") for raw in records)
    done: dict[str, dict] = (
        {} if args.force
        else (json.loads(TARGET.read_text(encoding="utf-8")) if TARGET.exists() else {})
    )

    queue = [
        raw for raw in records
        if needs_refining(raw, per_post[raw.get("source")] > 1) and raw["id"] not in done
    ]
    if args.limit:
        queue = queue[: args.limit]

    if not queue:
        print("Всё уже разобрано.")
        return 0

    print(f"К разбору {len(queue)} записей, модель {MODEL}.")
    try:
        client = _client()
    except Exception as error:
        print(f"Нет доступа к модели: {error}")
        print("Впиши ANTHROPIC_API_KEY в .env")
        return 1

    failed = 0
    for number, raw in enumerate(queue, 1):
        try:
            result = refine(client, raw)
        except Exception as error:
            print(f"  [{number}/{len(queue)}] ошибка: {error}")
            failed += 1
            continue

        if not result:
            failed += 1
            continue

        done[raw["id"]] = result
        print(f"  [{number}/{len(queue)}] {result['title']}")
        # Пишем на каждом шаге: прерванный запуск не теряет уже сделанное.
        TARGET.write_text(
            json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    print(f"\nГотово. Разобрано {len(done)}, не удалось {failed}.")
    print("Дальше: uv run python scripts/import_locations.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
