import os
import sys
import logging
import asyncio

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ConversationHandler, ContextTypes

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PhoneCodeExpiredError

from joiner import progress, load_groups, run_joiner

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("gc_bot")

# Env
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")

# Conversation states
PHONE, OTP, PASSWORD = range(3)

# Telethon user client state
user_client = None
session_string = os.getenv("SESSION_STRING", "")

# Stats message tracking
stats_message = None
stats_chat_id = None


async def get_user_client():
    global user_client
    if user_client is None:
        session = StringSession(session_string) if session_string else StringSession()
        user_client = TelegramClient(session, API_ID, API_HASH)
        await user_client.connect()
    return user_client


async def is_user_authorized():
    try:
        client = await get_user_client()
        return await client.is_user_authorized()
    except Exception:
        return False


def stats_text():
    s = progress.to_dict()
    done = s["joined"] + s["already_in"]
    total = s["total"] or 1
    pct = round((done / total) * 100)

    lines = [
        f"📊 *Progress*: {done} / {s['total']} ({pct}%)",
        f"",
        f"✅ Joined: `{s['joined']}`",
        f"🔁 Already in: `{s['already_in']}`",
        f"❌ Failed: `{s['failed']}`",
    ]
    if s["running"]:
        lines.append(f"")
        lines.append(f"⏳ Current: `{s['current']}` (#{s['current_index']})")
    if s["done"]:
        lines.append(f"")
        lines.append(f"🏁 *Complete!*")
    if s["errors"]:
        lines.append(f"")
        lines.append(f"⚠️ Last errors:")
        for e in s["errors"][-3:]:
            lines.append(f"  • `{e[:60]}`")
    return "\n".join(lines)


def status_keyboard():
    kb = []
    if not progress.running and not progress.done:
        kb.append([InlineKeyboardButton("▶️ Start Joining", callback_data="join")])
    if progress.running:
        kb.append([InlineKeyboardButton("⏹ Stop", callback_data="stop")])
    return InlineKeyboardMarkup(kb)


async def update_stats_message(context):
    global stats_message, stats_chat_id
    if stats_message and stats_chat_id:
        try:
            await stats_message.edit_text(
                stats_text(),
                parse_mode="Markdown",
                reply_markup=status_keyboard()
            )
        except Exception:
            pass


async def stats_callback_wrapper():
    await update_stats_message(None)


# --- Bot Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    authorized = await is_user_authorized()
    text = (
        "🤖 *TG GC Joiner Bot*\n\n"
        "Main Telegram groups mein auto-join karta hoon.\n\n"
        f"*Status:* {'✅ Logged in' if authorized else '❌ Not logged in'}\n\n"
        "*Commands:*\n"
        "/login - Login with your Telegram account\n"
        "/join - Start joining groups\n"
        "/stop - Stop joining\n"
        "/stats - Show progress\n"
        "/logout - Logout account\n"
        "/cancel - Cancel current operation"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_user_authorized():
        await update.message.reply_text("✅ Already logged in. Send /join to start.")
        return ConversationHandler.END

    await update.message.reply_text(
        "📱 Send your phone number with country code.\n"
        "Example: `+919876543210`",
        parse_mode="Markdown"
    )
    return PHONE


async def login_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    context.user_data["phone"] = phone

    try:
        client = await get_user_client()
        if client.is_connected():
            await client.disconnect()
        user_client = None

        client = await get_user_client()
        await client.send_code_request(phone)
        await update.message.reply_text(
            "📨 OTP sent to Telegram. Send me the code.\n"
            "Example: `12345`",
            parse_mode="Markdown"
        )
        return OTP
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}\nSend /login to try again.")
        return ConversationHandler.END


async def login_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    phone = context.user_data.get("phone")

    try:
        client = await get_user_client()
        await client.sign_in(phone=phone, code=code)

        global session_string
        session_string = client.session.save()
        me = await client.get_me()
        await update.message.reply_text(
            f"✅ *Logged in as* {me.first_name}!\n\n"
            "Send /join to start joining groups.",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    except SessionPasswordNeededError:
        await update.message.reply_text(
            "🔑 2FA is enabled. Send your password.",
            parse_mode="Markdown"
        )
        return PASSWORD
    except PhoneCodeInvalidError:
        await update.message.reply_text("❌ Invalid OTP. Send /login to try again.")
        return ConversationHandler.END
    except PhoneCodeExpiredError:
        await update.message.reply_text("❌ OTP expired. Send /login to request a new one.")
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}\nSend /login to try again.")
        return ConversationHandler.END


async def login_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()

    try:
        client = await get_user_client()
        await client.sign_in(password=password)

        global session_string
        session_string = client.session.save()
        me = await client.get_me()
        await update.message.reply_text(
            f"✅ *Logged in as* {me.first_name}!\n\n"
            "Send /join to start joining groups.",
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}\nSend /login to try again.")
        return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


async def join_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global stats_message, stats_chat_id

    if not await is_user_authorized():
        await update.message.reply_text("❌ Not logged in. Send /login first.")
        return

    if progress.running:
        await update.message.reply_text("⚠️ Already joining. Send /stop to stop.")
        return

    groups = load_groups()
    if not groups:
        await update.message.reply_text("❌ No groups found in groups.json")
        return

    from joiner import prepare_join_list
    joinable = prepare_join_list(groups)
    if not joinable:
        await update.message.reply_text("❌ No joinable groups found.")
        return

    delay_min = int(os.getenv("JOIN_DELAY_MIN", "30"))
    delay_max = int(os.getenv("JOIN_DELAY_MAX", "90"))
    max_joins = int(os.getenv("MAX_JOINS_PER_RUN", "50"))

    msg = await update.message.reply_text(
        f"▶️ *Starting...*\nJoining {len(joinable)} groups.",
        parse_mode="Markdown"
    )

    stats_message = msg
    stats_chat_id = update.effective_chat.id

    client = await get_user_client()

    async def cb():
        await update_stats_message(context)

    asyncio.create_task(run_joiner(
        client, groups,
        delay_min=delay_min, delay_max=delay_max, max_joins=max_joins,
        status_callback=cb
    ))


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not progress.running:
        await update.message.reply_text("⚠️ Not currently joining.")
        return
    progress.running = False
    await update.message.reply_text("⏹ Stopping... (will stop after current group)")


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if progress.total == 0:
        await update.message.reply_text("No join session started yet. Send /join to begin.")
        return
    await update.message.reply_text(stats_text(), parse_mode="Markdown")


async def logout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global user_client, session_string
    if user_client:
        await user_client.disconnect()
    user_client = None
    session_string = ""
    progress.reset()
    await update.message.reply_text("🔒 Logged out.")


# --- Callback Query Handler ---

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "join":
        if not await is_user_authorized():
            await query.edit_message_text("❌ Not logged in. Send /login first.")
            return
        if progress.running:
            await query.edit_message_text("⚠️ Already joining.")
            return

        groups = load_groups()
        if not groups:
            await query.edit_message_text("❌ No groups found.")
            return

        from joiner import prepare_join_list
        joinable = prepare_join_list(groups)
        delay_min = int(os.getenv("JOIN_DELAY_MIN", "30"))
        delay_max = int(os.getenv("JOIN_DELAY_MAX", "90"))
        max_joins = int(os.getenv("MAX_JOINS_PER_RUN", "50"))

        global stats_message, stats_chat_id
        stats_message = query.message
        stats_chat_id = update.effective_chat.id

        await query.edit_message_text("▶️ Starting...")

        client = await get_user_client()

        async def cb():
            await update_stats_message(context)

        asyncio.create_task(run_joiner(
            client, groups,
            delay_min=delay_min, delay_max=delay_max, max_joins=max_joins,
            status_callback=cb
        ))

    elif query.data == "stop":
        if progress.running:
            progress.running = False
            await query.edit_message_text("⏹ Stopping...")
        else:
            await query.edit_message_text("⚠️ Not running.")


def main():
    if not BOT_TOKEN or not API_ID or not API_HASH:
        logger.error("BOT_TOKEN, API_ID, and API_HASH must be set in .env")
        sys.exit(1)

    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("login", login_start)],
        states={
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_phone)],
            OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_otp)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_password)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("join", join_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("logout", logout_cmd))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, button_callback))

    logger.info("Bot started. Polling...")
    app.run_polling()


if __name__ == "__main__":
    main()
