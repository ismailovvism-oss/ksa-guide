"""Командная строка справочника.

    uv run python -m ksa <команда>

Команды идут в том порядке, в каком их обычно запускают:
    collect   — забрать новые сообщения из каналов
    parse     — разобрать собранное и склеить дубли
    publish   — вывести в выдачу карточки без спорных совпадений
    review    — показать очередь спорных склеек
    stats     — что вообще лежит в базе
    serve     — поднять сайт и API
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from . import categories, db
from .db import loads, rows


def cmd_collect(args: argparse.Namespace) -> int:
    from .ingest.collect import collect_all

    with db.session() as conn:
        stats = asyncio.run(collect_all(conn, args.limit))
    for channel, count in stats.items():
        print(f"  {channel}: {'ошибка' if count < 0 else f'{count} новых'}")
    return 0


def cmd_parse(args: argparse.Namespace) -> int:
    from .pipeline import run

    with db.session() as conn:
        stats = run(conn, limit=args.limit, use_model=not args.rules_only)
    print(
        f"Разобрано {stats['parsed']}, "
        f"склеено с существующими {stats['merged']}, новых карточек {stats['created']}"
    )
    return 0


def cmd_publish(_: argparse.Namespace) -> int:
    from .pipeline import publish_ready

    with db.session() as conn:
        print(f"Опубликовано карточек: {publish_ready(conn)}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    with db.session() as conn:
        pending = rows(
            conn,
            """SELECT s.*, o.title AS occurrence_title, l.title AS listing_title
                 FROM dedup_suggestion s
                 JOIN listing_occurrence o ON o.id = s.occurrence_id
                 JOIN listing l ON l.id = s.listing_id
                WHERE s.resolved_at IS NULL
                ORDER BY s.score DESC
                LIMIT ?""",
            (args.limit,),
        )
        if not pending:
            print("Спорных склеек нет.")
            return 0
        print(f"Спорных склеек: {len(pending)}\n")
        for row in pending:
            print(f"  [{row['score']:.2f}] {row['occurrence_title']}")
            print(f"       похоже на: {row['listing_title']}")
            for reason in loads(row["reasons"], []) or []:
                print(f"       — {reason}")
            print()
    return 0


def cmd_stats(_: argparse.Namespace) -> int:
    with db.session() as conn:
        counters = [
            ("Каналов", "SELECT COUNT(*) n FROM channel WHERE enabled = 1"),
            ("Сырых сообщений", "SELECT COUNT(*) n FROM raw_message"),
            ("Разобрано вхождений", "SELECT COUNT(*) n FROM listing_occurrence"),
            ("Карточек всего", "SELECT COUNT(*) n FROM listing"),
            ("  из них опубликовано", "SELECT COUNT(*) n FROM listing WHERE status='published'"),
            ("Спорных склеек", "SELECT COUNT(*) n FROM dedup_suggestion WHERE resolved_at IS NULL"),
        ]
        for label, sql in counters:
            print(f"{label}: {db.one(conn, sql)['n']}")

        merged = db.one(
            conn, "SELECT COALESCE(SUM(repost_count - 1), 0) n FROM listing"
        )["n"]
        if merged:
            print(f"\nПерепостов свёрнуто в существующие карточки: {merged}")

        print("\nПо категориям:")
        for row in rows(
            conn,
            """SELECT category, COUNT(*) n FROM listing WHERE status='published'
                GROUP BY category ORDER BY n DESC""",
        ):
            print(f"  {categories.title(row['category'])}: {row['n']}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("ksa.api.main:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ksa", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="забрать новые сообщения из каналов")
    collect.add_argument("--limit", type=int, default=None, help="сколько сообщений на канал")
    collect.set_defaults(func=cmd_collect)

    parse = sub.add_parser("parse", help="разобрать сообщения и склеить дубли")
    parse.add_argument("--limit", type=int, default=200)
    parse.add_argument(
        "--rules-only", action="store_true", help="без модели, только регулярки"
    )
    parse.set_defaults(func=cmd_parse)

    sub.add_parser("publish", help="опубликовать готовые карточки").set_defaults(func=cmd_publish)

    review = sub.add_parser("review", help="очередь спорных склеек")
    review.add_argument("--limit", type=int, default=20)
    review.set_defaults(func=cmd_review)

    sub.add_parser("stats", help="что лежит в базе").set_defaults(func=cmd_stats)

    serve = sub.add_parser("serve", help="поднять сайт и API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
