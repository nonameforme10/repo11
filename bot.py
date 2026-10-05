"""Navbatchilik Telegram boti. Python 3.12+, python-telegram-bot 21+."""
import io
import json
import logging
import os
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from data_store import PostgresStore

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
}


def today() -> date:
    return datetime.now(TZ).date()


def now() -> datetime:
    return datetime.now(TZ)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def member_by_id(member_id: str | None) -> dict | None:
    return STORE.member(member_id)


def mention(person: dict | None) -> str:
    if not person:
        return ""
    handle = (person.get("username") or "").strip().lstrip("@")
    return f"@{handle}" if handle else ""


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


def duty_message() -> str:
    ensure_today()
    person = current_person()
    duty_date = STORE.state.get("today_duty_date")
    if not person or not duty_date:
        return "⚠️ Bugungi navbatchi yo'q. Admin /ism_qosh bilan ro'yxatga odam qo'shsin."
    try:
        day_text = date.fromisoformat(duty_date).strftime("%d.%m.%Y")
    except ValueError:
        day_text = duty_date
    lines = [
        "🧹 BUGUNGI NAVBATCHI",
        "",
        f"👤 {person['name']}",
        f"📅 {day_text}",
    ]
    handle = mention(person)
    if handle:
        lines.extend(["", f"👉 {handle}"])
    if STORE.state.get("today_duty_done"):
        lines.extend(["", "✅ Bajarildi deb belgilangan."])
    return "\n".join(lines)


def scheduled_members() -> list[tuple[date, dict | None]]:
    ensure_today()
    state = STORE.state
    result: list[tuple[date, dict | None]] = []
    current_date = state.get("today_duty_date")
    if current_date == today().isoformat() and state.get("today_duty_id"):
        result.append((today(), current_person()))
    position = int(state.get("round_position", 0))
    next_day = today() + timedelta(days=1)
    for member_id in state.get("round_order", [])[position:]:
        result.append((next_day, member_by_id(member_id)))
        next_day += timedelta(days=1)
    return result


def schedule_text() -> str:
    entries = scheduled_members()
    if not entries:
        return "✅ Joriy davrada qolgan navbatchi yo'q. Yangi davra navbatdagi kuni boshlanadi."
    lines = ["🗓 NAVBATCHILIK JADVALI"]
    for number, (day, person) in enumerate(entries, 1):
        if day == today():
            heading = f"🔔 BUGUN · {day.strftime('%d.%m.%Y')}"
        elif day == today() + timedelta(days=1):
            heading = f"📅 ERTAGA · {day.strftime('%d.%m.%Y')}"
        else:
            heading = f"📅 {day.strftime('%d.%m.%Y')}"
        lines.append(f"{number}. {heading}\n   👤 {display_person(person)}")
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
        f"Davra: {state.get('round_number', 1)} · Bugungi navbatchi davraning {state.get('round_position', 0)}-kuni",
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
        [InlineKeyboardButton("📋 Jadval", callback_data="adm:jadval")],
        [InlineKeyboardButton("🔄 Yangi davra", callback_data="ui:reset")],
    ])
    return InlineKeyboardMarkup(rows)


def menu_keyboard(admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📅 Bugungi navbatchi", callback_data="ui:bugun")],
        [InlineKeyboardButton("🗓 Jadval", callback_data="ui:jadval")],
    ]
    if admin:
        rows.append([InlineKeyboardButton("🛠 Admin paneli", callback_data="ui:admin")])
    return InlineKeyboardMarkup(rows)


def reset_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Ha, yangi davra", callback_data="ui:reset_yes")],
        [InlineKeyboardButton("Bekor qilish", callback_data="ui:admin")],
    ])


def date_at_eight_or_later() -> bool:
    return now().time().replace(tzinfo=None) >= ANNOUNCE_AT.replace(tzinfo=None)


async def notify_admins(app: Application, text: str) -> None:
    delivered = 0
    for admin_id in sorted(ADMIN_IDS):
        try:
            await app.bot.send_message(chat_id=admin_id, text=text)
            delivered += 1
        except TelegramError as exc:
            log.warning("Admin %s ga xabar yuborilmadi: %s", admin_id, exc)
    if delivered == 0 and STORE.state.get("chat_id"):
        try:
            await app.bot.send_message(chat_id=STORE.state["chat_id"], text=f"⚠️ Admin xabari:\n{text}")
        except TelegramError as exc:
            log.warning("Admin xabari guruhga ham yuborilmadi: %s", exc)


async def publish_today_if_due(app: Application) -> None:
    ensure_today()
    if not date_at_eight_or_later():
        return
    key = today().isoformat()
    if STORE.state.get("last_announced_date") == key:
        return
    chat_id = STORE.state.get("chat_id")
    if not chat_id or not STORE.state.get("today_duty_id"):
        return
    try:
        sent = await app.bot.send_message(
            chat_id=chat_id,
            text=duty_message(),
            reply_markup=admin_keyboard(),
        )
    except TelegramError:
        log.exception("Bugungi navbatchi xabarini yuborib bo'lmadi")
        return
    STORE.state["last_announced_date"] = key
    STORE.state["last_message_id"] = sent.message_id
    STORE.save_state()


async def daily_announcement(context: ContextTypes.DEFAULT_TYPE) -> None:
    STORE.ensure_assignment_through(today())
    await publish_today_if_due(context.application)


async def post_init(app: Application) -> None:
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
    """Run the command first, then remove its incoming command message."""
    from functools import wraps

    @wraps(handler)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            return await handler(update, context)
        finally:
            message = update.effective_message
            if message:
                try:
                    await message.delete()
                except TelegramError as exc:
                    log.warning("Buyruq xabarini o'chirib bo'lmadi: %s", exc)
    return wrapped


async def send_plain(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None) -> None:
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        reply_markup=reply_markup,
    )


@delete_command_message
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_plain(
        update,
        context,
        "👋 Navbatchilik botiga xush kelibsiz! Kerakli bo'limni tanlang:",
        menu_keyboard(is_admin(update.effective_user.id)),
    )


@delete_command_message
async def setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    STORE.state["chat_id"] = update.effective_chat.id
    STORE.start_new_round_today(today())
    text = "🔄 Yangi davra boshlandi.\n\n" + duty_message()
    sent = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        reply_markup=admin_keyboard(),
    )
    STORE.state["last_announced_date"] = today().isoformat()
    STORE.state["last_message_id"] = sent.message_id
    STORE.save_state()


@delete_command_message
async def bugun(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_plain(update, context, duty_message())


@delete_command_message
async def jadval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_plain(update, context, schedule_text())


@delete_command_message
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    ensure_today()
    await send_plain(update, context, "🛠 Admin paneli\n\n" + duty_message(), admin_keyboard())


@delete_command_message
async def bajarildi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    ensure_today()
    if STORE.mark_today_done(today()):
        await send_plain(update, context, "✅ Bugungi navbatchilik bajarildi deb saqlandi.")
    else:
        await send_plain(update, context, "Bugungi navbatchi topilmadi yoki hali belgilanmagan.")


@delete_command_message
async def royxat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    await send_plain(update, context, roster_text())


@delete_command_message
async def tarix(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    await send_plain(update, context, history_text())


@delete_command_message
async def zaxira(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
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
                "today_duty_id", "today_duty_date", "today_duty_done",
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
    if not is_admin(update.effective_user.id):
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
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await send_plain(update, context, "Ishlatish: /ism_ochir 3  (/royxat dagi raqam)")
        return
    try:
        number = int(context.args[0])
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
        await context.bot.send_message(
            chat_id=message.chat_id,
            text=f"👤 {display_person(person)} joriy davrada qolmagan. Keyingi davrada navbat oladi.",
        )
        return
    day, place = duty
    if day == today():
        result = f"🔔 {display_person(person)} — bugungi navbatchi."
    else:
        delta = (day - today()).days
        when = "ertaga" if delta == 1 else f"{delta} kundan keyin"
        result = f"👤 {display_person(person)}\n📅 Navbati: {day.strftime('%d.%m.%Y')} ({when})\nOldida {place} kishi bor."
    await context.bot.send_message(chat_id=message.chat_id, text=result)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.text:
        return
    first = message.text.split()[0]
    command = first.split("@")[0].lstrip("/").casefold()
    is_command = first.startswith("/")
    if is_command and command in KNOWN_COMMANDS:
        return
    bot_name = (context.bot.username or "").casefold()
    mentioned = f"@{bot_name}" in message.text.casefold()
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
    ensure_today()
    kind, _, arg = query.data.partition(":")

    if kind == "ui":
        if arg in {"admin", "reset", "reset_yes"} and not is_admin(query.from_user.id):
            await query.answer("Faqat admin uchun", show_alert=True)
            return
        await query.answer()
        if arg == "menu":
            await query.edit_message_text(
                "👋 Kerakli bo'limni tanlang:", reply_markup=menu_keyboard(is_admin(query.from_user.id))
            )
            return
        if arg == "admin":
            text, markup = "🛠 Admin paneli\n\n" + duty_message(), admin_keyboard()
        elif arg == "bugun":
            text, markup = duty_message(), back_keyboard()
        elif arg == "jadval":
            text, markup = schedule_text(), back_keyboard()
        elif arg == "reset":
            text, markup = "Yangi davra boshlaymi? Joriy davra va uning tartibi yakunlanadi.", reset_confirm_keyboard()
        elif arg == "reset_yes":
            STORE.start_new_round_today(today())
            text, markup = "🔄 Yangi davra boshlandi.\n\n" + duty_message(), admin_keyboard()
            STORE.state["last_announced_date"] = today().isoformat()
            STORE.save_state()
        else:
            text, markup = "Bo'lim topilmadi.", back_keyboard()
        await query.edit_message_text(text, reply_markup=markup)
        return

    if kind == "adm":
        if not is_admin(query.from_user.id):
            await query.answer("Faqat admin uchun", show_alert=True)
            return
        await query.answer()
        await query.edit_message_text(schedule_text(), reply_markup=back_keyboard())
        return

    if kind == "done":
        if not is_admin(query.from_user.id):
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
    app.add_handler(CommandHandler("bajarildi", bajarildi))
    app.add_handler(CommandHandler("royxat", royxat))
    app.add_handler(CommandHandler("tarix", tarix))
    app.add_handler(CommandHandler("zaxira", zaxira))
    app.add_handler(CommandHandler("ism_qosh", ism_qosh))
    app.add_handler(CommandHandler("ism_ochir", ism_ochir))
    app.add_handler(CallbackQueryHandler(on_button, pattern=r"^(done|adm|ui):"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text), group=1)
    app.add_handler(MessageHandler(filters.COMMAND, on_text), group=1)
    app.job_queue.run_daily(daily_announcement, ANNOUNCE_AT)
    app.run_polling()


if __name__ == "__main__":
    main()
