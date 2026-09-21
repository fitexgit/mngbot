# -*- coding: utf-8 -*-
import os
import sys
import subprocess
import shutil
import time
import datetime
import telebot
import zipfile
import base64
import threading
import json
import tempfile
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- Initial Setup and Configuration ---

# Read sensitive data from environment variables (Railway / Docker / .env)
# BOT_TOKEN  → set in Railway Variables or .env
# ADMIN_IDS  → comma-separated list of admin user IDs (e.g. 6271667194,123456789)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    print("❌ ERROR: BOT_TOKEN environment variable is not set!")
    sys.exit(1)

_admin_raw = os.getenv("ADMIN_IDS", "")
if not _admin_raw:
    print("❌ ERROR: ADMIN_IDS environment variable is not set!")
    sys.exit(1)

try:
    ADMIN_IDS = [int(x.strip()) for x in _admin_raw.split(",") if x.strip()]
    if not ADMIN_IDS:
        raise ValueError("ADMIN_IDS is empty")
except Exception as e:
    print(f"❌ ERROR: Invalid ADMIN_IDS format. Use comma-separated numbers. Error: {e}")
    sys.exit(1)

# --- Terminal Logging with Colors and Emojis ---
class Logger:
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'

    @staticmethod
    def info(message): print(f"{Logger.CYAN}ℹ️  [INFO] {time.strftime('%Y-%m-%d %H:%M:%S')}: {message}{Logger.ENDC}")
    @staticmethod
    def success(message): print(f"{Logger.GREEN}✅ [SUCCESS] {time.strftime('%Y-%m-%d %H:%M:%S')}: {message}{Logger.ENDC}")
    @staticmethod
    def warning(message): print(f"{Logger.WARNING}⚠️  [WARNING] {time.strftime('%Y-%m-%d %H:%M:%S')}: {message}{Logger.ENDC}")
    @staticmethod
    def error(message): print(f"{Logger.FAIL}❌ [ERROR] {time.strftime('%Y-%m-%d %H:%M:%S')}: {message}{Logger.ENDC}")

# --- Initialize the main bot ---
try:
    bot = telebot.TeleBot(BOT_TOKEN)
    bot_info = bot.get_me()
    Logger.success(f"Connected to bot: {bot_info.first_name} (@{bot_info.username})")
except Exception as e:
    Logger.error(f"Failed to connect to Telegram API. Is your BOT_TOKEN valid? Error: {e}")
    sys.exit(1)

# --- Project Paths and Environment ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHON = sys.executable

# On platforms with a read-only app directory (Pxxl, Railway, etc.) the
# managed bots must live on a writable path. Prefer BOTS_DIR env, then /tmp.
# If you later attach a persistent volume, set e.g. BOTS_DIR=/data/bots
BOTS_DIR = os.getenv("BOTS_DIR", "/tmp/bots")
try:
    os.makedirs(BOTS_DIR, exist_ok=True)
except OSError as e:
    print(f"❌ ERROR: Cannot create BOTS_DIR at {BOTS_DIR}: {e}")
    sys.exit(1)

# Popular packages commonly used in Telegram bots (≈50 packages)
POPULAR_PACKAGES = [
    "pyTelegramBotAPI", "python-telegram-bot", "aiogram", "telethon", "pyrogram",
    "requests", "aiohttp", "httpx", "urllib3", "beautifulsoup4", "lxml",
    "pillow", "numpy", "pandas", "openpyxl", "python-dotenv",
    "psutil", "schedule", "APScheduler", "celery", "redis",
    "sqlalchemy", "aiosqlite", "pymongo", "motor", "peewee",
    "cryptography", "pycryptodome", "PyJWT", "passlib",
    "qrcode", "python-dateutil", "pytz", "arrow",
    "tqdm", "rich", "colorama", "loguru",
    "ffmpeg-python", "pydub", "mutagen",
    "google-api-python-client", "google-auth", "gspread",
    "openai", "anthropic", "langchain",
    "flask", "fastapi", "uvicorn", "gunicorn",
    "selenium", "playwright", "undetected-chromedriver",
    "yt-dlp", "youtube-dl", "instaloader",
    "emoji", "humanize", "tabulate", "prettytable"
]

# --- State Management ---
running_bots = {}  # {bot_name: {'process': obj, 'start_time': ts}}
user_states = {}   # For multi-step operations
watchdog_bots = set() # For auto-restart feature

# --- psutil Check ---
try:
    import psutil
    PSUTIL_AVAILABLE = True
    Logger.info("psutil library found. System and per-bot stats are enabled.")
except ImportError:
    PSUTIL_AVAILABLE = False
    Logger.warning("psutil library not found. System stats will be disabled. Run: pip install psutil")

# --- Security: Admin-Only Decorator ---
def is_admin(func):
    def wrapper(message):
        user_id = message.from_user.id
        if isinstance(message, telebot.types.CallbackQuery):
            user_id = message.from_user.id

        if user_id not in ADMIN_IDS:
            if isinstance(message, telebot.types.CallbackQuery):
                bot.answer_callback_query(message.id, "⚠️ Access Denied.", show_alert=True)
            else:
                bot.reply_to(message, "⚠️ Access Denied. You are not authorized.")
            Logger.warning(f"Unauthorized access attempt by user ID: {user_id}")
            return
        func(message)
    return wrapper

# --- Keyboards ---
def main_menu_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    buttons = [
        InlineKeyboardButton("🤖 Manage Bots", callback_data="manage_bots"),
        InlineKeyboardButton("🛠️ Tools", callback_data="tools"),
    ]
    if PSUTIL_AVAILABLE:
        buttons.append(InlineKeyboardButton("🖥️ System Stats", callback_data="system_stats"))
    keyboard.add(*buttons)
    return keyboard

def bot_menu_keyboard(bot_name):
    keyboard = InlineKeyboardMarkup(row_width=3)
    watchdog_text = "👁️ Monitor: ON 🟢" if bot_name in watchdog_bots else "👁️ Monitor: OFF 🔴"
    keyboard.add(
        InlineKeyboardButton("▶️ Start", callback_data=f"start_{bot_name}"),
        InlineKeyboardButton("⏹ Stop", callback_data=f"stop_{bot_name}"),
        InlineKeyboardButton("🔄 Restart", callback_data=f"restart_{bot_name}"),
        InlineKeyboardButton("📄 View Log", callback_data=f"log_{bot_name}"),
        InlineKeyboardButton("📜 Download Log", callback_data=f"downloadlog_{bot_name}"),
        InlineKeyboardButton("📊 Stats", callback_data=f"stats_{bot_name}"),
        InlineKeyboardButton("📂 Manage Files", callback_data=f"files_{bot_name}"),
        InlineKeyboardButton("✏️ Rename", callback_data=f"rename_{bot_name}"),
        InlineKeyboardButton(watchdog_text, callback_data=f"watchdog_{bot_name}"),
        InlineKeyboardButton("💾 Backup", callback_data=f"backup_{bot_name}"),
        InlineKeyboardButton("🔑 Variables", callback_data=f"vars_{bot_name}"),
        InlineKeyboardButton("▶️ Start Cmd", callback_data=f"startcmd_{bot_name}"),
        InlineKeyboardButton("🧹 Clear Log", callback_data=f"clearlog_{bot_name}"),
        InlineKeyboardButton("🗑️ Delete", callback_data=f"delete_{bot_name}")
    )
    keyboard.add(InlineKeyboardButton("⬅️ Back to Bot List", callback_data="manage_bots"))
    return keyboard

def tools_menu_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton("➕ Add New Bot", callback_data="create_bot"),
        InlineKeyboardButton("🔗 Add Bot from GitHub", callback_data="create_bot_github"),
        InlineKeyboardButton("🔄 Restore Backup", callback_data="restore_backup"),
        InlineKeyboardButton("📦 Install Python Packages", callback_data="install_package"),
        InlineKeyboardButton("⭐ Install Popular Packages (~50)", callback_data="install_popular"),
        InlineKeyboardButton("📚 List Installed Packages", callback_data="list_packages"),
        InlineKeyboardButton("⌨️ Run Command", callback_data="run_command"),
        InlineKeyboardButton("💾 Backup All Bots", callback_data="backup_all"),
        InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="main_menu")
    )
    return keyboard

# --- Bot Management Core Functions ---
def get_bots_list():
    if not os.path.exists(BOTS_DIR): os.makedirs(BOTS_DIR)
    return sorted([d for d in os.listdir(BOTS_DIR) if os.path.isdir(os.path.join(BOTS_DIR, d))])



def get_start_cmd(bot_name):
    """Custom start command file: one line, e.g. python main.py  or  ./decryptbot"""
    path = os.path.join(BOTS_DIR, bot_name, "start_cmd.txt")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cmd = f.read().strip()
                if cmd:
                    return cmd
        except:
            pass
    return None

def save_start_cmd(bot_name, cmd):
    path = os.path.join(BOTS_DIR, bot_name, "start_cmd.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(cmd.strip())

def get_bot_env(bot_name):
    """Load per-bot environment variables from env.json"""
    env_path = os.path.join(BOTS_DIR, bot_name, "env.json")
    if not os.path.exists(env_path):
        return {}
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        Logger.warning(f"Failed to load env.json for '{bot_name}': {e}")
        return {}

def save_bot_env(bot_name, env_dict):
    """Save per-bot environment variables to env.json"""
    env_path = os.path.join(BOTS_DIR, bot_name, "env.json")
    try:
        with open(env_path, "w", encoding="utf-8") as f:
            json.dump(env_dict, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        Logger.error(f"Failed to save env.json for '{bot_name}': {e}")
        return False

def detect_bot_type(bot_dir):
    """Detect whether a bot is Python or Go based on files present."""
    if os.path.exists(os.path.join(bot_dir, "go.mod")) or os.path.exists(os.path.join(bot_dir, "main.go")):
        return "go"
    for name in ("bot.py", "main.py", "app.py", "run.py"):
        if os.path.exists(os.path.join(bot_dir, name)):
            return "python"
    return None

def find_python_entry(bot_dir):
    for name in ("bot.py", "main.py", "app.py", "run.py"):
        p = os.path.join(bot_dir, name)
        if os.path.exists(p):
            return p
    return None

def ensure_data_dir(bot_name, bot_dir, bot_env):
    """
    Ensure a persistent data directory exists for the bot and set common
    path-related environment variables if the user has not overridden them.
    This makes bots that expect DATA_DIR=/data (or SOURCES_FILE etc.) work
    correctly when managed as a subprocess.
    """
    data_dir = os.path.join(bot_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    # Only auto-set if the user has not already defined them in Variables
    defaults = {
        "DATA_DIR": data_dir,
        "SOURCES_FILE": os.path.join(data_dir, "sources.json"),
        "STATE_FILE": os.path.join(data_dir, "last_ids.json"),
        "CREDS_FILE": os.path.join(data_dir, "x_credentials.json"),
        "DB_PATH": os.path.join(data_dir, "bot.db"),
    }
    applied = []
    for key, value in defaults.items():
        if key not in bot_env or not str(bot_env.get(key, "")).strip():
            bot_env[key] = value
            applied.append(key)
    if applied:
        Logger.info(f"Auto-set data paths for '{bot_name}': {applied} → {data_dir}")
    return data_dir

def start_bot(bot_name, chat_id, silent=False):
    if bot_name in running_bots and running_bots[bot_name]['process'].poll() is None:
        if not silent:
            bot.send_message(chat_id, f"⚠️ Bot `{bot_name}` is already running.", parse_mode="Markdown")
        return False

    bot_dir = os.path.join(BOTS_DIR, bot_name)
    if not os.path.isdir(bot_dir):
        if not silent:
            bot.send_message(chat_id, f"❌ Bot folder `{bot_name}` not found.", parse_mode="Markdown")
        return False

    bot_type = detect_bot_type(bot_dir)
    if bot_type is None:
        if not silent:
            bot.send_message(
                chat_id,
                f"❌ Cannot detect bot type for `{bot_name}`.\n"
                f"Need `bot.py` / `main.py` (Python) or `main.go` / `go.mod` (Go).",
                parse_mode="Markdown"
            )
        return False

    # Build command — custom start_cmd.txt overrides auto-detect
    custom_cmd = get_start_cmd(bot_name)
    if custom_cmd:
        # Support: "python main.py"  /  "python3 bot.py"  /  "./decryptbot"  /  "go run ."
        parts = custom_cmd.split()
        if parts[0] in ("python", "python3"):
            parts[0] = VENV_PYTHON
        cmd = parts
        type_label = f"Custom: {custom_cmd}"
        Logger.info(f"Using custom start command for '{bot_name}': {cmd}")
    elif bot_type == "python":
        entry = find_python_entry(bot_dir)
        if not entry:
            if not silent:
                bot.send_message(chat_id, f"❌ No Python entry file found for `{bot_name}`.\nUse «▶️ Start Cmd» to set e.g. `python main.py`", parse_mode="Markdown")
            return False
        cmd = [VENV_PYTHON, entry]
        type_label = "Python"
    else:  # go
        binary = os.path.join(bot_dir, "decryptbot")
        if not os.path.exists(binary):
            for f in os.listdir(bot_dir):
                fp = os.path.join(bot_dir, f)
                if os.path.isfile(fp) and os.access(fp, os.X_OK) and not f.endswith((".go", ".mod", ".sum")):
                    binary = fp
                    break
            else:
                binary = None

        if binary and os.path.exists(binary):
            cmd = [binary]
            type_label = "Go (binary)"
        else:
            go_bin = shutil.which("go")
            if not go_bin:
                if not silent:
                    bot.send_message(
                        chat_id,
                        f"❌ Go is not installed and no binary found for `{bot_name}`.\n"
                        f"Set a custom Start Cmd or upload a built binary.",
                        parse_mode="Markdown"
                    )
                Logger.error(f"Cannot start Go bot '{bot_name}': go not found and no binary")
                return False
            cmd = [go_bin, "run", "."]
            type_label = "Go (go run)"

    log_path = os.path.join(bot_dir, "bot.log")

    # Environment: system + custom Variables + auto data paths
    bot_env = os.environ.copy()
    custom_env = get_bot_env(bot_name)
    if custom_env:
        bot_env.update(custom_env)
        Logger.info(f"Loaded {len(custom_env)} custom Variable(s) for '{bot_name}': {list(custom_env.keys())}")

    data_dir = ensure_data_dir(bot_name, bot_dir, bot_env)

    try:
        with open(log_path, 'a', encoding='utf-8') as log_file:
            process = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=bot_dir,
                env=bot_env
            )
    except Exception as e:
        if not silent:
            bot.send_message(chat_id, f"❌ Failed to start `{bot_name}`: {e}", parse_mode="Markdown")
        Logger.error(f"Failed to start '{bot_name}': {e}")
        return False

    running_bots[bot_name] = {'process': process, 'start_time': time.time(), 'type': bot_type}
    if not silent:
        env_note = f"\n🔑 Variables: `{len(custom_env)}` loaded" if custom_env else ""
        data_note = f"\n📁 DATA_DIR: `{data_dir}`"
        bot.send_message(
            chat_id,
            f"✅ Bot `{bot_name}` started successfully.\n"
            f"📦 Type: `{type_label}` | PID: `{process.pid}`{env_note}{data_note}",
            parse_mode="Markdown"
        )
    Logger.success(
        f"Bot '{bot_name}' started ({type_label}) PID={process.pid}"
        + (f" vars={list(custom_env.keys())}" if custom_env else "")
        + f" data={data_dir}"
    )
    return True

def stop_bot(bot_name, chat_id, silent=False):
    watchdog_bots.discard(bot_name)
    if bot_name not in running_bots or running_bots[bot_name]['process'].poll() is not None:
        if not silent: bot.send_message(chat_id, f"⚠️ Bot `{bot_name}` is not currently running.", parse_mode="Markdown")
        return False
    process = running_bots[bot_name]['process']
    pid = process.pid
    try:
        if PSUTIL_AVAILABLE:
            parent = psutil.Process(pid)
            for child in parent.children(recursive=True): child.terminate()
            parent.terminate()
            process.wait(timeout=5)
        else:
            process.terminate()
            process.wait(timeout=5)
    except (psutil.NoSuchProcess, subprocess.TimeoutExpired, NameError):
        try:
            process.kill()
        except: # process may be already dead
            pass
    if bot_name in running_bots:
        del running_bots[bot_name]
    if not silent: bot.send_message(chat_id, f"🛑 Bot `{bot_name}` stopped successfully.", parse_mode="Markdown")
    Logger.info(f"Bot '{bot_name}' (PID: {pid}) stopped.")
    return True

def delete_bot(bot_name, chat_id):
    if bot_name in running_bots: stop_bot(bot_name, chat_id, silent=True)
    watchdog_bots.discard(bot_name)
    bot_folder = os.path.join(BOTS_DIR, bot_name)
    try:
        shutil.rmtree(bot_folder)
        bot.send_message(chat_id, f"🗑️ Bot folder `{bot_name}` was deleted successfully.", parse_mode="Markdown")
        Logger.info(f"Bot folder '{bot_name}' deleted.")
    except Exception as e:
        bot.send_message(chat_id, f"❌ Error deleting bot folder: {e}")
        Logger.error(f"Failed to delete bot folder '{bot_name}': {e}")

# --- Feature Functions (Logs, Stats, Backups) ---
def view_log(bot_name, chat_id, message_id):
    log_path = os.path.join(BOTS_DIR, bot_name, "bot.log")
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🔄 Refresh", callback_data=f"log_{bot_name}"), InlineKeyboardButton("⬅️ Back", callback_data=f"bot_{bot_name}"))
    
    log_text = f"📄 Log for `{bot_name}` is empty."
    if os.path.exists(log_path) and os.path.getsize(log_path) > 0:
        with open(log_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        last_lines = lines[-50:]
        log_content = ''.join(last_lines).strip()
        
        if log_content:
            header = f"📄 Last 50 lines of log for `{bot_name}`:\n\n"
            base_text = f"```\n{log_content}\n```"
            full_text = header + base_text
            
            if len(full_text) > 4096:
                allowed_len = 4096 - len(header) - len("```\n... (log truncated) ...\n\n```")
                truncated_content = log_content[-allowed_len:]
                log_text = f"{header}```\n... (log truncated) ...\n{truncated_content}\n```"
            else:
                log_text = full_text

    try:
        bot.edit_message_text(log_text, chat_id, message_id, reply_markup=keyboard, parse_mode="Markdown")
    except telebot.apihelper.ApiTelegramException as e:
        if 'message is not modified' not in e.description:
             Logger.error(f"Error updating log view: {e}")
             bot.send_message(chat_id, "❌ Error displaying logs. The log content might be too long or malformed.")


def get_bot_stats(call, bot_name):
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    if not PSUTIL_AVAILABLE:
        bot.answer_callback_query(call.id, "psutil library is required for this feature.", show_alert=True)
        return
    if bot_name not in running_bots or running_bots[bot_name]['process'].poll() is not None:
        bot.answer_callback_query(call.id, f"Bot {bot_name} is not running.", show_alert=True)
        return
    try:
        p = psutil.Process(running_bots[bot_name]['process'].pid)
        uptime = str(datetime.timedelta(seconds=int(time.time() - running_bots[bot_name]['start_time'])))
        stats_text = (f"📊 *Stats for Bot: `{bot_name}`*\n\n"
                      f"PID: `{p.pid}`\n"
                      f"CPU Usage: `{p.cpu_percent(interval=0.5):.2f}%`\n"
                      f"RAM Usage: `{p.memory_info().rss / (1024*1024):.2f} MB`\n"
                      f"Uptime: `{uptime}`")
        keyboard = InlineKeyboardMarkup().add(InlineKeyboardButton("🔄 Refresh", callback_data=f"stats_{bot_name}"), InlineKeyboardButton("⬅️ Back", callback_data=f"bot_{bot_name}"))
        bot.edit_message_text(stats_text, chat_id, message_id, reply_markup=keyboard, parse_mode="Markdown")
    except (psutil.NoSuchProcess, Exception) as e:
        bot.answer_callback_query(call.id, f"Could not fetch stats: {e}", show_alert=True)

def get_system_stats(chat_id, message_id):
    if not PSUTIL_AVAILABLE: return
    cpu = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    stats_text = (
        f"🖥️ *System Resource Status*\n\n"
        f"📊 *CPU Usage*: `{cpu}%`\n"
        f"🧠 *RAM Usage*: `{ram.used / (1024**3):.2f} / {ram.total / (1024**3):.2f} GB ({ram.percent}%)`\n"
        f"💾 *Disk Usage*: `{disk.used / (1024**3):.2f} / {disk.total / (1024**3):.2f} GB ({disk.percent}%)`"
    )
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🔄 Refresh", callback_data="system_stats"))
    keyboard.add(InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="main_menu"))
    try:
        bot.edit_message_text(stats_text, chat_id, message_id, reply_markup=keyboard, parse_mode="Markdown")
    except telebot.apihelper.ApiTelegramException as e:
        if 'message is not modified' not in e.description: raise

def backup_bot(bot_name, chat_id):
    bot_folder = os.path.join(BOTS_DIR, bot_name)
    if not os.path.isdir(bot_folder):
        bot.send_message(chat_id, f"❌ Bot folder `{bot_name}` not found.", parse_mode="Markdown")
        Logger.error(f"Backup failed: folder not found for '{bot_name}'")
        return

    req_path = os.path.join(bot_folder, "requirements_backup.txt")
    try:
        result = subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True, encoding='utf-8')
        with open(req_path, 'w', encoding='utf-8') as f:
            f.write(result)
        Logger.info(f"Generated requirements_backup.txt for '{bot_name}'")
    except Exception as e:
        Logger.warning(f"Could not generate requirements for backup of '{bot_name}': {e}")
    
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            backup_base = os.path.join(tmpdir, f"{bot_name}_backup")
            backup_path = shutil.make_archive(backup_base, 'zip', bot_folder)
            with open(backup_path, 'rb') as doc:
                bot.send_document(chat_id, doc, caption=f"📦 Complete backup for bot `{bot_name}` (includes requirements_backup.txt).", parse_mode="Markdown")
        Logger.success(f"Backup created and sent for '{bot_name}'")
    except Exception as e:
        bot.send_message(chat_id, f"❌ Backup failed for `{bot_name}`: {e}", parse_mode="Markdown")
        Logger.error(f"Backup failed for '{bot_name}': {e}")
    finally:
        if os.path.exists(req_path):
            try:
                os.remove(req_path)
            except:
                pass

def backup_all_bots(chat_id):
    bots = get_bots_list()
    if not bots:
        bot.send_message(chat_id, "📭 No bots found to back up.")
        Logger.info("Backup all skipped: no bots found")
        return

    Logger.info(f"Starting full backup of {len(bots)} bot(s)...")
    global_req = os.path.join(BOTS_DIR, "global_requirements_backup.txt")
    try:
        result = subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True, encoding='utf-8')
        with open(global_req, 'w', encoding='utf-8') as f:
            f.write(result)
        Logger.info("Generated global_requirements_backup.txt")
    except Exception as e:
        Logger.warning(f"Could not generate global requirements: {e}")
        
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            backup_base_name = os.path.join(tmpdir, f"all_bots_backup_{int(time.time())}")
            backup_path = shutil.make_archive(backup_base_name, 'zip', root_dir=BOTS_DIR)
            with open(backup_path, 'rb') as doc:
                bot.send_document(chat_id, doc, caption=f"📦 Full backup of {len(bots)} bot(s) + global requirements created.")
        Logger.success(f"Full backup created and sent ({len(bots)} bots)")
    except Exception as e:
        bot.send_message(chat_id, f"❌ Full backup failed: {e}")
        Logger.error(f"Full backup failed: {e}")
    finally:
        if os.path.exists(global_req):
            try:
                os.remove(global_req)
            except:
                pass

# --- Watchdog ---
def monitor_bots():
    Logger.info("Watchdog thread started.")
    while True:
        time.sleep(60)
        bots_to_monitor = list(watchdog_bots)
        for bot_name in bots_to_monitor:
            if not (bot_name in running_bots and running_bots[bot_name]['process'].poll() is None):
                Logger.warning(f"Watchdog: Bot '{bot_name}' is down! Restarting...")
                if start_bot(bot_name, ADMIN_IDS[0], silent=True):
                    Logger.success(f"Watchdog: Bot '{bot_name}' restarted successfully.")
                    bot.send_message(ADMIN_IDS[0], f"🚨 **Watchdog Alert** 🚨\n\nBot `{bot_name}` was found down and has been restarted automatically.", parse_mode="Markdown")
                else:
                    Logger.error(f"Watchdog: Failed to restart '{bot_name}'.")
                    bot.send_message(ADMIN_IDS[0], f"🚨 **Watchdog Alert** 🚨\n\nBot `{bot_name}` is down and could NOT be restarted. Please check its logs.", parse_mode="Markdown")
                    watchdog_bots.discard(bot_name)

# --- Command & Message Handlers ---
@bot.message_handler(commands=['start'])
@is_admin
def send_welcome(message):
    bot.send_message(message.chat.id, "Welcome to the Bot Management Panel.", reply_markup=main_menu_keyboard())

@bot.message_handler(commands=['reboot'])
@is_admin
def reboot_server_command(message):
    keyboard = InlineKeyboardMarkup().add(InlineKeyboardButton("⚠️ Yes, Reboot NOW", callback_data="confirm_reboot"), InlineKeyboardButton("↩️ Cancel", callback_data="main_menu"))
    bot.send_message(message.chat.id, "🚨 **DANGER ZONE** 🚨\n\nAre you sure you want to reboot the entire server?", parse_mode="Markdown", reply_markup=keyboard)

@bot.message_handler(content_types=['text'])
@is_admin
def handle_text_input(message):
    chat_id = message.chat.id
    if chat_id not in user_states: return
    state = user_states[chat_id]
    action = state.get('action')

    if action == 'create_bot':
        bot_name = message.text.strip()
        del user_states[chat_id]
        if not bot_name.isalnum() or " " in bot_name:
            bot.send_message(chat_id, "❌ Invalid name. Use letters/numbers only.", reply_markup=main_menu_keyboard())
            return
        new_bot_dir = os.path.join(BOTS_DIR, bot_name)
        if os.path.exists(new_bot_dir):
            bot.send_message(chat_id, f"❌ Bot `{bot_name}` already exists.", parse_mode="Markdown", reply_markup=main_menu_keyboard())
            return
        os.makedirs(new_bot_dir)
        user_states[chat_id] = {'action': 'upload_zip_for_new_bot', 'bot_name': bot_name}
        bot.send_message(chat_id, f"✅ Bot folder `{bot_name}` created.\n\nNow, upload the bot's source code as a `.zip` file.", parse_mode="Markdown")

    elif action == 'create_bot_github_name':
        bot_name = message.text.strip()
        if not bot_name.isalnum() or " " in bot_name:
            bot.send_message(chat_id, "❌ Invalid name. Use letters/numbers only.", reply_markup=main_menu_keyboard())
            del user_states[chat_id]
            return
        new_bot_dir = os.path.join(BOTS_DIR, bot_name)
        if os.path.exists(new_bot_dir):
            bot.send_message(chat_id, f"❌ Bot `{bot_name}` already exists.", parse_mode="Markdown", reply_markup=main_menu_keyboard())
            del user_states[chat_id]
            return
        user_states[chat_id] = {'action': 'create_bot_github_url', 'bot_name': bot_name}
        bot.send_message(chat_id, f"✅ Name accepted: `{bot_name}`\n\nNow send the **GitHub repository URL** (e.g. `https://github.com/user/repo` or `https://github.com/user/repo.git`)", parse_mode="Markdown")

    elif action == 'create_bot_github_url':
        github_url = message.text.strip()
        bot_name = state['bot_name']
        del user_states[chat_id]
        
        if not (github_url.startswith("https://github.com/") or github_url.startswith("git@github.com:")):
            bot.send_message(chat_id, "❌ Invalid GitHub URL. Must start with `https://github.com/`", parse_mode="Markdown", reply_markup=main_menu_keyboard())
            return
            
        new_bot_dir = os.path.join(BOTS_DIR, bot_name)
        os.makedirs(new_bot_dir, exist_ok=True)
        
        msg = bot.send_message(chat_id, f"⏳ Cloning repository into `{bot_name}`...\nThis may take a while.", parse_mode="Markdown")
        
        try:
            cmd = ["git", "clone", "--depth", "1", github_url, new_bot_dir]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=180, encoding='utf-8')
            
            if result.returncode != 0:
                shutil.rmtree(new_bot_dir, ignore_errors=True)
                error_msg = result.stderr or result.stdout or "Unknown error"
                bot.edit_message_text(f"❌ Clone failed:\n\n```\n{error_msg[:1500]}\n```", chat_id, msg.message_id, parse_mode="Markdown", reply_markup=main_menu_keyboard())
                return
            
            btype = detect_bot_type(new_bot_dir)
            if btype == "go":
                bot.edit_message_text(
                    f"✅ Go bot `{bot_name}` cloned successfully!\n\n"
                    f"🔵 Detected as **Go** project (`go.mod` / `main.go`).\n"
                    f"📁 Data will be stored in `bots/{bot_name}/data/`\n"
                    f"🔑 Set Variables (BOT_TOKEN, ADMIN_IDS, ...) before starting.",
                    chat_id, msg.message_id, parse_mode="Markdown", reply_markup=main_menu_keyboard()
                )
            elif btype == "python":
                entry = find_python_entry(new_bot_dir)
                entry_name = os.path.basename(entry) if entry else "?"
                bot.edit_message_text(
                    f"✅ Python bot `{bot_name}` cloned successfully!\n\n"
                    f"🐍 Entry point: `{entry_name}`",
                    chat_id, msg.message_id, parse_mode="Markdown", reply_markup=main_menu_keyboard()
                )
            else:
                bot.edit_message_text(
                    f"✅ Repository cloned into `{bot_name}`.\n\n"
                    f"⚠️ Could not detect Python or Go entry point.\n"
                    f"Make sure you have `bot.py`/`main.py` or `main.go`/`go.mod`.",
                    chat_id, msg.message_id, parse_mode="Markdown", reply_markup=main_menu_keyboard()
                )
                
            Logger.success(f"Bot '{bot_name}' cloned from {github_url}")
            
        except subprocess.TimeoutExpired:
            shutil.rmtree(new_bot_dir, ignore_errors=True)
            bot.edit_message_text("❌ Clone timed out (3 minutes). Try a smaller repo or upload zip instead.", chat_id, msg.message_id, reply_markup=main_menu_keyboard())
        except FileNotFoundError:
            shutil.rmtree(new_bot_dir, ignore_errors=True)
            bot.edit_message_text("❌ `git` is not installed on this server. Please install git or use zip upload.", chat_id, msg.message_id, reply_markup=main_menu_keyboard())
        except Exception as e:
            shutil.rmtree(new_bot_dir, ignore_errors=True)
            bot.edit_message_text(f"❌ Error during clone: `{e}`", chat_id, msg.message_id, parse_mode="Markdown", reply_markup=main_menu_keyboard())

    elif action == 'rename_bot':
        new_name = message.text.strip()
        old_name = state['bot_name']
        del user_states[chat_id]
        if not new_name.isalnum() or " " in new_name:
            bot.send_message(chat_id, "❌ Invalid name. Use letters/numbers only.", reply_markup=main_menu_keyboard())
            return
        if os.path.exists(os.path.join(BOTS_DIR, new_name)):
            bot.send_message(chat_id, f"❌ Bot `{new_name}` already exists.", parse_mode="Markdown", reply_markup=main_menu_keyboard())
            return
        
        was_running = old_name in running_bots and running_bots[old_name]['process'].poll() is None
        if was_running: stop_bot(old_name, chat_id, silent=True)
        
        os.rename(os.path.join(BOTS_DIR, old_name), os.path.join(BOTS_DIR, new_name))
        
        if old_name in watchdog_bots:
            watchdog_bots.discard(old_name)
            watchdog_bots.add(new_name)

        bot.send_message(chat_id, f"✅ Bot `{old_name}` renamed to `{new_name}`.", parse_mode="Markdown", reply_markup=main_menu_keyboard())
        if was_running:
            bot.send_message(chat_id, "Attempting to restart the bot with its new name...")
            start_bot(new_name, chat_id)

    elif action == 'install_packages':
        raw = message.text.strip()
        del user_states[chat_id]
        # Support both space-separated and requirements-style (pkg==ver)
        packages = []
        for line in raw.replace(",", " ").splitlines():
            for part in line.split():
                part = part.strip()
                if part and not part.startswith("#"):
                    packages.append(part)
        if not packages:
            bot.send_message(chat_id, "No package names entered.", reply_markup=main_menu_keyboard())
            return
        msg = bot.send_message(chat_id, f"📦 Installing `{len(packages)}` package(s)...\n`{' '.join(packages[:8])}`" + ("..." if len(packages)>8 else ""), parse_mode="Markdown")
        Logger.info(f"Installing packages: {packages}")
        try:
            command = [sys.executable, "-m", "pip", "install", "--upgrade"] + packages
            result = subprocess.check_output(command, stderr=subprocess.STDOUT, text=True, encoding='utf-8', timeout=300)
            # Keep only last lines to avoid MESSAGE_TOO_LONG
            lines = result.strip().splitlines()
            short = "\n".join(lines[-25:]) if len(lines) > 25 else result.strip()
            if len(short) > 3500:
                short = short[-3500:]
            bot.edit_message_text(
                f"✅ Successfully installed `{len(packages)}` package(s).\n\n```\n{short}\n```",
                chat_id, msg.message_id, parse_mode="Markdown", reply_markup=main_menu_keyboard()
            )
            Logger.success(f"Installed packages: {packages}")
        except subprocess.TimeoutExpired:
            bot.edit_message_text("❌ Install timed out (5 min). Some packages may be installed.", chat_id, msg.message_id, reply_markup=main_menu_keyboard())
            Logger.error("Package install timed out")
        except subprocess.CalledProcessError as e:
            out = (e.output or str(e))[-3000:]
            bot.edit_message_text(f"❌ Error installing:\n\n```\n{out}\n```", chat_id, msg.message_id, parse_mode="Markdown", reply_markup=main_menu_keyboard())
            Logger.error(f"Package install failed: {out[:200]}")

    elif action == 'run_command':
        command = message.text.strip()
        del user_states[chat_id]
        msg = bot.send_message(chat_id, f"Executing: `{command}`", parse_mode="Markdown")
        try:
            result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120, encoding='utf-8')
            output = f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}".strip()
            if not output: output = "Command executed with no output."
            if len(output) > 4000: output = output[:4000] + "\n\n[... Output truncated ...]"
            bot.edit_message_text(f"🖥️ **Output:**\n\n```\n{output}\n```", chat_id, msg.message_id, parse_mode="Markdown", reply_markup=main_menu_keyboard())
        except subprocess.TimeoutExpired:
            bot.edit_message_text(f"❌ **Error:** Command timed out.", chat_id, msg.message_id, reply_markup=main_menu_keyboard())
        except Exception as e:
            bot.edit_message_text(f"❌ **Error:**\n\n`{e}`", chat_id, msg.message_id, parse_mode="Markdown", reply_markup=main_menu_keyboard())



    elif action == 'set_start_cmd':
        bot_name = state['bot_name']
        del user_states[chat_id]
        cmd = message.text.strip()
        if cmd.upper() == "AUTO":
            path = os.path.join(BOTS_DIR, bot_name, "start_cmd.txt")
            if os.path.exists(path):
                os.remove(path)
            bot.send_message(chat_id, f"✅ Start command cleared. Will auto-detect for `{bot_name}`.", parse_mode="Markdown", reply_markup=bot_menu_keyboard(bot_name))
            Logger.info(f"Cleared start cmd for '{bot_name}'")
            return
        save_start_cmd(bot_name, cmd)
        bot.send_message(
            chat_id,
            f"✅ Start command set for `{bot_name}`:\n`{cmd}`\n\nRestart the bot to apply.",
            parse_mode="Markdown",
            reply_markup=bot_menu_keyboard(bot_name)
        )
        Logger.success(f"Start cmd for '{bot_name}': {cmd}")

    elif action == 'set_bot_vars':
        bot_name = state['bot_name']
        del user_states[chat_id]
        text = message.text.strip()
        
        if text.upper() == "CLEAR":
            save_bot_env(bot_name, {})
            bot.send_message(chat_id, f"✅ All variables cleared for `{bot_name}`.", parse_mode="Markdown", reply_markup=bot_menu_keyboard(bot_name))
            Logger.info(f"Cleared all variables for '{bot_name}'")
            return
        
        env = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                bot.send_message(chat_id, f"❌ Invalid line (missing `=`): `{line}`\n\nUse format: `KEY=value`", parse_mode="Markdown")
                return
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key:
                env[key] = value
        
        if not env:
            bot.send_message(chat_id, "❌ No valid variables found.", reply_markup=bot_menu_keyboard(bot_name))
            return
        
        # Merge with existing
        existing = get_bot_env(bot_name)
        existing.update(env)
        save_bot_env(bot_name, existing)
        
        lines = [f"`{k}` = `{v}`" for k, v in existing.items()]
        bot.send_message(
            chat_id,
            f"✅ Variables saved for `{bot_name}`:\n\n" + "\n".join(lines) + "\n\n⚠️ Restart the bot for changes to take effect.",
            parse_mode="Markdown",
            reply_markup=bot_menu_keyboard(bot_name)
        )
        Logger.success(f"Updated variables for '{bot_name}': {list(existing.keys())}")

    elif action == 'edit_file':
        file_path = state['file_path']
        bot_name = state['bot_name']
        file_name_encoded = state['file_name_encoded']
        del user_states[chat_id]
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(message.text)
            bot.send_message(chat_id, f"✅ File saved successfully!")
            call_data = f"viewfile_{bot_name}_{file_name_encoded}"
            mock_call = telebot.types.CallbackQuery(id=0, from_user=message.from_user, data=call_data, chat_instance=0, message=message)
            view_file(mock_call, bot_name, file_name_encoded)

        except Exception as e:
            bot.send_message(chat_id, f"❌ Failed to save file: {e}", reply_markup=bot_menu_keyboard(bot_name))

# --- Document Handler ---
@bot.message_handler(content_types=['document'])
@is_admin
def handle_document(message):
    chat_id = message.chat.id
    if chat_id not in user_states: return

    state = user_states.pop(chat_id)
    action = state.get('action')
    file_name = message.document.file_name

    def process_zip(zip_path, extract_path):
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_path)
        os.remove(zip_path)
        # If zip had a single top-level folder, move its contents up
        try:
            entries = [e for e in os.listdir(extract_path) if e not in ('.', '..')]
            if len(entries) == 1:
                only = os.path.join(extract_path, entries[0])
                if os.path.isdir(only):
                    for item in os.listdir(only):
                        src = os.path.join(only, item)
                        dst = os.path.join(extract_path, item)
                        if os.path.exists(dst):
                            if os.path.isdir(dst):
                                shutil.rmtree(dst)
                            else:
                                os.remove(dst)
                        shutil.move(src, dst)
                    os.rmdir(only)
                    Logger.info(f"Flattened nested folder '{entries[0]}' into bot root")
        except Exception as e:
            Logger.warning(f"Could not flatten zip structure: {e}")

    if action == 'restore_backup':
        if not file_name.lower().endswith('.zip'):
            bot.send_message(chat_id, "❌ Invalid file. Please upload a `.zip` backup.", reply_markup=main_menu_keyboard())
            return
        msg = bot.send_message(chat_id, "📥 Downloading and restoring backup...")
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        temp_zip_path = os.path.join(tempfile.gettempdir(), file_name)
        with open(temp_zip_path, 'wb') as new_file: new_file.write(downloaded_file)
        process_zip(temp_zip_path, BOTS_DIR)
        bot.edit_message_text("✅ Backup successfully restored.", chat_id, msg.message_id, reply_markup=main_menu_keyboard())

    elif action == 'upload_zip_for_new_bot' or action == 'upload_file_to_bot':
        bot_name = state['bot_name']
        bot_path = os.path.join(BOTS_DIR, bot_name)
        
        if action == 'upload_zip_for_new_bot' and not file_name.lower().endswith('.zip'):
            bot.send_message(chat_id, "❌ Invalid file. Must be a `.zip` for a new bot.", reply_markup=main_menu_keyboard())
            shutil.rmtree(bot_path, ignore_errors=True)
            return

        msg = bot.send_message(chat_id, "Downloading file...")
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        local_path = os.path.join(bot_path, file_name)
        with open(local_path, 'wb') as new_file: new_file.write(downloaded_file)

        if file_name.lower().endswith('.zip'):
            bot.edit_message_text("Extracting archive...", chat_id, msg.message_id)
            process_zip(local_path, bot_path)
            bot.edit_message_text(f"✅ Files extracted to `{bot_name}`.", chat_id, msg.message_id, parse_mode="Markdown", reply_markup=main_menu_keyboard())
        else:
            bot.edit_message_text(f"✅ File `{file_name}` uploaded to `{bot_name}`.", chat_id, msg.message_id, parse_mode="Markdown", reply_markup=bot_menu_keyboard(bot_name))

# --- File Management Functions ---
def list_files(call, bot_name):
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    bot_path = os.path.join(BOTS_DIR, bot_name)
    files = [f for f in os.listdir(bot_path)]
    keyboard = InlineKeyboardMarkup(row_width=1)
    for f in sorted(files):
        encoded_name = base64.urlsafe_b64encode(f.encode()).decode()
        icon = "📄" if os.path.isfile(os.path.join(bot_path, f)) else "📁"
        keyboard.add(InlineKeyboardButton(f"{icon} {f}", callback_data=f"viewfile_{bot_name}_{encoded_name}"))
    keyboard.add(InlineKeyboardButton("📤 Upload File", callback_data=f"upload_{bot_name}"))
    keyboard.add(InlineKeyboardButton("⬅️ Back to Bot Menu", callback_data=f"bot_{bot_name}"))
    bot.edit_message_text(f"Files in `{bot_name}`:", chat_id, message_id, reply_markup=keyboard, parse_mode="Markdown")

def view_file(call, bot_name, encoded_name):
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    file_name = base64.urlsafe_b64decode(encoded_name).decode()
    file_path = os.path.join(BOTS_DIR, bot_name, file_name)
    if os.path.isdir(file_path):
        bot.answer_callback_query(call.id, "Cannot view a directory.", show_alert=True)
        return
    
    keyboard = InlineKeyboardMarkup(row_width=3)
    keyboard.add(
        InlineKeyboardButton("✏️ Edit", callback_data=f"editfile_{bot_name}_{encoded_name}"),
        InlineKeyboardButton("📥 Download", callback_data=f"downloadfile_{bot_name}_{encoded_name}"),
        InlineKeyboardButton("🗑️ Delete", callback_data=f"deletefile_{bot_name}_{encoded_name}")
    )
    keyboard.add(InlineKeyboardButton("⬅️ Back to Files", callback_data=f"files_{bot_name}"))
    
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f: content = f.read(3800)
        message_text = f"📄 Content of `{file_name}`:\n\n```\n{content}\n```"
        if len(content) == 3800:
            message_text += "\n\n[... File content truncated ...]"
    except Exception:
        message_text = f"Binary file or read error: `{file_name}`"
    bot.edit_message_text(message_text, chat_id, message_id, reply_markup=keyboard, parse_mode="Markdown")

# --- Callback Query Handler ---
@bot.callback_query_handler(func=lambda call: True)
@is_admin
def callback_handler(call):
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    if call.data == "manage_bots":
        keyboard = InlineKeyboardMarkup(row_width=1)
        bots = get_bots_list()
        for bot_name in bots:
            if bot_name in running_bots and running_bots[bot_name]['process'].poll() is None:
                pid = running_bots[bot_name]['process'].pid
                button_text = f"{bot_name} 🟢 (PID: {pid})"
            else:
                button_text = f"{bot_name} 🔴"
            keyboard.add(InlineKeyboardButton(button_text, callback_data=f"bot_{bot_name}"))
        
        bulk_buttons = [ InlineKeyboardButton("▶️ Start All", callback_data="start_all"), InlineKeyboardButton("⏹️ Stop All", callback_data="stop_all"), InlineKeyboardButton("🔄 Restart All", callback_data="restart_all")]
        keyboard.row(*bulk_buttons)
        keyboard.add(InlineKeyboardButton("🔄 Refresh List", callback_data="manage_bots"))
        keyboard.add(InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="main_menu"))
        text = "Select a bot to manage:" if bots else "No bots found. Add one from the Tools menu."
        try:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=keyboard)
        except telebot.apihelper.ApiTelegramException:
            pass
        bot.answer_callback_query(call.id)
        return

    if call.data in ["start_all", "stop_all", "restart_all"]:
        bots_list = get_bots_list()
        running_bots_list = list(running_bots.keys())
        msg = None
        report_lines = []

        if call.data == "start_all":
            bot.answer_callback_query(call.id, "Starting all bots...")
            msg = bot.send_message(chat_id, f"▶️ Attempting to start all {len(bots_list)} bots...")
            started_bots = [b for b in bots_list if start_bot(b, chat_id, silent=True)]
            report_lines.append(f"✅ Bulk start complete. {len(started_bots)} of {len(bots_list)} bots started.")

        elif call.data == "stop_all":
            bot.answer_callback_query(call.id, "Stopping all bots...")
            msg = bot.send_message(chat_id, f"⏹️ Attempting to stop all {len(running_bots_list)} running bots...")
            stopped_bots = [b for b in running_bots_list if stop_bot(b, chat_id, silent=True)]
            report_lines.append(f"✅ Bulk stop complete. {len(stopped_bots)} bots were stopped.")

        elif call.data == "restart_all":
            bot.answer_callback_query(call.id, "Restarting all bots...")
            msg = bot.send_message(chat_id, f"🔄 Attempting to restart all bots...")
            
            stopped_bots = [b for b in running_bots_list if stop_bot(b, chat_id, silent=True)]
            bot.edit_message_text(f"🛑 {len(stopped_bots)} bots stopped. Now starting...", chat_id, msg.message_id)
            time.sleep(2)
            
            started_bots = [b for b in bots_list if start_bot(b, chat_id, silent=True)]
            
            report_lines.append("✅ Bulk restart complete.")
            report_lines.append(f"▶️ {len(started_bots)} of {len(bots_list)} bots were started.")
            
        if msg:
            bot.edit_message_text('\n'.join(report_lines), chat_id, msg.message_id, parse_mode="Markdown")
            
        time.sleep(1)
        call.data = "manage_bots"
        callback_handler(call)
        return

    # ========== EXACT TOOL ACTIONS (must be before split) ==========
    # These callbacks contain underscores and would be mis-parsed by split("_")
    exact_tools = {
        "main_menu": "main",
        "create_bot": "create_bot",
        "create_bot_github": "create_bot_github",
        "restore_backup": "restore_backup",
        "install_package": "install_package",
        "install_popular": "install_popular",
        "list_packages": "list_packages",
        "run_command": "run_command",
        "backup_all": "backup_all",
        "system_stats": "system_stats",
    }

    if call.data in exact_tools or call.data in ("main_menu", "tools"):
        bot.answer_callback_query(call.id)
        try:
            if call.data == "main_menu" or call.data == "main":
                bot.edit_message_text("Main Menu:", chat_id, message_id, reply_markup=main_menu_keyboard())
                return
            if call.data == "tools":
                bot.edit_message_text("Tools:", chat_id, message_id, reply_markup=tools_menu_keyboard())
                return
            if call.data == "system_stats":
                get_system_stats(chat_id, message_id)
                return
            if call.data == "create_bot":
                user_states[chat_id] = {'action': 'create_bot'}
                bot.edit_message_text("Please enter a name for the new bot (letters and numbers only).\n\n💡 Tip: For GitHub repos use the separate «Add Bot from GitHub» button.", chat_id, message_id)
                Logger.info(f"Admin {chat_id} started create_bot flow")
                return
            if call.data == "create_bot_github":
                user_states[chat_id] = {'action': 'create_bot_github_name'}
                bot.edit_message_text("Please enter a name for the new bot (letters and numbers only):", chat_id, message_id)
                Logger.info(f"Admin {chat_id} started create_bot_github flow")
                return
            if call.data == "restore_backup":
                user_states[chat_id] = {'action': 'restore_backup'}
                bot.edit_message_text("Please upload the `.zip` backup file.", chat_id, message_id)
                Logger.info(f"Admin {chat_id} started restore_backup flow")
                return
            if call.data == "install_package":
                user_states[chat_id] = {'action': 'install_packages'}
                bot.edit_message_text("Enter package names to install, separated by spaces (e.g., `pytelegrambotapi requests`):", chat_id, message_id, parse_mode="Markdown")
                Logger.info(f"Admin {chat_id} started install_package flow")
                return
            if call.data == "install_popular":
                bot.answer_callback_query(call.id, "Installing popular packages... this may take a few minutes.")
                msg = bot.edit_message_text(f"⭐ Installing {len(POPULAR_PACKAGES)} popular packages...\nThis can take 2-5 minutes. Please wait.", chat_id, message_id)
                Logger.info(f"Admin {chat_id} started popular packages installation ({len(POPULAR_PACKAGES)} packages)")
                try:
                    batch_size = 10
                    for i in range(0, len(POPULAR_PACKAGES), batch_size):
                        batch = POPULAR_PACKAGES[i:i+batch_size]
                        command = [sys.executable, "-m", "pip", "install", "--upgrade"] + batch
                        result = subprocess.run(command, capture_output=True, text=True, timeout=300, encoding='utf-8')
                        Logger.info(f"Installed batch {i//batch_size + 1}: {', '.join(batch[:3])}...")
                    summary = f"✅ Installation finished!\n\nInstalled/Updated ~{len(POPULAR_PACKAGES)} popular packages for Telegram bots, requests, AI, databases, etc."
                    bot.edit_message_text(summary, chat_id, msg.message_id, reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Back to Tools", callback_data="tools")))
                    Logger.success("Popular packages installation completed.")
                except subprocess.TimeoutExpired:
                    bot.edit_message_text("❌ Installation timed out. Some packages may have been installed. Try again or install manually.", chat_id, msg.message_id, reply_markup=main_menu_keyboard())
                    Logger.error("Popular packages installation timed out")
                except Exception as e:
                    bot.edit_message_text(f"❌ Error during installation:\n`{e}`", chat_id, msg.message_id, parse_mode="Markdown", reply_markup=main_menu_keyboard())
                    Logger.error(f"Popular packages installation error: {e}")
                return
            if call.data == "list_packages":
                msg = bot.edit_message_text("🔍 Fetching list of installed packages...", chat_id, message_id)
                Logger.info(f"Admin {chat_id} requested package list")
                result = subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True, encoding='utf-8')
                if len(result) > 4000: result = result[:4000] + "\n\n[... output truncated ...]"
                bot.edit_message_text(f"📦 *Installed Packages:*\n\n```\n{result}\n```", chat_id, msg.message_id, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Back to Tools", callback_data="tools")))
                return
            if call.data == "run_command":
                user_states[chat_id] = {'action': 'run_command'}
                bot.edit_message_text("Enter the shell command to execute:", chat_id, message_id)
                Logger.info(f"Admin {chat_id} started run_command flow")
                return
            if call.data == "backup_all":
                Logger.info(f"Admin {chat_id} requested full backup of all bots")
                backup_all_bots(chat_id)
                return
        except Exception as e:
            Logger.error(f"Error handling exact tool action '{call.data}': {e}")
            bot.answer_callback_query(call.id, "An error occurred.", show_alert=True)
            return

    # ========== PARAMETERIZED ACTIONS (bot_xxx, start_xxx, etc.) ==========
    parts = call.data.split("_", 2)
    action = parts[0]
    param = parts[1] if len(parts) > 1 else None
    extra_param = parts[2] if len(parts) > 2 else None

    bot.answer_callback_query(call.id)
    Logger.info(f"Callback: action={action} param={param} extra={extra_param} from={chat_id}")

    try:
        if action == "main":
            bot.edit_message_text("Main Menu:", chat_id, message_id, reply_markup=main_menu_keyboard())
        elif action == "tools":
            bot.edit_message_text("Tools:", chat_id, message_id, reply_markup=tools_menu_keyboard())
        elif action == "system":
            get_system_stats(chat_id, message_id)

        elif action == "bot":
            bot_dir = os.path.join(BOTS_DIR, param)
            btype = detect_bot_type(bot_dir) or "unknown"
            type_emoji = "🐍" if btype == "python" else ("🔵" if btype == "go" else "❓")
            status_text = f"Stopped 🔴"
            if param in running_bots and running_bots[param]['process'].poll() is None:
                pid = running_bots[param]['process'].pid
                uptime = str(datetime.timedelta(seconds=int(time.time() - running_bots[param]['start_time'])))
                status_text = f"Running 🟢\n*PID:* `{pid}`\n*Uptime:* `{uptime}`"
            env = get_bot_env(param)
            env_line = f"\n🔑 *Variables:* `{len(env)}` set" if env else "\n🔑 *Variables:* none"
            data_dir = os.path.join(bot_dir, "data")
            data_line = f"\n📁 *DATA_DIR:* `{data_dir}`" if os.path.isdir(data_dir) else ""
            bot.edit_message_text(
                f"Managing Bot: `{param}`\n\n"
                f"*Type:* {type_emoji} `{btype}`\n"
                f"*Status:* {status_text}{env_line}{data_line}",
                chat_id, message_id,
                reply_markup=bot_menu_keyboard(param),
                parse_mode="Markdown"
            )

        elif action == "start":
            Logger.info(f"Starting bot '{param}' requested by {chat_id}")
            start_bot(param, chat_id)
        elif action == "stop":
            Logger.info(f"Stopping bot '{param}' requested by {chat_id}")
            stop_bot(param, chat_id)
        elif action == "restart":
            Logger.info(f"Restarting bot '{param}' requested by {chat_id}")
            stop_bot(param, chat_id, silent=True)
            time.sleep(1)
            start_bot(param, chat_id)
        elif action == "log":
            view_log(param, chat_id, message_id)
        elif action == "stats":
            get_bot_stats(call, param)
        elif action == "backup":
            Logger.info(f"Backup requested for bot '{param}' by {chat_id}")
            backup_bot(param, chat_id)


        elif action == "vars":
            # Show current variables and allow editing
            env = get_bot_env(param)
            if env:
                lines = [f"`{k}` = `{v}`" for k, v in env.items()]
                text = f"🔑 *Variables for `{param}`:*\n\n" + "\n".join(lines)
            else:
                text = f"🔑 *Variables for `{param}`:*\n\n_No variables set yet._"
            
            text += "\n\n📝 To set/update variables, send them in this format:\n`KEY1=value1\nKEY2=value2`"
            text += "\n\nTo clear all variables, send: `CLEAR`"
            
            user_states[chat_id] = {'action': 'set_bot_vars', 'bot_name': param}
            keyboard = InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Back", callback_data=f"bot_{param}"))
            bot.edit_message_text(text, chat_id, message_id, reply_markup=keyboard, parse_mode="Markdown")
            Logger.info(f"Showing variables for '{param}' to admin {chat_id}")


        elif action == "startcmd":
            current = get_start_cmd(param) or "_auto-detect_"
            text = (
                f"▶️ *Start Command for `{param}`*\n\n"
                f"Current: `{current}`\n\n"
                f"Send the command to run this bot, examples:\n"
                f"• `python main.py`\n"
                f"• `python bot.py`\n"
                f"• `python -m app`\n"
                f"• `./decryptbot`\n"
                f"• `go run .`\n\n"
                f"Send `AUTO` to clear and use auto-detect."
            )
            user_states[chat_id] = {'action': 'set_start_cmd', 'bot_name': param}
            keyboard = InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Back", callback_data=f"bot_{param}"))
            bot.edit_message_text(text, chat_id, message_id, reply_markup=keyboard, parse_mode="Markdown")
            Logger.info(f"Start cmd setup for '{param}' by {chat_id}")

        elif action == "files":
            list_files(call, param)
        elif action == "viewfile":
            view_file(call, param, extra_param)
        elif action == "downloadfile":
            file_name = base64.urlsafe_b64decode(extra_param).decode()
            file_path = os.path.join(BOTS_DIR, param, file_name)
            if os.path.exists(file_path):
                with open(file_path, 'rb') as doc:
                    bot.send_document(chat_id, doc, caption=f"File `{file_name}` from `{param}`.", parse_mode="Markdown")
                Logger.info(f"Sent file '{file_name}' from bot '{param}'")
            else:
                bot.answer_callback_query(call.id, "File not found.", show_alert=True)
        elif action == "deletefile":
            file_name = base64.urlsafe_b64decode(extra_param).decode()
            file_path = os.path.join(BOTS_DIR, param, file_name)
            try:
                os.remove(file_path)
                bot.answer_callback_query(call.id, f"File {file_name} deleted.")
                Logger.info(f"Deleted file '{file_name}' from bot '{param}'")
                list_files(call, param)
            except Exception as e:
                bot.answer_callback_query(call.id, f"Error deleting file: {e}", show_alert=True)
                Logger.error(f"Failed to delete file '{file_name}': {e}")
        elif action == "editfile":
            file_name = base64.urlsafe_b64decode(extra_param).decode()
            file_path = os.path.join(BOTS_DIR, param, file_name)
            user_states[chat_id] = {'action': 'edit_file', 'bot_name': param, 'file_path': file_path, 'file_name_encoded': extra_param}
            bot.edit_message_text(f"Please send the new content for the file `{file_name}`.", chat_id, message_id, parse_mode="Markdown")
        elif action == "upload":
            user_states[chat_id] = {'action': 'upload_file_to_bot', 'bot_name': param}
            bot.edit_message_text(f"Please upload the file you want to add to `{param}`.", chat_id, message_id, parse_mode="Markdown")

        elif action == "rename":
            user_states[chat_id] = {'action': 'rename_bot', 'bot_name': param}
            bot.edit_message_text(f"Enter the new name for bot `{param}`:", chat_id, message_id, parse_mode="Markdown")
        elif action == "watchdog":
            if param in watchdog_bots:
                watchdog_bots.discard(param)
                bot.answer_callback_query(call.id, f"Monitoring for {param} is now OFF.")
                Logger.info(f"Watchdog OFF for '{param}'")
            else:
                watchdog_bots.add(param)
                bot.answer_callback_query(call.id, f"Monitoring for {param} is now ON.")
                Logger.info(f"Watchdog ON for '{param}'")
            status_text = f"Stopped 🔴"
            if param in running_bots and running_bots[param]['process'].poll() is None:
                pid = running_bots[param]['process'].pid
                uptime = str(datetime.timedelta(seconds=int(time.time() - running_bots[param]['start_time'])))
                status_text = f"Running 🟢\n*PID:* `{pid}`\n*Uptime:* `{uptime}`"
            bot.edit_message_text(f"Managing Bot: `{param}`\n\n*Status:* {status_text}", chat_id, message_id, reply_markup=bot_menu_keyboard(param), parse_mode="Markdown")

        elif action == "downloadlog":
            log_path = os.path.join(BOTS_DIR, param, "bot.log")
            if os.path.exists(log_path) and os.path.getsize(log_path) > 0:
                with open(log_path, 'rb') as doc:
                    bot.send_document(chat_id, doc, caption=f"Full log for `{param}`.", parse_mode="Markdown")
                Logger.info(f"Sent full log for '{param}'")
            else:
                bot.answer_callback_query(call.id, "Log file is empty or does not exist.", show_alert=True)

        elif action == "delete":
            keyboard = InlineKeyboardMarkup().add(
                InlineKeyboardButton("🗑️ Yes, Delete", callback_data=f"confirmdelete_{param}"),
                InlineKeyboardButton("↩️ Cancel", callback_data=f"bot_{param}")
            )
            bot.edit_message_text(f"⚠️ Are you sure you want to permanently delete `{param}`?", chat_id, message_id, reply_markup=keyboard, parse_mode="Markdown")
        elif action == "confirmdelete":
            Logger.warning(f"Deleting bot '{param}' requested by {chat_id}")
            bot.edit_message_text(f"Deleting bot `{param}`...", chat_id, message_id, parse_mode="Markdown")
            delete_bot(param, chat_id)
            call.data = "manage_bots"
            callback_handler(call)
        elif action == "clearlog":
            keyboard = InlineKeyboardMarkup().add(
                InlineKeyboardButton("🧹 Yes, Clear", callback_data=f"confirmclearlog_{param}"),
                InlineKeyboardButton("↩️ Cancel", callback_data=f"bot_{param}")
            )
            bot.edit_message_text(f"⚠️ Are you sure you want to clear the log for `{param}`?", chat_id, message_id, reply_markup=keyboard, parse_mode="Markdown")
        elif action == "confirmclearlog":
            log_path = os.path.join(BOTS_DIR, param, "bot.log")
            if os.path.exists(log_path):
                open(log_path, 'w').close()
            bot.edit_message_text(f"Log for `{param}` cleared.", chat_id, message_id, reply_markup=bot_menu_keyboard(param), parse_mode="Markdown")
            Logger.info(f"Log cleared for '{param}'")
        elif action == "confirm":
            if param == "reboot":
                bot.edit_message_text("✅ Server reboot command issued. The manager will go offline.", chat_id, message_id)
                Logger.warning(f"Reboot command issued by admin {chat_id}.")
                os.system("sudo reboot")

    except telebot.apihelper.ApiTelegramException as e:
        if 'message is not modified' not in e.description:
            Logger.error(f"API Error on callback '{call.data}': {e}")
            try:
                bot.answer_callback_query(call.id, "An API error occurred.", show_alert=True)
            except:
                pass
    except Exception as e:
        Logger.error(f"Generic Error on callback '{call.data}': {e}")
        try:
            bot.answer_callback_query(call.id, "An unexpected error occurred.", show_alert=True)
        except:
            pass


# --- Main execution block ---
if __name__ == "__main__":
    Logger.info("Starting bot manager...")
    Logger.info(f"BOTS_DIR = {BOTS_DIR}")
    if "VIRTUAL_ENV" not in os.environ:
         Logger.warning("No active virtual environment detected. This may cause dependency issues.")
    else:
        Logger.info(f"Using virtual environment at: {os.environ['VIRTUAL_ENV']}")
    
    if not os.path.exists(BOTS_DIR): os.makedirs(BOTS_DIR)
    
    monitor_thread = threading.Thread(target=monitor_bots, daemon=True)
    monitor_thread.start()
    
    Logger.success("Manager bot is running and waiting for commands.")
    try:
        bot.infinity_polling(timeout=60, long_polling_timeout=30)
    except Exception as e:
        Logger.error(f"An unexpected error occurred during polling: {e}")
