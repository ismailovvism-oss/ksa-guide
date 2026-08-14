"""Сбор сообщений из Telegram-каналов через Telethon.

Пишет только в raw_message: разбор и склейка — отдельный шаг (ksa.pipeline).
Такое разделение позволяет переразобрать весь архив, когда меняется логика,
не выкачивая ничего заново.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .. import config
from ..db import dumps, insert, one, update, utcnow
from ..dedup.phash import phash

# Сколько сообщений тянуть за один проход по каналу при первом сборе.
FIRST_RUN_LIMIT = 500


def _require_credentials() -> tuple[int, str]:
    if not config.TG_API_ID or not config.TG_API_HASH:
        raise RuntimeError(
            "Нет TG_API_ID / TG_API_HASH. Получить их: my.telegram.org → "
            "API development tools, затем вписать в .env"
        )
    return int(config.TG_API_ID), config.TG_API_HASH


def make_client():
    """Telethon-клиент. Первый запуск спросит телефон и код из Telegram."""
    from telethon import TelegramClient

    api_id, api_hash = _require_credentials()
    session_path = Path(config.TG_SESSION)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    return TelegramClient(str(session_path), api_id, api_hash)


def ensure_channel(conn: sqlite3.Connection, username: str, title: str = "", trust: int = 50) -> int:
    row = one(conn, "SELECT * FROM channel WHERE username = ?", (username,))
    if row is not None:
        if title and not row["title"]:
            update(conn, "channel", row["id"], {"title": title})
        return row["id"]
    return insert(
        conn,
        "channel",
        {
            "username": username,
            "title": title,
            "trust": trust,
            "enabled": 1,
            "last_msg_id": 0,
            "added_at": utcnow(),
        },
    )


async def _save_media(client, message, message_id: str) -> tuple[str | None, str | None]:
    """Скачивает фото поста. Возвращает (относительный путь, phash)."""
    if not message.photo:
        return None, None
    config.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    target = config.MEDIA_DIR / f"{message_id}.jpg"
    if not target.exists():
        saved = await client.download_media(message, file=str(target))
        if not saved:
            return None, None
    return f"media/{target.name}", phash(target)


def _flush_album(conn: sqlite3.Connection, channel_id: int, album: list[dict]) -> bool:
    """Пишет альбом одной записью. Возвращает True, если запись создана.

    В Telegram альбом — это несколько сообщений с общим grouped_id, и подпись
    есть только у одного из них. Поэтому текст берём оттуда, где он есть,
    а снимки собираем со всех.
    """
    if not album:
        return False

    with_text = next((m for m in album if m["text"].strip()), album[0])
    paths = [m["media_path"] for m in album if m["media_path"]]
    if not with_text["text"].strip() and not paths:
        return False

    try:
        insert(
            conn,
            "raw_message",
            {
                "channel_id": channel_id,
                # Идентификатор поста — сообщение с подписью: на него ведёт
                # ссылка «источник» на карточке.
                "tg_msg_id": with_text["id"],
                "grouped_id": with_text["grouped_id"],
                "posted_at": with_text["posted_at"],
                "edited_at": with_text["edited_at"],
                "text": with_text["text"],
                # Обложка — первый снимок: по ней считается хеш для дедупа.
                "media_path": paths[0] if paths else None,
                "media_phash": next(
                    (m["phash"] for m in album if m["media_path"] and m["phash"]), None
                ),
                "media_paths": dumps(paths) if paths else None,
                "fetched_at": utcnow(),
            },
        )
        return True
    except sqlite3.IntegrityError:
        return False  # пост уже забирали в прошлый раз


async def collect_channel(
    client, conn: sqlite3.Connection, username: str, limit: int | None = None
) -> int:
    """Докачивает новые сообщения канала. Возвращает число сохранённых постов."""
    channel_id = ensure_channel(conn, username)
    row = one(conn, "SELECT * FROM channel WHERE id = ?", (channel_id,))
    last_msg_id = row["last_msg_id"] if row else 0

    entity = await client.get_entity(username)
    if row is not None and not row["title"]:
        update(conn, "channel", channel_id, {"title": getattr(entity, "title", "")})

    saved = 0
    highest = last_msg_id
    album: list[dict] = []

    # reverse=True — от старых к новым, чтобы курсор двигался монотонно
    # и обрыв связи не оставлял дыр в архиве. Заодно сообщения одного
    # альбома идут подряд, и их достаточно копить в буфере.
    async for message in client.iter_messages(
        entity,
        min_id=last_msg_id,
        reverse=True,
        limit=limit if limit is not None else (None if last_msg_id else FIRST_RUN_LIMIT),
    ):
        text = message.message or ""
        if not text and not message.photo:
            continue

        # Кончился предыдущий альбом — записываем его и начинаем новый.
        if album and message.grouped_id != album[0]["grouped_id"]:
            saved += _flush_album(conn, channel_id, album)
            album = []

        media_path, media_phash = await _save_media(
            client, message, f"{username}_{message.id}"
        )
        album.append(
            {
                "id": message.id,
                "grouped_id": message.grouped_id,
                "posted_at": message.date.replace(microsecond=0).isoformat(),
                "edited_at": message.edit_date.isoformat() if message.edit_date else None,
                "text": text,
                "media_path": media_path,
                "phash": media_phash,
            }
        )
        # Одиночное сообщение — тот же альбом длиной в один снимок.
        if message.grouped_id is None:
            saved += _flush_album(conn, channel_id, album)
            album = []

        highest = max(highest, message.id)

    saved += _flush_album(conn, channel_id, album)
    update(conn, "channel", channel_id, {"last_msg_id": highest})
    return saved


async def collect_all(conn: sqlite3.Connection, limit: int | None = None) -> dict[str, int]:
    """Проходит по всем каналам из config/channels.yml."""
    channels = [c for c in config.load_channels() if c.enabled]
    if not channels:
        raise RuntimeError(
            "config/channels.yml пуст — впиши туда каналы, из которых собираем"
        )

    stats: dict[str, int] = {}
    client = make_client()
    async with client:
        for channel in channels:
            ensure_channel(conn, channel.username, channel.title, channel.trust)
            try:
                stats[channel.username] = await collect_channel(
                    client, conn, channel.username, limit
                )
            except Exception as error:  # один недоступный канал не должен рушить проход
                stats[channel.username] = -1
                print(f"  {channel.username}: ошибка — {error}")
    return stats
