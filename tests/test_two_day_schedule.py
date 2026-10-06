"""Two-day duty ranges and announcement recovery using production rollover."""

from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Bot, Chat
from telegram.error import TelegramError

import test_bot as fixtures
from test_store_reset import MemoryStore


bot = fixtures.bot
START = date(2026, 10, 1)


class TwoDayStore(MemoryStore):
    def __init__(self, start=START, done=False):
        super().__init__(last_day=start, position=1, done=done)
        self.people = [
            {"id": "a", "name": "Alice", "username": "@alice_one"},
            {"id": "b", "name": "Bob", "username": "@bob_two"},
            {"id": "c", "name": "Carol", "username": "@carol_three"},
        ]
        self.values["duty_started_date"] = start.isoformat()
        self.values["chat_id"] = str(fixtures.GROUP_ID)
        self.values.pop("last_announced_date", None)
        self.records[start.isoformat()].update(name="Alice", username="@alice_one")
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
                return {"id": member_id, "name": record["name"], "username": record["username"]}
        return None


class TwoDayScheduleTests(unittest.IsolatedAsyncioTestCase):
    user = staticmethod(fixtures.BotTests.user)
    update = fixtures.BotTests.update

    def setUp(self):
        self.store = TwoDayStore()
        self.day = START
        self.api = MagicMock(spec=Bot)
        self.api.id = 999
        self.api.username = "DutyBot"
        self.api.send_message = AsyncMock(return_value=SimpleNamespace(message_id=501))
        self.api.delete_message = AsyncMock(return_value=True)
        self.app = SimpleNamespace(bot=self.api, bot_data={})
        self.context = SimpleNamespace(bot=self.api, application=self.app, args=[], user_data={})
        for active_patch in (
            patch.object(bot, "STORE", self.store),
            patch.object(bot, "today", side_effect=lambda: self.day),
            patch.object(bot, "ADMIN_IDS", {fixtures.GLOBAL_ADMIN_ID}),
            patch.object(bot, "date_at_eight_or_later", return_value=True),
        ):
            active_patch.start()
            self.addCleanup(active_patch.stop)

    def schedule(self):
        return [(day, person["id"]) for day, person in bot.scheduled_members()]

    def assert_dates_in(self, text, *days):
        for day in days:
            self.assertIn(day.strftime("%d.%m.%Y"), text)

    def test_first_day_lists_two_day_ranges_starting_on_first_third_and_fifth(self):
        self.assertEqual(self.schedule(), [
            (START, "a"), (START + timedelta(days=2), "b"), (START + timedelta(days=4), "c"),
        ])
        text = bot.schedule_text()
        self.assert_dates_in(text, *(START + timedelta(days=offset) for offset in range(6)))
        self.assert_dates_in(bot.duty_message(), START, START + timedelta(days=1))

    def test_second_day_keeps_current_lookup_today_and_next_person_tomorrow(self):
        self.day = START + timedelta(days=1)
        self.assertEqual(self.schedule(), [
            (self.day, "a"), (START + timedelta(days=2), "b"), (START + timedelta(days=4), "c"),
        ])
        self.assertEqual(bot.member_duty("a"), (self.day, 0))
        self.assertEqual(bot.member_duty("b"), (self.day + timedelta(days=1), 1))
        self.assertEqual(bot.current_duty_start(), START)
        self.assert_dates_in(bot.duty_message(), START, self.day)
        self.assertEqual(self.store.state["round_position"], 1)

    def test_completing_first_day_holds_member_through_second_then_advances_on_third(self):
        self.assertTrue(self.store.mark_today_done(START))
        self.day = START + timedelta(days=1)
        self.assertEqual(self.schedule()[0], (self.day, "a"))
        self.assertEqual(self.store.state["duty_started_date"], START.isoformat())
        self.assertTrue(self.store.state["today_duty_done"])
        self.assertTrue(self.store.records[self.day.isoformat()]["done"])
        self.assertNotIn("⏳", bot.schedule_text())
        self.day = START + timedelta(days=2)
        self.assertEqual(self.schedule(), [(self.day, "b"), (START + timedelta(days=4), "c")])
        self.assertEqual(self.store.state["duty_started_date"], self.day.isoformat())
        self.assertEqual(self.store.state["round_position"], 2)

    def test_completed_people_advance_on_the_first_third_and_fifth(self):
        for offset, member_id in ((0, "a"), (2, "b"), (4, "c")):
            with self.subTest(offset=offset):
                self.day = START + timedelta(days=offset)
                self.assertEqual(self.schedule()[0], (self.day, member_id))
                self.assertTrue(self.store.mark_today_done(self.day))
                self.day += timedelta(days=1)
                self.assertEqual(self.schedule()[0], (self.day, member_id))
                self.assertTrue(self.store.state["today_duty_done"])

    def test_unfinished_span_holds_person_and_delays_future_two_day_ranges(self):
        self.day = START + timedelta(days=3)
        self.assertEqual(self.schedule(), [
            (self.day, "a"), (START + timedelta(days=4), "b"), (START + timedelta(days=6), "c"),
        ])
        self.assertEqual(self.store.state["duty_started_date"], START.isoformat())
        self.assertIn("⏳", bot.schedule_text())
        self.assert_dates_in(bot.schedule_text(), START, self.day,
                             START + timedelta(days=4), START + timedelta(days=5),
                             START + timedelta(days=6), START + timedelta(days=7))
        self.assertTrue(self.store.mark_today_done(self.day))
        self.day += timedelta(days=1)
        self.assertEqual(self.schedule(), [(self.day, "b"), (START + timedelta(days=6), "c")])

    def test_ranges_cross_a_month_boundary(self):
        start = date(2026, 1, 31)
        self.store = TwoDayStore(start=start)
        with patch.object(bot, "STORE", self.store):
            self.day = start
            self.assertEqual(self.schedule(), [(start, "a"), (date(2026, 2, 2), "b"), (date(2026, 2, 4), "c")])
            self.assert_dates_in(bot.duty_message(), start, date(2026, 2, 1))
            self.assert_dates_in(bot.schedule_text(), start, *(date(2026, 2, number) for number in range(1, 6)))
            self.day = date(2026, 2, 1)
            self.assertEqual(bot.member_duty("a"), (self.day, 0))
            self.assertEqual(bot.member_duty("b"), (date(2026, 2, 2), 1))

    async def test_second_day_member_lookup_still_reports_current_person_as_today(self):
        self.day = START + timedelta(days=1)
        await bot.on_text(self.update(text="/alice_one", chat_id=fixtures.REGULAR_USER_ID,
                                     chat_type=Chat.PRIVATE), self.context)
        call = self.api.send_message.await_args
        self.assertIn("bugungi navbatchi", call.kwargs["text"])
        fixtures.BotTests.assert_mentions(self, call, ["@alice_one"])

    async def test_first_day_announcement_contains_true_mention_and_two_day_period(self):
        self.assertTrue(await bot.publish_today_if_due(self.app))
        call = self.api.send_message.await_args
        self.assertEqual(call.kwargs["chat_id"], fixtures.GROUP_ID)
        self.assertFalse(call.kwargs["disable_notification"])
        fixtures.BotTests.assert_mentions(self, call, ["@alice_one"])
        self.assert_dates_in(call.kwargs["text"], START, START + timedelta(days=1))
        self.assertEqual(self.store.state["last_announced_date"], START.isoformat())

    async def test_announcements_send_once_per_two_day_block_while_duty_is_unfinished(self):
        for offset, should_send in ((0, True), (1, False), (2, True), (3, False), (4, True)):
            with self.subTest(offset=offset):
                self.day = START + timedelta(days=offset)
                before = self.api.send_message.await_count
                self.assertEqual(await bot.publish_today_if_due(self.app), should_send)
                self.assertEqual(self.api.send_message.await_count, before + int(should_send))
                self.assertFalse(await bot.publish_today_if_due(self.app))
                self.assertEqual(self.store.state["today_duty_id"], "a")
        self.assertEqual(self.api.send_message.await_count, 3)

    async def test_second_day_catches_up_when_first_day_was_never_announced(self):
        self.day = START + timedelta(days=1)
        self.assertTrue(await bot.publish_today_if_due(self.app))
        self.assertEqual(self.store.state["last_announced_date"], self.day.isoformat())
        self.assertFalse(await bot.publish_today_if_due(self.app))
        self.api.send_message.assert_awaited_once()

    async def test_first_day_send_failure_recovers_on_second_day(self):
        self.api.send_message.side_effect = [TelegramError("offline send"), SimpleNamespace(message_id=502)]
        with self.assertLogs("navbatchilik", level="ERROR"):
            self.assertFalse(await bot.publish_today_if_due(self.app))
        self.assertIsNone(self.store.state.get("last_announced_date"))
        self.day = START + timedelta(days=1)
        self.assertTrue(await bot.publish_today_if_due(self.app))
        self.assertEqual(self.store.state["last_announced_date"], self.day.isoformat())
        self.assertEqual(self.store.state["last_message_id"], 502)

    async def test_completed_second_day_is_silent_then_new_person_announced_on_third(self):
        self.assertTrue(await bot.publish_today_if_due(self.app))
        self.assertTrue(self.store.mark_today_done(START))
        self.day = START + timedelta(days=1)
        self.assertFalse(await bot.publish_today_if_due(self.app))
        self.assertEqual(self.store.state["today_duty_id"], "a")
        self.day = START + timedelta(days=2)
        self.assertTrue(await bot.publish_today_if_due(self.app))
        self.assertEqual(self.store.state["today_duty_id"], "b")
        fixtures.BotTests.assert_mentions(self, self.api.send_message.await_args, ["@bob_two"])
        self.api.send_message.assert_awaited()
        self.assertEqual(self.api.send_message.await_count, 2)

    async def test_force_announcement_can_send_on_second_day_before_eight(self):
        self.store.values["last_announced_date"] = START.isoformat()
        self.day = START + timedelta(days=1)
        with patch.object(bot, "date_at_eight_or_later", return_value=False):
            self.assertFalse(await bot.publish_today_if_due(self.app))
            self.assertTrue(await bot.publish_today_if_due(self.app, force=True))
        self.api.send_message.assert_awaited_once()
        fixtures.BotTests.assert_mentions(self, self.api.send_message.await_args, ["@alice_one"])

    async def test_manual_announcement_command_can_send_on_second_day(self):
        self.store.values["last_announced_date"] = START.isoformat()
        self.day = START + timedelta(days=1)
        update = self.update(fixtures.GLOBAL_ADMIN_ID, text="/elon", chat_id=fixtures.GLOBAL_ADMIN_ID,
                             chat_type=Chat.PRIVATE)
        with patch.object(bot, "date_at_eight_or_later", return_value=False):
            await bot.elon(update, self.context)
        group_calls = [call for call in self.api.send_message.await_args_list
                       if call.kwargs["chat_id"] == fixtures.GROUP_ID]
        self.assertEqual(len(group_calls), 1)
        fixtures.BotTests.assert_mentions(self, group_calls[0], ["@alice_one"])
        self.assertEqual(self.store.state["last_announced_date"], self.day.isoformat())


if __name__ == "__main__":
    unittest.main()
