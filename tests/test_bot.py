"""Bot regressions without a PostgreSQL connection or Telegram requests.

Run with ``python -m unittest discover -s tests -v`` in the project environment.
Real Telegram objects exercise their UTF-16 entity offsets and shortcut methods;
only the database and Bot network methods are mocked.
"""

import importlib
import os
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import (
    Bot,
    CallbackQuery,
    Chat,
    ChatMemberAdministrator,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberOwner,
    ChatMemberUpdated,
    Message,
    MessageEntity,
    Update,
    User,
)
from telegram.error import TelegramError


# Do not let importing bot load a developer's credentials or initialize storage.
with (
    patch.dict(os.environ, {"BOT_TOKEN": "123456:offline-test", "ADMIN_IDS": "42"}, clear=True),
    patch("dotenv.load_dotenv"),
    patch("data_store.PostgresStore"),
):
    bot = importlib.import_module("bot")


DAY = date(2026, 10, 5)
GROUP_ID = -100123456
GLOBAL_ADMIN_ID = 42
GROUP_ADMIN_ID = 84
REGULAR_USER_ID = 126


class FakeStore:
    def __init__(self):
        self.names = [
            {"id": "alice", "name": "Alice Example", "username": "@alice_one"},
            {"id": "bob", "name": "Bob Example", "username": "@bob_two"},
            {"id": "carol", "name": "Carol Example", "username": ""},
        ]
        self.state = {
            "chat_id": GROUP_ID,
            "round_order": ["alice", "bob", "carol"],
            "round_position": 1,
            "round_number": 7,
            "today_duty_id": "alice",
            "today_duty_date": DAY.isoformat(),
            "duty_started_date": DAY.isoformat(),
            "today_duty_done": False,
            "last_announced_date": None,
            "last_message_id": None,
        }
        self.history = {
            DAY.isoformat(): {**self.names[0], "member_id": "alice", "done": False},
        }
        self.storage_errors = []
        self.member = MagicMock(side_effect=lambda member_id: next(
            (person for person in self.names if person["id"] == member_id), None
        ))
        self.ensure_assignment_through = MagicMock()
        self.start_new_round_today = MagicMock(side_effect=self._start_round)
        self.mark_today_done = MagicMock(return_value=True)
        self.save_state = MagicMock()
        self.last_30_days = MagicMock(return_value=list(self.history.items()))
        self.add_name = MagicMock(return_value={
            "id": "dora", "name": "Dora Example", "username": "@dora_four",
        })
        self.remove_name = MagicMock(return_value=(self.names[1], False))

    def _start_round(self, day):
        self.state["round_number"] += 1
        self.state["round_position"] = 1
        self.state["today_duty_date"] = day.isoformat()
        self.state["today_duty_done"] = False
        self.state["last_announced_date"] = None


class BotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = FakeStore()
        self.api = MagicMock(spec=Bot)
        self.api.id = 999
        self.api.username = "DutyBot"
        self.api.send_message = AsyncMock(return_value=SimpleNamespace(message_id=501))
        self.api.send_document = AsyncMock()
        self.api.delete_message = AsyncMock(return_value=True)
        self.api.answer_callback_query = AsyncMock(return_value=True)
        self.api.edit_message_text = AsyncMock(return_value=True)
        self.api.edit_message_reply_markup = AsyncMock(return_value=True)
        self.api.get_chat_member = AsyncMock(return_value=ChatMemberMember(self.user(REGULAR_USER_ID)))
        self.api.set_my_commands = AsyncMock(return_value=True)
        self.api.set_chat_menu_button = AsyncMock(return_value=True)
        self.context = SimpleNamespace(
            bot=self.api, args=[], application=SimpleNamespace(bot=self.api, bot_data={})
        )
        self.patches = [
            patch.object(bot, "STORE", self.store),
            patch.object(bot, "ADMIN_IDS", {GLOBAL_ADMIN_ID}),
            patch.object(bot, "today", return_value=DAY),
        ]
        for active_patch in self.patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)

    @staticmethod
    def user(user_id, username="alice_one"):
        return User(id=user_id, first_name="Test User", is_bot=False, username=username)

    def update(self, user_id=REGULAR_USER_ID, text="/admin", chat_id=GROUP_ID,
               chat_type=Chat.SUPERGROUP, callback_data=None, reply_to=None,
               migrate_to=None, migrate_from=None):
        user = self.user(user_id)
        message = Message(
            message_id=100,
            date=datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc),
            chat=Chat(id=chat_id, type=chat_type),
            from_user=user,
            text=text,
            reply_to_message=reply_to,
            migrate_to_chat_id=migrate_to,
            migrate_from_chat_id=migrate_from,
        )
        message.set_bot(self.api)
        if callback_data is not None:
            query = CallbackQuery(
                id="callback-100", from_user=user, chat_instance="offline",
                message=message, data=callback_data,
            )
            query.set_bot(self.api)
            return Update(update_id=1, callback_query=query)
        return Update(update_id=1, message=message)

    def allow_group_admin(self, owner=False):
        if owner:
            member = ChatMemberOwner(self.user(GROUP_ADMIN_ID), is_anonymous=False)
        else:
            member = ChatMemberAdministrator(
                user=self.user(GROUP_ADMIN_ID), can_be_edited=False, is_anonymous=False,
                can_manage_chat=True, can_delete_messages=True, can_manage_video_chats=False,
                can_restrict_members=False, can_promote_members=False, can_change_info=False,
                can_invite_users=False, can_post_stories=False, can_edit_stories=False,
                can_delete_stories=False,
            )
        self.api.get_chat_member.return_value = member

    def membership_update(self, added=True, administrator=False, chat_id=GROUP_ID):
        bot_user = User(id=self.api.id, first_name="Duty Bot", is_bot=True, username="DutyBot")
        if administrator:
            member = ChatMemberAdministrator(
                user=bot_user, can_be_edited=False, is_anonymous=False,
                can_manage_chat=True, can_delete_messages=False, can_manage_video_chats=False,
                can_restrict_members=False, can_promote_members=False, can_change_info=False,
                can_invite_users=False, can_post_stories=False, can_edit_stories=False,
                can_delete_stories=False,
            )
        else:
            member = ChatMemberMember(bot_user)
        event = ChatMemberUpdated(
            chat=Chat(id=chat_id, type=Chat.SUPERGROUP),
            from_user=self.user(GROUP_ADMIN_ID),
            date=datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc),
            old_chat_member=ChatMemberLeft(bot_user) if added else member,
            new_chat_member=member if added else ChatMemberLeft(bot_user),
        )
        return Update(update_id=3, my_chat_member=event)

    def assert_mentions(self, call, expected_handles):
        """Decode real Telegram entities, catching Python versus UTF-16 offsets."""
        self.assertIn("entities", call.kwargs)
        entities = call.kwargs["entities"]
        text = call.kwargs.get("text")
        if text is None:
            text = call.args[0]
        encoded = text.encode("utf-16-le")
        decoded = []
        for entity in entities:
            self.assertIsInstance(entity, MessageEntity)
            self.assertEqual(entity.type, MessageEntity.MENTION)
            start = entity.offset * 2
            end = (entity.offset + entity.length) * 2
            decoded.append(encoded[start:end].decode("utf-16-le"))
        self.assertEqual(decoded, expected_handles)
        self.assertIsNone(call.kwargs.get("parse_mode"))

    def assert_no_management_mutation(self):
        self.store.start_new_round_today.assert_not_called()
        self.store.mark_today_done.assert_not_called()
        self.store.add_name.assert_not_called()
        self.store.remove_name.assert_not_called()
        self.store.save_state.assert_not_called()

    def test_mention_entities_use_utf16_offsets_after_emoji(self):
        text = "🧹 Bugun @alice_one, ertaga 👤 @bob_two."
        entities = bot.mention_entities(text)
        expected = []
        for handle in ("@alice_one", "@bob_two"):
            index = text.index(handle)
            expected.append(MessageEntity(
                MessageEntity.MENTION,
                offset=len(text[:index].encode("utf-16-le")) // 2,
                length=len(handle),
            ))
        self.assertEqual(entities, expected)

    def test_mentions_accept_complete_handles_at_length_boundaries(self):
        longest = "@" + "a" * 32
        text = f"(@Ab_12) / {longest}!"
        entities = bot.mention_entities(text)
        self.assertEqual(len(entities), 2)
        self.assertEqual(entities[0].length, 6)
        self.assertEqual(entities[1].length, 33)

    def test_mentions_do_not_tag_emails_or_partial_invalid_handles(self):
        invalid = [
            "name@alice_one", "test.name@bob_two", "user+tag@alice_one",
            "@abcd", "@" + "a" * 33, "@alice_one-too", "@alice_oneé", "@@alice_one",
        ]
        for text in invalid:
            with self.subTest(text=text):
                self.assertEqual(bot.mention_entities(text), [])

    async def test_send_plain_explicitly_tags_mentions(self):
        await bot.send_plain(self.update(), self.context, "👤 Alice (@alice_one)")
        self.assert_mentions(self.api.send_message.await_args, ["@alice_one"])
        self.assertEqual(self.api.send_message.await_args.kwargs["chat_id"], GROUP_ID)

    async def test_configured_admin_can_manage_without_membership_lookup(self):
        update = self.update(GLOBAL_ADMIN_ID, chat_id=GLOBAL_ADMIN_ID, chat_type=Chat.PRIVATE)
        self.assertTrue(await bot.can_manage(update, self.context))
        self.api.get_chat_member.assert_not_awaited()

    async def test_linked_group_administrator_and_owner_can_manage(self):
        for owner in (False, True):
            with self.subTest(owner=owner):
                self.allow_group_admin(owner=owner)
                self.api.get_chat_member.reset_mock()
                self.assertTrue(await bot.can_manage(self.update(GROUP_ADMIN_ID), self.context))
                self.api.get_chat_member.assert_awaited_once_with(chat_id=GROUP_ID, user_id=GROUP_ADMIN_ID)

    async def test_regular_linked_group_member_cannot_manage(self):
        self.assertFalse(await bot.can_manage(self.update(), self.context))

    async def test_group_permissions_do_not_extend_to_other_chats(self):
        self.allow_group_admin(owner=True)
        for chat_id, chat_type in ((-100987654, Chat.SUPERGROUP), (GROUP_ADMIN_ID, Chat.PRIVATE)):
            with self.subTest(chat_id=chat_id):
                self.api.get_chat_member.reset_mock()
                update = self.update(GROUP_ADMIN_ID, chat_id=chat_id, chat_type=chat_type)
                self.assertFalse(await bot.can_manage(update, self.context))
                self.api.get_chat_member.assert_not_awaited()

    async def test_membership_lookup_error_fails_closed(self):
        self.api.get_chat_member.side_effect = TelegramError("offline permission lookup")
        with self.assertLogs("navbatchilik", level="WARNING"):
            self.assertFalse(await bot.can_manage(self.update(GROUP_ADMIN_ID), self.context))
        self.assert_no_management_mutation()

    async def test_missing_user_or_chat_fails_closed(self):
        self.assertFalse(await bot.can_manage(Update(update_id=2), self.context))
        self.api.get_chat_member.assert_not_awaited()

    async def test_require_admin_denies_with_a_visible_message(self):
        self.assertFalse(await bot.require_admin(self.update(), self.context))
        self.api.send_message.assert_awaited_once()
        self.assertTrue(self.api.send_message.await_args.kwargs["text"].strip())
        self.assert_no_management_mutation()

    async def test_unauthorized_management_commands_reply_without_mutating(self):
        for name in ("admin_panel", "bajarildi", "royxat", "tarix", "zaxira", "ism_qosh", "ism_ochir"):
            with self.subTest(command=name):
                self.api.send_message.reset_mock()
                self.api.delete_message.reset_mock()
                self.context.args = ["2"] if name == "ism_ochir" else ["Dora", "@dora_four"]
                await getattr(bot, name)(self.update(text=f"/{name}"), self.context)
                self.api.send_message.assert_awaited_once()
                self.api.delete_message.assert_not_awaited()
                self.assert_no_management_mutation()
        self.api.send_document.assert_not_awaited()

    async def test_group_start_sends_help_without_a_keyboard(self):
        self.allow_group_admin()
        await bot.start(self.update(GROUP_ADMIN_ID, text="/start"), self.context)
        call = self.api.send_message.await_args
        self.assertIsNone(call.kwargs.get("reply_markup"))
        self.assertIn("/bugun", call.kwargs["text"])
        self.assertIn("/jadval", call.kwargs["text"])
        self.api.get_chat_member.assert_not_awaited()

    async def test_private_configured_admin_gets_admin_keyboard_from_start(self):
        await bot.start(self.update(GLOBAL_ADMIN_ID, text="/start", chat_id=GLOBAL_ADMIN_ID,
                                    chat_type=Chat.PRIVATE), self.context)
        markup = self.api.send_message.await_args.kwargs["reply_markup"]
        labels = [button.text for row in markup.keyboard for button in row]
        self.assertIn("⚙️ Admin paneli", labels)

    def test_first_group_subscription_replaces_missing_or_private_target(self):
        for linked, chat_type in ((None, Chat.GROUP), (GLOBAL_ADMIN_ID, Chat.SUPERGROUP)):
            with self.subTest(linked=linked):
                self.store.state["chat_id"] = linked
                self.store.state["last_announced_date"] = DAY.isoformat()
                self.store.state["last_message_id"] = 500
                self.store.save_state.reset_mock()
                bot.remember_group(Chat(id=GROUP_ID, type=chat_type))
                self.assertEqual(self.store.state["chat_id"], GROUP_ID)
                self.assertIsNone(self.store.state["last_announced_date"])
                self.assertIsNone(self.store.state["last_message_id"])
                self.store.save_state.assert_called_once()

    def test_existing_group_subscription_is_not_claimed_by_another_group(self):
        for linked in (GROUP_ID, -100987654):
            with self.subTest(linked=linked):
                self.store.state["chat_id"] = linked
                self.store.state["last_announced_date"] = DAY.isoformat()
                before = dict(self.store.state)
                bot.remember_group(Chat(id=GROUP_ID, type=Chat.SUPERGROUP))
                self.assertEqual(self.store.state, before)
                self.store.save_state.assert_not_called()

    def test_private_chat_and_channel_do_not_subscribe_for_daily_messages(self):
        self.store.state["chat_id"] = None
        before = dict(self.store.state)
        for chat in (Chat(id=GLOBAL_ADMIN_ID, type=Chat.PRIVATE),
                     Chat(id=-100987654, type=Chat.CHANNEL)):
            with self.subTest(chat_type=chat.type):
                bot.remember_group(chat)
                self.assertEqual(self.store.state, before)
                self.store.save_state.assert_not_called()

    async def test_group_duty_and_schedule_commands_register_daily_target(self):
        for handler in (bot.bugun, bot.jadval):
            with self.subTest(handler=handler.__name__):
                self.store.state["chat_id"] = None
                self.store.state["last_announced_date"] = DAY.isoformat()
                self.store.save_state.reset_mock()
                await handler(self.update(text=f"/{handler.__name__}"), self.context)
                self.assertEqual(self.store.state["chat_id"], GROUP_ID)
                self.assertIsNone(self.store.state["last_announced_date"])
                self.store.save_state.assert_called_once()
                self.assertIsNone(self.api.send_message.await_args.kwargs.get("reply_markup"))

    async def test_added_bot_registers_group_as_member_or_administrator(self):
        for administrator in (False, True):
            with self.subTest(administrator=administrator):
                self.store.state["chat_id"] = None
                self.store.state["last_announced_date"] = DAY.isoformat()
                self.store.save_state.reset_mock()
                await bot.on_membership_change(self.membership_update(administrator=administrator), self.context)
                self.assertEqual(self.store.state["chat_id"], GROUP_ID)
                self.assertIsNone(self.store.state["last_announced_date"])
                self.store.save_state.assert_called_once()
        self.api.send_message.assert_not_awaited()

    async def test_bot_leaving_linked_group_clears_daily_subscription(self):
        self.store.state["last_announced_date"] = DAY.isoformat()
        self.store.state["last_message_id"] = 500
        await bot.on_membership_change(self.membership_update(added=False), self.context)
        self.assertIsNone(self.store.state["chat_id"])
        self.assertIsNone(self.store.state["last_announced_date"])
        self.assertIsNone(self.store.state["last_message_id"])
        self.store.save_state.assert_called_once()
        self.api.send_message.assert_not_awaited()

    async def test_bot_leaving_another_group_preserves_linked_group(self):
        before = dict(self.store.state)
        await bot.on_membership_change(self.membership_update(added=False, chat_id=-100987654), self.context)
        self.assertEqual(self.store.state, before)
        self.store.save_state.assert_not_called()

    async def test_group_migration_changes_target_and_preserves_round(self):
        new_group_id = -100222333
        updates = (
            self.update(chat_type=Chat.GROUP, migrate_to=new_group_id),
            self.update(chat_id=new_group_id, migrate_from=GROUP_ID),
        )
        for update in updates:
            with self.subTest(chat_id=update.effective_chat.id):
                self.store.state["chat_id"] = GROUP_ID
                self.store.state["last_announced_date"] = DAY.isoformat()
                self.store.state["last_message_id"] = 500
                self.store.save_state.reset_mock()
                preserved = {key: value for key, value in self.store.state.items()
                             if key not in {"chat_id", "last_message_id"}}
                await bot.on_group_migration(update, self.context)
                self.assertEqual(self.store.state["chat_id"], new_group_id)
                self.assertIsNone(self.store.state["last_message_id"])
                self.assertEqual({key: self.store.state[key] for key in preserved}, preserved)
                self.store.start_new_round_today.assert_not_called()
                self.store.save_state.assert_called_once()

    async def test_unrelated_group_migration_does_not_change_target(self):
        before = dict(self.store.state)
        await bot.on_group_migration(self.update(chat_id=-100987654, migrate_to=-100222333), self.context)
        self.assertEqual(self.store.state, before)
        self.store.save_state.assert_not_called()

    async def test_group_duty_and_schedule_tag_users_without_deleting_commands(self):
        for handler, handles in ((bot.bugun, ["@alice_one"]),
                                 (bot.jadval, ["@alice_one", "@bob_two"])):
            with self.subTest(handler=handler.__name__):
                self.api.send_message.reset_mock()
                self.api.delete_message.reset_mock()
                await handler(self.update(text=f"/{handler.__name__}"), self.context)
                self.assert_mentions(self.api.send_message.await_args, handles)
                self.api.delete_message.assert_not_awaited()

    async def test_private_duty_command_removes_the_incoming_request(self):
        await bot.bugun(self.update(text="/bugun", chat_id=REGULAR_USER_ID, chat_type=Chat.PRIVATE), self.context)
        self.assert_mentions(self.api.send_message.await_args, ["@alice_one"])
        self.api.delete_message.assert_awaited_once()

    async def test_group_admin_roster_history_and_panel_tag_users(self):
        self.allow_group_admin()
        for handler, handles in ((bot.royxat, ["@alice_one", "@bob_two"]),
                                 (bot.tarix, ["@alice_one"]),
                                 (bot.admin_panel, ["@alice_one"])):
            with self.subTest(handler=handler.__name__):
                self.api.send_message.reset_mock()
                await handler(self.update(GROUP_ADMIN_ID), self.context)
                self.assert_mentions(self.api.send_message.await_args, handles)

    async def test_group_admin_can_complete_and_manage_names(self):
        self.allow_group_admin()
        update = self.update(GROUP_ADMIN_ID)
        await bot.bajarildi(update, self.context)
        self.store.mark_today_done.assert_called_once_with(DAY)
        self.context.args = ["Dora", "Example", "@dora_four"]
        await bot.ism_qosh(update, self.context)
        self.store.add_name.assert_called_once_with("Dora Example", "@dora_four")
        self.assert_mentions(self.api.send_message.await_args, ["@dora_four"])
        self.context.args = ["2"]
        await bot.ism_ochir(update, self.context)
        self.store.remove_name.assert_called_once_with("bob")
        self.assert_mentions(self.api.send_message.await_args, ["@bob_two"])

    async def test_nonpositive_roster_number_never_deletes_a_member(self):
        self.allow_group_admin()
        for number in ("0", "-1"):
            with self.subTest(number=number):
                self.context.args = [number]
                await bot.ism_ochir(self.update(GROUP_ADMIN_ID), self.context)
                self.store.remove_name.assert_not_called()

    async def test_id_command_is_available_to_regular_users(self):
        await bot.show_id(self.update(text="/id", chat_id=REGULAR_USER_ID, chat_type=Chat.PRIVATE), self.context)
        self.assertIn(str(REGULAR_USER_ID), self.api.send_message.await_args.kwargs["text"])
        self.api.get_chat_member.assert_not_awaited()
        self.api.delete_message.assert_awaited_once()

    async def test_setup_rejects_private_chat_even_for_configured_admin(self):
        before = dict(self.store.state)
        await bot.setup(self.update(GLOBAL_ADMIN_ID, chat_id=GLOBAL_ADMIN_ID, chat_type=Chat.PRIVATE), self.context)
        self.assertEqual(self.store.state, before)
        self.assert_no_management_mutation()
        self.api.send_message.assert_awaited_once()

    async def test_setup_cannot_be_claimed_or_moved_by_unconfigured_group_owner(self):
        self.allow_group_admin(owner=True)
        for linked in (None, -100987654):
            with self.subTest(linked=linked):
                self.store.state["chat_id"] = linked
                before = dict(self.store.state)
                await bot.setup(self.update(GROUP_ADMIN_ID, text="/setup"), self.context)
                self.assertEqual(self.store.state, before)
                self.assert_no_management_mutation()

    async def test_configured_admin_can_link_or_move_group(self):
        for linked in (None, -100987654):
            with self.subTest(linked=linked):
                self.store.state["chat_id"] = linked
                self.store.start_new_round_today.reset_mock()
                self.api.send_message.reset_mock()
                await bot.setup(self.update(GLOBAL_ADMIN_ID, text="/setup"), self.context)
                self.assertEqual(self.store.state["chat_id"], GROUP_ID)
                self.store.start_new_round_today.assert_not_called()
                self.store.ensure_assignment_through.assert_called_with(DAY)
                self.assert_mentions(self.api.send_message.await_args, ["@alice_one"])

    async def test_repeated_setup_in_linked_group_preserves_current_round(self):
        self.allow_group_admin()
        before = {key: self.store.state[key] for key in (
            "round_order", "round_position", "round_number", "today_duty_id", "today_duty_date"
        )}
        await bot.setup(self.update(GROUP_ADMIN_ID, text="/setup"), self.context)
        self.store.start_new_round_today.assert_not_called()
        self.assertEqual({key: self.store.state[key] for key in before}, before)
        self.assert_mentions(self.api.send_message.await_args, ["@alice_one"])

    async def test_member_lookup_command_tags_member_and_removes_request(self):
        await bot.on_text(self.update(text="/bob_two", chat_id=REGULAR_USER_ID, chat_type=Chat.PRIVATE), self.context)
        self.assert_mentions(self.api.send_message.await_args, ["@bob_two"])
        self.assertIn("07.10.2026", self.api.send_message.await_args.kwargs["text"])
        self.api.delete_message.assert_awaited_once()

    async def test_known_command_is_not_handled_a_second_time_by_text_handler(self):
        for text in ("/jadval@DutyBot", "/id"):
            await bot.on_text(self.update(text=text), self.context)
        self.api.send_message.assert_not_awaited()
        self.api.delete_message.assert_not_awaited()

    async def test_commands_for_other_bot_are_ignored(self):
        await bot.on_text(self.update(text="/bob_two@OtherBot"), self.context)
        self.api.send_message.assert_not_awaited()
        self.api.delete_message.assert_not_awaited()

    async def test_unaddressed_group_chat_text_is_ignored(self):
        for text in ("Alice will clean tomorrow", "@DutyBotExtra @bob_two", "   "):
            await bot.on_text(self.update(text=text), self.context)
        self.api.send_message.assert_not_awaited()
        self.api.delete_message.assert_not_awaited()

    async def test_bot_mention_lookup_tags_member_without_deleting_plain_message(self):
        await bot.on_text(self.update(text="@DutyBot @bob_two", chat_id=REGULAR_USER_ID,
                                      chat_type=Chat.PRIVATE), self.context)
        self.assert_mentions(self.api.send_message.await_args, ["@bob_two"])
        self.api.delete_message.assert_not_awaited()

    async def test_reply_keyboard_schedule_still_works(self):
        await bot.on_text(self.update(text="🗓 Jadval"), self.context)
        self.assert_mentions(self.api.send_message.await_args, ["@alice_one", "@bob_two"])

    async def test_group_member_lookups_are_ignored(self):
        for text in ("/bob_two", "@DutyBot @bob_two"):
            await bot.on_text(self.update(text=text), self.context)
        self.api.send_message.assert_not_awaited()
        self.api.delete_message.assert_not_awaited()

    async def test_public_schedule_callback_edits_with_mention_entities(self):
        await bot.on_button(self.update(callback_data="ui:jadval"), self.context)
        self.assert_mentions(self.api.edit_message_text.await_args, ["@alice_one", "@bob_two"])
        self.api.answer_callback_query.assert_awaited_once()

    async def test_group_admin_callbacks_use_group_permissions_and_entities(self):
        self.allow_group_admin()
        for data, handles in (("ui:admin", ["@alice_one"]),
                              ("adm:jadval", ["@alice_one", "@bob_two"]),
                              (f"ui:reset_yes:{DAY}:7", ["@alice_one"])):
            with self.subTest(data=data):
                self.api.edit_message_text.reset_mock()
                await bot.on_button(self.update(GROUP_ADMIN_ID, callback_data=data), self.context)
                self.assert_mentions(self.api.edit_message_text.await_args, handles)
        self.store.start_new_round_today.assert_called_once_with(DAY)

    async def test_unauthorized_callbacks_alert_and_do_not_mutate(self):
        for data in ("ui:admin", "ui:reset", "ui:reset_yes", "adm:jadval", f"done:{DAY}:alice"):
            with self.subTest(data=data):
                self.api.answer_callback_query.reset_mock()
                self.api.edit_message_text.reset_mock()
                await bot.on_button(self.update(callback_data=data), self.context)
                self.api.answer_callback_query.assert_awaited_once()
                self.assertTrue(self.api.answer_callback_query.await_args.kwargs.get("show_alert"))
                self.api.edit_message_text.assert_not_awaited()
                self.assert_no_management_mutation()

    async def test_old_reset_confirmation_never_starts_a_round(self):
        self.allow_group_admin()
        for data in ("ui:reset_yes", "ui:reset_yes:2026-10-04:7", f"ui:reset_yes:{DAY}:6"):
            with self.subTest(data=data):
                self.api.answer_callback_query.reset_mock()
                await bot.on_button(self.update(GROUP_ADMIN_ID, callback_data=data), self.context)
                self.assertTrue(self.api.answer_callback_query.await_args.kwargs.get("show_alert"))
                self.api.edit_message_text.assert_not_awaited()
                self.assert_no_management_mutation()

    async def test_reset_confirmation_can_only_be_used_for_its_original_round(self):
        self.allow_group_admin()
        await bot.on_button(self.update(GROUP_ADMIN_ID, callback_data="ui:reset"), self.context)
        markup = self.api.edit_message_text.await_args.kwargs["reply_markup"]
        confirm = markup.inline_keyboard[0][0].callback_data
        await bot.on_button(self.update(GROUP_ADMIN_ID, callback_data=confirm), self.context)
        self.assertEqual(self.store.state["round_number"], 8)
        self.assertIn("Davra: 8", bot.duty_message())
        self.assertIn("Davra: 8", bot.schedule_text())
        self.assert_mentions(self.api.edit_message_text.await_args, ["@alice_one"])
        self.api.answer_callback_query.reset_mock()
        await bot.on_button(self.update(GROUP_ADMIN_ID, callback_data=confirm), self.context)
        self.store.start_new_round_today.assert_called_once_with(DAY)
        self.assertTrue(self.api.answer_callback_query.await_args.kwargs.get("show_alert"))

    async def test_group_admin_cannot_use_done_button_for_old_date_or_person(self):
        self.allow_group_admin()
        for data in ("done:2026-10-04:alice", f"done:{DAY}:bob", "done:malformed"):
            with self.subTest(data=data):
                self.api.answer_callback_query.reset_mock()
                await bot.on_button(self.update(GROUP_ADMIN_ID, callback_data=data), self.context)
                self.assertTrue(self.api.answer_callback_query.await_args.kwargs.get("show_alert"))
                self.store.mark_today_done.assert_not_called()

    async def test_group_admin_can_complete_current_duty_from_callback(self):
        self.allow_group_admin()
        await bot.on_button(self.update(GROUP_ADMIN_ID, callback_data=f"done:{DAY}:alice"), self.context)
        self.store.mark_today_done.assert_called_once_with(DAY)
        self.api.edit_message_reply_markup.assert_awaited_once()
        self.api.send_message.assert_awaited_once()

    async def test_daily_announcement_tags_member_and_is_idempotent(self):
        with patch.object(bot, "date_at_eight_or_later", return_value=True):
            await bot.publish_today_if_due(self.context.application)
            self.assert_mentions(self.api.send_message.await_args, ["@alice_one"])
            self.assertNotIn("reply_markup", self.api.send_message.await_args.kwargs)
            self.assertEqual(self.store.state["last_announced_date"], DAY.isoformat())
            self.assertEqual(self.store.state["last_message_id"], 501)
            await bot.publish_today_if_due(self.context.application)
        self.api.send_message.assert_awaited_once()
        self.store.save_state.assert_called_once()

    async def test_daily_announcement_does_not_use_legacy_private_target(self):
        self.store.state["chat_id"] = GLOBAL_ADMIN_ID
        with patch.object(bot, "date_at_eight_or_later", return_value=True):
            await bot.publish_today_if_due(self.context.application)
        self.api.send_message.assert_not_awaited()
        self.assertIsNone(self.store.state["last_announced_date"])
        self.store.save_state.assert_not_called()

    async def test_failed_announcement_is_not_recorded_as_delivered(self):
        self.api.send_message.side_effect = TelegramError("offline send failure")
        with (
            patch.object(bot, "date_at_eight_or_later", return_value=True),
            self.assertLogs("navbatchilik", level="ERROR"),
        ):
            await bot.publish_today_if_due(self.context.application)
        self.assertIsNone(self.store.state["last_announced_date"])
        self.assertIsNone(self.store.state["last_message_id"])
        self.store.save_state.assert_not_called()

    async def test_admin_notifications_tag_member_and_never_fall_back_to_group(self):
        await bot.notify_admins(self.context.application, "🧹 Navbatchi @alice_one")
        self.assertEqual(self.api.send_message.await_args.kwargs["chat_id"], GLOBAL_ADMIN_ID)
        self.assert_mentions(self.api.send_message.await_args, ["@alice_one"])
        self.api.send_message.reset_mock()
        with patch.object(bot, "ADMIN_IDS", set()):
            await bot.notify_admins(self.context.application, "🧹 Navbatchi @alice_one")
        self.api.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
