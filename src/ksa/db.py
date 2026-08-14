"""Доступ к БД. Тонкий слой над sqlite3: соединение, миграции, хелперы.

Специально без ORM — запросов немного, а плоский SQL проще переносить
на Postgres, когда объёмы этого потребуют.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import config


def utcnow() -> str:
    """Единый формат времени по всей базе: ISO-8601, UTC, без микросекунд."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    db_path = Path(path or config.DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Прогоняет .sql из migrations/ по порядку имён, каждую по одному разу.

    Учёт нужен с тех пор, как появились ALTER TABLE: повторный ADD COLUMN
    падает, идемпотентной формы у него в SQLite нет.
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migration (
               name       TEXT PRIMARY KEY,
               applied_at TEXT NOT NULL
           )"""
    )
    applied = {row["name"] for row in conn.execute("SELECT name FROM schema_migration")}

    for path in sorted(config.MIGRATIONS_DIR.glob("*.sql")):
        if path.name in applied:
            continue
        conn.executescript(path.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO schema_migration (name, applied_at) VALUES (?, ?)",
            (path.name, utcnow()),
        )


@contextmanager
def session(path: Path | str | None = None):
    conn = connect(path)
    try:
        migrate(conn)
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection):
    conn.execute("BEGIN")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def insert(conn: sqlite3.Connection, table: str, values: dict[str, Any]) -> int:
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    cur = conn.execute(
        f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values())
    )
    return int(cur.lastrowid)


def update(
    conn: sqlite3.Connection, table: str, row_id: int, values: dict[str, Any]
) -> None:
    if not values:
        return
    assignments = ", ".join(f"{col} = ?" for col in values)
    conn.execute(
        f"UPDATE {table} SET {assignments} WHERE id = ?",
        (*values.values(), row_id),
    )


def _bind(params: Iterable[Any] | Mapping[str, Any]) -> Any:
    """Позиционные параметры приводим к кортежу, именованные оставляем как есть."""
    return params if isinstance(params, Mapping) else tuple(params)


def rows(
    conn: sqlite3.Connection, sql: str, params: Iterable[Any] | Mapping[str, Any] = ()
) -> list[sqlite3.Row]:
    return conn.execute(sql, _bind(params)).fetchall()


def one(
    conn: sqlite3.Connection, sql: str, params: Iterable[Any] | Mapping[str, Any] = ()
) -> sqlite3.Row | None:
    return conn.execute(sql, _bind(params)).fetchone()


def dumps(value: Any) -> str | None:
    """JSON-поля храним строкой; None остаётся None, а не строкой 'null'."""
    return None if value is None else json.dumps(value, ensure_ascii=False)


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default
