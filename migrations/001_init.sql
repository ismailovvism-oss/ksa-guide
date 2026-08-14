-- Схема справочника. Пишется под SQLite, но сознательно держится в рамках
-- переносимого SQL: типы TEXT/INTEGER/REAL, времена — ISO-8601 в UTC строкой,
-- JSON — строкой. Переезд на Postgres = замена INTEGER PRIMARY KEY на
-- GENERATED ALWAYS AS IDENTITY и TEXT-времён на timestamptz.

-- Каналы-источники.
CREATE TABLE IF NOT EXISTS channel (
    id            INTEGER PRIMARY KEY,
    tg_id         INTEGER UNIQUE,               -- числовой id в Telegram
    username      TEXT,                         -- без @; NULL у приватных
    title         TEXT NOT NULL DEFAULT '',
    enabled       INTEGER NOT NULL DEFAULT 1,
    -- 0..100. Влияет на то, чей текст и фото станут канонической карточкой,
    -- когда одно объявление пришло из нескольких каналов.
    trust         INTEGER NOT NULL DEFAULT 50,
    -- курсор инкрементального сбора: последний вычитанный tg_msg_id
    last_msg_id   INTEGER NOT NULL DEFAULT 0,
    added_at      TEXT NOT NULL
);

-- Сырые сообщения. Пишутся один раз и не меняются: это архив, из которого
-- можно пересчитать парсинг и дедуп заново, когда поменяется логика.
CREATE TABLE IF NOT EXISTS raw_message (
    id            INTEGER PRIMARY KEY,
    channel_id    INTEGER NOT NULL REFERENCES channel(id),
    tg_msg_id     INTEGER NOT NULL,
    grouped_id    INTEGER,                      -- id альбома, если пост из нескольких медиа
    posted_at     TEXT NOT NULL,
    edited_at     TEXT,
    text          TEXT NOT NULL DEFAULT '',
    media_path    TEXT,                         -- относительный путь в data/media
    media_phash   TEXT,                         -- перцептивный хеш фото, 16 hex-символов
    fetched_at    TEXT NOT NULL,
    UNIQUE (channel_id, tg_msg_id)
);

CREATE INDEX IF NOT EXISTS idx_raw_posted   ON raw_message(posted_at);
CREATE INDEX IF NOT EXISTS idx_raw_channel  ON raw_message(channel_id, tg_msg_id);

-- Каноническая карточка справочника: одно реальное объявление,
-- сколько бы раз его ни перепостили.
CREATE TABLE IF NOT EXISTS listing (
    id             INTEGER PRIMARY KEY,
    category       TEXT NOT NULL,               -- слаг из ksa.categories
    -- Уточнение внутри категории: «Музей» для локаций, «Вилла» для аренды.
    -- Свободный текст: набор значений разный у каждой категории.
    subcategory    TEXT,
    city           TEXT,
    district       TEXT,
    title          TEXT NOT NULL,
    summary        TEXT,
    price_amount   REAL,
    price_currency TEXT,
    price_period   TEXT,                        -- month | year | day | once
    rooms          REAL,
    area_sqm       REAL,
    contacts       TEXT,                        -- JSON: [{"type":"phone","value":"+9665..."}]
    -- Ключ идентичности: контакт + категория + город + ценовая корзина.
    -- Самый сильный сигнал «это то же самое объявление».
    identity_key   TEXT,
    map_url        TEXT,                        -- для категории «локации»
    lat            REAL,
    lon            REAL,
    photo          TEXT,
    -- Ссылка на исходный пост. Показывается на карточке всегда: мы
    -- пересобираем чужие объявления и обязаны отдавать авторство обратно.
    source_url     TEXT,
    -- Идентификатор записи во внешней выгрузке (сейчас — data/locations.json).
    -- Отдельно от source_url, потому что один пост канала описывает до
    -- десятка мест сразу и ссылкой их не различить.
    external_id    TEXT UNIQUE,
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,               -- ← свежесть в выдаче считается по нему
    repost_count   INTEGER NOT NULL DEFAULT 1,
    status         TEXT NOT NULL DEFAULT 'pending',  -- pending|published|rejected|expired
    canonical_occurrence_id INTEGER,            -- из какого вхождения взят текст карточки
    updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_listing_feed     ON listing(status, category, city, last_seen_at);
CREATE INDEX IF NOT EXISTS idx_listing_identity ON listing(identity_key);

-- Вхождение: результат разбора одного сообщения. Много вхождений → один listing.
CREATE TABLE IF NOT EXISTS listing_occurrence (
    id             INTEGER PRIMARY KEY,
    raw_message_id INTEGER NOT NULL UNIQUE REFERENCES raw_message(id),
    listing_id     INTEGER REFERENCES listing(id),   -- NULL = ещё не кластеризовано
    is_listing     INTEGER,                    -- 0 = не объявление (болталка, новости)
    category       TEXT,
    city           TEXT,
    district       TEXT,
    title          TEXT,
    summary        TEXT,
    price_amount   REAL,
    price_currency TEXT,
    price_period   TEXT,
    rooms          REAL,
    area_sqm       REAL,
    contacts       TEXT,                        -- JSON-массив
    norm_text      TEXT,                        -- нормализованный текст для сравнения
    parse_model    TEXT,                        -- чем разобрано: имя модели или 'rules'
    parsed_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_occ_listing ON listing_occurrence(listing_id);
CREATE INDEX IF NOT EXISTS idx_occ_bucket  ON listing_occurrence(category, city);

-- Оплаченный подъём наверх. Монетизация архитектурно — только эта таблица
-- плюс одно слагаемое в сортировке выдачи.
CREATE TABLE IF NOT EXISTS promotion (
    id          INTEGER PRIMARY KEY,
    listing_id  INTEGER NOT NULL REFERENCES listing(id),
    tier        INTEGER NOT NULL DEFAULT 1,     -- чем больше, тем выше
    starts_at   TEXT NOT NULL,
    ends_at     TEXT NOT NULL,
    note        TEXT
);

CREATE INDEX IF NOT EXISTS idx_promo_active ON promotion(listing_id, ends_at);

-- Серая зона дедупа: похоже, но недостаточно, чтобы склеить автоматически.
-- Отсюда наполняется очередь модерации; решение уходит в dedup_override.
CREATE TABLE IF NOT EXISTS dedup_suggestion (
    id             INTEGER PRIMARY KEY,
    occurrence_id  INTEGER NOT NULL REFERENCES listing_occurrence(id),
    listing_id     INTEGER NOT NULL REFERENCES listing(id),
    score          REAL NOT NULL,
    reasons        TEXT,                       -- JSON-массив человекочитаемых доводов
    created_at     TEXT NOT NULL,
    resolved_at    TEXT,
    UNIQUE (occurrence_id, listing_id)
);

CREATE INDEX IF NOT EXISTS idx_suggestion_open ON dedup_suggestion(resolved_at, score);

-- Решения модератора по склейке. Нужны, чтобы ручные правки переживали
-- пересчёт дедупа с новыми порогами.
CREATE TABLE IF NOT EXISTS dedup_override (
    id             INTEGER PRIMARY KEY,
    occurrence_id  INTEGER NOT NULL REFERENCES listing_occurrence(id),
    listing_id     INTEGER REFERENCES listing(id),  -- NULL = «это точно отдельное объявление»
    decided_at     TEXT NOT NULL,
    UNIQUE (occurrence_id)
);
