-- Desk Companion — схема из п.4 плана. Единственный источник правды:
-- setup-step1.sh держит копию для первичной загрузки, и test_db.py следит,
-- чтобы они не разъехались.
--
-- Ключевое правило п.4: не пишем каждое нажатие отдельной строкой. Это
-- утопило бы базу и лишний раз изнашивало microSD. Пишем агрегаты.

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

-- События смены состояния (мало записей, только на изменение)
CREATE TABLE IF NOT EXISTS state_events (
    id INTEGER PRIMARY KEY,
    ts DATETIME,
    presence BOOLEAN,
    active_app TEXT,
    category TEXT,          -- code / game / browser / other
    audio_active BOOLEAN,
    manual_status TEXT      -- NULL если не задан вручную
);
CREATE INDEX IF NOT EXISTS idx_state_events_ts ON state_events(ts);

-- Поминутные агрегаты активности (компактно, ~1440 строк/день)
CREATE TABLE IF NOT EXISTS activity_minute (
    ts DATETIME PRIMARY KEY,
    keystrokes INTEGER,
    mouse_clicks INTEGER,
    mouse_distance_px INTEGER,
    at_desk BOOLEAN,
    category TEXT
);

-- Показания воздуха (раз в 5-10 мин достаточно, не каждую секунду)
CREATE TABLE IF NOT EXISTS env_readings (
    ts DATETIME PRIMARY KEY,
    co2 INTEGER,
    temperature REAL,
    humidity REAL
);
