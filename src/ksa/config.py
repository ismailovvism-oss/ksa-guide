"""Пути, переменные окружения и список каналов."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
MEDIA_DIR = DATA_DIR / "media"
MIGRATIONS_DIR = ROOT / "migrations"
CHANNELS_FILE = ROOT / "config" / "channels.yml"

load_dotenv(ROOT / ".env")


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    return value.strip() if isinstance(value, str) else value


DB_PATH = Path(env("KSA_DB", str(DATA_DIR / "ksa.db")))

# Telegram API: получить на https://my.telegram.org → API development tools
TG_API_ID = env("TG_API_ID")
TG_API_HASH = env("TG_API_HASH")
TG_SESSION = env("TG_SESSION", str(ROOT / ".secrets" / "ksa.session"))

ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY")


@dataclass(frozen=True)
class ChannelConfig:
    username: str          # без @
    title: str = ""
    trust: int = 50
    enabled: bool = True


def load_channels() -> list[ChannelConfig]:
    """Читает config/channels.yml. Отсутствие файла — не ошибка, просто пусто."""
    if not CHANNELS_FILE.exists():
        return []
    raw = yaml.safe_load(CHANNELS_FILE.read_text(encoding="utf-8")) or {}
    out: list[ChannelConfig] = []
    for item in raw.get("channels") or []:
        if isinstance(item, str):
            out.append(ChannelConfig(username=item.lstrip("@")))
            continue
        out.append(
            ChannelConfig(
                username=str(item["username"]).lstrip("@"),
                title=item.get("title", ""),
                trust=int(item.get("trust", 50)),
                enabled=bool(item.get("enabled", True)),
            )
        )
    return out
