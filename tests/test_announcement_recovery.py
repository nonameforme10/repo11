"""Announcement delivery and keyboard regressions without Telegram or PostgreSQL."""
import asyncio
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from telegram import Chat, ReplyKeyboardMarkup
from telegram.error import TelegramError

import test_bot as fixtures


bot = fixtures.bot
DAY = fixtures.DAY
GROUP_ID = fixtures.GROUP_ID
ADMIN_ID = fixtures.GLOBAL_ADMIN_ID


class AnnouncementRecoveryTests(unittest.IsolatedAsyncioTestCase):
    # Reuse setup helpers without inheriting/importing its discovered test class.
    user = staticmethod(fixtures.BotTests.user)
    update = fixtures.BotTests.update
    assert_mentions = fixtures.BotTests.assert_mentions

    def setUp(self):
        fixtures.BotTests.setUp(self)
        self.context.user_data = {}
        self.context.application.bot_data = {}

        def start_round(day):
            self.store._start_round(day)
            # Production reset clears the old group's delivery marker.
            self.store.state["last_announced_date"] = None

        self.store.start_new_round_today.side_effect = start_round

    def private_reset_update(self):
        return self.update(
            ADMIN_ID, chat_id=ADMIN_ID, chat_type=Chat.PRIVATE,
            callback_data=f"ui:reset_yes:{DAY}:7",
        )

    def assert_group_delivery(self, call):
        self.assertEqual(call.kwargs["chat_id"], GROUP_ID)
        self.assert_mentions(call, ["@alice_one"])
        self.assertIsNone(call.kwargs.get("reply_markup"))

    async def test_private_reset_without_group_does_not_mark_duty_delivered(self):
        self.store.state["chat_id"] = None
        self.store.state["last_announced_date"] = DAY.isoformat()
        with patch.object(bot, "date_at_eight_or_later", return_value=True):
            await bot.on_button(self.private_reset_update(), self.context)

        self.store.start_new_round_today.assert_called_once_with(DAY)
        self.api.edit_message_text.assert_awaited_once()
        self.api.send_message.assert_not_awaited()
        self.assertIsNone(self.store.state["last_announced_date"])
        self.store.save_state.assert_not_called()

    async def test_failed_private_reset_group_send_leaves_announcement_pending(self):
        self.api.send_message.side_effect = TelegramError("offline announcement failure")
        with (
            patch.object(bot, "date_at_eight_or_later", return_value=True),
            self.assertLogs("navbatchilik", level="ERROR"),
        ):
            await bot.on_button(self.private_reset_update(), self.context)

        self.assert_group_delivery(self.api.send_message.await_args)
        self.api.edit_message_text.assert_awaited_once()
        self.assertIsNone(self.store.state["last_announced_date"])
        self.assertIsNone(self.store.state["last_message_id"])
        self.store.save_state.assert_not_called()

    async def test_private_reset_announces_new_round_in_group_with_mention(self):
        self.store.state["last_announced_date"] = DAY.isoformat()
        with patch.object(bot, "date_at_eight_or_later", return_value=True):
            await bot.on_button(self.private_reset_update(), self.context)

        self.api.send_message.assert_awaited_once()
        self.assert_group_delivery(self.api.send_message.await_args)
        self.assertIn("Davra: 8", self.api.send_message.await_args.kwargs["text"])
        self.assertEqual(self.store.state["last_announced_date"], DAY.isoformat())
        self.assertEqual(self.store.state["last_message_id"], 501)
        self.store.save_state.assert_called_once()

    async def test_recovery_job_retries_failure_and_stops_after_success(self):
        self.api.send_message.side_effect = [
            TelegramError("offline first attempt"), SimpleNamespace(message_id=502),
        ]
        with patch.object(bot, "date_at_eight_or_later", return_value=True):
            with self.assertLogs("navbatchilik", level="ERROR"):
                await bot.daily_announcement(self.context)
            self.assertIsNone(self.store.state["last_announced_date"])
            self.assertIsNone(self.store.state["last_message_id"])
            self.store.save_state.assert_not_called()

            await bot.daily_announcement(self.context)
            self.assertEqual(self.store.state["last_announced_date"], DAY.isoformat())
            self.assertEqual(self.store.state["last_message_id"], 502)
            await bot.daily_announcement(self.context)

        self.assertEqual(self.api.send_message.await_count, 2)
        for call in self.api.send_message.await_args_list:
            self.assert_group_delivery(call)
        self.store.save_state.assert_called_once()

    async def test_recovery_before_eight_keeps_announcement_pending(self):
        local_time = datetime(DAY.year, DAY.month, DAY.day, 7, 59, tzinfo=bot.TZ)
        with patch.object(bot, "now", return_value=local_time):
            await bot.daily_announcement(self.context)

        self.api.send_message.assert_not_awaited()
        self.assertIsNone(self.store.state["last_announced_date"])
        self.store.save_state.assert_not_called()

    async def test_private_reset_before_eight_waits_for_daily_announcement(self):
        self.store.state["last_announced_date"] = DAY.isoformat()
        with patch.object(bot, "date_at_eight_or_later", return_value=False):
            await bot.on_button(self.private_reset_update(), self.context)
        self.api.edit_message_text.assert_awaited_once()
        self.api.send_message.assert_not_awaited()
        self.assertIsNone(self.store.state["last_announced_date"])

        with patch.object(bot, "date_at_eight_or_later", return_value=True):
            await bot.daily_announcement(self.context)
        self.api.send_message.assert_awaited_once()
        self.assert_group_delivery(self.api.send_message.await_args)

    def test_daily_eight_job_and_periodic_recovery_are_registered(self):
        application = MagicMock()
        builder = MagicMock()
        builder.token.return_value = builder
        builder.post_init.return_value = builder
        builder.build.return_value = application
        with patch.object(bot.Application, "builder", return_value=builder):
            bot.main()

        application.job_queue.run_daily.assert_called_once_with(
            bot.daily_announcement, bot.ANNOUNCE_AT, name="daily_announcement",
        )
        self.assertEqual((bot.ANNOUNCE_AT.hour, bot.ANNOUNCE_AT.minute), (8, 0))
        self.assertEqual(bot.ANNOUNCE_AT.tzinfo.key, "Asia/Tashkent")
        application.job_queue.run_repeating.assert_called_once_with(
            bot.daily_announcement, interval=60, first=10, name="announcement_retry",
        )
        application.run_polling.assert_called_once()

    def test_admin_reply_menu_omits_legacy_roster_button(self):
        keyboard = bot.main_keyboard(admin=True)
        labels = [button.text for row in keyboard.keyboard for button in row]

        self.assertNotIn("📜 Ro'yxat", labels)
        self.assertIn("⚙️ Admin paneli", labels)
        self.assertIn("👤 Bugungi navbatchi", labels)
        self.assertIn("🗓 Jadval", labels)

    async def test_private_schedule_and_today_refresh_admin_reply_keyboard(self):
        for command in ("jadval", "bugun"):
            with self.subTest(command=command):
                self.api.send_message.reset_mock()
                await getattr(bot, command)(self.update(
                    ADMIN_ID, text=f"/{command}", chat_id=ADMIN_ID, chat_type=Chat.PRIVATE,
                ), self.context)

                self.api.send_message.assert_awaited_once()
                markup = self.api.send_message.await_args.kwargs.get("reply_markup")
                self.assertIsInstance(markup, ReplyKeyboardMarkup)
                self.assertEqual(markup, bot.main_keyboard(admin=True))

    async def test_group_schedule_and_today_have_no_reply_keyboard(self):
        for command in ("jadval", "bugun"):
            with self.subTest(command=command):
                self.api.send_message.reset_mock()
                await getattr(bot, command)(self.update(ADMIN_ID, text=f"/{command}"), self.context)

                self.api.send_message.assert_awaited_once()
                self.assertIsNone(self.api.send_message.await_args.kwargs.get("reply_markup"))

    async def test_simultaneous_recovery_calls_send_only_one_announcement(self):
        send_started = asyncio.Event()
        release_send = asyncio.Event()

        async def delayed_send(**kwargs):
            send_started.set()
            await release_send.wait()
            return SimpleNamespace(message_id=503)

        self.api.send_message.side_effect = delayed_send
        with patch.object(bot, "date_at_eight_or_later", return_value=True):
            first = asyncio.create_task(bot.publish_today_if_due(self.context.application))
            await send_started.wait()
            second = asyncio.create_task(bot.publish_today_if_due(self.context.application))
            await asyncio.sleep(0)
            release_send.set()
            results = await asyncio.gather(first, second)

        self.assertEqual(results, [True, False])
        self.api.send_message.assert_awaited_once()
        self.assert_group_delivery(self.api.send_message.await_args)
        self.store.save_state.assert_called_once()

    async def test_late_group_linkage_announces_on_the_next_recovery_tick(self):
        self.store.state["chat_id"] = None
        with patch.object(bot, "date_at_eight_or_later", return_value=True):
            await bot.daily_announcement(self.context)
            self.api.send_message.assert_not_awaited()
            self.assertIsNone(self.store.state["last_announced_date"])

            self.store.state["chat_id"] = GROUP_ID
            await bot.daily_announcement(self.context)

        self.api.send_message.assert_awaited_once()
        self.assert_group_delivery(self.api.send_message.await_args)
        self.assertEqual(self.store.state["last_announced_date"], DAY.isoformat())

    async def test_round_reset_while_sending_keeps_new_round_pending(self):
        send_count = 0

        async def send_and_reset(**kwargs):
            nonlocal send_count
            send_count += 1
            if send_count == 1:
                self.store.start_new_round_today(DAY)
            return SimpleNamespace(message_id=503 + send_count)

        self.api.send_message.side_effect = send_and_reset
        with patch.object(bot, "date_at_eight_or_later", return_value=True):
            self.assertTrue(await bot.publish_today_if_due(self.context.application))
            self.assertEqual(self.store.state["round_number"], 8)
            self.assertIsNone(self.store.state["last_announced_date"])
            self.assertIsNone(self.store.state["last_message_id"])
            self.store.save_state.assert_not_called()

            self.assertTrue(await bot.publish_today_if_due(self.context.application))
            self.assertFalse(await bot.publish_today_if_due(self.context.application))

        self.assertEqual(self.api.send_message.await_count, 2)
        first, second = self.api.send_message.await_args_list
        self.assertIn("Davra: 7", first.kwargs["text"])
        self.assertIn("Davra: 8", second.kwargs["text"])
        self.assert_group_delivery(second)
        self.assertEqual(self.store.state["last_announced_date"], DAY.isoformat())
        self.assertEqual(self.store.state["last_message_id"], 505)
        self.store.save_state.assert_called_once()

    async def test_group_switch_while_sending_keeps_new_group_pending(self):
        new_group_id = GROUP_ID - 20
        send_count = 0

        async def send_and_switch_group(**kwargs):
            nonlocal send_count
            send_count += 1
            if send_count == 1:
                self.store.state["chat_id"] = new_group_id
                self.store.state["last_announced_date"] = None
                self.store.state["last_message_id"] = None
            return SimpleNamespace(message_id=505 + send_count)

        self.api.send_message.side_effect = send_and_switch_group
        with patch.object(bot, "date_at_eight_or_later", return_value=True):
            self.assertTrue(await bot.publish_today_if_due(self.context.application))
            self.assertEqual(self.store.state["chat_id"], new_group_id)
            self.assertIsNone(self.store.state["last_announced_date"])
            self.assertIsNone(self.store.state["last_message_id"])
            self.store.save_state.assert_not_called()

            self.assertTrue(await bot.publish_today_if_due(self.context.application))
            self.assertFalse(await bot.publish_today_if_due(self.context.application))

        self.assertEqual(self.api.send_message.await_count, 2)
        first, second = self.api.send_message.await_args_list
        self.assertEqual(first.kwargs["chat_id"], GROUP_ID)
        self.assertEqual(second.kwargs["chat_id"], new_group_id)
        self.assert_mentions(second, ["@alice_one"])
        self.assertEqual(self.store.state["last_announced_date"], DAY.isoformat())
        self.assertEqual(self.store.state["last_message_id"], 507)
        self.store.save_state.assert_called_once()

    async def test_failed_setup_in_new_group_clears_old_delivery_flag_and_can_retry(self):
        new_group_id = GROUP_ID - 10
        self.store.state["last_announced_date"] = DAY.isoformat()
        self.store.state["last_message_id"] = 111
        self.api.send_message.side_effect = TelegramError("offline setup response failure")

        with self.assertRaises(TelegramError):
            await bot.setup(self.update(ADMIN_ID, text="/setup", chat_id=new_group_id), self.context)

        self.assertEqual(self.store.state["chat_id"], new_group_id)
        self.assertIsNone(self.store.state["last_announced_date"])
        self.assertIsNone(self.store.state["last_message_id"])
        self.api.send_message.side_effect = None
        self.api.send_message.reset_mock()
        with patch.object(bot, "date_at_eight_or_later", return_value=True):
            await bot.daily_announcement(self.context)

        self.api.send_message.assert_awaited_once()
        self.assertEqual(self.api.send_message.await_args.kwargs["chat_id"], new_group_id)
        self.assert_mentions(self.api.send_message.await_args, ["@alice_one"])
        self.assertEqual(self.store.state["last_announced_date"], DAY.isoformat())

    async def test_admin_elon_forces_group_announcement_despite_delivered_flag(self):
        self.store.state["last_announced_date"] = DAY.isoformat()
        with patch.object(bot, "date_at_eight_or_later", return_value=True):
            await bot.elon(self.update(
                ADMIN_ID, text="/elon", chat_id=ADMIN_ID, chat_type=Chat.PRIVATE,
            ), self.context)

        calls = self.api.send_message.await_args_list
        group_calls = [call for call in calls if call.kwargs.get("chat_id") == GROUP_ID]
        private_calls = [call for call in calls if call.kwargs.get("chat_id") == ADMIN_ID]
        self.assertEqual(len(group_calls), 1)
        self.assert_group_delivery(group_calls[0])
        self.assertEqual(len(private_calls), 1)
        self.assertTrue(private_calls[0].kwargs["text"].strip())
        self.assertEqual(self.store.state["last_announced_date"], DAY.isoformat())

    async def test_regular_user_cannot_force_announcement(self):
        before_state = dict(self.store.state)
        await bot.elon(self.update(
            text="/elon", chat_id=fixtures.REGULAR_USER_ID, chat_type=Chat.PRIVATE,
        ), self.context)

        self.api.send_message.assert_awaited_once()
        self.assertEqual(self.api.send_message.await_args.kwargs["chat_id"], fixtures.REGULAR_USER_ID)
        self.assertEqual(self.store.state, before_state)
        self.store.save_state.assert_not_called()


if __name__ == "__main__":
    unittest.main()
