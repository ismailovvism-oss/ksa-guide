"""Сквозная проверка: сырые сообщения → карточки без дублей.

Модель здесь не вызывается (use_model=False) — проверяется именно склейка,
а не качество разбора.
"""

import pytest

from ksa import db
from ksa.pipeline import run

# Одна и та же квартира, три раза в двух каналах, с разной вёрсткой.
FLAT_ORIGINAL = """🏠 Сдаётся 2-комнатная квартира в Медине, район Аль-Азизия.
2500 риалов в месяц. Кондиционер, кухня, до Харама 10 минут.
Телефон: 0512345678"""

FLAT_REPOST = """Сдается 2х комнатная квартира в Медине, р-н Аль-Азизия 🔥
2500 SAR/месяц. Есть кондиционер и кухня, до Харама 10 минут пешком.
тел +966512345678
Подписывайтесь: @ksa_arenda"""

FLAT_REPOST_LATER = """СДАЁТСЯ КВАРТИРА!!! Медина, Аль-Азизия, 2 комнаты.
2500 риалов/мес, кондиционер, кухня, 10 минут до Харама.
Звонить: 966512345678"""

# Другая квартира того же агента: тот же телефон, но иное жильё.
STUDIO_SAME_AGENT = """Сдаётся студия в Медине, район Куба.
900 риалов в месяц, без мебели, 1 комната.
Телефон: 0512345678"""


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "test.db")
    db.migrate(connection)
    yield connection
    connection.close()


def add_channel(conn, username, trust=50):
    return db.insert(
        conn,
        "channel",
        {"username": username, "title": username, "trust": trust,
         "enabled": 1, "last_msg_id": 0, "added_at": db.utcnow()},
    )


def add_message(conn, channel_id, msg_id, text, posted_at, phash=None):
    return db.insert(
        conn,
        "raw_message",
        {"channel_id": channel_id, "tg_msg_id": msg_id, "posted_at": posted_at,
         "text": text, "media_phash": phash, "fetched_at": db.utcnow()},
    )


def listings(conn):
    return db.rows(conn, "SELECT * FROM listing ORDER BY id")


def test_reposts_across_channels_collapse_into_one_card(conn):
    """Ради этого всё и затевалось: 3 поста → 1 карточка."""
    first = add_channel(conn, "ksa_arenda")
    second = add_channel(conn, "medina_chat")

    add_message(conn, first, 1, FLAT_ORIGINAL, "2026-08-01T09:00:00+00:00")
    add_message(conn, second, 1, FLAT_REPOST, "2026-08-03T12:00:00+00:00")
    add_message(conn, first, 2, FLAT_REPOST_LATER, "2026-08-10T08:00:00+00:00")

    stats = run(conn, use_model=False)

    assert stats["parsed"] == 3
    cards = listings(conn)
    assert len(cards) == 1, [c["title"] for c in cards]

    card = cards[0]
    assert card["repost_count"] == 3
    # Свежесть считается по последнему появлению, а первое появление хранится:
    # так карточка поднимается наверх, но видно, как давно она в обороте.
    assert card["last_seen_at"].startswith("2026-08-10")
    assert card["first_seen_at"].startswith("2026-08-01")


def test_different_flats_from_one_agent_stay_apart(conn):
    """Общий телефон не должен схлопывать разные объявления."""
    channel = add_channel(conn, "ksa_arenda")
    add_message(conn, channel, 1, FLAT_ORIGINAL, "2026-08-01T09:00:00+00:00")
    add_message(conn, channel, 2, STUDIO_SAME_AGENT, "2026-08-02T09:00:00+00:00")

    run(conn, use_model=False)
    assert len(listings(conn)) == 2


def test_rerunning_the_pipeline_changes_nothing(conn):
    """Конвейер идемпотентен: повторный запуск не создаёт вхождений заново."""
    channel = add_channel(conn, "ksa_arenda")
    add_message(conn, channel, 1, FLAT_ORIGINAL, "2026-08-01T09:00:00+00:00")

    run(conn, use_model=False)
    before = len(listings(conn))
    second = run(conn, use_model=False)

    assert second["parsed"] == 0
    assert len(listings(conn)) == before


def test_more_trusted_channel_supplies_the_card_text(conn):
    """Карточку представляет источник с большим доверием."""
    weak = add_channel(conn, "spam_channel", trust=10)
    strong = add_channel(conn, "good_channel", trust=90)

    add_message(conn, weak, 1, FLAT_ORIGINAL, "2026-08-01T09:00:00+00:00")
    add_message(conn, strong, 1, FLAT_REPOST, "2026-08-02T09:00:00+00:00")

    run(conn, use_model=False)
    card = listings(conn)[0]

    canonical = db.one(
        conn,
        """SELECT c.username AS username FROM listing_occurrence o
             JOIN raw_message r ON r.id = o.raw_message_id
             JOIN channel c ON c.id = r.channel_id
            WHERE o.id = ?""",
        (card["canonical_occurrence_id"],),
    )
    assert canonical["username"] == "good_channel"


def test_reposts_bring_their_own_photos_into_one_gallery(conn):
    """Ради этого и заводилась галерея: карточка полнее любого исходного поста."""
    first = add_channel(conn, "ksa_arenda")
    second = add_channel(conn, "medina_chat")

    one = add_message(conn, first, 1, FLAT_ORIGINAL, "2026-08-01T09:00:00+00:00")
    two = add_message(conn, second, 1, FLAT_REPOST, "2026-08-03T09:00:00+00:00")
    db.update(conn, "raw_message", one, {"media_path": "media/a.jpg"})
    db.update(conn, "raw_message", two, {"media_path": "media/b.jpg"})

    run(conn, use_model=False)

    cards = listings(conn)
    assert len(cards) == 1
    gallery = db.loads(cards[0]["photos"], [])
    assert gallery == ["media/a.jpg", "media/b.jpg"]
    # Обложка — снимок из первого поста, он же попадёт в плитку выдачи.
    assert cards[0]["photo"] == "media/a.jpg"


def test_the_same_photo_is_not_added_twice(conn):
    channel = add_channel(conn, "ksa_arenda")
    one = add_message(conn, channel, 1, FLAT_ORIGINAL, "2026-08-01T09:00:00+00:00")
    two = add_message(conn, channel, 2, FLAT_REPOST, "2026-08-02T09:00:00+00:00")
    for raw_id in (one, two):
        db.update(conn, "raw_message", raw_id, {"media_path": "media/same.jpg"})

    run(conn, use_model=False)
    assert db.loads(listings(conn)[0]["photos"], []) == ["media/same.jpg"]
