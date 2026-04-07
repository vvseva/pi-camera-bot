import os
import time
import subprocess
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
import pantilthat
from dotenv import load_dotenv

# Load the environment variables from the .env file
load_dotenv()

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv("INTERACTIVE_BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

# --- GUARDRAILS & SETTINGS ---
MAX_PAN = 75
MIN_PAN = -75
MAX_TILT = 75
MIN_TILT = -75
STEP = 5 

current_pan = 0
current_tilt = 0

# Center the camera on startup
pantilthat.pan(current_pan)
pantilthat.tilt(current_tilt)
time.sleep(1)

def get_reply_keyboard():
    """Generates the persistent bottom keyboard."""
    # resize_keyboard=True makes the buttons fit the screen nicely
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    
    # Row 1
    markup.row(KeyboardButton("⬆️ Up"))
    # Row 2
    markup.row(KeyboardButton("⬅️ Left"), KeyboardButton("🔄 Center"), KeyboardButton("➡️ Right"))
    # Row 3
    markup.row(KeyboardButton("⬇️ Down"))
    # Row 4
    markup.row(KeyboardButton("📸 Take Photo"))
    
    return markup

@bot.message_handler(commands=['start', 'control'])
def send_control_panel(message):
    """Sends the initial message and opens the persistent keyboard."""
    bot.send_message(
        message.chat.id, 
        "🎮 **Camera Controls Activated**\nUse the buttons at the bottom of your screen to move the camera.", 
        reply_markup=get_reply_keyboard(), 
        parse_mode='Markdown'
    )

# Listen for specific text messages that match our buttons
@bot.message_handler(func=lambda message: message.text in ["⬆️ Up", "⬇️ Down", "⬅️ Left", "➡️ Right", "🔄 Center", "📸 Take Photo"])
def handle_keyboard_buttons(message):
    global current_pan, current_tilt
    action = message.text
    moved = False

    if action == "⬆️ Up":
        new_tilt = max(current_tilt - STEP, MIN_TILT) # Using the inverted logic you needed
        if new_tilt != current_tilt:
            current_tilt = new_tilt
            pantilthat.tilt(current_tilt)
            moved = True
        else:
            bot.send_message(message.chat.id, "⚠️ Maximum upward tilt reached.")

    elif action == "⬇️ Down":
        new_tilt = min(current_tilt + STEP, MAX_TILT)
        if new_tilt != current_tilt:
            current_tilt = new_tilt
            pantilthat.tilt(current_tilt)
            moved = True
        else:
            bot.send_message(message.chat.id, "⚠️ Maximum downward tilt reached.")

    elif action == "⬅️ Left":
        new_pan = min(current_pan + STEP, MAX_PAN)
        if new_pan != current_pan:
            current_pan = new_pan
            pantilthat.pan(current_pan)
            moved = True
        else:
            bot.send_message(message.chat.id, "⚠️ Maximum left pan reached.")

    elif action == "➡️ Right":
        new_pan = max(current_pan - STEP, MIN_PAN)
        if new_pan != current_pan:
            current_pan = new_pan
            pantilthat.pan(current_pan)
            moved = True
        else:
            bot.send_message(message.chat.id, "⚠️ Maximum right pan reached.")

    elif action == "🔄 Center":
        if current_pan != 0 or current_tilt != -25:
            current_pan = 0
            current_tilt = -25
            pantilthat.pan(current_pan)
            pantilthat.tilt(current_tilt)
            moved = True

    elif action == "📸 Take Photo":
        msg = bot.send_message(message.chat.id, "Taking photo... 📸")
        filename = "manual_photo.jpg"
        try:
            # Using your full Pi 5 camera command
            subprocess.run(["rpicam-still", "-o", filename, "-t", "2000", "--vflip", "--hflip", "--camera", "0", "--autofocus-mode", "auto", "--awb", "auto"], check=True)
            with open(filename, "rb") as photo:
                bot.send_photo(message.chat.id, photo)
            # Delete the "Taking photo..." text to keep the chat clean
            bot.delete_message(chat_id=message.chat.id, message_id=msg.message_id)
        except Exception as e:
            bot.send_message(message.chat.id, f"Failed to take photo: {e}")
        time.sleep(1)

    if moved:
        time.sleep(1) # Enforce the 1-second delay after physical movement

print("Interactive Reply Keyboard bot is running...")
bot.infinity_polling()