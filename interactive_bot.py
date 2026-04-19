import os
import time
import subprocess
import threading
import shutil
import logging
import math
import cv2
import numpy as np
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
import pantilthat
from dotenv import load_dotenv

# --- LOGGING SETUP ---
# This will log to your console (and systemd journal) with timestamps
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load the environment variables from the .env file
load_dotenv()

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv("INTERACTIVE_BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

# --- THREADING & SAFETY ---
camera_lock = threading.Lock()

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

# --- COMPUTER VISION: HORIZON LEVELING ---
def detect_horizon_line(image_grayscaled):
    """Detect the horizon's starting and ending points in the given image

    The horizon line is detected by applying Otsu's threshold method to
    separate the sky from the remainder of the image.
    """
    msg = ('`image_grayscaled` should be a grayscale, 2-dimensional image '
           'of shape (height, width).')
    assert image_grayscaled.ndim == 2, msg
    image_blurred = cv2.GaussianBlur(image_grayscaled, ksize=(3, 3), sigmaX=0)

    _, image_thresholded = cv2.threshold(
        image_blurred, thresh=0, maxval=1,
        type=cv2.THRESH_BINARY+cv2.THRESH_OTSU
    )
    image_thresholded = image_thresholded - 1
    image_closed = cv2.morphologyEx(image_thresholded, cv2.MORPH_CLOSE,
                                    kernel=np.ones((9, 9), np.uint8))

    horizon_x1 = 0
    horizon_x2 = image_grayscaled.shape[1] - 1
    
    # Safe extraction: if image is pure black/white and separation fails, fallback to 0 tilt
    try:
        horizon_y1 = max(np.where(image_closed[:, horizon_x1] == 0)[0])
        horizon_y2 = max(np.where(image_closed[:, horizon_x2] == 0)[0])
    except ValueError:
        horizon_y1 = image_grayscaled.shape[0] // 2
        horizon_y2 = image_grayscaled.shape[0] // 2

    return horizon_x1, horizon_x2, horizon_y1, horizon_y2

def calculate_horizon_angle(image_path):
    """Calculates the rotation angle needed using Otsu horizon detection."""
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None: 
        return 0.0
        
    try:
        x1, x2, y1, y2 = detect_horizon_line(img)
        # math.atan2 calculates the angle in radians. 
        # In image coordinates (where Y increases downwards), this perfectly matches 
        # the rotation needed to bring the right side back up/down to level.
        angle_rad = math.atan2(y2 - y1, x2 - x1)
        return math.degrees(angle_rad)
    except Exception as e:
        logging.error(f"Horizon angle calculation failed: {e}")
        return 0.0

def align_image(image_path, angle):
    """Rotates the image to level the horizon and dynamically crops out black corners."""
    if abs(angle) < 0.1:
        return # Skip processing if the image is already perfectly flat

    img = cv2.imread(image_path)
    if img is None:
        return
        
    h, w = img.shape[:2]
    center = (w // 2, h // 2)
    
    # Calculate scale factor to dynamically crop out the black triangles caused by rotation
    rad = math.radians(abs(angle))
    denominator = math.cos(rad) - max(w/h, h/w) * math.sin(rad)
    scale = 1.0 / denominator if denominator > 0 else 1.2

    # Perform the rotation and crop
    M = cv2.getRotationMatrix2D(center, angle, scale)
    rotated = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC)
    
    # Overwrite the original file with the leveled version
    cv2.imwrite(image_path, rotated)

def snap_picture(filename="manual_photo.jpg"):
    """Centralized, thread-safe camera function."""
    with camera_lock:
        subprocess.run(["rpicam-still", "-o", filename, "-t", "2000", "--vflip", "--hflip", "--camera", "0", "--autofocus-mode", "auto", "--awb", "auto"], check=True)
    time.sleep(1) # Hardware cooldown

def get_reply_keyboard():
    """Generates the persistent bottom keyboard."""
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("⬆️ Up"))
    markup.row(KeyboardButton("⬅️ Left"), KeyboardButton("🔄 Center"), KeyboardButton("➡️ Right"))
    markup.row(KeyboardButton("⬇️ Down"))
    markup.row(KeyboardButton("📸 Take Photo"))
    return markup

@bot.message_handler(commands=['start', 'control'])
def send_control_panel(message):
    bot.send_message(
        message.chat.id, 
        "🎮 **Camera Controls Activated**\nUse the buttons to move the camera, or type `/makestimelapse [minutes]`.", 
        reply_markup=get_reply_keyboard(), 
        parse_mode='Markdown'
    )

# --- TIMELAPSE FUNCTIONALITY ---
@bot.message_handler(commands=['makestimelapse'])
def handle_timelapse(message):
    user = message.from_user
    args = message.text.split()
    duration_minutes = 5 # Default to 5 minutes if no argument provided
    
    if len(args) > 1:
        if args[1].isdigit():
            val = int(args[1])
            if 1 <= val <= 60:
                duration_minutes = val
            else:
                bot.reply_to(message, "⚠️ Duration must be between 1 and 60 minutes.")
                return
        else:
            bot.reply_to(message, "⚠️ Please provide a valid number. Example: `/makestimelapse 15`")
            return

    logging.info(f"TIMELAPSE: User @{user.username} (ID: {user.id}) started a {duration_minutes}-min capture.")
    bot.reply_to(message, f"⏱️ Starting a {duration_minutes}-minute timelapse! I will auto-level the frames and send the video when it's finished.")

    # Start the capture process in the background so it doesn't freeze the bot
    threading.Thread(target=process_timelapse, args=(message.chat.id, user.id, duration_minutes), daemon=True).start()

def process_timelapse(chat_id, user_id, duration_minutes):
    """Background task to capture images, auto-level, stitch video, and clean up."""
    # Create an isolated folder for this specific user's timelapse
    user_dir = f"timelapse_data_{user_id}"
    os.makedirs(user_dir, exist_ok=True)
    
    total_seconds = duration_minutes * 60
    
    # Calculate interval dynamically: Longer timelapses take photos less frequently.
    # We aim for roughly ~30 to 45 frames total to keep the output snappy.
    # The absolute minimum wait between shots is 5 seconds to give the camera sensor time.
    interval_seconds = max(5, int(total_seconds / 30)) 
    
    end_time = time.time() + total_seconds
    frame_count = 0
    horizon_angle = None # Will store the angle calculated from the first frame

    try:
        while time.time() < end_time:
            # Format filename as frame_0001.jpg, frame_0002.jpg for sequential sorting
            frame_name = os.path.join(user_dir, f"frame_{frame_count:04d}.jpg")
            try:
                snap_picture(frame_name)
                
                # Calculate angle on the very first frame of the timelapse
                if horizon_angle is None:
                    horizon_angle = calculate_horizon_angle(frame_name)
                    logging.info(f"TIMELAPSE: Detected horizon tilt {horizon_angle:.2f}°. Applying to sequence.")
                    
                # Apply the calculated angle to this frame
                align_image(frame_name, horizon_angle)
                
                frame_count += 1
            except Exception as e:
                logging.error(f"Failed to capture/align frame: {e}")
            
            # Wait before taking the next photo
            time.sleep(interval_seconds)

        bot.send_message(chat_id, "🎬 Capture complete! Stitching photos together (this takes a minute)...")
        
        # Output video filename
        output_file = f"timelapse_{user_id}.mp4"
        
        # Use FFmpeg to combine images into an MP4 (scales down to 1024px wide for Telegram size limits)
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-framerate", "10", "-pattern_type", "glob",
            "-i", f"{user_dir}/*.jpg", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-vf", "scale=1024:-2", output_file
        ]
        subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Send the file as an MP4 (Telegram automatically loops it like a GIF)
        with open(output_file, "rb") as video:
            bot.send_video(chat_id, video)
            
        logging.info(f"TIMELAPSE: Successfully sent to User ID {user_id}. Cleaning up.")
        
        # Delete the final video file
        os.remove(output_file)

    except Exception as e:
        bot.send_message(chat_id, f"❌ Error generating timelapse: {e}")
        logging.error(f"Timelapse Error: {e}")
        
    finally:
        # ABSOLUTE CLEANUP: Delete the entire user directory and all raw photos inside it
        if os.path.exists(user_dir):
            shutil.rmtree(user_dir)

# --- STANDARD CONTROLS ---
@bot.message_handler(func=lambda message: message.text in ["⬆️ Up", "⬇️ Down", "⬅️ Left", "➡️ Right", "🔄 Center", "📸 Take Photo"])
def handle_keyboard_buttons(message):
    global current_pan, current_tilt
    action = message.text
    moved = False

    if action == "⬆️ Up":
        new_tilt = max(current_tilt - STEP, MIN_TILT)
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
        user = message.from_user
        # LOGGING WHO TOOK THE PHOTO
        logging.info(f"MANUAL PHOTO: User @{user.username} (ID: {user.id}) took a photo.")
        
        msg = bot.send_message(message.chat.id, "Taking photo... 📸")
        filename = "manual_photo.jpg"
        try:
            # Using our new thread-safe snap function
            snap_picture(filename)
            
            # --- AUTO-LEVEL THE MANUAL PHOTO ---
            angle = calculate_horizon_angle(filename)
            align_image(filename, angle)
            logging.info(f"MANUAL PHOTO: Auto-leveled by {angle:.2f}°")
            
            with open(filename, "rb") as photo:
                bot.send_photo(message.chat.id, photo)
            bot.delete_message(chat_id=message.chat.id, message_id=msg.message_id)
        except Exception as e:
            bot.send_message(message.chat.id, f"Failed to take photo: {e}")

    if moved:
        time.sleep(1) # Enforce the 1-second delay after physical movement

print("Interactive Reply Keyboard bot is running...")
bot.infinity_polling()
