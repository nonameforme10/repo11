"""Round transactions tested without opening a database connection."""
from copy import deepcopy
from datetime import date, timedelta
import unittest
from unittest.mock import patch

from data_store import DUTY_DAYS, PostgresStore


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

    def execute(self, sql, params=None):
        sql = " ".join(sql.split())
        if sql.startswith("SELECT * FROM duty_history WHERE duty_date = %s"):
            self.result = deepcopy(self.store.records.get(params[0]))
        elif sql.startswith("SELECT duty_date, member_id, round_number, round_day, round_order, done "):
            self.result = deepcopy(max(self.store.records.values(), key=lambda row: row["duty_date"], default=None))
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

    def __init__(self, last_day=DAY, position=2, fail_at=None, done=True, started_day=None):
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
            # Existing fixture assignments normally represent the second day of a turn.
            "duty_started_date": (started_day or last_day - timedelta(days=DUTY_DAYS - 1)).isoformat(),
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
        if self.fail_at == "start" and key == "duty_started_date" and value is not None:
            raise InjectedFailure("after duty start write")

    def _cur(self):
        return MemoryCursor(self)

    def member_snapshot(self, member_id):
        person = next(person for person in self.people if person["id"] == member_id)
        return {"member_id": member_id, "name": person["name"], "username": person["username"]}


class RoundTransactionTests(unittest.TestCase):
    def assert_assignment_matches(self, store, day, round_number, round_day, member_id,
                                  done=False, started_day=None):
        record = store.records[day.isoformat()]
        self.assertEqual(store.values["round_number"], str(round_number))
        self.assertEqual(store.values["round_position"], str(round_day))
        self.assertEqual(store.values["today_duty_id"], member_id)
        self.assertEqual(store.values["last_member_id"], member_id)
        self.assertEqual(store.values["today_duty_date"], day.isoformat())
        self.assertEqual(store.values["last_assigned_date"], day.isoformat())
        self.assertEqual(store.values["today_duty_done"], "true" if done else "false")
        if started_day is not None:
            self.assertEqual(store.values["duty_started_date"], started_day.isoformat())
            self.assertEqual(store.duty_started_on(), started_day)
        self.assertEqual(record["round_number"], round_number)
        self.assertEqual(record["round_day"], round_day)
        self.assertEqual(record["member_id"], member_id)
        self.assertEqual(record["round_order"], store.values["round_order"].split(","))
        self.assertEqual(record["done"], done)

    @patch("data_store.random.shuffle", side_effect=lambda order: order.reverse())
    def test_manual_reset_commits_new_round_and_today_together(self, shuffle):
        store = MemoryStore()
        store.start_new_round_today(DAY)

        self.assert_assignment_matches(store, DAY, round_number=8, round_day=1, member_id="c", started_day=DAY)
        self.assertNotIn("last_announced_date", store.values)
        self.assertEqual(store._conn.commits, 1)
        self.assertEqual(store._conn.rollbacks, 0)

    @patch("data_store.random.shuffle", side_effect=lambda order: order.reverse())
    def test_manual_reset_rolls_back_failures_at_each_write_stage(self, shuffle):
        for stage in ("round", "clear", "history", "assignment", "start"):
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
        for stage in ("round", "history", "assignment", "start"):
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
        self.assertEqual(store.duty_started_on(), yesterday - timedelta(days=DUTY_DAYS - 1))
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
        self.assertEqual(store.duty_started_on(), DAY)
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
        self.assertNotIn("duty_started_date", store.values)
        self.assertEqual(store.values["today_duty_date"], DAY.isoformat())
        self.assertIsNone(store.records[DAY.isoformat()]["member_id"])
        self.assertEqual(store.records[DAY.isoformat()]["round_number"], 7)
        self.assertEqual(store._conn.commits, 1)

    def test_completed_turns_start_on_october_first_third_and_fifth(self):
        first = date(2026, 10, 1)
        store = MemoryStore(last_day=first, position=1, done=False, started_day=first)

        for offset, member_id in ((0, "a"), (2, "b"), (4, "c")):
            start = first + timedelta(days=offset)
            position = offset // DUTY_DAYS + 1
            store.ensure_assignment_through(start)
            self.assert_assignment_matches(store, start, 7, position, member_id, started_day=start)
            self.assertTrue(store.mark_today_done(start))

            second_day = start + timedelta(days=1)
            store.ensure_assignment_through(second_day)
            self.assert_assignment_matches(store, second_day, 7, position, member_id,
                                           done=True, started_day=start)
            self.assertTrue(store.records[start.isoformat()]["done"])

        self.assertEqual([store.records[(first + timedelta(days=offset)).isoformat()]["member_id"]
                          for offset in range(6)], ["a", "a", "b", "b", "c", "c"])

    def test_completed_first_day_survives_restart_and_second_day(self):
        store = MemoryStore(last_day=DAY, done=False, started_day=DAY)
        store.mark_today_done(DAY)
        completed_record = deepcopy(store.records[DAY.isoformat()])
        restarted = MemoryStore()
        restarted.values, restarted.records = deepcopy(store._conn.committed)
        restarted._conn = MemoryConnection(restarted)

        second_day = DAY + timedelta(days=1)
        restarted.ensure_assignment_through(second_day)
        self.assert_assignment_matches(restarted, second_day, 7, 2, "b", done=True, started_day=DAY)
        self.assertEqual(restarted.records[DAY.isoformat()], completed_record)
        self.assertEqual(restarted._conn.commits, 1)

        third_day = DAY + timedelta(days=2)
        restarted.ensure_assignment_through(third_day)
        self.assert_assignment_matches(restarted, third_day, 7, 3, "c", started_day=third_day)
        self.assertTrue(restarted.records[second_day.isoformat()]["done"])

    def test_unfinished_turn_holds_original_start_after_two_days_until_completed(self):
        store = MemoryStore(last_day=DAY, done=False, started_day=DAY)
        original = deepcopy(store.records[DAY.isoformat()])
        overdue = DAY + timedelta(days=4)
        store.ensure_assignment_through(overdue)

        self.assert_assignment_matches(store, overdue, 7, 2, "b", started_day=DAY)
        self.assertEqual(store.records[DAY.isoformat()], original)
        self.assertEqual(store._conn.commits, 4)
        store.mark_today_done(overdue)
        next_day = overdue + timedelta(days=1)
        store.ensure_assignment_through(next_day)
        self.assert_assignment_matches(store, next_day, 7, 3, "c", started_day=next_day)

    def test_offline_catchup_keeps_completed_second_day_then_holds_next_unfinished_turn(self):
        first = date(2026, 10, 1)
        store = MemoryStore(last_day=first, position=1, done=True, started_day=first)
        original = deepcopy(store.records[first.isoformat()])
        target = first + timedelta(days=5)
        store.ensure_assignment_through(target)

        second_day = first + timedelta(days=1)
        self.assertEqual(store.records[second_day.isoformat()]["member_id"], "a")
        self.assertTrue(store.records[second_day.isoformat()]["done"])
        next_start = first + timedelta(days=2)
        for offset in range(2, 6):
            record = store.records[(first + timedelta(days=offset)).isoformat()]
            self.assertEqual(record["member_id"], "b")
            self.assertEqual(record["round_day"], 2)
            self.assertFalse(record["done"])
        self.assert_assignment_matches(store, target, 7, 2, "b", started_day=next_start)
        self.assertEqual(store.records[first.isoformat()], original)
        self.assertEqual(store._conn.commits, 5)

    @patch("data_store.random.shuffle", side_effect=lambda order: None)
    def test_completed_last_member_waits_for_second_day_before_new_round(self, shuffle):
        store = MemoryStore(last_day=DAY, position=3, done=True, started_day=DAY)
        second_day = DAY + timedelta(days=1)
        store.ensure_assignment_through(second_day)

        self.assert_assignment_matches(store, second_day, 7, 3, "c", done=True, started_day=DAY)
        shuffle.assert_not_called()
        next_start = DAY + timedelta(days=2)
        store.ensure_assignment_through(next_start)
        self.assert_assignment_matches(store, next_start, 8, 1, "a", started_day=next_start)
        shuffle.assert_called_once()

    def test_completed_second_day_rolls_back_history_and_start_write_failures(self):
        for stage in ("history", "assignment", "start", "done_state"):
            with self.subTest(stage=stage):
                store = MemoryStore(last_day=DAY, done=True, started_day=DAY, fail_at=stage)
                before = deepcopy((store.values, store.records))
                with self.assertRaises(InjectedFailure):
                    store.ensure_assignment_through(DAY + timedelta(days=1))
                self.assertEqual((store.values, store.records), before)
                self.assertEqual(store._conn.commits, 0)
                self.assertEqual(store._conn.rollbacks, 1)

    def test_legacy_current_assignment_adopts_start_once_without_rewriting_history(self):
        store = MemoryStore(done=True)
        store.values.pop("duty_started_date")
        original_records = deepcopy(store.records)
        store._conn = MemoryConnection(store)

        store.ensure_assignment_through(DAY)
        self.assertEqual(store.duty_started_on(), DAY)
        self.assertEqual(store.values["duty_started_date"], DAY.isoformat())
        self.assertEqual(store.records, original_records)
        self.assertEqual(store.values["round_position"], "2")
        self.assertEqual(store._conn.commits, 1)
        store.ensure_assignment_through(DAY)
        self.assertEqual(store._conn.commits, 1)

        second_day = DAY + timedelta(days=1)
        store.ensure_assignment_through(second_day)
        self.assert_assignment_matches(store, second_day, 7, 2, "b", done=True, started_day=DAY)

    def test_legacy_start_adoption_rolls_back_if_state_write_fails(self):
        store = MemoryStore(fail_at="start")
        store.values.pop("duty_started_date")
        store._conn = MemoryConnection(store)
        before = deepcopy((store.values, store.records))

        with self.assertRaises(InjectedFailure):
            store.ensure_assignment_through(DAY)

        self.assertEqual((store.values, store.records), before)
        self.assertEqual(store._conn.commits, 0)
        self.assertEqual(store._conn.rollbacks, 1)

    def test_invalid_or_future_start_falls_back_to_stored_assignment_date(self):
        for invalid in ("invalid", "", (DAY + timedelta(days=1)).isoformat()):
            with self.subTest(start=invalid):
                store = MemoryStore()
                store.values["duty_started_date"] = invalid
                store._conn = MemoryConnection(store)
                self.assertEqual(store.duty_started_on(), DAY)
                store.ensure_assignment_through(DAY)
                self.assertEqual(store.values["duty_started_date"], DAY.isoformat())
                self.assertEqual(store._conn.commits, 1)

        store = MemoryStore()
        store.values.pop("today_duty_id")
        self.assertIsNone(store.duty_started_on())
        store.values["today_duty_id"] = "b"
        store.values["today_duty_date"] = "invalid"
        store.values["duty_started_date"] = "invalid"
        self.assertIsNone(store.duty_started_on())

    def test_restoring_existing_second_day_preserves_start_for_same_member_and_round(self):
        store = MemoryStore(last_day=DAY, done=True, started_day=DAY)
        second_day = DAY + timedelta(days=1)
        existing = deepcopy(store.records[DAY.isoformat()])
        existing["duty_date"] = second_day
        store.records[second_day.isoformat()] = existing
        store._conn = MemoryConnection(store)

        store._assign_day(second_day)

        self.assert_assignment_matches(store, second_day, 7, 2, "b", done=True, started_day=DAY)
        self.assertEqual(store.records[second_day.isoformat()], existing)
        self.assertEqual(store._conn.commits, 1)

    def test_reconciliation_of_same_turn_keeps_start_and_completion(self):
        store = MemoryStore(last_day=DAY, done=True, started_day=DAY)
        second_day = DAY + timedelta(days=1)
        existing = deepcopy(store.records[DAY.isoformat()])
        existing["duty_date"] = second_day
        store.records[second_day.isoformat()] = existing
        store._conn = MemoryConnection(store)
        original_records = deepcopy(store.records)

        store._reconcile_history_state()

        self.assert_assignment_matches(store, second_day, 7, 2, "b", done=True, started_day=DAY)
        self.assertEqual(store.records, original_records)
        self.assertEqual(store._conn.commits, 1)

    def test_reconciliation_of_another_member_or_round_adopts_latest_date(self):
        for member_id, round_number, position in (("c", 7, 3), ("b", 8, 2)):
            with self.subTest(member_id=member_id, round_number=round_number):
                store = MemoryStore(last_day=DAY, done=True, started_day=DAY)
                latest = DAY + timedelta(days=1)
                person = next(person for person in store.people if person["id"] == member_id)
                store.records[latest.isoformat()] = {
                    "duty_date": latest, "member_id": member_id, "name": person["name"],
                    "username": person["username"], "round_number": round_number,
                    "round_day": position, "round_order": ["a", "b", "c"], "done": False,
                }
                store._conn = MemoryConnection(store)

                store._reconcile_history_state()

                self.assert_assignment_matches(store, latest, round_number, position, member_id,
                                               started_day=latest)


if __name__ == "__main__":
    unittest.main()
