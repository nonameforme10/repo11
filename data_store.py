"""PostgreSQL storage and daily rotation logic for the duty bot."""
from __future__ import annotations

import json
import logging
import os
import random
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras
from psycopg2.extensions import connection as PgConnection

log = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Tashkent")

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Fallback list used only when no JSON files exist at all on first run.
DEFAULT_NAMES = [
    {"name": "Mamadaliyev Hojakbar", "username": "@xojiakbar_01010"},
    {"name": "Shonazarov Sherali", "username": "@mfbads"},
    {"name": "Abduraimov G'anisher", "username": "@abduraimov066"},
    {"name": "Baxtiyorov Abdulloh", "username": "@baaxtrv"},
    {"name": "Yusupov Azimjon", "username": "@zimidev"},
    {"name": "Kahharov Muhammad", "username": "@qahhoroff_m"},
    {"name": "Egamberdiyev Shaxriyor", "username": "@Shaxriyor_Egamberdiyev"},
    {"name": "Salimov Shuxrat", "username": "@renownix"},
    {"name": "Anvarov Abdulloh", "username": ""},
    {"name": "Safarboyev Sardorbek", "username": "@safarboyevv"},
    {"name": "Baxtiyorov Donyor", "username": ""},
    {"name": "Akilbkov Sherzot", "username": "@sherzod_prvt"},
    {"name": "Bakbergenov Sardarbek", "username": ""},
    {"name": "Osimjonov Ilhomjon", "username": "@x7_571"},
]


def _read_json_file(path: Path) -> Any | None:
    """Read and parse a JSON file; return None if missing or invalid."""
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        log.warning("Could not read %s: %s", path, exc)
        return None


def _normalize_username(u: str | None) -> str:
    if not u:
        return ""
    u = u.strip()
    return ("@" + u) if u and not u.startswith("@") else u


def _load_canonical_members() -> list[dict]:
    """
    Merge members from JSON files into a canonical list.

    Priority:
      - data/names.json  → has proper UUIDs (ID-authoritative)
      - members.json     → has the most complete usernames (username-authoritative)
      - DEFAULT_NAMES    → last resort if no files exist

    People present in members.json but absent from data/names.json are appended
    as new entries. Usernames from members.json override empty ones.
    """
    data_names = _read_json_file(Path("data/names.json")) or []
    members    = _read_json_file(Path("members.json"))    or []

    # Build username lookup from members.json
    members_username: dict[str, str] = {
        item["name"].strip().casefold(): _normalize_username(item.get("username"))
        for item in members
        if isinstance(item, dict) and item.get("name")
    }

    canonical: list[dict] = []
    seen: set[str] = set()

    # Step 1 – take entries from data/names.json (preserves UUIDs)
    for item in data_names:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        name = item["name"].strip()
        mid  = str(item.get("id") or uuid.uuid5(uuid.NAMESPACE_URL, name.casefold()).hex)
        # Prefer username from members.json; fall back to whatever is in data/names.json
        username = (
            members_username.get(name.casefold())
            or _normalize_username(item.get("username"))
        )
        canonical.append({"id": mid, "name": name, "username": username})
        seen.add(name.casefold())

    # Step 2 – append people only in members.json (newly added since last sync)
    for item in members:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        name = item["name"].strip()
        if name.casefold() in seen:
            continue
        mid      = uuid.uuid4().hex
        username = _normalize_username(item.get("username"))
        canonical.append({"id": mid, "name": name, "username": username})
        seen.add(name.casefold())
        log.info("New member from members.json not in data/names.json: %s %s", name, username)

    # Step 3 – if still empty, fall back to hardcoded defaults
    if not canonical:
        log.warning("No JSON member files found – seeding from DEFAULT_NAMES.")
        for item in DEFAULT_NAMES:
            name = item["name"].strip()
            mid  = uuid.uuid5(uuid.NAMESPACE_URL, name.casefold()).hex
            canonical.append({"id": mid, "name": name, "username": item.get("username", "")})

    return canonical


def _get_conn() -> PgConnection:
    """Return a new psycopg2 connection using DATABASE_URL."""
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def iso_day(value: date) -> str:
    return value.isoformat()


def tashkent_today() -> date:
    return datetime.now(TZ).date()


# ---------------------------------------------------------------------------
# Database schema bootstrap
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
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
    duty_date   DATE PRIMARY KEY,
    member_id   TEXT,
    name        TEXT NOT NULL DEFAULT '',
    username    TEXT NOT NULL DEFAULT '',
    round_number INTEGER NOT NULL DEFAULT 1,
    round_day   INTEGER NOT NULL DEFAULT 0,
    round_order TEXT[] NOT NULL DEFAULT '{}',
    done        BOOLEAN NOT NULL DEFAULT FALSE
);
"""


def _bootstrap_schema(conn: PgConnection) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
    conn.commit()


# ---------------------------------------------------------------------------
# PostgresStore – drop-in replacement for JsonStore
# ---------------------------------------------------------------------------

class PostgresStore:
    """Stores names, state and history in PostgreSQL instead of JSON files."""

    storage_errors: list[str]

    def __init__(self) -> None:
        self.storage_errors = []
        self._conn = _get_conn()
        _bootstrap_schema(self._conn)
        self._seed_if_empty()
        self._reconcile_history_state()
        self._remove_unknown_ids()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cur(self):
        return self._conn.cursor()

    def _commit(self) -> None:
        self._conn.commit()

    # ------------------------------------------------------------------
    # names
    # ------------------------------------------------------------------

    def _seed_if_empty(self) -> None:
        """
        Populate members table from JSON files if it is empty.

        Reads data/names.json (ID-authoritative) merged with members.json
        (username-authoritative). Falls back to DEFAULT_NAMES only if no
        files exist at all.
        """
        with self._cur() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM members")
            row = cur.fetchone()
        if row["cnt"] != 0:
            return

        people = _load_canonical_members()
        log.info("members table is empty – seeding %d members from JSON files", len(people))
        for person in people:
            with self._cur() as cur:
                cur.execute(
                    "INSERT INTO members (id, name, username) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (person["id"], person["name"], person["username"]),
                )
        self._commit()

        # Seed state with a shuffled round order
        order = [p["id"] for p in people]
        random.shuffle(order)
        self._set_state_key("round_number", "1")
        self._set_state_key("round_position", "0")
        self._set_state_key("round_order", ",".join(order))
        self._commit()


    @property
    def names(self) -> list[dict[str, str]]:
        with self._cur() as cur:
            cur.execute("SELECT id, name, username FROM members ORDER BY created_at, name")
            return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # state helpers
    # ------------------------------------------------------------------

    def _get_state_key(self, key: str, default: Any = None) -> str | None:
        with self._cur() as cur:
            cur.execute("SELECT value FROM bot_state WHERE key = %s", (key,))
            row = cur.fetchone()
        return row["value"] if row else default

    def _set_state_key(self, key: str, value: str | None) -> None:
        if value is None:
            with self._cur() as cur:
                cur.execute("DELETE FROM bot_state WHERE key = %s", (key,))
        else:
            with self._cur() as cur:
                cur.execute(
                    """
                    INSERT INTO bot_state (key, value) VALUES (%s, %s)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                    """,
                    (key, str(value)),
                )

    @property
    def state(self) -> _StateProxy:
        return _StateProxy(self)

    def save_state(self) -> None:
        """No-op: state is written immediately via _StateProxy.__setitem__."""

    # ------------------------------------------------------------------
    # history
    # ------------------------------------------------------------------

    @property
    def history(self) -> _HistoryProxy:
        return _HistoryProxy(self)

    def save_history(self) -> None:
        """No-op: history is written immediately via _HistoryProxy.__setitem__."""

    def save_names(self) -> None:
        """No-op: names are written immediately via add_name / remove_name."""

    # ------------------------------------------------------------------
    # Reconcile & cleanup
    # ------------------------------------------------------------------

    def _reconcile_history_state(self) -> None:
        """If history has a newer entry than last_assigned_date, sync state."""
        with self._cur() as cur:
            cur.execute(
                "SELECT duty_date, member_id, round_number, round_day, round_order, done "
                "FROM duty_history ORDER BY duty_date DESC LIMIT 1"
            )
            row = cur.fetchone()
        if not row:
            return
        newest = row["duty_date"].isoformat()
        state_last = self._get_state_key("last_assigned_date")
        if state_last and newest <= state_last:
            return
        self._set_state_key("round_order", ",".join(row["round_order"]))
        self._set_state_key("round_position", str(row["round_day"]))
        self._set_state_key("round_number", str(row["round_number"]))
        self._set_state_key("today_duty_id", row["member_id"])
        self._set_state_key("today_duty_date", newest)
        self._set_state_key("today_duty_done", "true" if row["done"] else "false")
        self._set_state_key("last_member_id", row["member_id"])
        self._set_state_key("last_assigned_date", newest)
        self._commit()

    def _remove_unknown_ids(self) -> None:
        """Purge member IDs that are no longer in the members table from round_order."""
        active = {p["id"] for p in self.names}
        order_str = self._get_state_key("round_order", "")
        order = [mid for mid in (order_str or "").split(",") if mid] if order_str else []
        cursor = max(0, min(int(self._get_state_key("round_position", "0") or "0"), len(order)))
        removed_before = sum(1 for mid in order[:cursor] if mid not in active)
        order = [mid for mid in order if mid in active]
        cursor = max(0, cursor - removed_before)
        present = set(order)
        for person in self.names:
            if person["id"] not in present:
                insert_at = random.randint(cursor, len(order))
                order.insert(insert_at, person["id"])
        self._set_state_key("round_order", ",".join(order))
        self._set_state_key("round_position", str(cursor))
        self._commit()

    # ------------------------------------------------------------------
    # Public API (mirrors JsonStore)
    # ------------------------------------------------------------------

    def member(self, member_id: str | None) -> dict[str, str] | None:
        if member_id is None:
            return None
        with self._cur() as cur:
            cur.execute("SELECT id, name, username FROM members WHERE id = %s", (member_id,))
            row = cur.fetchone()
        if row:
            return dict(row)
        # Fallback: check history
        with self._cur() as cur:
            cur.execute(
                "SELECT member_id, name, username FROM duty_history WHERE member_id = %s ORDER BY duty_date DESC LIMIT 1",
                (member_id,),
            )
            row = cur.fetchone()
        if row:
            return {"id": member_id, "name": row["name"], "username": row["username"]}
        return None

    def member_snapshot(self, member_id: str) -> dict[str, Any]:
        person = self.member(member_id)
        if person is None:
            return {"member_id": member_id, "name": "O'chirilgan a'zo", "username": ""}
        return {"member_id": member_id, "name": person["name"], "username": person.get("username", "")}

    def history_record(
        self, member_id: str, round_number: int, round_day: int, order: list[str], done: bool = False
    ) -> dict[str, Any]:
        return {
            **self.member_snapshot(member_id),
            "round_number": round_number,
            "round_day": round_day,
            "round_order": list(order),
            "done": done,
        }

    def _start_round(self) -> None:
        order = [p["id"] for p in self.names]
        random.shuffle(order)
        previous = self._get_state_key("last_member_id")
        if len(order) > 1 and order[0] == previous:
            swap_with = random.randrange(1, len(order))
            order[0], order[swap_with] = order[swap_with], order[0]
        self._set_state_key("round_order", ",".join(order))
        self._set_state_key("round_position", "0")
        round_number = int(self._get_state_key("round_number", "0") or "0") + 1
        self._set_state_key("round_number", str(round_number))
        self._commit()

    def _assign_day(self, day: date, overwrite: bool = False) -> None:
        key = iso_day(day)
        if not overwrite:
            with self._cur() as cur:
                cur.execute("SELECT * FROM duty_history WHERE duty_date = %s", (key,))
                existing = cur.fetchone()
            if existing:
                order = list(existing["round_order"])
                self._set_state_key("round_order", ",".join(order))
                self._set_state_key("round_position", str(existing["round_day"]))
                self._set_state_key("round_number", str(existing["round_number"]))
                self._set_state_key("today_duty_id", existing["member_id"])
                self._set_state_key("today_duty_date", key)
                self._set_state_key("today_duty_done", "true" if existing["done"] else "false")
                self._set_state_key("last_member_id", existing["member_id"])
                self._set_state_key("last_assigned_date", key)
                self._commit()
                return

        names = self.names
        if not names:
            self._set_state_key("today_duty_id", None)
            self._set_state_key("today_duty_date", key)
            self._set_state_key("today_duty_done", "false")
            self._set_state_key("last_assigned_date", key)
            order_str = self._get_state_key("round_order", "")
            order = [mid for mid in (order_str or "").split(",") if mid]
            round_number = int(self._get_state_key("round_number", "1") or "1")
            with self._cur() as cur:
                cur.execute(
                    """
                    INSERT INTO duty_history (duty_date, member_id, name, username, round_number, round_day, round_order, done)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (duty_date) DO UPDATE SET
                        member_id = EXCLUDED.member_id,
                        name = EXCLUDED.name,
                        username = EXCLUDED.username,
                        round_number = EXCLUDED.round_number,
                        round_day = EXCLUDED.round_day,
                        round_order = EXCLUDED.round_order,
                        done = EXCLUDED.done
                    """,
                    (key, None, "Navbatchi ro'yxati bo'sh", "", round_number, 0, order, False),
                )
            self._commit()
            return

        order_str = self._get_state_key("round_order", "")
        order = [mid for mid in (order_str or "").split(",") if mid]
        position = int(self._get_state_key("round_position", "0") or "0")
        if not order or position >= len(order):
            self._start_round()
            order_str = self._get_state_key("round_order", "")
            order = [mid for mid in (order_str or "").split(",") if mid]
            position = 0

        member_id = order[position]
        position += 1
        round_number = int(self._get_state_key("round_number", "1") or "1")

        with self._cur() as cur:
            cur.execute(
                """
                INSERT INTO duty_history (duty_date, member_id, name, username, round_number, round_day, round_order, done)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (duty_date) DO UPDATE SET
                    member_id = EXCLUDED.member_id,
                    name = EXCLUDED.name,
                    username = EXCLUDED.username,
                    round_number = EXCLUDED.round_number,
                    round_day = EXCLUDED.round_day,
                    round_order = EXCLUDED.round_order,
                    done = EXCLUDED.done
                """,
                (
                    key,
                    member_id,
                    self.member_snapshot(member_id)["name"],
                    self.member_snapshot(member_id)["username"],
                    round_number,
                    position,
                    order,
                    False,
                ),
            )
        self._set_state_key("round_position", str(position))
        self._set_state_key("last_member_id", member_id)
        self._set_state_key("last_assigned_date", key)
        self._set_state_key("today_duty_id", member_id)
        self._set_state_key("today_duty_date", key)
        self._set_state_key("today_duty_done", "false")
        self._commit()

    def ensure_assignment_through(self, target: date) -> None:
        last_text = self._get_state_key("last_assigned_date")
        if not last_text:
            self._assign_day(target)
            return
        try:
            last = date.fromisoformat(last_text)
        except (TypeError, ValueError):
            last = target - timedelta(days=1)
            self._set_state_key("last_assigned_date", last.isoformat())
            self._commit()
        if last >= target:
            return
        day = last + timedelta(days=1)
        while day <= target:
            self._assign_day(day)
            day += timedelta(days=1)

    def start_new_round_today(self, today: date) -> None:
        self._start_round()
        self._set_state_key("round_position", "0")
        self._set_state_key("last_assigned_date", (today - timedelta(days=1)).isoformat())
        self._set_state_key("last_announced_date", None)
        self._set_state_key("today_duty_id", None)
        self._set_state_key("today_duty_date", None)
        self._set_state_key("today_duty_done", "false")
        self._commit()
        self._assign_day(today, overwrite=True)

    def mark_today_done(self, today: date) -> bool:
        key = self._get_state_key("today_duty_date")
        member_id = self._get_state_key("today_duty_id")
        if not key or key != today.isoformat() or not member_id:
            return False
        self._set_state_key("today_duty_done", "true")
        with self._cur() as cur:
            cur.execute(
                "UPDATE duty_history SET done = TRUE WHERE duty_date = %s",
                (key,),
            )
        self._commit()
        return True

    def add_name(self, name: str, username: str = "") -> dict[str, str]:
        normalized = name.strip()
        existing = self.names
        if any(p["name"].casefold() == normalized.casefold() for p in existing):
            raise ValueError("Bu ism ro'yxatda bor.")
        handle = username.strip()
        if handle and not handle.startswith("@"):
            handle = "@" + handle
        if handle and any((p.get("username") or "").casefold() == handle.casefold() for p in existing):
            raise ValueError("Bu username ro'yxatda bor.")
        member_id = uuid.uuid4().hex
        with self._cur() as cur:
            cur.execute(
                "INSERT INTO members (id, name, username) VALUES (%s, %s, %s)",
                (member_id, normalized, handle),
            )
        order_str = self._get_state_key("round_order", "")
        order = [mid for mid in (order_str or "").split(",") if mid]
        cursor = max(0, min(int(self._get_state_key("round_position", "0") or "0"), len(order)))
        order.insert(random.randint(cursor, len(order)), member_id)
        self._set_state_key("round_order", ",".join(order))
        self._commit()
        return {"id": member_id, "name": normalized, "username": handle}

    def remove_name(self, member_id: str) -> tuple[dict[str, str], bool]:
        person = next((p for p in self.names if p["id"] == member_id), None)
        if person is None:
            raise ValueError("Ism topilmadi.")
        today_id = self._get_state_key("today_duty_id")
        today_date = self._get_state_key("today_duty_date")
        was_today = today_id == member_id and today_date == tashkent_today().isoformat()
        order_str = self._get_state_key("round_order", "")
        order = [mid for mid in (order_str or "").split(",") if mid]
        cursor = int(self._get_state_key("round_position", "0") or "0")
        if member_id in order:
            idx = order.index(member_id)
            order.pop(idx)
            if idx < cursor:
                cursor -= 1
        self._set_state_key("round_order", ",".join(order))
        self._set_state_key("round_position", str(max(0, cursor)))
        with self._cur() as cur:
            cur.execute("DELETE FROM members WHERE id = %s", (member_id,))
        self._commit()
        return person, was_today

    def last_30_days(self, today: date) -> list[tuple[str, dict[str, Any] | None]]:
        days = [(today - timedelta(days=offset)).isoformat() for offset in range(29, -1, -1)]
        placeholders = ",".join(["%s"] * len(days))
        with self._cur() as cur:
            cur.execute(
                f"SELECT duty_date::text, member_id, name, username, done FROM duty_history WHERE duty_date::text IN ({placeholders})",
                days,
            )
            rows = {r["duty_date"]: dict(r) for r in cur.fetchall()}
        return [(d, rows.get(d)) for d in days]


# ---------------------------------------------------------------------------
# Proxy objects: let bot.py use store.state["key"] and store.history["date"]
# as before, but the data lives in Postgres.
# ---------------------------------------------------------------------------

class _StateProxy:
    """Dict-like proxy that reads/writes the bot_state table."""

    def __init__(self, store: PostgresStore) -> None:
        self._store = store

    def get(self, key: str, default: Any = None) -> Any:
        raw = self._store._get_state_key(key)
        if raw is None:
            return default
        # Deserialize booleans stored as strings
        if raw.lower() == "true":
            return True
        if raw.lower() == "false":
            return False
        # Round order stored as comma-separated string
        if key == "round_order":
            return [mid for mid in raw.split(",") if mid]
        # Numeric fields
        if key in ("round_position", "round_number", "last_message_id"):
            try:
                return int(raw)
            except ValueError:
                return default
        # chat_id may be a large integer
        if key == "chat_id":
            try:
                return int(raw)
            except ValueError:
                return raw
        return raw

    def __getitem__(self, key: str) -> Any:
        val = self.get(key)
        if val is None:
            raise KeyError(key)
        return val

    def __setitem__(self, key: str, value: Any) -> None:
        if value is None:
            self._store._set_state_key(key, None)
        elif key == "round_order" and isinstance(value, list):
            self._store._set_state_key(key, ",".join(str(v) for v in value))
        elif isinstance(value, bool):
            self._store._set_state_key(key, "true" if value else "false")
        else:
            self._store._set_state_key(key, str(value))
        self._store._commit()

    def __contains__(self, key: str) -> bool:
        return self._store._get_state_key(key) is not None

    def setdefault(self, key: str, default: Any = None) -> Any:
        val = self.get(key)
        if val is None:
            self[key] = default
            return default
        return val

    def update(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            self[k] = v


class _HistoryProxy:
    """Dict-like proxy that reads/writes the duty_history table."""

    def __init__(self, store: PostgresStore) -> None:
        self._store = store

    def get(self, key: str, default: Any = None) -> dict[str, Any] | None:
        with self._store._cur() as cur:
            cur.execute("SELECT * FROM duty_history WHERE duty_date::text = %s", (key,))
            row = cur.fetchone()
        if row is None:
            return default
        r = dict(row)
        r["duty_date"] = r["duty_date"].isoformat() if hasattr(r["duty_date"], "isoformat") else r["duty_date"]
        return r

    def __getitem__(self, key: str) -> dict[str, Any]:
        val = self.get(key)
        if val is None:
            raise KeyError(key)
        return val

    def __setitem__(self, key: str, value: dict[str, Any]) -> None:
        with self._store._cur() as cur:
            cur.execute(
                """
                INSERT INTO duty_history (duty_date, member_id, name, username, round_number, round_day, round_order, done)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (duty_date) DO UPDATE SET
                    member_id = EXCLUDED.member_id,
                    name = EXCLUDED.name,
                    username = EXCLUDED.username,
                    round_number = EXCLUDED.round_number,
                    round_day = EXCLUDED.round_day,
                    round_order = EXCLUDED.round_order,
                    done = EXCLUDED.done
                """,
                (
                    key,
                    value.get("member_id"),
                    value.get("name", ""),
                    value.get("username", ""),
                    value.get("round_number", 1),
                    value.get("round_day", 0),
                    value.get("round_order", []),
                    value.get("done", False),
                ),
            )
        self._store._commit()

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None

    def values(self):
        with self._store._cur() as cur:
            cur.execute("SELECT * FROM duty_history ORDER BY duty_date")
            return [dict(r) for r in cur.fetchall()]

    def __bool__(self) -> bool:
        with self._store._cur() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM duty_history")
            row = cur.fetchone()
        return row["cnt"] > 0
