"""Group subscription state and backup imports without database connections."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import runpy
import unittest
from unittest.mock import MagicMock, patch

from data_store import PostgresStore
from test_store_reset import DAY, MemoryStore


GROUPS = {
    "-100123456789": {
        "title": "O'quvchilar 💬",
        "last_announced_date": "2026-10-06",
        "last_message_id": 501,
        "last_announced_round_number": 7,
        "last_announced_member_id": "b",
    },
    "-100987654321": {
        "title": "Ikkinchi guruh",
        "last_announced_date": None,
        "last_message_id": None,
        "last_announced_round_number": None,
        "last_announced_member_id": None,
    },
}


class StateMemoryStore(PostgresStore):
    """Exercise production state proxy methods while replacing only persistence."""
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.writes = []
        self.commits = 0

    def _get_state_key(self, key, default=None):
        return self.values.get(key, default)

    def _set_state_key(self, key, value):
        self.writes.append((key, value))
        if value is None:
            self.values.pop(key, None)
        else:
            self.values[key] = value

    def _commit(self):
        self.commits += 1


class GroupStateTests(unittest.TestCase):
    def test_groups_and_per_group_delivery_fields_survive_roundtrip_and_restart(self):
        store = StateMemoryStore()
        store.state["group_subscriptions"] = deepcopy(GROUPS)

        self.assertEqual(json.loads(store.values["group_subscriptions"]), GROUPS)
        self.assertEqual(store.state["group_subscriptions"], GROUPS)
        self.assertIn("O'quvchilar 💬", store.values["group_subscriptions"])
        self.assertEqual(store.commits, 1)
        restarted = StateMemoryStore(store.values)
        self.assertEqual(restarted.state["group_subscriptions"], GROUPS)
        self.assertIsInstance(restarted.state["group_subscriptions"]["-100123456789"]
                              ["last_announced_round_number"], int)

    def test_malformed_or_non_object_json_logs_and_returns_callers_default(self):
        for raw in ("invalid json", "{broken", "[]", "null", "true", "123", '"text"'):
            with self.subTest(raw=raw):
                store = StateMemoryStore({"group_subscriptions": raw})
                default = {}
                with self.assertLogs("data_store", level="WARNING"):
                    self.assertIs(store.state.get("group_subscriptions", default), default)
                self.assertEqual(store.values["group_subscriptions"], raw)
                self.assertEqual(store.writes, [])
                self.assertEqual(store.commits, 0)

    def test_missing_groups_return_default_without_warning_or_write(self):
        store = StateMemoryStore()
        default = {}
        with self.assertNoLogs("data_store", level="WARNING"):
            self.assertIs(store.state.get("group_subscriptions", default), default)
        self.assertEqual(store.writes, [])

    def test_non_dictionary_writes_fail_before_storage_changes(self):
        for value in ([], [GROUPS], "{}", True, 5):
            with self.subTest(value=value):
                store = StateMemoryStore({"group_subscriptions": json.dumps(GROUPS)})
                before = dict(store.values)
                with self.assertRaisesRegex(ValueError, "must be a dictionary"):
                    store.state["group_subscriptions"] = value
                self.assertEqual(store.values, before)
                self.assertEqual(store.writes, [])
                self.assertEqual(store.commits, 0)

    def test_non_json_values_fail_before_storage_changes(self):
        for invalid in ({"-1": {"title": object()}}, {"-1": {"last_message_id": float("nan")}}):
            with self.subTest(invalid=invalid):
                store = StateMemoryStore()
                with self.assertRaises((TypeError, ValueError)):
                    store.state["group_subscriptions"] = invalid
                self.assertEqual(store.writes, [])
                self.assertEqual(store.commits, 0)

    def test_empty_dictionary_and_explicit_clear_are_supported(self):
        store = StateMemoryStore()
        store.state["group_subscriptions"] = {}
        self.assertEqual(store.values["group_subscriptions"], "{}")
        self.assertEqual(store.state.get("group_subscriptions"), {})
        store.state["group_subscriptions"] = None
        self.assertNotIn("group_subscriptions", store.values)
        self.assertIsNone(store.state.get("group_subscriptions"))
        self.assertEqual(store.commits, 2)

    def test_read_data_is_detached_and_changes_require_explicit_save(self):
        store = StateMemoryStore({"group_subscriptions": json.dumps(GROUPS)})
        groups = store.state["group_subscriptions"]
        groups["-100123456789"]["last_message_id"] = 502
        self.assertEqual(store.state["group_subscriptions"], GROUPS)

        store.state["group_subscriptions"] = groups

        self.assertEqual(store.state["group_subscriptions"]["-100123456789"]["last_message_id"], 502)
        self.assertEqual(store.state["group_subscriptions"]["-100987654321"], GROUPS["-100987654321"])

    def test_other_state_types_keep_their_existing_serialization(self):
        store = StateMemoryStore()
        store.state["round_order"] = ["a", "b"]
        store.state["today_duty_done"] = True
        store.state["round_number"] = 7
        store.state["chat_id"] = -100123456789

        self.assertEqual(store.state["round_order"], ["a", "b"])
        self.assertIs(store.state["today_duty_done"], True)
        self.assertEqual(store.state["round_number"], 7)
        self.assertEqual(store.state["chat_id"], -100123456789)

    @patch("data_store.random.shuffle", side_effect=lambda order: order.reverse())
    def test_reset_preserves_group_records_but_invalidates_their_announcement_identity(self, shuffle):
        store = MemoryStore()
        store.state["group_subscriptions"] = deepcopy(GROUPS)
        before = store.values["group_subscriptions"]
        old_round = store.state["round_number"]

        store.start_new_round_today(DAY)

        self.assertEqual(store.values["group_subscriptions"], before)
        self.assertEqual(store.state["group_subscriptions"], GROUPS)
        self.assertEqual(store.state["round_number"], old_round + 1)
        self.assertIsNone(store.state.get("last_announced_date"))
        self.assertNotEqual(store.state["round_number"],
                            GROUPS["-100123456789"]["last_announced_round_number"])


class GroupBackupImportTests(unittest.TestCase):
    def run_import(self, state):
        inputs = {
            "data/names.json": [{"id": "a", "name": "Alice", "username": "@alice_one"}],
            "members.json": [{"name": "Alice", "username": "@alice_one"}],
            "data/state.json": state,
            "state.json": {},
            "data/history.json": {},
            "history.json": {},
        }
        connection = MagicMock()
        cursor = connection.cursor.return_value

        def open_json(path, *args, **kwargs):
            return io.StringIO(json.dumps(inputs.get(path.as_posix()), ensure_ascii=False))

        importer = Path(__file__).resolve().parents[1] / "migrate_to_pg.py"
        with (
            patch.dict(os.environ, {"DATABASE_URL": "postgresql://offline-test"}, clear=True),
            patch("dotenv.load_dotenv"),
            patch("psycopg2.connect", return_value=connection),
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "open", open_json),
            redirect_stdout(io.StringIO()),
        ):
            runpy.run_path(str(importer))
        return cursor

    def group_state_writes(self, cursor):
        return [call.args[1][1] for call in cursor.execute.call_args_list
                if call.args[0].startswith("INSERT INTO bot_state")
                and call.args[1][0] == "group_subscriptions"]

    def test_new_format_backup_restores_all_groups_with_delivery_markers(self):
        store = StateMemoryStore()
        store.state["group_subscriptions"] = deepcopy(GROUPS)
        backup_state = json.loads(json.dumps({
            "round_order": ["a"], "round_position": 1, "round_number": 7,
            "group_subscriptions": store.state["group_subscriptions"],
        }))

        cursor = self.run_import(backup_state)

        writes = self.group_state_writes(cursor)
        self.assertEqual(len(writes), 1)
        self.assertEqual(json.loads(writes[0]), GROUPS)
        self.assertEqual(writes[0], store.values["group_subscriptions"])

    def test_legacy_format_backup_also_restores_subscription_dictionary(self):
        cursor = self.run_import({"order": [0], "pos": 0, "group_subscriptions": deepcopy(GROUPS)})
        writes = self.group_state_writes(cursor)
        self.assertEqual(len(writes), 1)
        self.assertEqual(json.loads(writes[0]), GROUPS)

    def test_older_backup_without_group_key_leaves_existing_subscriptions_untouched(self):
        cursor = self.run_import({"round_order": ["a"]})
        self.assertEqual(self.group_state_writes(cursor), [])
        group_deletes = [call for call in cursor.execute.call_args_list
                         if call.args[0].startswith("DELETE FROM bot_state")
                         and call.args[1][0] == "group_subscriptions"]
        self.assertEqual(group_deletes, [])

    def test_import_rejects_non_dictionary_groups(self):
        with self.assertRaisesRegex(ValueError, "must be a dictionary"):
            self.run_import({"round_order": ["a"], "group_subscriptions": ["-100123456789"]})


if __name__ == "__main__":
    unittest.main()
