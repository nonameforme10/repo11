"""Member edits tested with the real store API and an offline transaction backend."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from data_store import PostgresStore


DAY = date(2026, 10, 6)


class InjectedFailure(RuntimeError):
    pass


class MemoryConnection:
    def __init__(self, store):
        self.store = store
        self.commits = 0
        self.rollbacks = 0
        self.committed = self.snapshot()

    def snapshot(self):
        return deepcopy((self.store.people, self.store.values, self.store.records))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False

    def commit(self):
        self.commits += 1
        self.committed = self.snapshot()

    def rollback(self):
        self.rollbacks += 1
        self.store.people, self.store.values, self.store.records = deepcopy(self.committed)


class MemoryCursor:
    def __init__(self, store):
        self.store = store
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, sql, params):
        sql = " ".join(sql.split())
        self.store.queries.append((sql, params))
        if sql == "SELECT id, name, username FROM members WHERE id = %s FOR UPDATE":
            self.result = next((deepcopy(person) for person in self.store.people
                                if person["id"] == params[0]), None)
        elif sql == "SELECT id, name FROM members WHERE id <> %s":
            self.result = [deepcopy(person) for person in self.store.people
                           if person["id"] != params[0]]
        elif sql == "UPDATE members SET name = %s WHERE id = %s RETURNING id, name, username":
            name, member_id = params
            person = next(person for person in self.store.people if person["id"] == member_id)
            person["name"] = name
            self.result = {key: person[key] for key in ("id", "name", "username")}
            if self.store.fail_at == "member":
                raise InjectedFailure("after member write")
        elif sql == "UPDATE duty_history SET name = %s WHERE duty_date = %s AND member_id = %s":
            name, day, member_id = params
            record = self.store.records.get(day)
            if record is not None and record["member_id"] == member_id:
                record["name"] = name
            if self.store.fail_at == "history":
                raise InjectedFailure("after history write")
        else:
            raise AssertionError(f"Unexpected SQL: {sql}")

    def fetchone(self):
        return deepcopy(self.result)

    def fetchall(self):
        return deepcopy(self.result)


class MemoryStore(PostgresStore):
    def __init__(self, fail_at=None):
        self.people = [
            {"id": "a", "name": "Alice", "username": "@alice",
             "created_at": datetime(2026, 9, 1, tzinfo=timezone.utc)},
            {"id": "b", "name": "Bob", "username": "@bob",
             "created_at": datetime(2026, 9, 2, tzinfo=timezone.utc)},
            {"id": "c", "name": "Straße", "username": "",
             "created_at": datetime(2026, 9, 3, tzinfo=timezone.utc)},
        ]
        self.values = {
            "round_order": "b,a,c", "round_position": "2", "round_number": "7",
            "today_duty_id": "a", "today_duty_date": DAY.isoformat(),
            "today_duty_done": "false", "last_member_id": "a",
        }
        self.records = {
            day.isoformat(): {
                "duty_date": day, "member_id": "a", "name": "Alice", "username": "@alice",
                "round_number": 7, "round_day": 2, "round_order": ["b", "a", "c"],
                "done": False,
            }
            for day in (DAY - timedelta(days=1), DAY)
        }
        self.fail_at = fail_at
        self.queries = []
        self._conn = MemoryConnection(self)

    @property
    def names(self):
        return [{key: person[key] for key in ("id", "name", "username")} for person in self.people]

    def _cur(self):
        return MemoryCursor(self)


class RenameMemberTests(unittest.TestCase):
    def setUp(self):
        current_day = patch("data_store.tashkent_today", return_value=DAY)
        current_day.start()
        self.addCleanup(current_day.stop)

    def test_rename_commits_stable_identity_rotation_and_current_snapshot(self):
        store = MemoryStore()
        before = store._conn.snapshot()

        result = store.rename_name("a", "  Alice Updated  ")

        self.assertEqual(result, {"id": "a", "name": "Alice Updated", "username": "@alice"})
        expected_people, expected_values, expected_records = deepcopy(before)
        expected_people[0]["name"] = "Alice Updated"
        expected_records[DAY.isoformat()]["name"] = "Alice Updated"
        self.assertEqual(store._conn.snapshot(), (expected_people, expected_values, expected_records))
        self.assertEqual(store._conn.committed, store._conn.snapshot())
        self.assertEqual(store._conn.commits, 1)
        self.assertEqual(store._conn.rollbacks, 0)

    def test_rename_other_member_keeps_today_history_untouched(self):
        store = MemoryStore()
        before_records = deepcopy(store.records)
        before_values = deepcopy(store.values)

        store.rename_name("b", "Robert")

        self.assertEqual(store.people[1]["name"], "Robert")
        self.assertEqual(store.records, before_records)
        self.assertEqual(store.values, before_values)
        self.assertEqual(store._conn.commits, 1)

    def test_rename_without_today_record_does_not_create_assignment(self):
        store = MemoryStore()
        store.records.pop(DAY.isoformat())
        store._conn = MemoryConnection(store)
        before_records = deepcopy(store.records)

        store.rename_name("a", "Alicia")

        self.assertEqual(store.records, before_records)
        self.assertEqual(store.people[0]["name"], "Alicia")

    def test_same_member_may_keep_its_name_or_change_capitalization(self):
        store = MemoryStore()

        self.assertEqual(store.rename_name("a", "Alice")["name"], "Alice")
        self.assertEqual(store.rename_name("a", "ALICE")["name"], "ALICE")
        self.assertEqual(store._conn.commits, 2)

    def test_duplicate_names_are_case_insensitive_including_unicode(self):
        for name in ("  BOB ", "STRASSE"):
            with self.subTest(name=name):
                store = MemoryStore()
                before = store._conn.snapshot()

                with self.assertRaisesRegex(ValueError, "ro'yxatda bor"):
                    store.rename_name("a", name)

                self.assertEqual(store._conn.snapshot(), before)
                self.assertEqual(store._conn.commits, 0)
                self.assertEqual(store._conn.rollbacks, 1)
                self.assertFalse(any(sql.startswith("UPDATE") for sql, params in store.queries))

    def test_missing_active_member_is_rejected_even_if_it_has_history(self):
        store = MemoryStore()
        store.people = [person for person in store.people if person["id"] != "a"]
        store._conn = MemoryConnection(store)
        before = store._conn.snapshot()

        with self.assertRaisesRegex(ValueError, "topilmadi"):
            store.rename_name("a", "Restored")

        self.assertEqual(store._conn.snapshot(), before)
        self.assertEqual(store._conn.commits, 0)
        self.assertEqual(store._conn.rollbacks, 1)

    def test_invalid_names_rejected_before_database_access_for_add_and_rename(self):
        for name in ("", " \t\n ", "x" * 101):
            for action in ("add", "rename"):
                with self.subTest(name=name, action=action):
                    store = MemoryStore()
                    before = store._conn.snapshot()
                    with self.assertRaises(ValueError):
                        if action == "add":
                            store.add_name(name)
                        else:
                            store.rename_name("a", name)
                    self.assertEqual(store._conn.snapshot(), before)
                    self.assertEqual(store.queries, [])
                    self.assertEqual(store._conn.commits, 0)
                    self.assertEqual(store._conn.rollbacks, 0)

    def test_name_length_limit_is_applied_after_trimming(self):
        store = MemoryStore()

        renamed = store.rename_name("a", "  " + "x" * 100 + "  ")

        self.assertEqual(renamed["name"], "x" * 100)
        self.assertEqual(store._conn.commits, 1)

    def test_member_and_current_history_roll_back_after_either_write_failure(self):
        for stage in ("member", "history"):
            with self.subTest(stage=stage):
                store = MemoryStore(fail_at=stage)
                before = store._conn.snapshot()

                with self.assertRaises(InjectedFailure):
                    store.rename_name("a", "Alice Updated")

                self.assertEqual(store._conn.snapshot(), before)
                self.assertEqual(store._conn.commits, 0)
                self.assertEqual(store._conn.rollbacks, 1)

    def test_name_and_id_are_passed_as_parameters(self):
        store = MemoryStore()
        member_id = "a'; DELETE FROM members; --"
        store.people[0]["id"] = member_id
        store._conn = MemoryConnection(store)
        name = "O'Connor %s; DROP TABLE members; --"

        result = store.rename_name(member_id, name)

        self.assertEqual(result["id"], member_id)
        self.assertEqual(result["name"], name)
        for sql, params in store.queries:
            self.assertNotIn(name, sql)
            self.assertNotIn(member_id, sql)
            self.assertIsInstance(params, tuple)
        self.assertEqual(store.queries[-2][1], (name, member_id))
        self.assertEqual(store.queries[-1][1], (name, DAY.isoformat(), member_id))


if __name__ == "__main__":
    unittest.main()
