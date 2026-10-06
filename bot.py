"""Navbatchilik Telegram boti. Python 3.12+, python-telegram-bot 21+."""
import asyncio
import io
import json
import logging
import os
import re
import secrets
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from telegram import (
    BotCommand, BotCommandScopeAllChatAdministrators, BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats, BotCommandScopeChat, BotCommandScopeChatMember,
    BotCommandScopeDefault, ForceReply, InlineKeyboardButton, InlineKeyboardMarkup,
    MenuButtonCommands, MessageEntity, ReplyKeyboardMarkup, Update,
)
from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import BadRequest, NetworkError, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from data_store import DUTY_DAYS, PostgresStore

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("navbatchilik")
logging.getLogger("httpx").setLevel(logging.WARNING)

TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMIN_IDS_RAW = os.environ.get("ADMIN_IDS", "").strip()
try:
    ADMIN_IDS = {int(part.strip()) for part in ADMIN_IDS_RAW.split(",") if part.strip()}
except ValueError as exc:
    raise RuntimeError("ADMIN_IDS faqat vergul bilan ajratilgan Telegram ID'lar bo'lishi kerak") from exc

TZ = ZoneInfo("Asia/Tashkent")
ANNOUNCE_AT = time(8, 0, tzinfo=TZ)
STORE = PostgresStore()
KNOWN_COMMANDS = {
    "start", "setup", "bugun", "jadval", "bajarildi", "admin",
    "royxat", "tarix", "zaxira", "ism_qosh", "ism_ochir",
    "odamlar", "bekor", "cancel", "elon",
}
MENTION_PATTERN = re.compile(r"(?<![\w@])@[A-Za-z0-9_]{5,32}(?![\w@-])")
PEOPLE_PAGE_SIZE = 10
PEOPLE_MENU_TEXTS = {
    "👤 Bugungi navbatchi", "🗓 Jadval", "📜 Ro'yxat", "📚 Tarix", "⚙️ Admin paneli",
}


def today() -> date:
    return datetime.now(TZ).date()


def now() -> datetime:
    return datetime.now(TZ)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def can_manage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user:
        return False
    if is_admin(user.id):
        return True
    chat = update.effective_chat
    if (
        user.is_bot
        or not chat
        or chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}
        or chat.id != STORE.state.get("chat_id")
    ):
        return False
    try:
        member = await context.bot.get_chat_member(chat_id=chat.id, user_id=user.id)
    except TelegramError as exc:
        log.warning("Guruh admin huquqini tekshirib bo'lmadi: %s", exc)
        return False
    return member.status in {ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR}


async def require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if await can_manage(update, context):
        return True
    user_id = update.effective_user.id if update.effective_user else "noma'lum"
    await send_plain(
        update, context,
        "Bu bo'lim faqat bot adminlari va ulangan guruh adminlari uchun.\n"
        f"Sizning Telegram ID: {user_id}. Bot egasi bu ID'ni ADMIN_IDS ga qo'shishi mumkin.",
    )
    return False


def mention_entities(text: str) -> list[MessageEntity]:
    """Mark usernames explicitly; Telegram offsets count UTF-16 code units."""
    return [
        MessageEntity(
            type=MessageEntity.MENTION,
            offset=len(text[:match.start()].encode("utf-16-le")) // 2,
            length=len(match.group()),
        )
        for match in MENTION_PATTERN.finditer(text)
    ]


def member_by_id(member_id: str | None) -> dict | None:
    return STORE.member(member_id)


def mention(person: dict | None) -> str:
    """Return the visible username; outgoing messages attach mention entities."""
    if not person:
        return ""
    handle = (person.get("username") or "").strip().lstrip("@")
    if not handle:
        return ""
    return f"@{handle}"


def display_person(person: dict | None, include_username: bool = True) -> str:
    if not person:
        return "Navbatchi belgilanmagan"
    handle = mention(person)
    if include_username and handle:
        return f"{person['name']} ({handle})"
    return person["name"]


def current_person() -> dict | None:
    member_id = STORE.state.get("today_duty_id")
    duty_date = STORE.state.get("today_duty_date")
    record = STORE.history.get(duty_date or "")
    if record and record.get("member_id") == member_id:
        return {
            "id": member_id,
            "name": record.get("name", "O'chirilgan a'zo"),
            "username": record.get("username", ""),
        }
    return member_by_id(member_id)


def ensure_today() -> None:
    STORE.ensure_assignment_through(today())


def remember_group(chat) -> bool:
    """Use the first real group; changing an existing group uses /setup."""
    if chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        return False
    linked = STORE.state.get("chat_id")
    if linked is not None and int(linked) < 0:
        return False
    STORE.state["last_announced_date"] = None
    STORE.state["last_message_id"] = None
    STORE.state["chat_id"] = chat.id
    STORE.save_state()
    return True


async def on_membership_change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    change = update.my_chat_member
    member = change.new_chat_member
    if member.status in {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR}:
        remember_group(change.chat)
        if change.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
            await register_group_commands(context.application, change.chat.id)
    elif (
        member.status in {ChatMemberStatus.LEFT, ChatMemberStatus.BANNED}
        and change.chat.id == STORE.state.get("chat_id")
    ):
        STORE.state["last_announced_date"] = None
        STORE.state["last_message_id"] = None
        STORE.state["chat_id"] = None
        STORE.save_state()


async def on_group_migration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    linked = STORE.state.get("chat_id")
    if message.migrate_to_chat_id and linked == message.chat_id:
        STORE.state["chat_id"] = message.migrate_to_chat_id
    elif message.migrate_from_chat_id and linked == message.migrate_from_chat_id:
        STORE.state["chat_id"] = message.chat_id
    else:
        return
    STORE.state["last_message_id"] = None
    STORE.save_state()
    await register_group_commands(context.application, STORE.state["chat_id"])


def current_duty_start() -> date:
    value = STORE.state.get("duty_started_date") or STORE.state.get("today_duty_date")
    try:
        return min(date.fromisoformat(value), today())
    except (TypeError, ValueError):
        return today()


def duty_date_text(day: date) -> str:
    return day.strftime("%d.%m.%Y")


def duty_message() -> str:
    ensure_today()
    person = current_person()
    duty_date = STORE.state.get("today_duty_date")
    if not person or not duty_date:
        return "⚠️ Bugungi navbatchi yo'q. Admin /ism_qosh bilan ro'yxatga odam qo'shsin."
    lines = [
        "🧹 BUGUNGI NAVBATCHI",
        f"Davra: {STORE.state.get('round_number', 1)} · {STORE.state.get('round_position', 0)}-navbat",
        "",
        f"👤 {person['name']}",
        f"📅 {duty_date_text(current_duty_start())}",
    ]
    handle = mention(person)
    if handle:
        lines.extend(["", f"👉 {handle}"])
    if STORE.state.get("today_duty_done"):
        lines.extend(["", "✅ Bajarildi deb belgilangan."])
    elif today() >= current_duty_start() + timedelta(days=DUTY_DAYS):
        lines.extend(["", "⏳ Ikki kunlik muddat tugagan. Bajarildi deb belgilanguncha navbatchi shu odam bo'lib qoladi."])
    return "\n".join(lines)


def scheduled_members() -> list[tuple[date, dict | None]]:
    ensure_today()
    state = STORE.state
    result: list[tuple[date, dict | None]] = []
    current_date = state.get("today_duty_date")
    next_day = today()
    if current_date == today().isoformat() and state.get("today_duty_id"):
        result.append((today(), current_person()))
        next_day = max(today() + timedelta(days=1), current_duty_start() + timedelta(days=DUTY_DAYS))
    position = int(state.get("round_position", 0))
    for member_id in state.get("round_order", [])[position:]:
        result.append((next_day, member_by_id(member_id)))
        next_day += timedelta(days=DUTY_DAYS)
    return result


def schedule_text() -> str:
    entries = scheduled_members()
    if not entries:
        return "✅ Joriy davrada qolgan navbatchi yo'q. Yangi davra joriy ikki kunlik navbat tugagach boshlanadi."
    lines = ["🗓 NAVBATCHILIK JADVALI", f"Davra: {STORE.state.get('round_number', 1)} · Har bir odamga {DUTY_DAYS} kun"]
    for number, (day, person) in enumerate(entries, 1):
        if day == today():
            heading = f"🔔 BUGUN · {duty_date_text(current_duty_start())}"
        elif day == today() + timedelta(days=1):
            heading = f"📅 ERTAGA · {duty_date_text(day)}"
        else:
            heading = f"📅 {duty_date_text(day)}"
        lines.append(f"{number}. {heading}\n   👤 {display_person(person)}")
    if STORE.state.get("today_duty_id") and not STORE.state.get("today_duty_done"):
        lines.extend([
            "",
            "⏳ Ikki kunlik navbatchilik bajarildi deb belgilanmasa, shu navbatchi muddatdan keyin ham qoladi "
            "va keyingi sanalar har bir qo'shimcha kun uchun bir kunga suriladi.",
        ])
    if STORE.state.get("round_position", 0) >= len(STORE.state.get("round_order", [])):
        lines.extend(["", "🔀 Davra tugagach, keyingi davra yangi tartibda boshlanadi."])
    return "\n\n".join(lines)


def roster_text() -> str:
    ensure_today()
    state = STORE.state
    round_order = state.get("round_order", [])
    cursor = int(state.get("round_position", 0))
    current_id = state.get("today_duty_id") if state.get("today_duty_date") == today().isoformat() else None
    lines = [
        f"📋 ISMLAR VA NAVBAT TARTIBI · {len(STORE.names)} kishi",
        f"Davra: {state.get('round_number', 1)} · Bugungi navbatchi davraning {state.get('round_position', 0)}-navbati",
        "",
    ]
    for number, person in enumerate(STORE.names, 1):
        member_id = person["id"]
        if member_id == current_id:
            status = "🔔 BUGUN"
        elif member_id in round_order and round_order.index(member_id) < cursor:
            status = "✅ O'tdi"
        elif member_id in round_order:
            status = "⏳ Kutilmoqda"
        else:
            status = "➕ Keyingi davrada"
        lines.append(f"{number}. {person['name']} {f'({mention(person)})' if mention(person) else ''}\n   {status}")
    return "\n\n".join(lines)


def history_text() -> str:
    ensure_today()
    lines = ["📚 OXIRGI 30 KUNLIK NAVBATCHILIK TARIXI", ""]
    for day_text, record in STORE.last_30_days(today()):
        if not record or not record.get("member_id"):
            lines.append(f"{date.fromisoformat(day_text).strftime('%d.%m.%Y')} — navbatchi yo'q")
            continue
        handle = (record.get("username") or "").strip().lstrip("@")
        suffix = " · bajarildi" if record.get("done") else ""
        tag = f" (@{handle})" if handle else ""
        lines.append(f"{date.fromisoformat(day_text).strftime('%d.%m.%Y')} — {record.get('name', 'Noma\'lum')}{tag}{suffix}")
    return "\n".join(lines)


def admin_keyboard() -> InlineKeyboardMarkup:
    rows = []
    member_id = STORE.state.get("today_duty_id")
    duty_date = STORE.state.get("today_duty_date")
    if member_id and duty_date == today().isoformat() and not STORE.state.get("today_duty_done"):
        rows.append([InlineKeyboardButton("✅ Bajarildi", callback_data=f"done:{duty_date}:{member_id}")])
    rows.extend([
        [InlineKeyboardButton("👥 Odamlarni boshqarish", callback_data="people:list")],
        [InlineKeyboardButton("🗓 Jadval", callback_data="adm:jadval")],
        [InlineKeyboardButton("🔄 Yangi davra", callback_data="ui:reset")],
    ])
    return InlineKeyboardMarkup(rows)


def people_page(mode: str = "list", page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    people = STORE.names
    last_page = max(0, (len(people) - 1) // PEOPLE_PAGE_SIZE)
    page = max(0, min(page, last_page))
    start = page * PEOPLE_PAGE_SIZE
    actions = {"list": "person", "edit": "rename", "delete": "remove"}
    instructions = {
        "list": "Odamni yoki quyidagi amalni tanlang.",
        "edit": "Ismini tahrirlash uchun odamni tanlang.",
        "delete": "O'chirish uchun odamni tanlang.",
    }
    lines = [f"👥 ODAMLARNI BOSHQARISH · {len(people)} kishi", instructions[mode], ""]
    rows = []
    for number, person in enumerate(people[start:start + PEOPLE_PAGE_SIZE], start + 1):
        handle = person.get("username") or ""
        lines.append(f"{number}. {person['name']}" + (f" ({handle})" if handle else ""))
        rows.append([InlineKeyboardButton(
            f"{number}. {person['name'][:60]}",
            callback_data=f"people:{actions[mode]}:{person['id']}",
        )])
    if not people:
        lines.append("Ro'yxat bo'sh. Odam qo'shish tugmasidan foydalaning.")
    if last_page:
        lines.extend(["", f"Sahifa: {page + 1}/{last_page + 1}"])
        navigation = []
        if page:
            navigation.append(InlineKeyboardButton("⬅️ Oldingi", callback_data=f"people:{mode}:{page - 1}"))
        if page < last_page:
            navigation.append(InlineKeyboardButton("Keyingi ➡️", callback_data=f"people:{mode}:{page + 1}"))
        rows.append(navigation)
    rows.extend([
        [InlineKeyboardButton("➕ Odam qo'shish", callback_data="people:add")],
        [InlineKeyboardButton("✏️ Tahrirlash", callback_data="people:edit"),
         InlineKeyboardButton("🗑 O'chirish", callback_data="people:delete")],
    ])
    if mode != "list":
        rows.append([InlineKeyboardButton("⬅️ Ro'yxat", callback_data="people:list")])
    rows.append([InlineKeyboardButton("⚙️ Admin paneli", callback_data="ui:admin")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def clear_people_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = getattr(context, "user_data", None)
    pending = data.get("people_pending") if data is not None else None
    if pending and pending["chat_id"] == update.effective_chat.id:
        data.pop("people_pending", None)


def active_member(member_id: str) -> dict | None:
    """Management only operates on the active roster, never history snapshots."""
    return next((person for person in STORE.names if person["id"] == member_id), None)


async def send_people_page(update: Update, context: ContextTypes.DEFAULT_TYPE, notice: str = "") -> None:
    text, markup = people_page()
    if notice:
        text = notice + "\n\n" + text
    await context.bot.send_message(
        chat_id=update.effective_chat.id, text=text, parse_mode=None,
        entities=mention_entities(text), reply_markup=markup,
    )


async def edit_people_message(query, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await query.edit_message_text(
            text, parse_mode=None, entities=mention_entities(text), reply_markup=markup,
        )
    except BadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def send_people_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, instruction: str) -> None:
    user = update.effective_user
    text = f"{user.full_name}, {instruction}\n\nBekor qilish: /bekor"
    prompt = await context.bot.send_message(
        chat_id=update.effective_chat.id, text=text, parse_mode=None,
        entities=[MessageEntity(
            type=MessageEntity.TEXT_MENTION, offset=0,
            length=len(user.full_name.encode("utf-16-le")) // 2, user=user,
        )],
        reply_markup=ForceReply(selective=True, input_field_placeholder="Ismni kiriting"),
    )
    context.user_data["people_pending"]["prompt_id"] = prompt.message_id


async def on_people_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not await can_manage(update, context):
        await query.answer("Faqat admin uchun", show_alert=True)
        return
    ensure_today()
    parts = query.data.split(":", 2)
    action = parts[1]
    arg = parts[2] if len(parts) > 2 else ""
    if action == "cancel":
        pending = context.user_data.get("people_pending")
        if not pending or pending["chat_id"] != update.effective_chat.id or pending["nonce"] != arg:
            await query.answer("Bu so'rov sizga tegishli emas yoki eskirgan.", show_alert=True)
            return
        clear_people_input(update, context)
        await query.answer("Bekor qilindi")
        await edit_people_message(query, *people_page())
        return
    if action in {"list", "edit", "delete"}:
        if arg and not re.fullmatch(r"\d{1,6}", arg):
            await query.answer("Sahifa topilmadi.", show_alert=True)
            return
        clear_people_input(update, context)
        await query.answer()
        await edit_people_message(query, *people_page(action, int(arg or "0")))
        return
    person = active_member(arg) if arg else None
    if action != "add" and (action not in {"person", "rename", "remove", "remove_yes"} or not person):
        await query.answer("Odam topilmadi. Ro'yxatni qayta oching.", show_alert=True)
        return
    clear_people_input(update, context)
    if action in {"add", "rename"}:
        nonce = secrets.token_hex(4)
        context.user_data["people_pending"] = {
            "action": action, "chat_id": update.effective_chat.id, "nonce": nonce,
            "member_id": person["id"] if person else None,
        }
        if action == "add":
            instruction = "yangi odamning ism va familiyasini yuboring. Username ixtiyoriy.\nMasalan: Ali Valiyev @ali_valiyev"
        else:
            instruction = f"{person['name']} uchun yangi ismni yuboring."
        await query.answer()
        await edit_people_message(query, instruction, InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Bekor qilish", callback_data=f"people:cancel:{nonce}")],
        ]))
        await send_people_prompt(update, context, instruction)
        return
    if action == "remove_yes":
        try:
            removed, was_today = STORE.remove_name(person["id"])
        except ValueError as exc:
            await query.answer(str(exc), show_alert=True)
            return
        await query.answer("O'chirildi")
        notice = f"✅ {removed['name']} ro'yxatdan o'chirildi."
        if was_today:
            notice += "\n⚠️ U bugungi navbatchi edi; adminlarga xabar berdim."
            await notify_admins(context.application, f"⚠️ Bugungi navbatchi {display_person(removed)} ro'yxatdan o'chirildi.")
        text, markup = people_page()
        await edit_people_message(query, notice + "\n\n" + text, markup)
        return
    await query.answer()
    rows = []
    if action == "person":
        text = f"👤 {person['name']}"
        if person.get("username"):
            text += f"\n{person['username']}"
        rows.extend([
            [InlineKeyboardButton("✏️ Tahrirlash", callback_data=f"people:rename:{person['id']}")],
            [InlineKeyboardButton("🗑 O'chirish", callback_data=f"people:remove:{person['id']}")],
        ])
    else:
        text = f"🗑 {person['name']} ro'yxatdan o'chirilsinmi?"
        rows.append([InlineKeyboardButton("✅ Ha, o'chirish", callback_data=f"people:remove_yes:{person['id']}")])
    rows.append([InlineKeyboardButton("⬅️ Ro'yxat", callback_data="people:list")])
    await edit_people_message(query, text, InlineKeyboardMarkup(rows))


async def handle_people_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    data = getattr(context, "user_data", None)
    pending = data.get("people_pending") if data is not None else None
    if not pending or pending["chat_id"] != update.effective_chat.id:
        return False
    message = update.effective_message
    reply = message.reply_to_message
    if reply or update.effective_chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
        if not reply or reply.message_id != pending.get("prompt_id") or not reply.from_user or reply.from_user.id != context.bot.id:
            return False
    if not await can_manage(update, context):
        clear_people_input(update, context)
        await require_admin(update, context)
        return True
    name = message.text.strip()
    handle = ""
    try:
        if "\n" in name or "\r" in name:
            raise ValueError("Ismni bitta qatorda yuboring.")
        if pending["action"] == "add":
            parts = name.split()
            if parts and parts[-1].startswith("@"):
                handle = parts.pop()
                if not re.fullmatch(r"@[A-Za-z0-9_]{5,32}", handle):
                    raise ValueError("Username noto'g'ri. Masalan: @ali_valiyev. Username ixtiyoriy.")
            name = " ".join(parts)
        if not name:
            raise ValueError("Ism bo'sh bo'lmasin. Ism va familiyani yuboring.")
        if len(name) > 100:
            raise ValueError("Ismni bitta qatorda, 100 ta belgidan oshirmay yuboring.")
        ensure_today()
        if pending["action"] == "add":
            person = STORE.add_name(name, handle)
            notice = f"✅ {person['name']} qo'shildi. Joriy davra davom etadi."
        else:
            if not active_member(pending["member_id"]):
                clear_people_input(update, context)
                await send_people_page(update, context, "⚠️ Bu odam ro'yxatdan o'chirilgan.")
                return True
            person = STORE.rename_name(pending["member_id"], name)
            notice = f"✅ Ism {person['name']} deb saqlandi. Navbat tartibi saqlandi."
    except ValueError as exc:
        await send_people_prompt(update, context, f"{exc}\nQayta yuboring.")
        return True
    clear_people_input(update, context)
    await send_people_page(update, context, notice)
    return True


def menu_keyboard(admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("👤 Bugungi navbatchi", callback_data="ui:bugun")],
        [InlineKeyboardButton("🗓 Jadval", callback_data="ui:jadval")],
    ]
    if admin:
        rows.append([InlineKeyboardButton("⚙️ Admin paneli", callback_data="ui:admin")])
    return InlineKeyboardMarkup(rows)


def reset_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "✅ Ha, yangi davra",
            callback_data=f"ui:reset_yes:{today().isoformat()}:{STORE.state.get('round_number', 1)}",
        )],
        [InlineKeyboardButton("❌ Bekor qilish", callback_data="ui:admin")],
    ])


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Ortga", callback_data="ui:menu")]
    ])


def main_keyboard(admin: bool = False) -> ReplyKeyboardMarkup:
    buttons = [
        ["👤 Bugungi navbatchi", "🗓 Jadval"],
    ]
    if admin:
        buttons.append(["⚙️ Admin paneli"])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def date_at_eight_or_later() -> bool:
    return now().time().replace(tzinfo=None) >= ANNOUNCE_AT.replace(tzinfo=None)


async def notify_admins(app: Application, text: str) -> None:
    for admin_id in sorted(ADMIN_IDS):
        try:
            await app.bot.send_message(chat_id=admin_id, text=text, parse_mode=None, entities=mention_entities(text))
        except TelegramError as exc:
            log.warning("Admin %s ga xabar yuborilmadi: %s", admin_id, exc)


async def publish_today_if_due(app: Application, *, force: bool = False) -> bool:
    lock = app.bot_data.setdefault("announcement_lock", asyncio.Lock())
    async with lock:
        ensure_today()
        if not force and not date_at_eight_or_later():
            return False
        key = today().isoformat()
        if not force and STORE.state.get("last_announced_date") == key:
            return False
        if not force:
            start = current_duty_start()
            block_start = start + timedelta(days=((today() - start).days // DUTY_DAYS) * DUTY_DAYS)
            try:
                last_announced = date.fromisoformat(STORE.state.get("last_announced_date"))
            except (TypeError, ValueError):
                last_announced = None
            if last_announced and block_start <= last_announced <= today():
                return False
        chat_id = STORE.state.get("chat_id")
        if not chat_id or int(chat_id) >= 0 or not STORE.state.get("today_duty_id"):
            return False
        assignment = (chat_id, key, STORE.state.get("round_number"), STORE.state.get("today_duty_id"))
        try:
            text = duty_message()
            sent = await app.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=None,
                entities=mention_entities(text),
                disable_notification=False,
            )
        except TelegramError:
            log.exception("Bugungi navbatchi xabarini yuborib bo'lmadi; keyingi tekshiruvda qayta uriniladi")
            return False
        current_assignment = (
            STORE.state.get("chat_id"), today().isoformat(),
            STORE.state.get("round_number"), STORE.state.get("today_duty_id"),
        )
        if current_assignment != assignment:
            # A reset or group switch while sending must not mark the new target delivered.
            log.info("E'lon yuborildi, ammo guruh yoki navbat o'zgardi; yangi e'lon hali kutilmoqda")
            return True
        STORE.state["last_announced_date"] = key
        STORE.state["last_message_id"] = sent.message_id
        STORE.save_state()
        log.info("Bugungi e'lon guruhga yuborildi: sana=%s, chat_id=%s, message_id=%s", key, chat_id, sent.message_id)
        return True


async def daily_announcement(context: ContextTypes.DEFAULT_TYPE) -> None:
    pending = context.application.bot_data.get("command_menu_retry")
    retry_after = context.application.bot_data.get("command_menu_retry_after")
    if pending and (retry_after is None or now() >= retry_after):
        await apply_command_menus(context.application, pending)
    await publish_today_if_due(context.application)


def bot_commands(admin: bool = False) -> list[BotCommand]:
    commands = [
        ("start", "Botni ochish va asosiy menyu"),
        ("bugun", "Bugungi navbatchi"),
        ("jadval", "Ikki kunlik navbatchilik jadvali"),
    ]
    if admin:
        commands.extend([
            ("admin", "Admin panelini ochish"),
            ("odamlar", "Odamlarni qo'shish, tahrirlash va o'chirish"),
            ("bajarildi", "Navbatchilikni bajarildi deb belgilash"),
            ("elon", "Bugungi navbatchini guruhga e'lon qilish"),
            ("setup", "Botni shu guruhga ulash"),
            ("tarix", "Oxirgi 30 kunlik navbatchilik tarixi"),
            ("zaxira", "Ma'lumotlarning zaxira nusxasini olish"),
            ("ism_qosh", "Ism va ixtiyoriy username bilan odam qo'shish"),
            ("ism_ochir", "Ro'yxatdagi raqam bo'yicha odamni o'chirish"),
            ("bekor", "Ism kiritish so'rovini bekor qilish"),
        ])
    return [BotCommand(command, description) for command, description in commands]


async def apply_command_menus(app: Application, targets: list) -> None:
    lock = app.bot_data.setdefault("command_menu_lock", asyncio.Lock())
    async with lock:
        await _apply_command_menus_locked(app, targets)


async def _apply_command_menus_locked(app: Application, targets: list) -> None:
    pending = app.bot_data.get("command_menu_retry", [])
    blocked_until = app.bot_data.get("command_menu_retry_after")
    if blocked_until and now() < blocked_until:
        app.bot_data["command_menu_retry"] = pending + [target for target in targets if target not in pending]
        return
    retry = [target for target in app.bot_data.get("command_menu_retry", []) if target not in targets]
    retry_after = app.bot_data.get("command_menu_retry_after") if retry else None
    for index, (scope, admin) in enumerate(targets):
        try:
            if scope is None:
                await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
            else:
                await app.bot.set_my_commands(bot_commands(admin), scope=scope)
        except TelegramError as exc:
            log.warning("Telegram buyruqlar menyusini sozlab bo'lmadi (%s): %s", scope, exc)
            if isinstance(exc, RetryAfter) or (isinstance(exc, NetworkError) and not isinstance(exc, BadRequest)):
                retry.append((scope, admin))
            if isinstance(exc, RetryAfter):
                delay = exc.retry_after
                if not isinstance(delay, timedelta):
                    delay = timedelta(seconds=delay)
                deadline = now() + delay
                retry_after = max(retry_after, deadline) if retry_after else deadline
                retry.extend(target for target in targets[index + 1:] if target not in retry)
                break
    app.bot_data["command_menu_retry"] = retry
    app.bot_data["command_menu_retry_after"] = retry_after


async def register_group_commands(app: Application, chat_id: int) -> None:
    targets = [
        (BotCommandScopeChatMember(chat_id=chat_id, user_id=admin_id), True)
        for admin_id in sorted(ADMIN_IDS)
    ]
    await apply_command_menus(app, targets)


async def register_bot_commands(app: Application) -> None:
    targets = [
        (BotCommandScopeDefault(), False),
        (BotCommandScopeAllPrivateChats(), False),
        (BotCommandScopeAllGroupChats(), False),
        (BotCommandScopeAllChatAdministrators(), True),
    ]
    targets.extend((BotCommandScopeChat(chat_id=admin_id), True) for admin_id in sorted(ADMIN_IDS))
    chat_id = STORE.state.get("chat_id")
    if chat_id and int(chat_id) < 0:
        targets.extend(
            (BotCommandScopeChatMember(chat_id=int(chat_id), user_id=admin_id), True)
            for admin_id in sorted(ADMIN_IDS)
        )
    targets.append((None, False))
    await apply_command_menus(app, targets)


async def post_init(app: Application) -> None:
    await register_bot_commands(app)
    ensure_today()
    current = display_person(current_person())
    notice = (
        f"Bot qayta ishga tushdi, {len(STORE.names)} ta ism yuklandi, "
        f"bugungi navbatchi: {current}"
    )
    if STORE.storage_errors:
        notice += "\n\n⚠️ Saqlash fayli bo'yicha muammo:\n" + "\n".join(STORE.storage_errors)
    await notify_admins(app, notice)
    await publish_today_if_due(app)


def delete_command_message(handler):
    """Keep group commands visible; remove command clutter in private chats."""
    from functools import wraps

    @wraps(handler)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            return await handler(update, context)
        finally:
            message = update.effective_message
            if message and update.effective_chat.type == ChatType.PRIVATE:
                try:
                    await message.delete()
                except TelegramError as exc:
                    log.warning("Buyruq xabarini o'chirib bo'lmadi: %s", exc)
    return wrapped


async def send_plain(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None) -> None:
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        parse_mode=None,
        entities=mention_entities(text),
        reply_markup=reply_markup,
    )


@delete_command_message
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
        if remember_group(update.effective_chat):
            await register_group_commands(context.application, update.effective_chat.id)
        await send_plain(update, context, "🧹 /bugun — bugungi navbatchi\n🗓 /jadval — navbatchilik jadvali\nHar bir odamga 2 kun. E'lon navbat boshida Toshkent vaqti bilan 08:00 da yuboriladi.")
        return
    if is_admin(update.effective_user.id):
        await apply_command_menus(
            context.application, [(BotCommandScopeChat(chat_id=update.effective_chat.id), True)],
        )
    await send_plain(
        update,
        context,
        "👋 Navbatchilik botiga xush kelibsiz! Tugmalardan foydalaning:",
        main_keyboard(await can_manage(update, context)),
    )


@delete_command_message
async def setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        await send_plain(update, context, "Botni ulash uchun /setup buyrug'ini guruhda yuboring.")
        return
    if not await require_admin(update, context):
        return
    already_linked = STORE.state.get("chat_id") == chat.id
    STORE.state["chat_id"] = chat.id
    if not already_linked:
        STORE.state["last_announced_date"] = None
        STORE.state["last_message_id"] = None
    ensure_today()
    heading = "✅ Guruh allaqachon ulangan. Joriy davra davom etadi." if already_linked else "✅ Guruh ulandi."
    text = heading + "\n\n" + duty_message()
    sent = await context.bot.send_message(
        chat_id=chat.id,
        text=text,
        parse_mode=None,
        entities=mention_entities(text),
        reply_markup=admin_keyboard(),
    )
    STORE.state["last_announced_date"] = today().isoformat()
    STORE.state["last_message_id"] = sent.message_id
    STORE.save_state()
    await register_group_commands(context.application, chat.id)


@delete_command_message
async def bugun(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if remember_group(update.effective_chat):
        await register_group_commands(context.application, update.effective_chat.id)
    markup = main_keyboard(await can_manage(update, context)) if update.effective_chat.type == ChatType.PRIVATE else None
    await send_plain(update, context, duty_message(), markup)


@delete_command_message
async def jadval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if remember_group(update.effective_chat):
        await register_group_commands(context.application, update.effective_chat.id)
    markup = main_keyboard(await can_manage(update, context)) if update.effective_chat.type == ChatType.PRIVATE else None
    await send_plain(update, context, schedule_text(), markup)


@delete_command_message
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    clear_people_input(update, context)
    ensure_today()
    await send_plain(update, context, "🛠 Admin paneli\n\n" + duty_message(), admin_keyboard())


@delete_command_message
async def odamlar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    clear_people_input(update, context)
    ensure_today()
    await send_people_page(update, context)


@delete_command_message
async def bekor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    clear_people_input(update, context)
    await send_people_page(update, context, "So'rov bekor qilindi.")


@delete_command_message
async def elon(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    if await publish_today_if_due(context.application, force=True):
        text = "✅ Bugungi navbatchi guruhga teg bilan e'lon qilindi."
    else:
        text = "⚠️ E'lon yuborilmadi. /setup bilan guruhni ulang va botning guruhda xabar yuborish huquqini tekshiring."
    await send_plain(update, context, text)


@delete_command_message
async def bajarildi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    ensure_today()
    if STORE.mark_today_done(today()):
        await send_plain(update, context, "✅ Bugungi navbatchilik bajarildi deb saqlandi.")
    else:
        await send_plain(update, context, "Bugungi navbatchi topilmadi yoki hali belgilanmagan.")


@delete_command_message
async def royxat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    await send_plain(update, context, roster_text())


@delete_command_message
async def tarix(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    await send_plain(update, context, history_text())


@delete_command_message
async def zaxira(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    target = update.effective_user.id

    # Build in-memory snapshots from the database
    exports = {
        "names.json": STORE.names,
        "state.json": {
            k: STORE.state.get(k)
            for k in (
                "chat_id", "round_order", "round_position", "round_number",
                "last_member_id", "last_assigned_date", "last_announced_date",
                "today_duty_id", "today_duty_date", "today_duty_done", "duty_started_date",
            )
        },
        "history.json": {
            day: record
            for day, record in STORE.last_30_days(today())
            if record is not None
        },
    }

    try:
        for filename, data in exports.items():
            buf = io.BytesIO(json.dumps(data, ensure_ascii=False, indent=2).encode())
            buf.name = filename
            await context.bot.send_document(chat_id=target, document=buf, filename=filename)
    except TelegramError as exc:
        log.warning("Zaxira adminning shaxsiy chatiga yuborilmadi: %s", exc)
        await send_plain(
            update,
            context,
            "Zaxirani shaxsiy chatga yubora olmadim. Bot bilan avval /start orqali shaxsiy chatni oching.",
        )


@delete_command_message
async def ism_qosh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    args = list(context.args)
    handle = ""
    if args and args[-1].startswith("@"):
        handle = args.pop()
    name = " ".join(args).strip()
    if not name:
        await send_plain(update, context, "Ishlatish: /ism_qosh Ism Familiya @username")
        return
    try:
        person = STORE.add_name(name, handle)
    except ValueError as exc:
        await send_plain(update, context, str(exc))
        return
    await send_plain(update, context, f"➕ {display_person(person)} qo'shildi. Joriy davra davom etadi.")


@delete_command_message
async def ism_ochir(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    if not context.args:
        await send_plain(update, context, "Ishlatish: /ism_ochir 3  (/royxat dagi raqam)")
        return
    try:
        number = int(context.args[0])
        if number < 1:
            raise ValueError("Noto'g'ri raqam")
        person = STORE.names[number - 1]
        removed, was_today = STORE.remove_name(person["id"])
    except (ValueError, IndexError):
        await send_plain(update, context, "Noto'g'ri raqam. /royxat dagi ism raqamini kiriting.")
        return
    note = f"➖ {display_person(removed)} ro'yxatdan o'chirildi."
    if was_today:
        note += "\n⚠️ U bugungi navbatchi edi; adminlarga xabar berdim."
        await notify_admins(
            context.application,
            f"⚠️ Bugungi navbatchi {display_person(removed)} ro'yxatdan o'chirildi.",
        )
    await send_plain(update, context, note)


def find_member(query: str) -> dict | None:
    needle = query.strip().lstrip("/@").casefold()
    if not needle:
        return None
    for person in STORE.names:
        handle = (person.get("username") or "").strip().lstrip("@").casefold()
        if handle and handle == needle:
            return person
    for person in STORE.names:
        if len(needle) >= 3 and needle in person["name"].casefold():
            return person
    return None


def member_duty(member_id: str) -> tuple[date, int] | None:
    entries = scheduled_members()
    for place, (day, person) in enumerate(entries):
        if person and person["id"] == member_id:
            return day, place
    return None


async def member_lookup_response(
    update: Update, context: ContextTypes.DEFAULT_TYPE, message, bot_name: str
) -> None:
    query = re.sub(rf"@{re.escape(bot_name)}", " ", message.text, flags=re.IGNORECASE).strip()
    person = None
    for part in query.split():
        person = find_member(part)
        if person:
            break
    if person is None and not query.lstrip("/").strip():
        person = find_member(update.effective_user.username or "")
    if not person:
        await context.bot.send_message(chat_id=message.chat_id, text="Bunday odam topilmadi. Ism yoki username'ni tekshiring.")
        return
    duty = member_duty(person["id"])
    if duty is None:
        text = f"👤 {display_person(person)} joriy davrada qolmagan. Keyingi davrada navbat oladi."
        await context.bot.send_message(
            chat_id=message.chat_id,
            text=text,
            parse_mode=None,
            entities=mention_entities(text),
        )
        return
    day, place = duty
    if day == today():
        result = f"🔔 {display_person(person)} — bugungi navbatchi.\n📅 Navbati: {duty_date_text(current_duty_start())}"
    else:
        delta = (day - today()).days
        when = "ertaga" if delta == 1 else f"{delta} kundan keyin"
        result = f"👤 {display_person(person)}\n📅 Navbati: {duty_date_text(day)} ({when})\nOldida {place} kishi bor."
    await context.bot.send_message(chat_id=message.chat_id, text=result, parse_mode=None, entities=mention_entities(result))


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.text:
        return
    text = message.text.strip()
    if text.startswith("/") or text in PEOPLE_MENU_TEXTS:
        clear_people_input(update, context)
    elif await handle_people_input(update, context):
        return
    if not text:
        return
    # Handle ReplyKeyboardMarkup buttons
    if text == "👤 Bugungi navbatchi":
        await bugun(update, context)
        return
    if text == "🗓 Jadval":
        await jadval(update, context)
        return
    if text == "📜 Ro'yxat":
        await royxat(update, context)
        return
    if text == "📚 Tarix":
        await tarix(update, context)
        return
    if text == "⚙️ Admin paneli":
        await admin_panel(update, context)
        return

    if update.effective_chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
        return
    first = text.split()[0]
    command = first.split("@")[0].lstrip("/").casefold()
    is_command = first.startswith("/")
    if is_command and command in KNOWN_COMMANDS:
        return
    bot_name = (context.bot.username or "").casefold()
    mentioned = bool(bot_name) and bool(re.search(
        rf"(?<![\w@])@{re.escape(bot_name)}(?![\w@])", message.text, flags=re.IGNORECASE
    ))
    if is_command and "@" in first and first.split("@", 1)[1].casefold() != bot_name:
        return
    replied = (
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id == context.bot.id
    )
    if not (mentioned or replied or is_command):
        return
    try:
        await member_lookup_response(update, context, message, bot_name)
    finally:
        if is_command:
            try:
                await message.delete()
            except TelegramError as exc:
                log.warning("Noma'lum buyruq xabarini o'chirib bo'lmadi: %s", exc)


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    kind, _, arg = query.data.partition(":")
    if kind == "people":
        await on_people_button(update, context)
        return
    ensure_today()

    if kind == "ui":
        action = arg.split(":", 1)[0]
        if action in {"admin", "reset", "reset_yes"} and not await can_manage(update, context):
            await query.answer("Faqat admin uchun", show_alert=True)
            return
        if action == "reset_yes":
            expected = f"reset_yes:{today().isoformat()}:{STORE.state.get('round_number', 1)}"
            if arg != expected:
                await query.answer("Bu tasdiqlash eskirgan. Admin panelini qayta oching.", show_alert=True)
                return
        await query.answer()
        clear_people_input(update, context)
        if action == "menu":
            await query.edit_message_text(
                "👋 Kerakli bo'limni tanlang:", reply_markup=menu_keyboard(await can_manage(update, context))
            )
            return
        if action == "admin":
            text, markup = "🛠 Admin paneli\n\n" + duty_message(), admin_keyboard()
        elif action == "bugun":
            text, markup = duty_message(), back_keyboard()
        elif action == "jadval":
            text, markup = schedule_text(), back_keyboard()
        elif action == "reset":
            text, markup = "Yangi davra boshlaymi? Joriy davra va uning tartibi yakunlanadi.", reset_confirm_keyboard()
        elif action == "reset_yes":
            STORE.start_new_round_today(today())
            text, markup = "🔄 Yangi davra boshlandi.\n\n" + duty_message(), admin_keyboard()
            await publish_today_if_due(context.application)
        else:
            text, markup = "Bo'lim topilmadi.", back_keyboard()
        await query.edit_message_text(text, parse_mode=None, entities=mention_entities(text), reply_markup=markup)
        return

    if kind == "adm":
        if not await can_manage(update, context):
            await query.answer("Faqat admin uchun", show_alert=True)
            return
        await query.answer()
        text = schedule_text()
        await query.edit_message_text(text, parse_mode=None, entities=mention_entities(text), reply_markup=back_keyboard())
        return

    if kind == "done":
        if not await can_manage(update, context):
            await query.answer("Faqat admin belgilay oladi", show_alert=True)
            return
        try:
            duty_date, member_id = query.data.split(":", 2)[1:]
        except ValueError:
            await query.answer("Bu eski tugma. Yangi xabarni oching.", show_alert=True)
            return
        if duty_date != today().isoformat() or member_id != STORE.state.get("today_duty_id"):
            await query.answer("Bu xabar eskirgan.", show_alert=True)
            return
        if STORE.mark_today_done(today()):
            await query.answer("Bajarildi ✅")
            await query.edit_message_reply_markup(None)
            await context.bot.send_message(query.message.chat_id, "✅ Bugungi navbatchilik bajarildi deb saqlandi.")
        else:
            await query.answer("Bugungi navbatchini topmadim.", show_alert=True)


def main() -> None:
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN topilmadi. .env faylga BOT_TOKEN kiriting.")
    if not ADMIN_IDS:
        raise RuntimeError("ADMIN_IDS topilmadi. .env faylga admin Telegram ID'larini kiriting.")

    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setup", setup))
    app.add_handler(CommandHandler("bugun", bugun))
    app.add_handler(CommandHandler("jadval", jadval))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("elon", elon))
    app.add_handler(CommandHandler("odamlar", odamlar))
    app.add_handler(CommandHandler(["bekor", "cancel"], bekor))
    app.add_handler(CommandHandler("bajarildi", bajarildi))
    app.add_handler(CommandHandler("royxat", royxat))
    app.add_handler(CommandHandler("tarix", tarix))
    app.add_handler(CommandHandler("zaxira", zaxira))
    app.add_handler(CommandHandler("ism_qosh", ism_qosh))
    app.add_handler(CommandHandler("ism_ochir", ism_ochir))
    app.add_handler(CallbackQueryHandler(on_button, pattern=r"^(done|adm|ui|people):"))
    app.add_handler(ChatMemberHandler(on_membership_change, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.StatusUpdate.MIGRATE, on_group_migration))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text), group=1)
    app.add_handler(MessageHandler(filters.COMMAND, on_text), group=1)
    app.job_queue.run_daily(daily_announcement, ANNOUNCE_AT, name="daily_announcement")
    app.job_queue.run_repeating(daily_announcement, interval=60, first=10, name="announcement_retry")
    app.run_polling(allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY, Update.MY_CHAT_MEMBER])


if __name__ == "__main__":
    main()
