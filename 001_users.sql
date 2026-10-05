-- ============================================================
--  001_users.sql
--  Run this as the postgres superuser ONCE before anything else.
--  Usage:
--    sudo -u postgres psql -f 001_users.sql
-- ============================================================


-- ── 1. Role & Database ───────────────────────────────────────────────────────

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'navbat_user') THEN
        CREATE ROLE navbat_user WITH LOGIN PASSWORD 'MAN!152634879man';
        RAISE NOTICE 'Role navbat_user created.';
    ELSE
        -- Update the password in case it changed
        ALTER ROLE navbat_user WITH PASSWORD 'MAN!152634879man';
        RAISE NOTICE 'Role navbat_user already exists – password updated.';
    END IF;
END
$$;

SELECT current_database();   -- show we are still in postgres db

-- Create the app database (must be run outside a transaction block via \gexec trick)
-- psql will just print a NOTICE if it already exists.
SELECT 'CREATE DATABASE navbatchilik OWNER navbat_user'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'navbatchilik') \gexec

-- ── 2. Connect to the app database ───────────────────────────────────────────
\connect navbatchilik

-- Grant all on the schema to our user
GRANT ALL PRIVILEGES ON DATABASE navbatchilik TO navbat_user;
GRANT ALL ON SCHEMA public TO navbat_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO navbat_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO navbat_user;

-- ── 3. Tables ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS members (
    id          TEXT        PRIMARY KEY,
    name        TEXT        NOT NULL,
    username    TEXT        NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bot_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS duty_history (
    duty_date    DATE    PRIMARY KEY,
    member_id    TEXT,
    name         TEXT    NOT NULL DEFAULT '',
    username     TEXT    NOT NULL DEFAULT '',
    round_number INTEGER NOT NULL DEFAULT 1,
    round_day    INTEGER NOT NULL DEFAULT 0,
    round_order  TEXT[]  NOT NULL DEFAULT '{}',
    done         BOOLEAN NOT NULL DEFAULT FALSE
);

-- Give explicit ownership to navbat_user (in case created as postgres)
ALTER TABLE IF EXISTS members      OWNER TO navbat_user;
ALTER TABLE IF EXISTS bot_state    OWNER TO navbat_user;
ALTER TABLE IF EXISTS duty_history OWNER TO navbat_user;


-- ── 4. Seed members from the JSON files (via literal INSERT) ─────────────────
--  These values come from the merged data/names.json + members.json.
--  UUID IDs are preserved exactly as they appear in data/names.json.

INSERT INTO members (id, name, username) VALUES
    ('55edb2b7e4155898a597bacf1f3837e6', 'Mamadaliyev Hojakbar',    '@xojiakbar_01010'),
    ('83f3ed0038265d84adc5fefbe29ad316', 'Shonazarov Sherali',      '@mfbads'),
    ('6bb08391fe6a5bf1ab10f1516a3eb91a', 'Abduraimov G''anisher',   '@abduraimov066'),
    ('670802dde87a5e4fb2585afb30acd78d', 'Baxtiyorov Abdulloh',     '@baaxtrv'),
    ('e6b53f09dbcb53038acee2feb21a892e', 'Yusupov Azimjon',         '@zimidev'),
    ('8900732fa7385aa2ad0dfed88b63c33a', 'Kahharov Muhammad',       '@qahhoroff_m'),
    ('c32d333f11bf52f6aac42bd2b4db9403', 'Egamberdiyev Shaxriyor',  '@Shaxriyor_Egamberdiyev'),
    ('81d4c50e61345d6583883414ea90d282', 'Salimov Shuxrat',         '@renownix'),
    ('30b6f0a3c41e564ca127a748aa100ddf', 'Anvarov Abdulloh',        ''),
    ('b492a7ec8f9e5e5c8579ddf4463c6b9f', 'Safarboyev Sardorbek',    '@safarboyevv'),
    ('de730742d76c52049ea594ffbd8bb129', 'Baxtiyorov Donyor',       ''),
    ('d27d3844dd4e59d6bbad1197a0fac32f', 'Akilbkov Sherzot',        '@sherzod_prvt'),
    ('efa407c302255e38b32c27835b0ebea7', 'Bakbergenov Sardarbek',   ''),
    ('ae1524acf6d65d46892b1b73635b5aec', 'Osimjonov Ilhomjon',      '@x7_571'),
    ('42f4263c75fe51399cfe3a993ddd4e34', 'Shakirov Shoxrux',        '@VIPSHAKH')
ON CONFLICT (id) DO UPDATE
    SET name     = EXCLUDED.name,
        username = EXCLUDED.username;


-- ── 5. Seed bot_state from current state.json ────────────────────────────────
--  round_order matches the round_order array from data/history.json record.

INSERT INTO bot_state (key, value) VALUES
    ('chat_id',             '-5378536033'),
    ('round_number',        '1'),
    ('round_position',      '1'),
    ('round_order',
        '670802dde87a5e4fb2585afb30acd78d,'  -- Baxtiyorov Abdulloh  (duty today)
        '83f3ed0038265d84adc5fefbe29ad316,'  -- Shonazarov Sherali
        'efa407c302255e38b32c27835b0ebea7,'  -- Bakbergenov Sardarbek
        'de730742d76c52049ea594ffbd8bb129,'  -- Baxtiyorov Donyor
        '81d4c50e61345d6583883414ea90d282,'  -- Salimov Shuxrat
        'c32d333f11bf52f6aac42bd2b4db9403,'  -- Egamberdiyev Shaxriyor
        'e6b53f09dbcb53038acee2feb21a892e,'  -- Yusupov Azimjon
        'b492a7ec8f9e5e5c8579ddf4463c6b9f,'  -- Safarboyev Sardorbek
        '55edb2b7e4155898a597bacf1f3837e6,'  -- Mamadaliyev Hojakbar
        '8900732fa7385aa2ad0dfed88b63c33a,'  -- Kahharov Muhammad
        '42f4263c75fe51399cfe3a993ddd4e34,'  -- Shakirov Shoxrux
        'd27d3844dd4e59d6bbad1197a0fac32f,'  -- Akilbkov Sherzot
        '6bb08391fe6a5bf1ab10f1516a3eb91a,'  -- Abduraimov G''anisher
        '30b6f0a3c41e564ca127a748aa100ddf,'  -- Anvarov Abdulloh
        'ae1524acf6d65d46892b1b73635b5aec'   -- Osimjonov Ilhomjon
    ),
    ('last_member_id',      '670802dde87a5e4fb2585afb30acd78d'),
    ('last_assigned_date',  '2026-10-05'),
    ('last_announced_date', NULL),            -- will be set by bot after first announce
    ('today_duty_id',       '670802dde87a5e4fb2585afb30acd78d'),
    ('today_duty_date',     '2026-10-05'),
    ('today_duty_done',     'false')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;

-- Remove the NULL row inserted above (bot_state.value is NOT NULL)
DELETE FROM bot_state WHERE key = 'last_announced_date';


-- ── 6. Seed duty_history from data/history.json ──────────────────────────────

INSERT INTO duty_history
    (duty_date, member_id, name, username, round_number, round_day, round_order, done)
VALUES (
    '2026-10-05',
    '670802dde87a5e4fb2585afb30acd78d',
    'Baxtiyorov Abdulloh',
    '@baaxtrv',
    1,
    1,
    ARRAY[
        '670802dde87a5e4fb2585afb30acd78d',
        '83f3ed0038265d84adc5fefbe29ad316',
        'efa407c302255e38b32c27835b0ebea7',
        'de730742d76c52049ea594ffbd8bb129',
        '81d4c50e61345d6583883414ea90d282',
        'c32d333f11bf52f6aac42bd2b4db9403',
        'e6b53f09dbcb53038acee2feb21a892e',
        'b492a7ec8f9e5e5c8579ddf4463c6b9f',
        '55edb2b7e4155898a597bacf1f3837e6',
        '8900732fa7385aa2ad0dfed88b63c33a',
        '42f4263c75fe51399cfe3a993ddd4e34',
        'd27d3844dd4e59d6bbad1197a0fac32f',
        '6bb08391fe6a5bf1ab10f1516a3eb91a',
        '30b6f0a3c41e564ca127a748aa100ddf',
        'ae1524acf6d65d46892b1b73635b5aec'
    ],
    false
)
ON CONFLICT (duty_date) DO UPDATE
    SET member_id    = EXCLUDED.member_id,
        name         = EXCLUDED.name,
        username     = EXCLUDED.username,
        round_number = EXCLUDED.round_number,
        round_day    = EXCLUDED.round_day,
        round_order  = EXCLUDED.round_order,
        done         = EXCLUDED.done;


-- ═══════════════════════════════════════════════════════════════════════════
--  VERIFICATION QUERIES — run these to confirm everything transferred
-- ═══════════════════════════════════════════════════════════════════════════

\echo ''
\echo '══════════════════════════════════════════════════'
\echo '  VERIFICATION'
\echo '══════════════════════════════════════════════════'

\echo ''
\echo '── 1. Members (expect 15 rows) ───────────────────'
SELECT
    ROW_NUMBER() OVER (ORDER BY created_at, name) AS "#",
    name,
    CASE WHEN username = '' THEN '(no username)' ELSE username END AS username,
    id
FROM members
ORDER BY created_at, name;

\echo ''
\echo '── 2. Member count ───────────────────────────────'
SELECT COUNT(*) AS total_members FROM members;

\echo ''
\echo '── 3. Bot state ──────────────────────────────────'
SELECT key, value FROM bot_state ORDER BY key;

\echo ''
\echo '── 4. Today duty (from state) ────────────────────'
SELECT
    bs_date.value  AS duty_date,
    m.name         AS duty_person,
    m.username     AS username,
    bs_done.value  AS done
FROM bot_state bs_date
JOIN bot_state bs_id   ON bs_id.key   = 'today_duty_id'
JOIN bot_state bs_done ON bs_done.key = 'today_duty_done'
JOIN members m         ON m.id        = bs_id.value
WHERE bs_date.key = 'today_duty_date';

\echo ''
\echo '── 5. Duty history ───────────────────────────────'
SELECT
    duty_date,
    name,
    username,
    round_number,
    round_day,
    done,
    array_length(round_order, 1) AS order_size
FROM duty_history
ORDER BY duty_date DESC;

\echo ''
\echo '── 6. Round order expanded (15 positions expected) ──'
SELECT
    gs.pos                          AS position,
    m.name                          AS member,
    m.username                      AS username,
    CASE WHEN gs.pos <= (SELECT value::int FROM bot_state WHERE key = 'round_position')
         THEN '✓ done'
         ELSE '⏳ upcoming'
    END AS status
FROM (
    SELECT
        generate_series(1, array_length(round_order, 1)) AS pos,
        unnest(round_order)                               AS member_id
    FROM duty_history
    ORDER BY duty_date DESC
    LIMIT 1
) gs
LEFT JOIN members m ON m.id = gs.member_id
ORDER BY gs.pos;

\echo ''
\echo '══════════════════════════════════════════════════'
\echo '  All checks done. If counts match, migration OK.'
\echo '══════════════════════════════════════════════════'
