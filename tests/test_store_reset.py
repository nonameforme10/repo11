"""Round transactions tested without opening a database connection."""
from copy import deepcopy
from datetime import date, timedelta
import unittest
from unittest.mock import patch

from data_store import PostgresStore


DAY = date(2026, 10, 5)


class InjectedFailure(RuntimeError):
    pass


class MemoryConnection:
    """Model commit/rollback boundaries, including premature inner commits."""

    def __init__(self, store):
        self.store = store
        self.commits = 0
        self.rollbacks = 0
        self.committed = deepcopy((store.values, store.records))

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
        self.committed = deepcopy((self.store.values, self.store.records))

    def rollback(self):
        self.rollbacks += 1
        self.store.values, self.store.records = deepcopy(self.committed)


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
        if sql.startswith("SELECT * FROM duty_history WHERE duty_date = %s"):
            self.result = deepcopy(self.store.records.get(params[0]))
        elif sql.startswith("INSERT INTO duty_history"):
            day, member_id, name, username, round_number, round_day, order, done = params
            self.store.records[day] = {
                "duty_date": date.fromisoformat(day),
                "member_id": member_id,
                "name": name,
                "username": username,
                "round_number": round_number,
                "round_day": round_day,
                "round_order": list(order),
                "done": done,
            }
            if self.store.fail_at == "history":
                raise InjectedFailure("after history write")
        elif sql == "UPDATE duty_history SET done = TRUE WHERE duty_date = %s":
            record = self.store.records.get(params[0])
            if record is not None:
                record["done"] = True
            if self.store.fail_at == "done_history":
                raise InjectedFailure("after history completion write")
        else:
            raise AssertionError(f"Unexpected SQL: {sql}")

    def fetchone(self):
        return self.result


class MemoryStore(PostgresStore):
    """Use the production rotation methods with a transactional memory backend."""

    def __init__(self, last_day=DAY, position=2, fail_at=None, done=True):
        self.people = [
            {"id": "a", "name": "Alice", "username": "@alice"},
            {"id": "b", "name": "Bob", "username": "@bob"},
            {"id": "c", "name": "Carol", "username": "@carol"},
        ]
        person = self.people[position - 1]
        self.values = {
            "round_number": "7",
            "round_order": "a,b,c",
            "round_position": str(position),
            "last_member_id": person["id"],
            "last_assigned_date": last_day.isoformat(),
            "last_announced_date": last_day.isoformat(),
            "today_duty_id": person["id"],
            "today_duty_date": last_day.isoformat(),
            "today_duty_done": "true" if done else "false",
        }
        self.records = {
            last_day.isoformat(): {
                "duty_date": last_day,
                "member_id": person["id"],
                "name": person["name"],
                "username": person["username"],
                "round_number": 7,
                "round_day": position,
                "round_order": ["a", "b", "c"],
                "done": done,
            },
        }
        self.fail_at = fail_at
        self._conn = MemoryConnection(self)

    @property
    def names(self):
        return deepcopy(self.people)

    def _get_state_key(self, key, default=None):
        return self.values.get(key, default)

    def _set_state_key(self, key, value):
        if value is None:
            self.values.pop(key, None)
        else:
            self.values[key] = str(value)
        if self.fail_at == "round" and key == "round_number":
            raise InjectedFailure("after round write")
        if self.fail_at == "clear" and key == "today_duty_date" and value is None:
            raise InjectedFailure("after clearing today's assignment")
        if self.fail_at == "assignment" and key == "today_duty_id" and value is not None:
            raise InjectedFailure("after assigning today's member")
        if self.fail_at == "done_state" and key == "today_duty_done" and value == "true":
            raise InjectedFailure("after state completion write")

    def _cur(self):
        return MemoryCursor(self)

    def member_snapshot(self, member_id):
        person = next(person for person in self.people if person["id"] == member_id)
        return {"member_id": member_id, "name": person["name"], "username": person["username"]}


class RoundTransactionTests(unittest.TestCase):
    def assert_assignment_matches(self, store, day, round_number, round_day, member_id):
        record = store.records[day.isoformat()]
        self.assertEqual(store.values["round_number"], str(round_number))
        self.assertEqual(store.values["round_position"], str(round_day))
        self.assertEqual(store.values["today_duty_id"], member_id)
        self.assertEqual(store.values["last_member_id"], member_id)
        self.assertEqual(store.values["today_duty_date"], day.isoformat())
        self.assertEqual(store.values["last_assigned_date"], day.isoformat())
        self.assertEqual(store.values["today_duty_done"], "false")
        self.assertEqual(record["round_number"], round_number)
        self.assertEqual(record["round_day"], round_day)
        self.assertEqual(record["member_id"], member_id)
        self.assertEqual(record["round_order"], store.values["round_order"].split(","))
        self.assertFalse(record["done"])

    @patch("data_store.random.shuffle", side_effect=lambda order: order.reverse())
    def test_manual_reset_commits_new_round_and_today_together(self, shuffle):
        store = MemoryStore()
        store.start_new_round_today(DAY)

        self.assert_assignment_matches(store, DAY, round_number=8, round_day=1, member_id="c")
        self.assertNotIn("last_announced_date", store.values)
        self.assertEqual(store._conn.commits, 1)
        self.assertEqual(store._conn.rollbacks, 0)

    @patch("data_store.random.shuffle", side_effect=lambda order: order.reverse())
    def test_manual_reset_rolls_back_failures_at_each_write_stage(self, shuffle):
        for stage in ("round", "clear", "history", "assignment"):
            with self.subTest(stage=stage):
                store = MemoryStore(fail_at=stage)
                before = deepcopy((store.values, store.records))
                with self.assertRaises(InjectedFailure):
                    store.start_new_round_today(DAY)
                self.assertEqual((store.values, store.records), before)
                self.assertEqual(store._conn.commits, 0)
                self.assertEqual(store._conn.rollbacks, 1)

    @patch("data_store.random.shuffle", side_effect=lambda order: None)
    def test_automatic_round_transition_preserves_previous_day(self, shuffle):
        yesterday = DAY - timedelta(days=1)
        store = MemoryStore(last_day=yesterday, position=3)
        previous_record = deepcopy(store.records[yesterday.isoformat()])
        store.ensure_assignment_through(DAY)

        self.assert_assignment_matches(store, DAY, round_number=8, round_day=1, member_id="a")
        self.assertEqual(store.records[yesterday.isoformat()], previous_record)
        self.assertEqual(store._conn.commits, 1)

    @patch("data_store.random.shuffle", side_effect=lambda order: None)
    def test_automatic_transition_failure_preserves_previous_round(self, shuffle):
        for stage in ("round", "history", "assignment"):
            with self.subTest(stage=stage):
                store = MemoryStore(last_day=DAY - timedelta(days=1), position=3, fail_at=stage)
                before = deepcopy((store.values, store.records))
                with self.assertRaises(InjectedFailure):
                    store.ensure_assignment_through(DAY)
                self.assertEqual((store.values, store.records), before)
                self.assertNotIn(DAY.isoformat(), store.records)
                self.assertEqual(store._conn.commits, 0)
                self.assertEqual(store._conn.rollbacks, 1)

    def test_daily_assignment_within_round_does_not_increment_round(self):
        store = MemoryStore(last_day=DAY - timedelta(days=1))
        store.ensure_assignment_through(DAY)

        self.assert_assignment_matches(store, DAY, round_number=7, round_day=3, member_id="c")
        self.assertEqual(store._conn.commits, 1)

    def test_pending_duty_repeats_until_completed_then_advances(self):
        yesterday = DAY - timedelta(days=1)
        store = MemoryStore(last_day=yesterday, done=False)
        previous_record = deepcopy(store.records[yesterday.isoformat()])

        store.ensure_assignment_through(DAY)
        self.assert_assignment_matches(store, DAY, round_number=7, round_day=2, member_id="b")
        self.assertEqual(store.records[yesterday.isoformat()], previous_record)
        self.assertEqual(store._conn.commits, 1)

        self.assertTrue(store.mark_today_done(DAY))
        self.assertEqual(store.values["today_duty_done"], "true")
        self.assertTrue(store.records[DAY.isoformat()]["done"])
        self.assertEqual(store.records[yesterday.isoformat()], previous_record)
        self.assertFalse(store.records[yesterday.isoformat()]["done"])

        tomorrow = DAY + timedelta(days=1)
        store.ensure_assignment_through(tomorrow)
        self.assert_assignment_matches(store, tomorrow, round_number=7, round_day=3, member_id="c")
        self.assertTrue(store.records[DAY.isoformat()]["done"])
        self.assertFalse(store.records[yesterday.isoformat()]["done"])
        self.assertEqual(store._conn.commits, 3)

    def test_pending_duty_repeats_across_days_missed_while_bot_was_offline(self):
        last_day = DAY - timedelta(days=4)
        store = MemoryStore(last_day=last_day, done=False)
        previous_record = deepcopy(store.records[last_day.isoformat()])

        store.ensure_assignment_through(DAY)

        self.assert_assignment_matches(store, DAY, round_number=7, round_day=2, member_id="b")
        for offset in range(1, 5):
            day = last_day + timedelta(days=offset)
            record = store.records[day.isoformat()]
            self.assertEqual(record["member_id"], "b")
            self.assertEqual(record["round_number"], 7)
            self.assertEqual(record["round_day"], 2)
            self.assertEqual(record["round_order"], ["a", "b", "c"])
            self.assertFalse(record["done"])
        self.assertEqual(store.records[last_day.isoformat()], previous_record)
        self.assertEqual(store._conn.commits, 4)
        self.assertEqual(store._conn.rollbacks, 0)

        # A restart reads the persisted latest assignment and continues the same duty.
        restarted = MemoryStore()
        restarted.values, restarted.records = deepcopy(store._conn.committed)
        restarted._conn = MemoryConnection(restarted)
        tomorrow = DAY + timedelta(days=1)
        restarted.ensure_assignment_through(tomorrow)
        self.assert_assignment_matches(restarted, tomorrow, round_number=7, round_day=2, member_id="b")
        self.assertEqual(restarted._conn.commits, 1)

    def test_pending_assignment_is_idempotent_on_same_date(self):
        store = MemoryStore(last_day=DAY - timedelta(days=1), done=False)
        store.ensure_assignment_through(DAY)
        before = deepcopy((store.values, store.records))
        commits = store._conn.commits

        store.ensure_assignment_through(DAY)
        store.ensure_assignment_through(DAY)

        self.assertEqual((store.values, store.records), before)
        self.assertEqual(store._conn.commits, commits)

    @patch("data_store.random.shuffle", side_effect=lambda order: None)
    def test_pending_last_member_does_not_start_new_round_until_completed(self, shuffle):
        store = MemoryStore(last_day=DAY - timedelta(days=1), position=3, done=False)

        store.ensure_assignment_through(DAY)
        self.assert_assignment_matches(store, DAY, round_number=7, round_day=3, member_id="c")
        shuffle.assert_not_called()

        self.assertTrue(store.mark_today_done(DAY))
        tomorrow = DAY + timedelta(days=1)
        store.ensure_assignment_through(tomorrow)

        self.assert_assignment_matches(store, tomorrow, round_number=8, round_day=1, member_id="a")
        shuffle.assert_called_once()

    @patch("data_store.random.shuffle", side_effect=lambda order: order.reverse())
    def test_manual_reset_replaces_pending_duty(self, shuffle):
        store = MemoryStore(done=False)

        store.start_new_round_today(DAY)

        self.assert_assignment_matches(store, DAY, round_number=8, round_day=1, member_id="c")
        self.assertNotIn("last_announced_date", store.values)
        self.assertEqual(store._conn.commits, 1)

    def test_deleted_pending_member_does_not_block_next_member(self):
        yesterday = DAY - timedelta(days=1)
        store = MemoryStore(last_day=yesterday, done=False)
        previous_record = deepcopy(store.records[yesterday.isoformat()])
        store.people = [person for person in store.people if person["id"] != "b"]
        store.values["round_order"] = "a,c"
        store.values["round_position"] = "1"
        store._conn = MemoryConnection(store)

        store.ensure_assignment_through(DAY)

        self.assert_assignment_matches(store, DAY, round_number=7, round_day=2, member_id="c")
        self.assertEqual(store.records[yesterday.isoformat()], previous_record)
        self.assertEqual(store._conn.commits, 1)

    def test_pending_carryover_rolls_back_history_and_assignment_failures(self):
        for stage in ("history", "assignment"):
            with self.subTest(stage=stage):
                store = MemoryStore(last_day=DAY - timedelta(days=1), fail_at=stage, done=False)
                before = deepcopy((store.values, store.records))

                with self.assertRaises(InjectedFailure):
                    store.ensure_assignment_through(DAY)

                self.assertEqual((store.values, store.records), before)
                self.assertNotIn(DAY.isoformat(), store.records)
                self.assertEqual(store._conn.commits, 0)
                self.assertEqual(store._conn.rollbacks, 1)

    def test_completion_commits_state_and_history_together(self):
        store = MemoryStore(done=False)

        self.assertTrue(store.mark_today_done(DAY))

        self.assertEqual(store.values["today_duty_done"], "true")
        self.assertTrue(store.records[DAY.isoformat()]["done"])
        self.assertEqual(store._conn.commits, 1)
        self.assertEqual(store._conn.rollbacks, 0)

    def test_completion_rolls_back_state_and_history_failures(self):
        for stage in ("done_state", "done_history"):
            with self.subTest(stage=stage):
                store = MemoryStore(fail_at=stage, done=False)
                before = deepcopy((store.values, store.records))

                with self.assertRaises(InjectedFailure):
                    store.mark_today_done(DAY)

                self.assertEqual((store.values, store.records), before)
                self.assertEqual(store._conn.commits, 0)
                self.assertEqual(store._conn.rollbacks, 1)

    def test_completion_rejects_wrong_date_without_changing_pending_duty(self):
        store = MemoryStore(done=False)
        before = deepcopy((store.values, store.records))

        self.assertFalse(store.mark_today_done(DAY + timedelta(days=1)))

        self.assertEqual((store.values, store.records), before)

    def test_existing_assignment_restores_its_round_and_done_state(self):
        store = MemoryStore()
        existing = deepcopy(store.records[DAY.isoformat()])
        store.values["round_number"] = "6"
        store.values["today_duty_done"] = "false"
        store._conn = MemoryConnection(store)
        store._assign_day(DAY)

        self.assertEqual(store.values["round_number"], "7")
        self.assertEqual(store.values["round_position"], "2")
        self.assertEqual(store.values["today_duty_done"], "true")
        self.assertEqual(store.records[DAY.isoformat()], existing)
        self.assertEqual(store._conn.commits, 1)

    def test_empty_roster_assignment_also_commits_once(self):
        store = MemoryStore(last_day=DAY - timedelta(days=1))
        store.people = []
        store.values["round_order"] = ""
        store.values["round_position"] = "0"
        store._conn = MemoryConnection(store)
        store._assign_day(DAY)

        self.assertNotIn("today_duty_id", store.values)
        self.assertEqual(store.values["today_duty_date"], DAY.isoformat())
        self.assertIsNone(store.records[DAY.isoformat()]["member_id"])
        self.assertEqual(store.records[DAY.isoformat()]["round_number"], 7)
        self.assertEqual(store._conn.commits, 1)


if __name__ == "__main__":
    unittest.main()
