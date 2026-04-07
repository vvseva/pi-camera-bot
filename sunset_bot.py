import os
import time
import subprocess
import threading
import telebot
from datetime import datetime, timedelta, timezone
from astral import LocationInfo
from astral.sun import sun
from dotenv import load_dotenv

# Load the environment variables from the .env file
load_dotenv()

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv("SUNSET_BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
CITY = LocationInfo("Norrköping", "Sweden", "Europe/Stockholm", 58.5812, 16.1826)

# Initialize the bot
bot = telebot.TeleBot(BOT_TOKEN)

def take_photo(filename="sunset.jpg"):
    print("Snapping photo...")
    # Using the fully updated command with flips and Pi 5 fixes
    subprocess.run(["rpicam-still", "-o", filename, "-t", "2000", "--vflip", "--hflip", "--camera", "0", "--autofocus-mode", "auto", "--awb", "auto"], check=True)

def post_to_channel(filename="sunset.jpg", is_test=False):
    print("Posting to Telegram channel...")
    try:
        with open(filename, "rb") as photo:
            caption = f"Norrköping Sunset 🌅\n{datetime.now().strftime('%Y-%m-%d')}"
            if is_test:
                caption = "🛠 **Test Photo** 🛠\n" + caption
            
            bot.send_photo(chat_id=CHANNEL_ID, photo=photo, caption=caption, parse_mode="Markdown")
        print("Successfully posted!")
    except Exception as e:
        print(f"Failed to post: {e}")

# --- TELEGRAM COMMANDS ---
@bot.message_handler(commands=['test'])
def handle_test(message):
    bot.reply_to(message, "Running channel post test... taking photo now 📸")
    try:
        take_photo("test_sunset.jpg")
        post_to_channel("test_sunset.jpg", is_test=True)
        bot.reply_to(message, "✅ Test photo successfully posted to the channel!")
    except Exception as e:
        bot.reply_to(message, f"❌ Failed during test: {e}")

# --- BACKGROUND SUNSET LOOP ---
def sunset_loop():
    while True:
        now = datetime.now(timezone.utc)
        s = sun(CITY.observer, date=now)
        sunset = s["sunset"]

        # If sunset has already passed today, calculate for tomorrow
        if now > sunset:
            s = sun(CITY.observer, date=now + timedelta(days=1))
            sunset = s["sunset"]

        wait_seconds = (sunset - datetime.now(timezone.utc)).total_seconds()
        
        print(f"Next sunset is at {sunset.astimezone().strftime('%Y-%m-%d %H:%M:%S')}.")
        time.sleep(wait_seconds)
        
        take_photo("sunset.jpg")
        post_to_channel("sunset.jpg")
        
        # Sleep for 5 minutes to ensure we don't double-trigger
        time.sleep(300)

# Start the sunset time-checker in a separate background thread
loop_thread = threading.Thread(target=sunset_loop, daemon=True)
loop_thread.start()

print("Sunset bot is running and listening for /test...")
bot.infinity_polling()