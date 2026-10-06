"""Exercise two-day duty rollover through the schedule and completion UI."""

from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Bot, CallbackQuery, Chat, Message, Update, User

from test_bot import bot, GLOBAL_ADMIN_ID, GROUP_ID
from test_store_reset import MemoryStore


OCTOBER_12 = date(2026, 10, 12)
OCTOBER_13 = date(2026, 10, 13)
PENDING_NOTE = "⏳"


class ScheduleStore(MemoryStore):
    """Keep production state/rotation methods while exposing memory snapshots."""

    def __init__(self):
        super().__init__(last_day=OCTOBER_12, position=1, done=False)
        self.values["duty_started_date"] = OCTOBER_12.isoformat()
        self.people = [
            {"id": "a", "name": "Azim", "username": "@azim_one"},
            {"id": "b", "name": "Bahrom", "username": "@bahrom_two"},
            {"id": "c", "name": "Dilshod", "username": "@dilshod_three"},
        ]
        self.records[OCTOBER_12.isoformat()].update(
            name="Azim", username="@azim_one"
        )
        self._conn.commit()

    @property
    def history(self):
        return deepcopy(self.records)

    def member(self, member_id):
        for person in self.people:
            if person["id"] == member_id:
                return deepcopy(person)
        for day in sorted(self.records, reverse=True):
            record = self.records[day]
            if record["member_id"] == member_id:
                return {
                    "id": member_id,
                    "name": record["name"],
                    "username": record["username"],
                }
        return None


class RolloverScheduleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = ScheduleStore()
        self.day = OCTOBER_13
        for active_patch in (
            patch.object(bot, "STORE", self.store),
            patch.object(bot, "today", side_effect=lambda: self.day),
            patch.object(bot, "ADMIN_IDS", {GLOBAL_ADMIN_ID}),
        ):
            active_patch.start()
            self.addCleanup(active_patch.stop)

    def schedule(self):
        return [
            (day, person["name"])
            for day, person in bot.scheduled_members()
        ]

    def test_unfinished_azim_repeats_and_shifts_bahrom_until_completion(self):
        self.store.ensure_assignment_through(OCTOBER_13)

        self.assertEqual(self.schedule(), [
            (OCTOBER_13, "Azim"),
            (date(2026, 10, 14), "Bahrom"),
            (date(2026, 10, 16), "Dilshod"),
        ])
        self.assertIn(PENDING_NOTE, bot.schedule_text())
        self.assertFalse(self.store.records[OCTOBER_12.isoformat()]["done"])

        self.assertTrue(self.store.mark_today_done(OCTOBER_13))
        self.assertNotIn(PENDING_NOTE, bot.schedule_text())
        self.assertTrue(self.store.records[OCTOBER_13.isoformat()]["done"])

        self.day = date(2026, 10, 14)
        self.assertEqual(self.schedule(), [
            (self.day, "Bahrom"),
            (date(2026, 10, 16), "Dilshod"),
        ])
        self.assertIn(PENDING_NOTE, bot.schedule_text())

    def test_schedule_after_several_missed_days_keeps_azim_and_shifts_everyone(self):
        self.day = date(2026, 10, 16)

        self.assertEqual(self.schedule(), [
            (self.day, "Azim"),
            (date(2026, 10, 17), "Bahrom"),
            (date(2026, 10, 19), "Dilshod"),
        ])
        self.assertEqual(len(self.store.records), 5)
        for record in self.store.records.values():
            self.assertEqual(record["member_id"], "a")
            self.assertFalse(record["done"])
        self.assertEqual(self.store.state["round_position"], 1)
        self.assertEqual(self.store.state["round_number"], 7)

        records = deepcopy(self.store.records)
        self.assertIn(PENDING_NOTE, bot.schedule_text())
        self.assertEqual(self.store.records, records)

    async def test_yesterdays_done_button_cannot_complete_rolled_over_duty(self):
        api = MagicMock(spec=Bot)
        api.answer_callback_query = AsyncMock(return_value=True)
        api.edit_message_reply_markup = AsyncMock(return_value=True)
        api.send_message = AsyncMock()
        admin = User(id=GLOBAL_ADMIN_ID, first_name="Admin", is_bot=False)
        message = Message(
            message_id=100,
            date=datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc),
            chat=Chat(id=GROUP_ID, type=Chat.SUPERGROUP),
            text="Azim's October 12 duty",
        )
        message.set_bot(api)
        query = CallbackQuery(
            id="old-done-button", from_user=admin, chat_instance="offline",
            message=message, data=f"done:{OCTOBER_12}:a",
        )
        query.set_bot(api)
        update = Update(update_id=1, callback_query=query)
        context = SimpleNamespace(bot=api)

        with patch.object(
            self.store, "mark_today_done", wraps=self.store.mark_today_done
        ) as mark_done:
            await bot.on_button(update, context)
            mark_done.assert_not_called()

        self.assertEqual(self.store.state["today_duty_date"], OCTOBER_13.isoformat())
        self.assertFalse(self.store.records[OCTOBER_13.isoformat()]["done"])
        self.assertTrue(api.answer_callback_query.await_args.kwargs["show_alert"])
        api.edit_message_reply_markup.assert_not_awaited()
        api.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
