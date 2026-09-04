import os
import time
import json
import random
import requests
from instagrapi import Client
import google.generativeai as genai
from duckduckgo_search import DDGS

# ==========================================
# 1. RAILWAY ENVIRONMENT VARIABLES
# ==========================================
IG_USERNAME = os.getenv("hey_rud_")
IG_PASSWORD = os.getenv("himu01")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PROXY_URL = os.getenv("PROXY_URL") # Optional: "http://user:pass@host:port"

# Parse comma-separated admin usernames from Railway
admins_env = os.getenv("BOT_ADMINS", "@0exzo,@maaz1_x",@_aneii_90_)
ENV_ADMINS = [u.strip().lower() for u in admins_env.split(",") if u.strip()]

if not IG_USERNAME or not IG_PASSWORD:
    raise ValueError("Missing IG_USERNAME or IG_PASSWORD in Railway environment variables!")

# Configure Gemini AI
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    ai_model = genai.GenerativeModel("gemini-1.5-flash")
else:
    ai_model = None
    print("Warning: GEMINI_API_KEY not set. AI features will be unavailable.")

# ==========================================
# 2. PERSISTENT VOLUME STORAGE CONFIG
# ==========================================
# Detect if running on Railway with a mounted volume at /app/data
DATA_DIR = "/app/data" if os.path.exists("/app/data") else "."
MEMORY_FILE = os.path.join(DATA_DIR, "bot_memory.json")
SESSION_FILE = os.path.join(DATA_DIR, "session.json")

memory = {
    "last_processed_messages": {},
    "known_group_members": {},
    "user_warnings": {},
    "bot_admins": ENV_ADMINS
}

def load_memory():
    global memory
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r") as f:
                loaded = json.load(f)
                memory.update(loaded)
                # Ensure admins from environment variables are always included
                for admin in ENV_ADMINS:
                    if admin not in memory["bot_admins"]:
                        memory["bot_admins"].append(admin)
        except Exception as e:
            print(f"Failed to load memory file: {e}")

def save_memory():
    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump(memory, f, indent=4)
    except Exception as e:
        print(f"Failed to save memory: {e}")

# ==========================================
# 3. STEALTH CLIENT INITIALIZATION
# ==========================================
cl = Client()
cl.delay_range = [1, 3] # Built-in random micro-delays for internal requests

# Route through proxy if configured (crucial on datacenter cloud hosts like Railway)
if PROXY_URL:
    cl.set_proxy(PROXY_URL)
    print(f"Proxy configured: {PROXY_URL.split('@')[-1]}")

# Load previous device fingerprint from persistent volume
if os.path.exists(SESSION_FILE):
    try:
        cl.load_settings(SESSION_FILE)
        print("Loaded existing session from persistent storage.")
    except Exception as e:
        print(f"Could not load session settings: {e}")

try:
    cl.login(IG_USERNAME, IG_PASSWORD)
    cl.dump_settings(SESSION_FILE)
    print(f"🤖 Stealth Bacchu Bot is logged in as @{IG_USERNAME}!")
except Exception as e:
    print(f"Login failed: {e}")
    exit(1)

load_memory()

# ==========================================
# 4. HELPER FUNCTIONS
# ==========================================
def is_admin(username):
    return username.lower() in [adm.lower() for adm in memory["bot_admins"]]

def human_typing_pause(min_sec=1.5, max_sec=3.5):
    """Simulates realistic human typing speed."""
    time.sleep(random.uniform(min_sec, max_sec))

# ==========================================
# 5. CORE GROUP MANAGEMENT ENGINE
# ==========================================
def process_group_management():
    try:
        threads = cl.direct_threads(amount=10)

        for thread in threads:
            if not getattr(thread, "is_group", False) or not thread.messages:
                continue

            thread_id = str(thread.id)
            current_users = {str(user.pk): user.username for user in thread.users}
            latest_message = thread.messages[0]

            # --- WELCOME SYSTEM ---
            if thread_id in memory["known_group_members"]:
                known_ids = set(memory["known_group_members"][thread_id].keys())
                current_ids = set(current_users.keys())
                new_user_ids = current_ids - known_ids

                for uid in new_user_ids:
                    username = current_users[uid]
                    human_typing_pause(2.0, 4.0)
                    cl.direct_send(
                        f"Welcome to the group, @{username}! Type .help to see what I can do.",
                        thread_ids=[thread_id]
                    )

            memory["known_group_members"][thread_id] = current_users

            # Skip if this message has already been processed
            if memory["last_processed_messages"].get(thread_id) == latest_message.id:
                continue

            text = (latest_message.text or "").lower().strip()
            original_text = (latest_message.text or "").strip()
            sender_id = str(latest_message.user_id)
            sender_username = cl.user_info(sender_id).username
            replied_msg = getattr(latest_message, "reply_to", None)

            # ----------------------------------------------------
            # 1. AI CONVERSATION TRIGGER ("bacchu bot")
            # ----------------------------------------------------
            if "bacchu bot" in text:
                prompt = text.replace("bacchu bot", "").strip()
                if prompt and ai_model:
                    try:
                        human_typing_pause(2.0, 4.0)
                        response = ai_model.generate_content(
                            f"Keep this answer natural and under 250 words: {prompt}"
                        )
                        reply = f"🤖 {response.text}"[:900]
                        cl.direct_send(reply, thread_ids=[thread_id])
                    except Exception:
                        cl.direct_send("❌ AI is temporarily resting.", thread_ids=[thread_id])

            # ----------------------------------------------------
            # 2. WEB SEARCH COMMAND (.search)
            # ----------------------------------------------------
            elif text.startswith(".search "):
                query = original_text[8:].strip()
                try:
                    human_typing_pause(1.0, 2.0)
                    cl.direct_send(f"🔍 Searching: {query}...", thread_ids=[thread_id])
                    results = list(DDGS().text(query, max_results=3))

                    human_typing_pause(2.0, 3.5)
                    if results:
                        reply = "🌐 WEB RESULTS:\n\n"
                        for i, res in enumerate(results):
                            reply += f"{i+1}. {res['title']}\n{res['body'][:120]}...\n\n"
                        cl.direct_send(reply[:950], thread_ids=[thread_id])
                    else:
                        cl.direct_send("❌ No results found.", thread_ids=[thread_id])
                except Exception:
                    cl.direct_send("❌ Search error occurred.", thread_ids=[thread_id])

            # ----------------------------------------------------
            # 3. MATH CALCULATOR (.calc)
            # ----------------------------------------------------
            elif text.startswith(".calc "):
                equation = text[6:].strip()
                try:
                    math_url = f"http://api.mathjs.org/v4/?expr={requests.utils.quote(equation)}"
                    result = requests.get(math_url, timeout=5).text
                    human_typing_pause()
                    cl.direct_send(f"🧮 Result: {result}", thread_ids=[thread_id])
                except Exception:
                    cl.direct_send("❌ Invalid calculation.", thread_ids=[thread_id])

            # ----------------------------------------------------
            # 4. HELP COMMAND (.help)
            # ----------------------------------------------------
            elif text == ".help":
                human_typing_pause()
                help_text = (
                    "🤖 BACCHU BOT COMMANDS 🤖\n\n"
                    "Ask AI: Just say 'bacchu bot [question]'\n\n"
                    "Public:\n"
                    ".search [query] - Web search\n"
                    ".calc [math] - Calculate\n\n"
                    "Admin Only:\n"
                    ".tagall - Ping all members\n"
                    ".hack [user] - Troll animation\n"
                    ".warn - Add warning strike (reply)\n"
                    ".kick - Remove member (reply)"
                )
                cl.direct_send(help_text, thread_ids=[thread_id])

            # ----------------------------------------------------
            # 5. TAGALL (Admin) - Batched with randomized jitter
            # ----------------------------------------------------
            elif text == ".tagall":
                if not is_admin(sender_username):
                    cl.direct_send("❌ Admin only.", thread_ids=[thread_id])
                else:
                    users = list(current_users.values())
                    chunk_size = 5
                    for i in range(0, len(users), chunk_size):
                        chunk = users[i:i + chunk_size]
                        mentions = " ".join([f"@{u}" for u in chunk])
                        cl.direct_send(f"📢 ANNOUNCEMENT:\n{mentions}", thread_ids=[thread_id])
                        human_typing_pause(1.5, 3.2)

            # ----------------------------------------------------
            # 6. HACK ANIMATION (Admin)
            # ----------------------------------------------------
            elif text.startswith(".hack "):
                if not is_admin(sender_username):
                    cl.direct_send("❌ Admin only.", thread_ids=[thread_id])
                else:
                    target = text.split(".hack ")[1].strip()
                    frames = [
                        f"💻 Initiating breach on @{target}...",
                        "🟩⬜⬜⬜⬜ 20% - Bypassing firewall",
                        "🟩🟩🟩⬜⬜ 60% - Extracting database",
                        "🟩🟩🟩🟩🟩 100% - Infiltration complete",
                        f"🔓 Compromised @{target}! \nPassword found: ilovemyex123"
                    ]
                    for frame in frames:
                        cl.direct_send(frame, thread_ids=[thread_id])
                        human_typing_pause(1.8, 2.8)

            # ----------------------------------------------------
            # 7. WARN & KICK (Admin)
            # ----------------------------------------------------
            elif text in [".warn", ".kick"]:
                if not is_admin(sender_username):
                    cl.direct_send("❌ Admin only.", thread_ids=[thread_id])
                elif replied_msg:
                    target_id = str(replied_msg.user_id)
                    target_username = cl.user_info(target_id).username
                    human_typing_pause()

                    if text == ".warn":
                        if target_id not in memory["user_warnings"]:
                            memory["user_warnings"][target_id] = 0

                        memory["user_warnings"][target_id] += 1
                        strikes = memory["user_warnings"][target_id]

                        if strikes >= 3:
                            cl.direct_send(
                                f"🚨 @{target_username} reached 3 strikes. Removing...",
                                thread_ids=[thread_id]
                            )
                            human_typing_pause(1.0, 2.0)
                            cl.direct_thread_remove_users(thread_id, [target_id])
                            memory["user_warnings"][target_id] = 0
                        else:
                            cl.direct_send(
                                f"⚠️ @{target_username} warned. (Strike {strikes}/3)",
                                thread_ids=[thread_id]
                            )

                    elif text == ".kick":
                        if target_id == str(cl.user_id):
                            cl.direct_send("I cannot kick myself!", thread_ids=[thread_id])
                        else:
                            try:
                                cl.direct_thread_remove_users(thread_id, [target_id])
                                cl.direct_send("Target removed.", thread_ids=[thread_id])
                            except Exception:
                                cl.direct_send("❌ Failed to kick. Make sure I am a Group Admin.", thread_ids=[thread_id])

            # Update memory and write to volume
            memory["last_processed_messages"][thread_id] = latest_message.id
            save_memory()

    except Exception as e:
        # Silent loop pass to prevent app crash on transient network errors
        pass

# ==========================================
# 6. EXECUTION LOOP WITH ANTI-BOT JITTER
# ==========================================
while True:
    process_group_management()
    # Random sleep interval between 6 to 13 seconds
    time.sleep(random.uniform(6.0, 13.0))
