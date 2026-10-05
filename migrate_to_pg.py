#!/usr/bin/env python3
"""
migrate_to_pg.py — One-time migration: imports existing JSON data into PostgreSQL.

Strategy:
  - Members: data/names.json is the ID-source of truth (has real UUIDs).
             members.json has the most complete usernames.
             → Merge: use data/names.json IDs, patch usernames from members.json.
             Any person in members.json but NOT in data/names.json is added as new.
             Any person in data/names.json but NOT in members.json is kept as-is.
  - State:   data/state.json (new round_order format) → bot_state table.
             Falls back to root state.json (legacy index format) if needed.
  - History: data/history.json → duty_history table.

Usage (on VPS, from project root):
    python3 migrate_to_pg.py

DATABASE_URL must be set in .env or the environment.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import date
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    sys.exit("ERROR: DATABASE_URL is not set. Add it to .env and retry.")

conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
conn.autocommit = False
cur = conn.cursor()

# ── Schema ──────────────────────────────────────────────────────────────────
cur.execute("""
CREATE TABLE IF NOT EXISTS members (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    username    TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS bot_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS duty_history (
    duty_date    DATE PRIMARY KEY,
    member_id    TEXT,
    name         TEXT NOT NULL DEFAULT '',
    username     TEXT NOT NULL DEFAULT '',
    round_number INTEGER NOT NULL DEFAULT 1,
    round_day    INTEGER NOT NULL DEFAULT 0,
    round_order  TEXT[] NOT NULL DEFAULT '{}',
    done         BOOLEAN NOT NULL DEFAULT FALSE
);
""")
conn.commit()
print("✅ Schema ensured.")


# ── Helpers ──────────────────────────────────────────────────────────────────

def read_json(path: Path):
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  ⚠ Could not read {path}: {e}")
        return None


def normalize_username(u: str | None) -> str:
    if not u:
        return ""
    u = u.strip()
    if u and not u.startswith("@"):
        u = "@" + u
    return u


# ── 1. Members ───────────────────────────────────────────────────────────────

# Load data/names.json — the ID-authoritative source
data_names_raw = read_json(Path("data/names.json")) or []

# Load members.json — the username-authoritative source
members_raw = read_json(Path("members.json")) or []

# Build a username lookup from members.json keyed by lowercased name
members_username_by_name: dict[str, str] = {}
members_by_name: dict[str, dict] = {}
for item in members_raw:
    key = item["name"].strip().casefold()
    members_username_by_name[key] = normalize_username(item.get("username", ""))
    members_by_name[key] = item

# Build the canonical members list from data/names.json, patching usernames from members.json
canonical: list[dict] = []
seen_ids: set[str] = set()
seen_names: set[str] = set()

for item in data_names_raw:
    name = item["name"].strip()
    member_id = str(item.get("id") or uuid.uuid5(uuid.NAMESPACE_URL, name.casefold()).hex)
    # Prefer username from members.json (more complete), fall back to data/names.json
    username = (
        members_username_by_name.get(name.casefold())
        or normalize_username(item.get("username", ""))
    )
    canonical.append({"id": member_id, "name": name, "username": username})
    seen_ids.add(member_id)
    seen_names.add(name.casefold())

# Add people who are in members.json but NOT in data/names.json (newly added)
for item in members_raw:
    name = item["name"].strip()
    if name.casefold() in seen_names:
        continue  # already included
    member_id = uuid.uuid4().hex
    username = normalize_username(item.get("username", ""))
    canonical.append({"id": member_id, "name": name, "username": username})
    seen_names.add(name.casefold())
    print(f"  ➕ New person from members.json (not in data/names.json): {name} {username}")

print(f"\n👥 Final member list ({len(canonical)} people):")
for p in canonical:
    print(f"   {p['name']}  {p['username'] or '(no username)'}")

# Insert into DB
id_map: dict[str, str] = {}  # name.casefold() → id
for p in canonical:
    cur.execute(
        """
        INSERT INTO members (id, name, username)
        VALUES (%s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, username = EXCLUDED.username
        """,
        (p["id"], p["name"], p["username"]),
    )
    id_map[p["name"].casefold()] = p["id"]

conn.commit()
print(f"\n✅ {len(canonical)} members inserted/updated in PostgreSQL.")


# ── 2. State ─────────────────────────────────────────────────────────────────

def upsert_state(key: str, value) -> None:
    if value is None:
        cur.execute("DELETE FROM bot_state WHERE key = %s", (key,))
        return
    if isinstance(value, list):
        value = ",".join(str(v) for v in value)
    elif isinstance(value, bool):
        value = "true" if value else "false"
    else:
        value = str(value)
    cur.execute(
        "INSERT INTO bot_state (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (key, value),
    )


raw_state = read_json(Path("data/state.json")) or read_json(Path("state.json")) or {}

if "round_order" in raw_state:
    # New-format state (UUID-based round_order list)
    print("\n📋 New-format state detected (round_order with UUIDs).")
    upsert_state("chat_id", raw_state.get("chat_id"))
    order = raw_state.get("round_order", [])
    # Filter to only IDs that exist in current member list
    valid_ids = {p["id"] for p in canonical}
    order = [mid for mid in order if mid in valid_ids]
    upsert_state("round_order", ",".join(order))
    upsert_state("round_position", raw_state.get("round_position", 0))
    upsert_state("round_number", raw_state.get("round_number", 1))
    upsert_state("last_member_id", raw_state.get("last_member_id"))
    upsert_state("last_assigned_date", raw_state.get("last_assigned_date"))
    upsert_state("last_announced_date", raw_state.get("last_announced_date"))
    upsert_state("today_duty_id", raw_state.get("today_duty_id"))
    upsert_state("today_duty_date", raw_state.get("today_duty_date"))
    upsert_state("today_duty_done", raw_state.get("today_duty_done", False))

else:
    # Legacy format: "order" is a list of integer indices, "pos", "duty_start"
    print("\n📋 Legacy state format detected (index-based order).")
    names_list = [p["id"] for p in canonical]  # ordered as in canonical list
    chat_id = raw_state.get("chat_id")
    upsert_state("chat_id", chat_id)

    old_order_indices = raw_state.get("order", [])
    order_ids: list[str] = []
    for idx in old_order_indices:
        if isinstance(idx, int) and 0 <= idx < len(names_list):
            mid = names_list[idx]
            if mid not in order_ids:
                order_ids.append(mid)
    # Append anyone not already in the order
    present = set(order_ids)
    for mid in names_list:
        if mid not in present:
            order_ids.append(mid)

    pos = raw_state.get("pos", 0)
    duty_start = raw_state.get("duty_start")
    upsert_state("round_order", ",".join(order_ids))
    upsert_state("round_position", pos)
    upsert_state("round_number", 1)
    upsert_state("last_assigned_date", duty_start)
    if duty_start and order_ids and pos < len(order_ids):
        upsert_state("today_duty_id", order_ids[pos])
        upsert_state("today_duty_date", duty_start)
    upsert_state("today_duty_done", False)

conn.commit()
print("✅ State imported.")


# ── 3. History ───────────────────────────────────────────────────────────────

raw_history = read_json(Path("data/history.json")) or read_json(Path("history.json")) or {}

inserted = 0
for day_str, record in raw_history.items():
    try:
        date.fromisoformat(day_str)
    except ValueError:
        print(f"  ⚠ Skipping invalid history key: {day_str}")
        continue
    # Patch username from current member list if available
    mid = record.get("member_id")
    person_username = record.get("username", "")
    if mid:
        match = next((p for p in canonical if p["id"] == mid), None)
        if match:
            person_username = match["username"]

    cur.execute(
        """
        INSERT INTO duty_history (duty_date, member_id, name, username, round_number, round_day, round_order, done)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (duty_date) DO NOTHING
        """,
        (
            day_str,
            mid,
            record.get("name", ""),
            person_username,
            record.get("round_number", 1),
            record.get("round_day", 0),
            record.get("round_order", []),
            record.get("done", False),
        ),
    )
    inserted += 1

conn.commit()
print(f"✅ History imported ({inserted} records).")
conn.close()
print("\n🎉 Migration complete. Start the bot with: pm2 start ecosystem.config.js")
