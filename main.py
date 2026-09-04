import os
import time
import json
import random
import threading
import asyncio
from queue import Queue

import requests
from instagrapi import Client
import google.generativeai as genai
from duckduckgo_search import DDGS

from telegram import Update, ForceReply
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    ContextTypes,
)

# ==========================================
# 1. ENVIRONMENT VARIABLES (set in Railway)
# ==========================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
PROXY_URL = os.getenv("PROXY_URL", "")          # Optional
DATA_DIR = "/app/data" if os.path.exists("/app/data") else "."

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("Missing TELEGRAM_BOT_TOKEN in environment variables!")

# ==========================================
# 2. GLOBAL STATE
# ==========================================
ig_client = None                # Instagram client (single user)
loop_thread = None              # Background thread running IG bot
is_logged_in = False            # Flag indicating successful IG login
stop_loop = threading.Event()   # Event to stop the loop
memory = {
    "last_processed_messages": {},
    "known_group_members": {},
    "user_warnings": {},
    "bot_admins": []
}
MEMORY_FILE = os.path.join(DATA_DIR, "bot_memory.json")
SESSION_FILE = os.path.join(DATA_DIR, "session.json")

# Queue for sending messages from IG thread to Telegram
telegram_queue = Queue()

# ==========================================
# 3. HELPER FUNCTIONS
# ==========================================
def load_memory():
    global memory
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r") as f:
                loaded = json.load(f)
                memory.update(loaded)
        except Exception as e:
            print(f"Failed to load memory: {e}")

def save_memory():
    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump(memory, f, indent=4)
    except Exception as e:
        print(f"Failed to save memory: {e}")

def is_admin(username):
    return username.lower() in [adm.lower() for adm in memory["bot_admins"]]

def human_typing_pause(min_sec=1.5, max_sec=3.5):
    time.sleep(random.uniform(min_sec, max_sec))

# ==========================================
# 4. INSTAGRAM BOT CORE LOGIC
# ==========================================
def instagram_bot_loop():
    """Background loop that processes Instagram groups."""
    global ig_client, stop_loop

    while not stop_loop.is_set():
        try:
            threads = ig_client.direct_threads(amount=10)
            for thread in threads:
                if not getattr(thread, "is_group", False) or not thread.messages:
                    continue

                thread_id = str(thread.id)
                current_users = {str(user.pk): user.username for user in thread.users}
                latest_message = thread.messages[0]

                # Welcome new members
                if thread_id in memory["known_group_members"]:
                    known_ids = set(memory["known_group_members"][thread_id].keys())
                    current_ids = set(current_users.keys())
                    new_user_ids = current_ids - known_ids
                    for uid in new_user_ids:
                        username = current_users[uid]
                        human_typing_pause(2.0, 4.0)
                        ig_client.direct_send(
                            f"Welcome to the group, @{username}! Type .help to see commands.",
                            thread_ids=[thread_id]
                        )
                memory["known_group_members"][thread_id] = current_users

                # Skip already processed messages
                if memory["last_processed_messages"].get(thread_id) == latest_message.id:
                    continue

                text = (latest_message.text or "").lower().strip()
                original_text = (latest_message.text or "").strip()
                sender_id = str(latest_message.user_id)
                sender_username = ig_client.user_info(sender_id).username
                replied_msg = getattr(latest_message, "reply_to", None)

                # 1) AI conversation trigger
                if "bacchu bot" in text:
                    prompt = text.replace("bacchu bot", "").strip()
                    if prompt and ai_model:
                        try:
                            human_typing_pause(2.0, 4.0)
                            response = ai_model.generate_content(
                                f"Keep this answer natural and under 250 words: {prompt}"
                            )
                            reply = f"🤖 {response.text}"[:900]
                            ig_client.direct_send(reply, thread_ids=[thread_id])
                        except Exception:
                            ig_client.direct_send("❌ AI is temporarily resting.", thread_ids=[thread_id])

                # 2) Web search
                elif text.startswith(".search "):
                    query = original_text[8:].strip()
                    try:
                        human_typing_pause(1.0, 2.0)
                        ig_client.direct_send(f"🔍 Searching: {query}...", thread_ids=[thread_id])
                        results = list(DDGS().text(query, max_results=3))
                        human_typing_pause(2.0, 3.5)
                        if results:
                            reply = "🌐 WEB RESULTS:\n\n"
                            for i, res in enumerate(results):
                                reply += f"{i+1}. {res['title']}\n{res['body'][:120]}...\n\n"
                            ig_client.direct_send(reply[:950], thread_ids=[thread_id])
                        else:
                            ig_client.direct_send("❌ No results found.", thread_ids=[thread_id])
                    except Exception:
                        ig_client.direct_send("❌ Search error occurred.", thread_ids=[thread_id])

                # 3) Calculator
                elif text.startswith(".calc "):
                    equation = text[6:].strip()
                    try:
                        math_url = f"http://api.mathjs.org/v4/?expr={requests.utils.quote(equation)}"
                        result = requests.get(math_url, timeout=5).text
                        human_typing_pause()
                        ig_client.direct_send(f"🧮 Result: {result}", thread_ids=[thread_id])
                    except Exception:
                        ig_client.direct_send("❌ Invalid calculation.", thread_ids=[thread_id])

                # 4) Help command
                elif text == ".help":
                    human_typing_pause()
                    help_text = (
                        "🤖 BACCHU BOT COMMANDS 🤖\n\n"
                        "Ask AI: 'bacchu bot [question]'\n\n"
                        "Public:\n"
                        ".search [query] - Web search\n"
                        ".calc [math] - Calculate\n\n"
                        "Admin Only:\n"
                        ".tagall - Ping all members\n"
                        ".hack [user] - Troll animation\n"
                        ".warn - Add warning strike (reply)\n"
                        ".kick - Remove member (reply)"
                    )
                    ig_client.direct_send(help_text, thread_ids=[thread_id])

                # 5) Tag all (admin)
                elif text == ".tagall":
                    if not is_admin(sender_username):
                        ig_client.direct_send("❌ Admin only.", thread_ids=[thread_id])
                    else:
                        users = list(current_users.values())
                        chunk_size = 5
                        for i in range(0, len(users), chunk_size):
                            chunk = users[i:i+chunk_size]
                            mentions = " ".join([f"@{u}" for u in chunk])
                            ig_client.direct_send(f"📢 ANNOUNCEMENT:\n{mentions}", thread_ids=[thread_id])
                            human_typing_pause(1.5, 3.2)

                # 6) Hack animation (admin)
                elif text.startswith(".hack "):
                    if not is_admin(sender_username):
                        ig_client.direct_send("❌ Admin only.", thread_ids=[thread_id])
                    else:
                        target = text.split(".hack ")[1].strip()
                        frames = [
                            f"💻 Initiating breach on @{target}...",
                            "🟩⬜⬜⬜⬜ 20% - Bypassing firewall",
                            "🟩🟩🟩⬜⬜ 60% - Extracting database",
                            "🟩🟩🟩🟩🟩 100% - Infiltration complete",
                            f"🔓 Compromised @{target}!\nPassword found: ilovemyex123"
                        ]
                        for frame in frames:
                            ig_client.direct_send(frame, thread_ids=[thread_id])
                            human_typing_pause(1.8, 2.8)

                # 7) Warn and kick (admin)
                elif text in [".warn", ".kick"]:
                    if not is_admin(sender_username):
                        ig_client.direct_send("❌ Admin only.", thread_ids=[thread_id])
                    elif replied_msg:
                        target_id = str(replied_msg.user_id)
                        target_username = ig_client.user_info(target_id).username
                        human_typing_pause()
                        if text == ".warn":
                            if target_id not in memory["user_warnings"]:
                                memory["user_warnings"][target_id] = 0
                            memory["user_warnings"][target_id] += 1
                            strikes = memory["user_warnings"][target_id]
                            if strikes >= 3:
                                ig_client.direct_send(
                                    f"🚨 @{target_username} reached 3 strikes. Removing...",
                                    thread_ids=[thread_id]
                                )
                                human_typing_pause(1.0, 2.0)
                                ig_client.direct_thread_remove_users(thread_id, [target_id])
                                memory["user_warnings"][target_id] = 0
                            else:
                                ig_client.direct_send(
                                    f"⚠️ @{target_username} warned. (Strike {strikes}/3)",
                                    thread_ids=[thread_id]
                                )
                        elif text == ".kick":
                            if target_id == str(ig_client.user_id):
                                ig_client.direct_send("I cannot kick myself!", thread_ids=[thread_id])
                            else:
                                try:
                                    ig_client.direct_thread_remove_users(thread_id, [target_id])
                                    ig_client.direct_send("Target removed.", thread_ids=[thread_id])
                                except Exception:
                                    ig_client.direct_send("❌ Failed to kick. Make sure I am a Group Admin.", thread_ids=[thread_id])

                memory["last_processed_messages"][thread_id] = latest_message.id
                save_memory()

        except Exception as e:
            # Silent catch to avoid crashing the thread
            pass

        # Random sleep between 6-13 seconds (same as original)
        time.sleep(random.uniform(6.0, 13.0))

# ==========================================
# 5. TELEGRAM BOT HANDLERS
# ==========================================
# States for conversation handler
USERNAME, PASSWORD, OTP = range(3)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a welcome message with available commands."""
    await update.message.reply_text(
        "👋 Welcome! I am your Instagram Bot Manager.\n\n"
        "Commands:\n"
        "/login - Log in to Instagram\n"
        "/logout - Log out and stop bot\n"
        "/status - Check current status\n"
        "/stop - Stop the bot (keep login)\n"
        "/help - Show this help"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_logged_in, loop_thread
    msg = f"Logged in: {'Yes' if is_logged_in else 'No'}\n"
    msg += f"Bot running: {'Yes' if loop_thread and loop_thread.is_alive() else 'No'}"
    await update.message.reply_text(msg)

async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start login conversation, ask for username."""
    global ig_client, is_logged_in
    if is_logged_in:
        await update.message.reply_text("Already logged in. Use /logout first.")
        return ConversationHandler.END

    # Create a new Instagram client (if not exists)
    if ig_client is None:
        ig_client = Client()
        ig_client.delay_range = [1, 3]
        if PROXY_URL:
            ig_client.set_proxy(PROXY_URL)
            print(f"Proxy configured: {PROXY_URL.split('@')[-1]}")

    await update.message.reply_text("📝 Please send your Instagram username:")
    return USERNAME

async def login_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Store username, ask for password."""
    context.user_data['ig_username'] = update.message.text.strip()
    await update.message.reply_text("🔑 Now send your password (it will be stored only for this session):")
    return PASSWORD

async def login_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Store password, attempt login. Handle OTP if needed."""
    global ig_client, is_logged_in, loop_thread, stop_loop

    username = context.user_data.get('ig_username')
    password = update.message.text.strip()
    # Delete the password message for privacy
    try:
        await update.message.delete()
    except:
        pass

    await update.message.reply_text("🔐 Attempting to log in...")

    try:
        # Try to load previous session if exists
        if os.path.exists(SESSION_FILE):
            try:
                ig_client.load_settings(SESSION_FILE)
                print("Loaded existing session.")
            except:
                pass

        # Attempt login
        ig_client.login(username, password)
        ig_client.dump_settings(SESSION_FILE)
        is_logged_in = True
        await update.message.reply_text("✅ Login successful! Starting the Instagram bot loop...")

        # Start background loop if not already running
        if loop_thread is None or not loop_thread.is_alive():
            stop_loop.clear()
            loop_thread = threading.Thread(target=instagram_bot_loop, daemon=True)
            loop_thread.start()

        return ConversationHandler.END

    except Exception as e:
        error_msg = str(e)
        # Handle OTP / 2FA
        if "TwoFactorRequired" in error_msg or "2FA" in error_msg:
            await update.message.reply_text("🔐 Two-factor authentication required.\nPlease send the verification code (from your authenticator app):")
            context.user_data['ig_password'] = password
            return OTP
        elif "ChallengeRequired" in error_msg or "challenge" in error_msg.lower():
            # Instagram may send a code via email or phone
            await update.message.reply_text(
                "📧 Instagram has sent a verification code to your email/phone.\n"
                "Please send that code here:"
            )
            context.user_data['ig_password'] = password
            return OTP
        else:
            await update.message.reply_text(f"❌ Login failed: {error_msg}")
            return ConversationHandler.END

async def login_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle OTP code submission."""
    global ig_client, is_logged_in, loop_thread, stop_loop

    username = context.user_data.get('ig_username')
    password = context.user_data.get('ig_password')
    otp_code = update.message.text.strip()
    try:
        await update.message.delete()
    except:
        pass

    await update.message.reply_text("🔐 Verifying code...")

    try:
        # Attempt login with verification code
        ig_client.login(username, password, verification_code=otp_code)
        ig_client.dump_settings(SESSION_FILE)
        is_logged_in = True
        await update.message.reply_text("✅ Login successful! Starting Instagram bot loop...")

        if loop_thread is None or not loop_thread.is_alive():
            stop_loop.clear()
            loop_thread = threading.Thread(target=instagram_bot_loop, daemon=True)
            loop_thread.start()

        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"❌ OTP verification failed: {e}")
        return ConversationHandler.END

async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Logout and stop the bot."""
    global ig_client, is_logged_in, loop_thread, stop_loop
    if ig_client:
        try:
            ig_client.logout()
            # Remove session file
            if os.path.exists(SESSION_FILE):
                os.remove(SESSION_FILE)
        except:
            pass
    if loop_thread and loop_thread.is_alive():
        stop_loop.set()
        loop_thread.join(timeout=2)
    ig_client = None
    is_logged_in = False
    memory["last_processed_messages"] = {}
    memory["known_group_members"] = {}
    memory["user_warnings"] = {}
    save_memory()
    await update.message.reply_text("👋 Logged out and bot stopped.")

async def stop_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop the loop but keep login."""
    global loop_thread, stop_loop
    if loop_thread and loop_thread.is_alive():
        stop_loop.set()
        loop_thread.join(timeout=2)
        await update.message.reply_text("🛑 Bot loop stopped. You are still logged in.")
    else:
        await update.message.reply_text("⚠️ Bot is not running.")

# ==========================================
# 6. MAIN SETUP AND LAUNCH
# ==========================================
def main():
    # Load memory
    load_memory()

    # Configure Gemini AI
    global ai_model
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        ai_model = genai.GenerativeModel("gemini-1.5-flash")
    else:
        ai_model = None
        print("Warning: GEMINI_API_KEY not set. AI features disabled.")

    # Create Telegram application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Conversation handler for login
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('login', login_start)],
        states={
            USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_username)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_password)],
            OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_otp)],
        },
        fallbacks=[CommandHandler('cancel', start)],
    )

    # Register handlers
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(CommandHandler('status', status))
    application.add_handler(CommandHandler('logout', logout))
    application.add_handler(CommandHandler('stop', stop_bot))
    application.add_handler(conv_handler)

    # Start the bot
    print("🤖 Telegram bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
