"""Offline regressions for the inline admin people-management workflow."""

import copy
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from telegram import Chat, ChatMemberMember, ForceReply, InlineKeyboardMarkup, Message, User

import test_bot as fixtures


GLOBAL_ADMIN_ID = fixtures.GLOBAL_ADMIN_ID
GROUP_ADMIN_ID = fixtures.GROUP_ADMIN_ID
GROUP_ID = fixtures.GROUP_ID
REGULAR_USER_ID = fixtures.REGULAR_USER_ID
bot = fixtures.bot


class PeopleManagementTests(unittest.IsolatedAsyncioTestCase):
    # Reuse Telegram fixtures without inheriting the unrelated baseline tests.
    user = staticmethod(fixtures.BotTests.user)
    update = fixtures.BotTests.update
    allow_group_admin = fixtures.BotTests.allow_group_admin

    def setUp(self):
        fixtures.BotTests.setUp(self)
        self.context.user_data = {}
        self.store.add_name = MagicMock(side_effect=self.add_person)
        self.store.rename_name = MagicMock(side_effect=self.rename_person)
        self.store.remove_name = MagicMock(side_effect=self.remove_person)
        self.next_person = 1

    def add_person(self, name, username=""):
        if any(person["name"].casefold() == name.casefold() for person in self.store.names):
            raise ValueError("Bu ism allaqachon mavjud.")
        person = {"id": f"new-{self.next_person}", "name": name, "username": username}
        self.next_person += 1
        self.store.names.append(person)
        return person

    def rename_person(self, member_id, name):
        person = self.store.member(member_id)
        if person is None:
            raise ValueError("Odam topilmadi.")
        if any(other["id"] != member_id and other["name"].casefold() == name.casefold()
               for other in self.store.names):
            raise ValueError("Bu ism allaqachon mavjud.")
        person["name"] = name
        return person

    def remove_person(self, member_id):
        person = self.store.member(member_id)
        if person is None:
            raise ValueError("Odam topilmadi.")
        self.store.names.remove(person)
        return person, member_id == self.store.state["today_duty_id"]

    def retain_deleted_history(self, member_id):
        """Match PostgresStore.member's fallback to removed historical members."""
        historical = copy.deepcopy(self.store.member(member_id))
        self.store.names = [person for person in self.store.names if person["id"] != member_id]
        active_lookup = self.store.member.side_effect
        self.store.member.side_effect = lambda candidate: historical if candidate == member_id else active_lookup(candidate)
        return historical

    def private_update(self, text="", callback_data=None, reply_to=None):
        return self.update(
            GLOBAL_ADMIN_ID, text=text, callback_data=callback_data, reply_to=reply_to,
            chat_id=GLOBAL_ADMIN_ID, chat_type=Chat.PRIVATE,
        )

    @staticmethod
    def callbacks(markup):
        return [button.callback_data for row in markup.inline_keyboard for button in row]

    def assert_no_member_mutation(self):
        self.store.add_name.assert_not_called()
        self.store.rename_name.assert_not_called()
        self.store.remove_name.assert_not_called()
        self.store.start_new_round_today.assert_not_called()

    def bot_prompt(self, message_id, chat_id=GROUP_ID, bot_id=None, is_bot=True):
        message = Message(
            message_id=message_id,
            date=datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc),
            chat=Chat(id=chat_id, type=Chat.PRIVATE if chat_id > 0 else Chat.SUPERGROUP),
            from_user=User(id=self.api.id if bot_id is None else bot_id,
                           first_name="Duty Bot", is_bot=is_bot),
            text="Enter a name",
        )
        message.set_bot(self.api)
        return message

    async def start_form(self, action="add", member_id="bob", group=False):
        callback = "people:add" if action == "add" else f"people:rename:{member_id}"
        if group:
            self.allow_group_admin()
            update = self.update(GROUP_ADMIN_ID, callback_data=callback)
        else:
            update = self.private_update(callback_data=callback)
        await bot.on_button(update, self.context)
        pending = self.context.user_data["people_pending"]
        self.assertEqual(pending["action"], action)
        self.assertEqual(pending["chat_id"], update.effective_chat.id)
        self.assertTrue(pending["nonce"])
        self.assertEqual(pending["prompt_id"], 501)
        if action == "rename":
            self.assertEqual(pending["member_id"], member_id)
        call = next(call for call in reversed(self.api.send_message.await_args_list)
                    if isinstance(call.kwargs.get("reply_markup"), ForceReply))
        self.assertIsNone(call.kwargs.get("parse_mode"))
        return pending

    def latest_sent_inline(self):
        return next(call for call in reversed(self.api.send_message.await_args_list)
                    if isinstance(call.kwargs.get("reply_markup"), InlineKeyboardMarkup))

    def test_admin_panel_has_people_management_button(self):
        self.assertIn("people:list", self.callbacks(bot.admin_keyboard()))

    async def test_people_command_sends_roster_and_bottom_actions(self):
        await bot.odamlar(self.private_update(text="/odamlar"), self.context)
        call = self.latest_sent_inline()
        markup = call.kwargs["reply_markup"]
        callbacks = self.callbacks(markup)
        for member_id in ("alice", "bob", "carol"):
            self.assertIn(f"people:person:{member_id}", callbacks)
        for action in ("people:add", "people:edit", "people:delete"):
            self.assertIn(action, callbacks)
        self.assertTrue(all(button.callback_data.startswith("people:person:")
                            for row in markup.inline_keyboard[:3] for button in row))
        self.assertIsNone(call.kwargs.get("parse_mode"))
        self.assert_no_member_mutation()

    async def test_people_command_is_admin_only(self):
        await bot.odamlar(self.update(text="/odamlar"), self.context)
        self.api.send_message.assert_awaited_once()
        self.assert_no_member_mutation()

    async def test_inline_roster_edit_and_delete_selectors_use_member_ids(self):
        for action, expected in (("list", "person"), ("edit", "rename"), ("delete", "remove")):
            with self.subTest(action=action):
                self.api.edit_message_text.reset_mock()
                await bot.on_button(self.private_update(callback_data=f"people:{action}"), self.context)
                call = self.api.edit_message_text.await_args
                callbacks = self.callbacks(call.kwargs["reply_markup"])
                for member_id in ("alice", "bob", "carol"):
                    self.assertIn(f"people:{expected}:{member_id}", callbacks)
                self.assertIsNone(call.kwargs.get("parse_mode"))
        self.assert_no_member_mutation()

    async def test_person_detail_offers_rename_remove_and_roster(self):
        await bot.on_button(self.private_update(callback_data="people:person:bob"), self.context)
        call = self.api.edit_message_text.await_args
        self.assertIn("Bob Example", call.kwargs["text"])
        callbacks = self.callbacks(call.kwargs["reply_markup"])
        for value in ("people:rename:bob", "people:remove:bob", "people:list"):
            self.assertIn(value, callbacks)
        self.assert_no_member_mutation()

    async def test_all_people_callbacks_require_admin_permission(self):
        values = (
            "people:list", "people:edit", "people:delete", "people:person:bob",
            "people:add", "people:rename:bob", "people:remove:bob",
            "people:remove_yes:bob", "people:cancel:other-session",
        )
        for value in values:
            with self.subTest(callback=value):
                self.api.answer_callback_query.reset_mock()
                await bot.on_button(self.update(callback_data=value), self.context)
                self.api.answer_callback_query.assert_awaited_once()
                self.assertTrue(self.api.answer_callback_query.await_args.kwargs.get("show_alert"))
                self.assert_no_member_mutation()
        self.api.edit_message_text.assert_not_awaited()
        self.api.send_message.assert_not_awaited()
        self.assertNotIn("people_pending", self.context.user_data)

    async def test_private_add_accepts_name_and_optional_username_preserving_round(self):
        before = copy.deepcopy(self.store.state)
        await self.start_form()
        await bot.on_text(self.private_update(text="Dora Example @dora_four"), self.context)
        self.store.add_name.assert_called_once_with("Dora Example", "@dora_four")
        self.assertNotIn("people_pending", self.context.user_data)
        self.assertEqual(self.store.state, before)
        self.store.start_new_round_today.assert_not_called()
        callbacks = self.callbacks(self.latest_sent_inline().kwargs["reply_markup"])
        self.assertIn("people:person:new-1", callbacks)

    async def test_private_add_accepts_name_without_username(self):
        await self.start_form()
        await bot.on_text(self.private_update(text="Dora Example"), self.context)
        self.store.add_name.assert_called_once_with("Dora Example", "")
        self.assertNotIn("people_pending", self.context.user_data)

    async def test_rename_targets_id_and_preserves_username_and_round(self):
        before = copy.deepcopy(self.store.state)
        await self.start_form(action="rename", member_id="bob")
        self.store.names.reverse()
        await bot.on_text(self.private_update(text="Bob Renamed"), self.context)
        self.store.rename_name.assert_called_once_with("bob", "Bob Renamed")
        self.assertEqual(self.store.member("bob")["username"], "@bob_two")
        self.assertEqual(self.store.member("alice")["name"], "Alice Example")
        self.assertEqual(self.store.state, before)
        self.store.start_new_round_today.assert_not_called()
        self.assertNotIn("people_pending", self.context.user_data)
        self.assertIn("Bob Renamed", self.latest_sent_inline().kwargs["text"])

    async def test_delete_requires_confirmation_and_targets_id_after_reordering(self):
        before = copy.deepcopy(self.store.state)
        await bot.on_button(self.private_update(callback_data="people:remove:bob"), self.context)
        self.store.remove_name.assert_not_called()
        callbacks = self.callbacks(self.api.edit_message_text.await_args.kwargs["reply_markup"])
        self.assertIn("people:remove_yes:bob", callbacks)
        self.store.names.reverse()
        await bot.on_button(self.private_update(callback_data="people:remove_yes:bob"), self.context)
        self.store.remove_name.assert_called_once_with("bob")
        self.assertIsNone(self.store.member("bob"))
        self.assertIsNotNone(self.store.member("alice"))
        self.assertEqual(self.store.state, before)
        self.store.start_new_round_today.assert_not_called()
        callbacks = self.callbacks(self.api.edit_message_text.await_args.kwargs["reply_markup"])
        self.assertNotIn("people:person:bob", callbacks)

    async def test_repeated_delete_confirmation_cannot_remove_another_person(self):
        await bot.on_button(self.private_update(callback_data="people:remove_yes:bob"), self.context)
        self.api.answer_callback_query.reset_mock()
        await bot.on_button(self.private_update(callback_data="people:remove_yes:bob"), self.context)
        self.store.remove_name.assert_called_once_with("bob")
        self.assertEqual({person["id"] for person in self.store.names}, {"alice", "carol"})
        self.assertTrue(self.api.answer_callback_query.await_args.kwargs.get("show_alert"))

    async def test_stale_member_buttons_fail_without_mutating(self):
        self.store.names = [person for person in self.store.names if person["id"] != "bob"]
        for action in ("person", "rename", "remove", "remove_yes"):
            with self.subTest(action=action):
                self.api.answer_callback_query.reset_mock()
                await bot.on_button(self.private_update(callback_data=f"people:{action}:bob"), self.context)
                self.assertTrue(self.api.answer_callback_query.await_args.kwargs.get("show_alert"))
                self.assert_no_member_mutation()
        self.assertNotIn("people_pending", self.context.user_data)

    async def test_deleted_historical_member_cannot_be_managed_by_stale_buttons(self):
        historical = self.retain_deleted_history("bob")
        self.assertEqual(self.store.member("bob"), historical)
        for action in ("person", "rename", "remove", "remove_yes"):
            with self.subTest(action=action):
                self.api.answer_callback_query.reset_mock()
                await bot.on_button(self.private_update(callback_data=f"people:{action}:bob"), self.context)
                self.assertTrue(self.api.answer_callback_query.await_args.kwargs.get("show_alert"))
                self.assert_no_member_mutation()
                self.assertNotIn("people_pending", self.context.user_data)
        self.api.edit_message_text.assert_not_awaited()
        self.api.send_message.assert_not_awaited()

    async def test_rename_input_for_deleted_historical_member_closes_form_and_refreshes_roster(self):
        before = copy.deepcopy(self.store.state)
        await self.start_form(action="rename", member_id="bob")
        historical = self.retain_deleted_history("bob")
        self.assertEqual(self.store.member("bob"), historical)
        await bot.on_text(self.private_update(text="Bob Renamed"), self.context)
        self.store.rename_name.assert_not_called()
        self.assertNotIn("people_pending", self.context.user_data)
        callbacks = self.callbacks(self.latest_sent_inline().kwargs["reply_markup"])
        self.assertNotIn("people:person:bob", callbacks)
        self.assertIn("people:person:alice", callbacks)
        self.assertEqual(self.store.state, before)
        self.assert_no_member_mutation()

    async def test_invalid_add_input_keeps_form_for_retry(self):
        await self.start_form()
        invalid = ("   ", "a" * 101, "Dora @abcd", "Dora @" + "a" * 33,
                   "Dora @dora-too", "Dora @dora_é", "@dora_four")
        for text in invalid:
            with self.subTest(text=text):
                self.api.send_message.reset_mock()
                await bot.on_text(self.private_update(text=text), self.context)
                self.store.add_name.assert_not_called()
                self.assertEqual(self.context.user_data["people_pending"]["action"], "add")
                self.api.send_message.assert_awaited()
        await bot.on_text(self.private_update(text="Dora Example @dora_four"), self.context)
        self.store.add_name.assert_called_once_with("Dora Example", "@dora_four")
        self.assertNotIn("people_pending", self.context.user_data)

    async def test_duplicate_add_and_rename_keep_form_for_retry(self):
        for action, duplicate, valid in (("add", "Alice Example", "Dora Example"),
                                         ("rename", "Alice Example", "Bob Renamed")):
            with self.subTest(action=action):
                await self.start_form(action=action)
                await bot.on_text(self.private_update(text=duplicate), self.context)
                self.assertEqual(self.context.user_data["people_pending"]["action"], action)
                await bot.on_text(self.private_update(text=valid), self.context)
                self.assertNotIn("people_pending", self.context.user_data)

    async def test_permission_is_rechecked_when_group_admin_submits(self):
        pending = await self.start_form(group=True)
        self.api.get_chat_member.return_value = ChatMemberMember(self.user(GROUP_ADMIN_ID))
        self.api.get_chat_member.reset_mock()
        await bot.on_text(self.update(GROUP_ADMIN_ID, text="Dora Example",
                                     reply_to=self.bot_prompt(pending["prompt_id"])), self.context)
        self.api.get_chat_member.assert_any_await(chat_id=GROUP_ID, user_id=GROUP_ADMIN_ID)
        self.assert_no_member_mutation()

    async def test_group_add_requires_reply_to_exact_bot_prompt(self):
        pending = await self.start_form(group=True)
        wrong_replies = (
            None,
            self.bot_prompt(pending["prompt_id"] + 1),
            self.bot_prompt(pending["prompt_id"], bot_id=1234),
        )
        for reply in wrong_replies:
            with self.subTest(reply_id=reply.message_id if reply else None):
                await bot.on_text(self.update(GROUP_ADMIN_ID, text="Dora Example", reply_to=reply), self.context)
                self.assert_no_member_mutation()
                self.assertIn("people_pending", self.context.user_data)
        await bot.on_text(self.update(GROUP_ADMIN_ID, text="Dora Example",
                                     reply_to=self.bot_prompt(pending["prompt_id"])), self.context)
        self.store.add_name.assert_called_once_with("Dora Example", "")
        self.assertNotIn("people_pending", self.context.user_data)

    async def test_stale_explicit_private_reply_cannot_submit_a_newer_form(self):
        await self.start_form(action="rename", member_id="bob")
        self.api.send_message.return_value = SimpleNamespace(message_id=502)
        await bot.on_button(self.private_update(callback_data="people:rename:alice"), self.context)
        self.assertEqual(self.context.user_data["people_pending"]["prompt_id"], 502)
        await bot.on_text(self.private_update(text="Name for Bob",
                                             reply_to=self.bot_prompt(501, chat_id=GLOBAL_ADMIN_ID)), self.context)
        self.store.rename_name.assert_not_called()
        self.assertEqual(self.context.user_data["people_pending"]["member_id"], "alice")
        await bot.on_text(self.private_update(text="Alice Renamed"), self.context)
        self.store.rename_name.assert_called_once_with("alice", "Alice Renamed")
        self.assertEqual(self.store.member("bob")["name"], "Bob Example")

    async def test_form_submission_in_other_chat_is_ignored(self):
        pending = await self.start_form()
        await bot.on_text(self.update(GLOBAL_ADMIN_ID, text="Dora Example", chat_id=-100777888,
                                     reply_to=self.bot_prompt(pending["prompt_id"], chat_id=-100777888)),
                          self.context)
        self.assert_no_member_mutation()
        self.assertEqual(self.context.user_data["people_pending"]["chat_id"], GLOBAL_ADMIN_ID)

    async def test_another_users_text_cannot_submit_original_users_form(self):
        await self.start_form()
        other_context = SimpleNamespace(bot=self.api, args=[], application=self.context.application, user_data={})
        await bot.on_text(self.update(REGULAR_USER_ID, text="Dora Example", chat_id=GLOBAL_ADMIN_ID,
                                     chat_type=Chat.PRIVATE), other_context)
        self.assert_no_member_mutation()
        self.assertIn("people_pending", self.context.user_data)

    async def test_cancel_clears_matching_session_and_returns_roster(self):
        pending = await self.start_form()
        await bot.on_button(self.private_update(callback_data=f"people:cancel:{pending['nonce']}"), self.context)
        self.assertNotIn("people_pending", self.context.user_data)
        self.assertIn("people:add", self.callbacks(self.api.edit_message_text.await_args.kwargs["reply_markup"]))
        await bot.on_text(self.private_update(text="Dora Example"), self.context)
        self.assert_no_member_mutation()

    async def test_stale_nonce_and_other_chat_cannot_cancel_current_session(self):
        pending = await self.start_form()
        for update in (
            self.private_update(callback_data="people:cancel:stale-nonce"),
            self.update(GLOBAL_ADMIN_ID, callback_data=f"people:cancel:{pending['nonce']}", chat_id=-100777888),
        ):
            with self.subTest(chat_id=update.effective_chat.id):
                await bot.on_button(update, self.context)
                self.assertEqual(self.context.user_data["people_pending"]["nonce"], pending["nonce"])
        self.assert_no_member_mutation()

    async def test_another_admin_cannot_cancel_original_users_session(self):
        pending = await self.start_form(group=True)
        other_context = SimpleNamespace(bot=self.api, args=[], application=self.context.application, user_data={})
        await bot.on_button(self.update(GLOBAL_ADMIN_ID, callback_data=f"people:cancel:{pending['nonce']}"),
                            other_context)
        self.assertEqual(self.context.user_data["people_pending"]["nonce"], pending["nonce"])
        self.assert_no_member_mutation()

    async def test_pagination_limits_roster_buttons_and_keeps_actions_at_bottom(self):
        self.store.names = [{"id": f"member-{number}", "name": f"Person {number}", "username": ""}
                            for number in range(23)]
        for page, expected_ids in ((0, range(10)), (1, range(10, 20)), (2, range(20, 23))):
            with self.subTest(page=page):
                await bot.on_button(self.private_update(callback_data=f"people:list:{page}"), self.context)
                markup = self.api.edit_message_text.await_args.kwargs["reply_markup"]
                callbacks = self.callbacks(markup)
                person_callbacks = [value for value in callbacks if value.startswith("people:person:")]
                self.assertEqual(person_callbacks, [f"people:person:member-{number}" for number in expected_ids])
                for action in ("people:add", "people:edit", "people:delete"):
                    self.assertIn(action, callbacks)
                if page:
                    self.assertIn(f"people:list:{page - 1}", callbacks)
                if page < 2:
                    self.assertIn(f"people:list:{page + 1}", callbacks)
                last_person_row = max(index for index, row in enumerate(markup.inline_keyboard)
                                      if any(button.callback_data.startswith("people:person:") for button in row))
                action_row = next(index for index, row in enumerate(markup.inline_keyboard)
                                  if any(button.callback_data == "people:add" for button in row))
                self.assertGreater(action_row, last_person_row)

    async def test_selection_pagination_keeps_edit_or_delete_mode(self):
        self.store.names = [{"id": f"member-{number}", "name": f"Person {number}", "username": ""}
                            for number in range(23)]
        for mode, action in (("edit", "rename"), ("delete", "remove")):
            with self.subTest(mode=mode):
                await bot.on_button(self.private_update(callback_data=f"people:{mode}:1"), self.context)
                callbacks = self.callbacks(self.api.edit_message_text.await_args.kwargs["reply_markup"])
                self.assertIn(f"people:{mode}:0", callbacks)
                self.assertIn(f"people:{mode}:2", callbacks)
                self.assertEqual([value for value in callbacks if value.startswith(f"people:{action}:")],
                                 [f"people:{action}:member-{number}" for number in range(10, 20)])

    async def test_special_characters_are_literal_in_forms_lists_and_details(self):
        name = "<Alice & Bob>"
        await self.start_form()
        await bot.on_text(self.private_update(text=name), self.context)
        self.store.add_name.assert_called_once_with(name, "")
        list_call = self.latest_sent_inline()
        self.assertIn(name, list_call.kwargs["text"])
        self.assertIsNone(list_call.kwargs.get("parse_mode"))
        await bot.on_button(self.private_update(callback_data="people:person:new-1"), self.context)
        detail_call = self.api.edit_message_text.await_args
        self.assertIn(name, detail_call.kwargs["text"])
        self.assertIsNone(detail_call.kwargs.get("parse_mode"))
        await self.start_form(action="rename", member_id="new-1")
        prompt_call = next(call for call in reversed(self.api.send_message.await_args_list)
                           if isinstance(call.kwargs.get("reply_markup"), ForceReply))
        self.assertIn(name, prompt_call.kwargs["text"])
        self.assertIsNone(prompt_call.kwargs.get("parse_mode"))

    async def test_renamed_current_name_stays_literal_in_duty_and_admin_messages_with_real_mentions(self):
        name = "<Alice & Bob>"
        await self.start_form(action="rename", member_id="alice")
        await bot.on_text(self.private_update(text=name), self.context)
        self.assertEqual(self.store.member("alice")["name"], name)
        # The real store also updates today's persisted duty snapshot on rename.
        self.store.history[fixtures.DAY.isoformat()]["name"] = name
        for handler in (bot.bugun, bot.admin_panel):
            with self.subTest(handler=handler.__name__):
                self.api.send_message.reset_mock()
                await handler(self.private_update(text=f"/{handler.__name__}"), self.context)
                call = self.api.send_message.await_args
                self.assertIn(name, call.kwargs["text"])
                self.assertNotIn("<a ", call.kwargs["text"])
                self.assertIn("@alice_one", call.kwargs["text"])
                fixtures.BotTests.assert_mentions(self, call, ["@alice_one"])


if __name__ == "__main__":
    unittest.main()
