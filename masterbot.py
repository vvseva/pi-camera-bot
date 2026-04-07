import os
import time
import subprocess
import threading
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
import pantilthat
from datetime import datetime, timedelta, timezone
from astral import LocationInfo
from astral.sun import sun
from dotenv import load_dotenv

# Load the environment variables from the .env file
load_dotenv()

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv("INTERACTIVE_BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
CITY = LocationInfo("Norrköping", "Sweden", "Europe/Stockholm", 58.5812, 16.1826)

bot = telebot.TeleBot(BOT_TOKEN)

# --- HARDWARE STATE & LIMITS ---
MAX_PAN = 75
MIN_PAN = -75
MAX_TILT = 75
MIN_TILT = -75
STEP = 5 

current_pan = 0
current_tilt = 0

# --- THREADING SAFETY & TIMERS ---
camera_lock = threading.Lock()
idle_timer = None
IDLE_TIMEOUT = 300  # 300 seconds = 5 minutes

def park_camera():
    """Moves the camera to a safe privacy angle when idle."""
    if current_pan != 0 or current_tilt != 50:
        print("Idle timeout reached. Parking camera for privacy.")
        move_camera(0, 50)

def reset_idle_timer():
    """Cancels the current countdown and starts a fresh 5-minute timer."""
    global idle_timer
    if idle_timer is not None:
        idle_timer.cancel()
    
    idle_timer = threading.Timer(IDLE_TIMEOUT, park_camera)
    idle_timer.daemon = True
    idle_timer.start()

def move_camera(pan, tilt):
    """Centralized function to safely move servos."""
    global current_pan, current_tilt
    with camera_lock:
        current_pan = pan
        current_tilt = tilt
        pantilthat.pan(current_pan)
        pantilthat.tilt(current_tilt)
        time.sleep(1) # Allow servos to settle

# Center the camera on startup, then start the idle countdown
move_camera(0, 0)
reset_idle_timer()

# --- SHARED CAMERA FUNCTION ---
def snap_picture(filename="photo.jpg"):
    """Takes a picture with the optimal Pi 5 / Autofocus settings."""
    with camera_lock:
        subprocess.run(["rpicam-still", "-o", filename, "-t", "2000", "--vflip", "--hflip", "--camera", "0", "--autofocus-mode", "auto", "--awb", "auto"], check=True)
    time.sleep(1)

# --- BACKGROUND SUNSET ROUTINE ---
def post_sunset_to_channel(filename="sunset.jpg", is_test=False):
    try:
        with open(filename, "rb") as photo:
            caption = f"Norrköping Sunset 🌅\n{datetime.now().strftime('%Y-%m-%d')}"
            if is_test:
                caption = "🛠 **Test Photo** 🛠\n" + caption
            bot.send_photo(chat_id=CHANNEL_ID, photo=photo, caption=caption, parse_mode="Markdown")
    except Exception as e:
        print(f"Failed to post to channel: {e}")

def get_next_sunset():
    """Calculates the exact time of the next sunset."""
    now = datetime.now(timezone.utc)
    s = sun(CITY.observer, date=now)
    sunset_time = s["sunset"]

    # If today's sunset has already passed, calculate for tomorrow
    if now > sunset_time:
        s = sun(CITY.observer, date=now + timedelta(days=1))
        sunset_time = s["sunset"]
        
    print(f"Next sunset scheduled for: {sunset_time.astimezone().strftime('%Y-%m-%d %H:%M:%S')}")
    return sunset_time

def sunset_loop():
    """Background loop that ticks every 60 seconds without freezing the bot."""
    next_sunset = get_next_sunset()

    while True:
        now = datetime.now(timezone.utc)

        if now >= next_sunset:
            print("Sunset reached! Triggering camera...")
            move_camera(0, -25)
            snap_picture("sunset.jpg")
            post_sunset_to_channel("sunset.jpg")
            
            reset_idle_timer()
            next_sunset = get_next_sunset()

        # Sleep inside the thread. This does NOT halt the main bot.
        time.sleep(60) 

# ==========================================
# ⚠️ CRITICAL: LAUNCH THE BACKGROUND THREAD
# ==========================================
# This line ensures the sunset_loop runs parallel to the Telegram listener.
# Without this specific threading call, the bot will completely freeze.
threading.Thread(target=sunset_loop, daemon=True).start()

# --- INTERACTIVE TELEGRAM UI ---
def get_reply_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("⬆️ Up"))
    markup.row(KeyboardButton("⬅️ Left"), KeyboardButton("🔄 Center"), KeyboardButton("➡️ Right"))
    markup.row(KeyboardButton("⬇️ Down"))
    markup.row(KeyboardButton("📸 Take Photo"))
    return markup

@bot.message_handler(commands=['start', 'control'])
def send_control_panel(message):
    bot.send_message(message.chat.id, "🎮 **Master Camera Control**\nPrivacy auto-park enabled (5 min).", reply_markup=get_reply_keyboard(), parse_mode='Markdown')

@bot.message_handler(commands=['test_sunset'])
def handle_test(message):
    reset_idle_timer()
    bot.reply_to(message, "Running sunset test... moving camera to (0, -25) and taking photo 📸")
    try:
        move_camera(0, -25)
        snap_picture("test_sunset.jpg")
        post_sunset_to_channel("test_sunset.jpg", is_test=True)
        bot.reply_to(message, "✅ Test photo posted to channel. Camera will park in 5 minutes.")
    except Exception as e:
        bot.reply_to(message, f"❌ Failed: {e}")

@bot.message_handler(func=lambda message: message.text in ["⬆️ Up", "⬇️ Down", "⬅️ Left", "➡️ Right", "🔄 Center", "📸 Take Photo"])
def handle_keyboard_buttons(message):
    global current_pan, current_tilt
    action = message.text
    
    reset_idle_timer()

    if action == "⬆️ Up":
        new_tilt = max(current_tilt - STEP, MIN_TILT)
        if new_tilt != current_tilt:
            move_camera(current_pan, new_tilt)
        else:
            bot.send_message(message.chat.id, "⚠️ Limit reached.")

    elif action == "⬇️ Down":
        new_tilt = min(current_tilt + STEP, MAX_TILT)
        if new_tilt != current_tilt:
            move_camera(current_pan, new_tilt)
        else:
            bot.send_message(message.chat.id, "⚠️ Limit reached.")

    elif action == "⬅️ Left":
        new_pan = min(current_pan + STEP, MAX_PAN)
        if new_pan != current_pan:
            move_camera(new_pan, current_tilt)
        else:
            bot.send_message(message.chat.id, "⚠️ Limit reached.")

    elif action == "➡️ Right":
        new_pan = max(current_pan - STEP, MIN_PAN)
        if new_pan != current_pan:
            move_camera(new_pan, current_tilt)
        else:
            bot.send_message(message.chat.id, "⚠️ Limit reached.")

    elif action == "🔄 Center":
        move_camera(0, 0)

    elif action == "📸 Take Photo":
        msg = bot.send_message(message.chat.id, "Taking photo... 📸")
        try:
            snap_picture("manual_photo.jpg")
            with open("manual_photo.jpg", "rb") as photo:
                bot.send_photo(message.chat.id, photo)
            bot.delete_message(chat_id=message.chat.id, message_id=msg.message_id)
        except Exception as e:
            bot.send_message(message.chat.id, f"Failed: {e}")

print("Master Bot is running. Listening for Telegram commands...")
# This runs the main thread forever, listening to your buttons
bot.infinity_polling()