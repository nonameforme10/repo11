"""Native Telegram slash-command suggestions without Telegram API requests."""

import asyncio
import re
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from telegram import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeChatMember,
    BotCommandScopeDefault,
    Chat,
    MenuButtonCommands,
)
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError

import test_bot as fixtures


bot = fixtures.bot
PUBLIC_COMMANDS = {"start", "bugun", "jadval", "id"}
MANAGEMENT_COMMANDS = {
    "setup", "admin", "odamlar", "bajarildi", "elon", "tarix", "zaxira",
    "ism_qosh", "ism_ochir", "bekor",
}


class CommandMenuTests(unittest.IsolatedAsyncioTestCase):
    user = staticmethod(fixtures.BotTests.user)
    update = fixtures.BotTests.update
    membership_update = fixtures.BotTests.membership_update

    def setUp(self):
        fixtures.BotTests.setUp(self)
        self.context.user_data = {}
        self.api.set_my_commands = AsyncMock(return_value=True)
        self.api.set_chat_menu_button = AsyncMock(return_value=True)

    @staticmethod
    def command_names(call):
        commands = call.kwargs.get("commands", call.args[0] if call.args else ())
        return {command.command for command in commands}

    def scope_calls(self, scope_type):
        return [call for call in self.api.set_my_commands.await_args_list
                if isinstance(call.kwargs.get("scope"), scope_type)]

    async def queue_public_scope_failure(self):
        async def private_scope_offline(commands, scope=None, **kwargs):
            if isinstance(scope, BotCommandScopeAllPrivateChats):
                raise NetworkError("offline public private-chat menu")
            return True

        self.api.set_my_commands.side_effect = private_scope_offline
        with self.assertLogs("navbatchilik", level="WARNING"):
            await bot.register_bot_commands(self.context.application)
        self.assertTrue(self.context.application.bot_data.get("command_menu_retry"))
        self.api.set_my_commands.reset_mock()

    async def test_public_commands_are_available_in_group_private_and_default_scopes(self):
        await bot.register_bot_commands(self.context.application)
        for scope_type in (BotCommandScopeDefault, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats):
            with self.subTest(scope=scope_type.__name__):
                calls = self.scope_calls(scope_type)
                self.assertTrue(calls)
                for call in calls:
                    names = self.command_names(call)
                    self.assertTrue(PUBLIC_COMMANDS.issubset(names))
                    self.assertTrue(MANAGEMENT_COMMANDS.isdisjoint(names))
                    self.assertNotIn("royxat", names)
                    self.assertNotIn("cancel", names)

    async def test_group_administrators_get_management_commands(self):
        await bot.register_bot_commands(self.context.application)
        calls = self.scope_calls(BotCommandScopeAllChatAdministrators)
        self.assertTrue(calls)
        for call in calls:
            names = self.command_names(call)
            self.assertTrue((PUBLIC_COMMANDS | MANAGEMENT_COMMANDS).issubset(names))
            self.assertNotIn("royxat", names)
            self.assertNotIn("cancel", names)

    async def test_configured_admin_has_private_and_linked_group_member_scopes(self):
        await bot.register_bot_commands(self.context.application)
        private_calls = [call for call in self.scope_calls(BotCommandScopeChat)
                         if call.kwargs["scope"].chat_id == fixtures.GLOBAL_ADMIN_ID]
        group_calls = [call for call in self.scope_calls(BotCommandScopeChatMember)
                       if call.kwargs["scope"].chat_id == fixtures.GROUP_ID
                       and call.kwargs["scope"].user_id == fixtures.GLOBAL_ADMIN_ID]
        self.assertTrue(private_calls)
        self.assertTrue(group_calls)
        for call in private_calls + group_calls:
            self.assertTrue((PUBLIC_COMMANDS | MANAGEMENT_COMMANDS).issubset(self.command_names(call)))
        self.api.get_chat_member.assert_not_awaited()

    async def test_registered_command_metadata_is_accepted_by_telegram(self):
        await bot.register_bot_commands(self.context.application)
        self.assertTrue(self.api.set_my_commands.await_args_list)
        for call in self.api.set_my_commands.await_args_list:
            commands = call.kwargs.get("commands", call.args[0] if call.args else ())
            self.assertGreater(len(commands), 0)
            self.assertLessEqual(len(commands), 100)
            self.assertEqual(len(commands), len({command.command for command in commands}))
            for command in commands:
                with self.subTest(command=command.command):
                    self.assertIsInstance(command, BotCommand)
                    self.assertIsNotNone(re.fullmatch(r"[a-z0-9_]{1,32}", command.command))
                    self.assertGreater(len(command.description.strip()), 0)
                    self.assertLessEqual(len(command.description), 256)

    async def test_private_menu_button_opens_native_commands(self):
        await bot.register_bot_commands(self.context.application)
        self.api.set_chat_menu_button.assert_awaited()
        default_calls = [call for call in self.api.set_chat_menu_button.await_args_list
                         if call.kwargs.get("chat_id") is None]
        self.assertTrue(default_calls)
        for call in default_calls:
            self.assertIsInstance(call.kwargs["menu_button"], MenuButtonCommands)

    async def test_startup_registers_native_commands(self):
        register = AsyncMock()
        with (
            patch.object(bot, "register_bot_commands", register),
            patch.object(bot, "notify_admins", AsyncMock()),
            patch.object(bot, "publish_today_if_due", AsyncMock(return_value=False)),
        ):
            await bot.post_init(self.context.application)
        register.assert_awaited_once_with(self.context.application)

    async def test_one_scope_failure_does_not_stop_remaining_registration(self):
        async def reject_private(commands, scope=None, **kwargs):
            if isinstance(scope, BotCommandScopeAllPrivateChats):
                raise TelegramError("offline private scope")
            return True

        self.api.set_my_commands.side_effect = reject_private
        with self.assertLogs("navbatchilik", level="WARNING"):
            await bot.register_bot_commands(self.context.application)
        self.assertTrue(self.scope_calls(BotCommandScopeAllGroupChats))
        self.assertTrue(self.scope_calls(BotCommandScopeAllChatAdministrators))
        self.assertTrue(self.scope_calls(BotCommandScopeChatMember))
        self.api.set_chat_menu_button.assert_awaited()

    async def test_one_configured_admin_failure_does_not_skip_other_admins(self):
        async def reject_one_admin(commands, scope=None, **kwargs):
            if isinstance(scope, BotCommandScopeChat) and scope.chat_id == fixtures.GLOBAL_ADMIN_ID:
                raise TelegramError("offline admin private chat")
            return True

        self.api.set_my_commands.side_effect = reject_one_admin
        with patch.object(bot, "ADMIN_IDS", {fixtures.GLOBAL_ADMIN_ID, 43}), self.assertLogs("navbatchilik", level="WARNING"):
            await bot.register_bot_commands(self.context.application)
        private_ids = {call.kwargs["scope"].chat_id for call in self.scope_calls(BotCommandScopeChat)}
        group_ids = {call.kwargs["scope"].user_id for call in self.scope_calls(BotCommandScopeChatMember)}
        self.assertIn(43, private_ids)
        self.assertEqual(group_ids, {fixtures.GLOBAL_ADMIN_ID, 43})

    async def test_menu_button_failure_does_not_break_command_registration(self):
        self.api.set_chat_menu_button.side_effect = TelegramError("offline menu button")
        with self.assertLogs("navbatchilik", level="WARNING"):
            await bot.register_bot_commands(self.context.application)
        self.assertTrue(self.scope_calls(BotCommandScopeAllGroupChats))
        self.assertTrue(self.scope_calls(BotCommandScopeAllPrivateChats))
        self.assertTrue(self.scope_calls(BotCommandScopeAllChatAdministrators))

    async def test_command_api_failure_does_not_abort_startup_notifications(self):
        self.api.set_my_commands.side_effect = TelegramError("offline command menu")
        notify = AsyncMock()
        publish = AsyncMock(return_value=False)
        with (
            patch.object(bot, "notify_admins", notify),
            patch.object(bot, "publish_today_if_due", publish),
            self.assertLogs("navbatchilik", level="WARNING"),
        ):
            await bot.post_init(self.context.application)
        notify.assert_awaited_once()
        publish.assert_awaited_once_with(self.context.application)

    async def test_group_member_scopes_are_not_registered_for_missing_or_private_target(self):
        for target in (None, fixtures.GLOBAL_ADMIN_ID):
            with self.subTest(target=target):
                self.store.state["chat_id"] = target
                self.api.set_my_commands.reset_mock()
                await bot.register_bot_commands(self.context.application)
                self.assertEqual(self.scope_calls(BotCommandScopeChatMember), [])

    async def test_setup_registers_global_admin_suggestions_in_new_group(self):
        new_group_id = -100987654
        self.store.state["chat_id"] = fixtures.GROUP_ID
        await bot.setup(self.update(fixtures.GLOBAL_ADMIN_ID, text="/setup", chat_id=new_group_id), self.context)
        calls = [call for call in self.scope_calls(BotCommandScopeChatMember)
                 if call.kwargs["scope"].chat_id == new_group_id
                 and call.kwargs["scope"].user_id == fixtures.GLOBAL_ADMIN_ID]
        self.assertTrue(calls)
        self.assertTrue(MANAGEMENT_COMMANDS.issubset(self.command_names(calls[-1])))

    async def test_network_failure_retries_only_failed_scope_on_next_announcement_check(self):
        rejected = False

        async def fail_default_once(commands, scope=None, **kwargs):
            nonlocal rejected
            if isinstance(scope, BotCommandScopeDefault) and not rejected:
                rejected = True
                raise NetworkError("offline connection")
            return True

        self.api.set_my_commands.side_effect = fail_default_once
        with self.assertLogs("navbatchilik", level="WARNING"):
            await bot.register_bot_commands(self.context.application)
        self.assertTrue(self.context.application.bot_data.get("command_menu_retry"))
        self.api.set_my_commands.reset_mock()
        self.api.set_chat_menu_button.reset_mock()
        publish = AsyncMock(return_value=False)
        with patch.object(bot, "publish_today_if_due", publish):
            await bot.daily_announcement(self.context)
        self.api.set_my_commands.assert_awaited_once()
        call = self.api.set_my_commands.await_args
        self.assertIsInstance(call.kwargs["scope"], BotCommandScopeDefault)
        self.assertEqual(self.command_names(call), PUBLIC_COMMANDS)
        self.api.set_chat_menu_button.assert_not_awaited()
        self.assertFalse(self.context.application.bot_data.get("command_menu_retry"))
        publish.assert_awaited_once_with(self.context.application)

    async def test_rate_limited_admin_scope_retries_with_management_commands(self):
        rejected = False

        async def rate_limit_admin_once(commands, scope=None, **kwargs):
            nonlocal rejected
            if isinstance(scope, BotCommandScopeAllChatAdministrators) and not rejected:
                rejected = True
                raise RetryAfter(120)
            return True

        self.api.set_my_commands.side_effect = rate_limit_admin_once
        initial_time = datetime(2026, 10, 5, 8, 0, tzinfo=bot.TZ)
        with (
            patch.object(bot, "now", return_value=initial_time),
            patch.dict("os.environ", {"PTB_TIMEDELTA": "true"}),
            self.assertLogs("navbatchilik", level="WARNING"),
        ):
            await bot.register_bot_commands(self.context.application)
        self.assertEqual(self.scope_calls(BotCommandScopeChat), [])
        self.assertEqual(self.scope_calls(BotCommandScopeChatMember), [])
        self.api.set_chat_menu_button.assert_not_awaited()
        self.api.set_my_commands.reset_mock()
        publish = AsyncMock(return_value=False)
        with (
            patch.object(bot, "now", return_value=initial_time + timedelta(seconds=60)),
            patch.object(bot, "publish_today_if_due", publish),
        ):
            await bot.daily_announcement(self.context)
            await bot.start(self.update(fixtures.GLOBAL_ADMIN_ID, text="/start", chat_id=fixtures.GLOBAL_ADMIN_ID,
                                        chat_type=Chat.PRIVATE), self.context)
        self.api.set_my_commands.assert_not_awaited()
        self.assertTrue(self.context.application.bot_data.get("command_menu_retry"))
        publish.assert_awaited_once_with(self.context.application)
        with (
            patch.object(bot, "now", return_value=initial_time + timedelta(seconds=120)),
            patch.object(bot, "publish_today_if_due", AsyncMock(return_value=False)),
        ):
            await bot.daily_announcement(self.context)
        admin_calls = self.scope_calls(BotCommandScopeAllChatAdministrators)
        self.assertEqual(len(admin_calls), 1)
        call = admin_calls[0]
        self.assertTrue(MANAGEMENT_COMMANDS.issubset(self.command_names(call)))
        self.assertEqual(len(self.scope_calls(BotCommandScopeChat)), 1)
        self.assertEqual(len(self.scope_calls(BotCommandScopeChatMember)), 1)
        self.api.set_chat_menu_button.assert_awaited_once()
        self.assertFalse(self.context.application.bot_data.get("command_menu_retry"))

    async def test_transient_menu_button_failure_retries_without_registering_other_scopes_again(self):
        self.api.set_chat_menu_button.side_effect = [NetworkError("offline menu"), True]
        with self.assertLogs("navbatchilik", level="WARNING"):
            await bot.register_bot_commands(self.context.application)
        self.api.set_my_commands.reset_mock()
        self.api.set_chat_menu_button.reset_mock()
        with patch.object(bot, "publish_today_if_due", AsyncMock(return_value=False)):
            await bot.daily_announcement(self.context)
        self.api.set_chat_menu_button.assert_awaited_once()
        self.assertIsInstance(self.api.set_chat_menu_button.await_args.kwargs["menu_button"], MenuButtonCommands)
        self.api.set_my_commands.assert_not_awaited()
        self.assertFalse(self.context.application.bot_data.get("command_menu_retry"))

    async def test_repeated_network_failure_remains_pending_and_duty_publication_still_runs(self):
        async def group_scope_offline(commands, scope=None, **kwargs):
            if isinstance(scope, BotCommandScopeAllGroupChats):
                raise NetworkError("still offline")
            return True

        self.api.set_my_commands.side_effect = group_scope_offline
        with self.assertLogs("navbatchilik", level="WARNING"):
            await bot.register_bot_commands(self.context.application)
        self.api.set_my_commands.reset_mock()
        publish = AsyncMock(return_value=False)
        with patch.object(bot, "publish_today_if_due", publish), self.assertLogs("navbatchilik", level="WARNING"):
            await bot.daily_announcement(self.context)
        self.api.set_my_commands.assert_awaited_once()
        self.assertIsInstance(self.api.set_my_commands.await_args.kwargs["scope"], BotCommandScopeAllGroupChats)
        self.assertEqual(len(self.context.application.bot_data["command_menu_retry"]), 1)
        publish.assert_awaited_once_with(self.context.application)

    async def test_permanent_command_errors_are_not_queued_or_retried(self):
        for error in (BadRequest("invalid command scope"), Forbidden("bot blocked"), TelegramError("permanent API failure")):
            with self.subTest(error=type(error).__name__):
                self.api.set_my_commands.side_effect = error
                with self.assertLogs("navbatchilik", level="WARNING"):
                    await bot.register_bot_commands(self.context.application)
                self.assertFalse(self.context.application.bot_data.get("command_menu_retry"))
                self.api.set_my_commands.reset_mock()
                publish = AsyncMock(return_value=False)
                with patch.object(bot, "publish_today_if_due", publish):
                    await bot.daily_announcement(self.context)
                self.api.set_my_commands.assert_not_awaited()
                publish.assert_awaited_once_with(self.context.application)

    async def test_first_group_commands_register_configured_admin_suggestions(self):
        new_group_id = -100987654
        for handler in (bot.start, bot.bugun, bot.jadval):
            with self.subTest(handler=handler.__name__):
                self.store.state["chat_id"] = None
                self.api.set_my_commands.reset_mock()
                await handler(self.update(text=f"/{handler.__name__}", chat_id=new_group_id), self.context)
                calls = [call for call in self.scope_calls(BotCommandScopeChatMember)
                         if call.kwargs["scope"].chat_id == new_group_id
                         and call.kwargs["scope"].user_id == fixtures.GLOBAL_ADMIN_ID]
                self.assertTrue(calls)
                self.assertTrue(MANAGEMENT_COMMANDS.issubset(self.command_names(calls[-1])))

    async def test_bot_addition_or_promotion_refreshes_configured_admin_suggestions(self):
        for administrator in (False, True):
            with self.subTest(administrator=administrator):
                self.store.state["chat_id"] = None
                self.api.set_my_commands.reset_mock()
                await bot.on_membership_change(self.membership_update(administrator=administrator), self.context)
                calls = [call for call in self.scope_calls(BotCommandScopeChatMember)
                         if call.kwargs["scope"].chat_id == fixtures.GROUP_ID
                         and call.kwargs["scope"].user_id == fixtures.GLOBAL_ADMIN_ID]
                self.assertTrue(calls)

    async def test_group_migration_registers_configured_admin_suggestions_for_new_id(self):
        new_group_id = -100987654
        await bot.on_group_migration(self.update(migrate_to=new_group_id), self.context)
        calls = self.scope_calls(BotCommandScopeChatMember)
        self.assertTrue(calls)
        self.assertTrue(all(call.kwargs["scope"].chat_id == new_group_id for call in calls))
        self.assertTrue(any(call.kwargs["scope"].user_id == fixtures.GLOBAL_ADMIN_ID for call in calls))

    async def test_setup_network_failure_retries_without_dropping_existing_failed_scope(self):
        await self.queue_public_scope_failure()
        self.api.set_my_commands.side_effect = NetworkError("offline new group menu")
        new_group_id = -100987654
        with self.assertLogs("navbatchilik", level="WARNING"):
            await bot.setup(self.update(fixtures.GLOBAL_ADMIN_ID, text="/setup", chat_id=new_group_id), self.context)
        self.api.set_my_commands.side_effect = None
        self.api.set_my_commands.reset_mock()
        with patch.object(bot, "publish_today_if_due", AsyncMock(return_value=False)):
            await bot.daily_announcement(self.context)
        self.assertTrue(self.scope_calls(BotCommandScopeAllPrivateChats))
        group_calls = self.scope_calls(BotCommandScopeChatMember)
        self.assertTrue(any(call.kwargs["scope"].chat_id == new_group_id for call in group_calls))
        self.assertFalse(self.context.application.bot_data.get("command_menu_retry"))

    async def test_private_start_network_failure_retries_without_dropping_existing_failed_scope(self):
        await self.queue_public_scope_failure()
        self.api.set_my_commands.side_effect = NetworkError("offline private admin menu")
        with self.assertLogs("navbatchilik", level="WARNING"):
            await bot.start(self.update(fixtures.GLOBAL_ADMIN_ID, text="/start", chat_id=fixtures.GLOBAL_ADMIN_ID,
                                        chat_type=Chat.PRIVATE), self.context)
        self.api.set_my_commands.side_effect = None
        self.api.set_my_commands.reset_mock()
        with patch.object(bot, "publish_today_if_due", AsyncMock(return_value=False)):
            await bot.daily_announcement(self.context)
        self.assertTrue(self.scope_calls(BotCommandScopeAllPrivateChats))
        private_calls = [call for call in self.scope_calls(BotCommandScopeChat)
                         if call.kwargs["scope"].chat_id == fixtures.GLOBAL_ADMIN_ID]
        self.assertTrue(private_calls)
        self.assertTrue(MANAGEMENT_COMMANDS.issubset(self.command_names(private_calls[-1])))
        self.assertFalse(self.context.application.bot_data.get("command_menu_retry"))

    async def test_successful_private_menu_refresh_preserves_another_pending_failed_scope(self):
        await self.queue_public_scope_failure()
        self.api.set_my_commands.side_effect = None
        await bot.start(self.update(fixtures.GLOBAL_ADMIN_ID, text="/start", chat_id=fixtures.GLOBAL_ADMIN_ID,
                                   chat_type=Chat.PRIVATE), self.context)
        self.api.set_my_commands.reset_mock()
        with patch.object(bot, "publish_today_if_due", AsyncMock(return_value=False)):
            await bot.daily_announcement(self.context)
        self.api.set_my_commands.assert_awaited_once()
        self.assertIsInstance(self.api.set_my_commands.await_args.kwargs["scope"], BotCommandScopeAllPrivateChats)
        self.assertFalse(self.context.application.bot_data.get("command_menu_retry"))

    async def test_concurrent_old_retry_and_new_private_refresh_preserve_new_failure_and_deadline(self):
        await self.queue_public_scope_failure()
        old_request_started = asyncio.Event()
        release_old_request = asyncio.Event()
        private_refresh_started = asyncio.Event()

        async def concurrent_requests(commands, scope=None, **kwargs):
            if isinstance(scope, BotCommandScopeAllPrivateChats):
                old_request_started.set()
                await release_old_request.wait()
                return True
            if isinstance(scope, BotCommandScopeChat):
                raise RetryAfter(120)
            return True

        async def refresh_private_menu():
            private_refresh_started.set()
            await bot.start(self.update(fixtures.GLOBAL_ADMIN_ID, text="/start", chat_id=fixtures.GLOBAL_ADMIN_ID,
                                        chat_type=Chat.PRIVATE), self.context)

        self.api.set_my_commands.side_effect = concurrent_requests
        initial_time = datetime(2026, 10, 5, 8, 0, tzinfo=bot.TZ)
        publish = AsyncMock(return_value=False)
        with (
            patch.object(bot, "now", return_value=initial_time),
            patch.object(bot, "publish_today_if_due", publish),
            patch.dict("os.environ", {"PTB_TIMEDELTA": "true"}),
            self.assertLogs("navbatchilik", level="WARNING"),
        ):
            old_retry = asyncio.create_task(bot.daily_announcement(self.context))
            private_refresh = None
            try:
                await asyncio.wait_for(old_request_started.wait(), timeout=2)
                private_refresh = asyncio.create_task(refresh_private_menu())
                await asyncio.wait_for(private_refresh_started.wait(), timeout=2)
                self.assertEqual(self.scope_calls(BotCommandScopeChat), [])
            finally:
                release_old_request.set()
                pending_tasks = [old_retry] + ([private_refresh] if private_refresh is not None else [])
                await asyncio.wait_for(asyncio.gather(*pending_tasks), timeout=2)
        pending = self.context.application.bot_data["command_menu_retry"]
        self.assertEqual(pending, [(BotCommandScopeChat(chat_id=fixtures.GLOBAL_ADMIN_ID), True)])
        self.assertEqual(self.context.application.bot_data["command_menu_retry_after"], initial_time + timedelta(seconds=120))
        publish.assert_awaited_once_with(self.context.application)
        self.api.set_my_commands.side_effect = None
        self.api.set_my_commands.reset_mock()
        with (
            patch.object(bot, "now", return_value=initial_time + timedelta(seconds=120)),
            patch.object(bot, "publish_today_if_due", AsyncMock(return_value=False)),
        ):
            await bot.daily_announcement(self.context)
        self.api.set_my_commands.assert_awaited_once()
        self.assertIsInstance(self.api.set_my_commands.await_args.kwargs["scope"], BotCommandScopeChat)
        self.assertFalse(self.context.application.bot_data.get("command_menu_retry"))
        self.assertIsNone(self.context.application.bot_data.get("command_menu_retry_after"))


if __name__ == "__main__":
    unittest.main()
