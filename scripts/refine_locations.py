"""Пересборка карточек мест по шаблону справочника.

Зачем это нужно. Тексты в выгрузке — авторские посты канала: восторженный
стиль, обращения к читателю, и один пост описывает до 11 мест сразу, из-за
чего в каждую запись попал текст про всю подборку. Публиковать их как есть
нельзя ни по смыслу (карточка выглядит списком чужих мест), ни по форме
(это чужой текст).

Скрипт берёт пост и одно конкретное место из него и просит модель собрать
карточку заново по шаблону: своё название, нейтральное описание, польза,
особенности, часы работы, цены, для кого, ориентир.

ГЛАВНОЕ ПРАВИЛО: поле заполняется, только если факт есть в посте. Часов
работы и цен там чаще всего нет — придуманные часы хуже отсутствующих,
потому что человек приедет к закрытым дверям.

Результат ложится в data/locations_refined.json, а не в locations.json:
выгрузка канала — сырьё, её не переписываем (тот же принцип, что и с
raw_message в основном конвейере). Скрипт можно прервать и запустить
снова — сделанное сохраняется на каждом шаге и не пересчитывается.

    uv run python scripts/refine_locations.py --limit 10   # посмотреть качество
    uv run python scripts/refine_locations.py              # остальные
    uv run python scripts/refine_locations.py --force      # пересобрать заново
    uv run python scripts/import_locations.py              # применить к базе
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

CITIES = [
    "Мекка", "Медина", "Джидда", "Эр-Рияд", "Даммам",
    "Таиф", "Янбу", "Абха", "Аль-Ула", "Риджаль-Альма",
]

TYPES = [
    "Ресторан", "Кафе", "Продукты", "Магазин", "Рынок", "Парк", "Пляж",
    "Природа", "Мечеть", "Зиярат", "Достопримечательность", "Музей",
    "Развлечения", "Аквапарк", "Спорт", "Отель", "Больница", "Учёба",
    "Транспорт", "Парковка", "Сервис", "Работа", "Другое",
]

SYSTEM = f"""Ты собираешь карточки мест для справочника по Саудовской Аравии.
Читатели — русскоязычные приезжие: паломники, студенты, живущие в стране.

Тебе дают пост из телеграм-канала и одно конкретное место из него: адрес
в Google Maps и ссылку. В посте может быть описано несколько мест сразу.

Собери карточку ТОЛЬКО про это место. Соседние места из подборки не
пересказывай и не упоминай.

Стиль: нейтральный, деловой, по делу. Пиши своими словами, не копируй
формулировки автора. Убери восторженность, восклицания, обращения к
читателю («обязательно посетите», «МашаАллах», «друзья»), эмодзи и
религиозные формулы. Не оценивай («прекрасное место») — сообщай факты.

ГЛАВНОЕ ПРАВИЛО: заполняй поле, только если факт есть в посте. Ничего не
достраивай по общим знаниям и не угадывай. Если про часы работы, цены или
что-то ещё в посте не сказано — оставь null. Пустое поле честнее
выдуманного: по выдуманным часам человек приедет к закрытым дверям.

Поля:

name — название по-русски, до 60 знаков. Если у места есть собственное имя
(Нур Молл, Самарканд кебаб) — используй его, при необходимости добавив
пояснение («Парковка Аль-Захир»). Арабский адрес из карт названием не
является: переведи или назови по смыслу. Без эмодзи, без адреса, без города.

summary — 2-3 предложения: что это за место и чем оно полезно приезжему.

purpose — одна короткая строка, зачем сюда идут: «Пообедать узбекской
кухней», «Оставить машину рядом с Харамом». До 60 знаков, без точки.

features — до 4 коротких фактов из поста: чем отличается, что рядом, что
есть на месте. Каждый до 90 знаков. Пустой список, если фактов нет.

hours — часы работы, если названы в посте. Иначе null.

price — про деньги, если названо: «Вход платный», «Около 30 риалов».
Иначе null.

audience — для кого, если сказано: «Для семей», «Только для мужчин»,
«Только для женщин». Иначе null.

landmark — ориентир или расстояние, если названо: «10 минут пешком до
Харама», «2 часа езды от Абхи». Иначе null.

city — город из списка: {", ".join(CITIES)}. Ориентируйся на адрес места
в Google Maps, а не на города других мест из подборки. Если определить
нельзя — null.

type — тип места, ровно одно значение из списка: {", ".join(TYPES)}."""

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "summary": {"type": "string"},
        "purpose": {"type": ["string", "null"]},
        "features": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "hours": {"type": ["string", "null"]},
        "price": {"type": ["string", "null"]},
        "audience": {"type": ["string", "null"]},
        "landmark": {"type": ["string", "null"]},
        "city": {"type": ["string", "null"], "enum": [*CITIES, None]},
        "type": {"type": "string", "enum": TYPES},
    },
    "required": [
        "name", "summary", "purpose", "features",
        "hours", "price", "audience", "landmark", "city", "type",
    ],
    "additionalProperties": False,
}

CONFIG = {**OUTPUT_CONFIG, "format": {"type": "json_schema", "schema": SCHEMA}}

ARABIC = re.compile(r"[؀-ۿ]")


def prompt(raw: dict) -> str:
    return (
        f"<пост>\n{(raw.get('body') or '').strip()}\n</пост>\n\n"
        f"<место>\n"
        f"Адрес в Google Maps: {raw.get('title')}\n"
        f"Ссылка: {raw.get('mapUrl')}\n"
        f"Город по адресу: {raw.get('city') or 'не определён'}\n"
        f"</место>"
    )


def clean(value) -> str | None:
    text = (value or "").strip()
    return text or None


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

    title = clean(data.get("name"))
    if not title:
        return None

    details = {
        "purpose": clean(data.get("purpose")),
        "features": [f.strip() for f in (data.get("features") or []) if f.strip()][:4],
        "hours": clean(data.get("hours")),
        "price": clean(data.get("price")),
        "audience": clean(data.get("audience")),
        "landmark": clean(data.get("landmark")),
    }
    return {
        "title": title[:120],
        "summary": clean(data.get("summary")),
        "subcategory": clean(data.get("type")),
        "city": clean(data.get("city")),
        # Пустые поля не храним: их отсутствие и есть «в посте не сказано».
        "details": {k: v for k, v in details.items() if v},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="сколько записей собрать")
    parser.add_argument("--force", action="store_true", help="пересобрать уже готовое")
    args = parser.parse_args()

    if not SOURCE.exists():
        print(f"Нет файла {SOURCE}")
        return 1

    records = json.loads(SOURCE.read_text(encoding="utf-8"))
    done: dict[str, dict] = (
        {} if args.force
        else (json.loads(TARGET.read_text(encoding="utf-8")) if TARGET.exists() else {})
    )

    # Собираем заново все карточки: тексты авторские, публиковать как есть
    # нельзя ни одну — дело не только в подборках.
    queue = [raw for raw in records if raw["id"] not in done]
    if args.limit:
        queue = queue[: args.limit]

    if not queue:
        print("Всё уже собрано.")
        return 0

    multi = collections.Counter(raw.get("source") for raw in records)
    print(f"К сборке {len(queue)} карточек, модель {MODEL}.")
    print(f"Из них из постов-подборок: {sum(1 for r in queue if multi[r.get('source')] > 1)}")
    print(f"С арабским адресом вместо названия: "
          f"{sum(1 for r in queue if ARABIC.search(r.get('title') or ''))}\n")

    try:
        client = _client()
    except Exception as error:
        print(f"Нет доступа к модели: {error}")
        print("Впиши ANTHROPIC_API_KEY в .env")
        return 1

    failed = moved = 0
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

        was = (raw.get("city") or "").strip() or None
        if result["city"] and was and result["city"] != was:
            moved += 1
            print(f"  [{number}/{len(queue)}] {result['title']}  "
                  f"(город: {was} → {result['city']})")
        else:
            print(f"  [{number}/{len(queue)}] {result['title']}")

        done[raw["id"]] = result
        TARGET.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\nГотово. Собрано {len(done)}, не удалось {failed}, город уточнён у {moved}.")
    print("Дальше: uv run python scripts/import_locations.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
