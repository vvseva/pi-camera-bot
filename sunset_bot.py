import os
import time
import subprocess
import threading
import shutil
import math
import cv2
import numpy as np
import telebot
import pantilthat
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

# --- COMPUTER VISION: HORIZON LEVELING ---
def calculate_horizon_angle(image_path):
    """Detects the horizon line and returns the exact rotation angle needed to level it."""
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None: 
        return 0.0
        
    edges = cv2.Canny(img, 50, 150, apertureSize=3)
    lines = cv2.HoughLines(edges, 1, np.pi/180, 200)
    
    angles = []
    if lines is not None:
        for line in lines:
            rho, theta = line[0]
            angle_deg = math.degrees(theta) - 90
            
            # Filter for roughly horizontal lines (within 15 degrees)
            if -15 < angle_deg < 15:
                angles.append(angle_deg)
                
    # Return the inverse median angle to determine the correction rotation, 
    # or 0.0 if the horizon is obscured by clouds/buildings
    return -np.median(angles) if angles else 0.0

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

# --- CAMERA FUNCTIONS ---
def take_photo(filename="sunset.jpg"):
    subprocess.run(["rpicam-still", "-o", filename, "-t", "00", "--vflip", "--hflip", "--camera", "0", "--autofocus-mode", "auto", "--awb", "auto"], check=True)

def post_to_channel(photo_file, video_file=None, is_test=False):
    print("Posting to Telegram channel...")
    try:
        caption = f"Norrköping Sunset 🌅\n{datetime.now().strftime('%Y-%m-%d')}"
        if is_test:
            caption = "🛠 **Test Photo & Video** 🛠\n" + caption
        
        with open(photo_file, "rb") as photo:
            bot.send_photo(chat_id=CHANNEL_ID, photo=photo, caption=caption, parse_mode="Markdown")
            
        if video_file and os.path.exists(video_file):
            with open(video_file, "rb") as video:
                bot.send_video(chat_id=CHANNEL_ID, video=video)
                
        print("Successfully posted both!")
    except Exception as e:
        print(f"Failed to post: {e}")

# --- TELEGRAM COMMANDS ---
@bot.message_handler(commands=['test'])
def handle_test(message):
    bot.reply_to(message, "Running channel post test... taking photos, auto-leveling, and generating video 📸")
    try:
        pantilthat.pan(0)
        pantilthat.tilt(-25)
        time.sleep(2)
        
        os.makedirs("test_data", exist_ok=True)
        horizon_angle = 0.0 # Variable to hold the calculated angle for this sequence
        
        for i in range(5):
            frame_name = f"test_data/frame_{i:04d}.jpg"
            take_photo(frame_name)
            
            # If this is the FIRST photo, calculate the angle
            if i == 0:
                horizon_angle = calculate_horizon_angle(frame_name)
                print(f"Detected horizon tilt: {horizon_angle:.2f}°. Applying to entire sequence.")
                
            # Apply the calculated angle to ALL photos in the sequence
            align_image(frame_name, horizon_angle)
            
        shutil.copy("test_data/frame_0000.jpg", "test_main.jpg")

        ffmpeg_cmd = [
            "ffmpeg", "-y", "-framerate", "5", "-pattern_type", "glob",
            "-i", "test_data/*.jpg", "-c:v", "libx264", "-pix_fmt", "yuv4p",
            "-vf", "scale=1024:-2", "test_video.mp4"
        ]
        subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        post_to_channel("test_main.jpg", "test_video.mp4", is_test=True)
        bot.reply_to(message, f"✅ Test complete! Horizon automatically leveled by {abs(horizon_angle):.2f}°.")

    except Exception as e:
        bot.reply_to(message, f"❌ Failed during test: {e}")
        
    finally:
        if os.path.exists("test_data"): shutil.rmtree("test_data")
        if os.path.exists("test_main.jpg"): os.remove("test_main.jpg")
        if os.path.exists("test_video.mp4"): os.remove("test_video.mp4")

# --- BACKGROUND SUNSET LOOP ---
def get_next_sunset_timings():
    now = datetime.now(timezone.utc)
    s = sun(CITY.observer, date=now)
    sunset_time = s["sunset"]
    
    start_time = sunset_time - timedelta(minutes=20)
    end_time = sunset_time + timedelta(minutes=20)

    if now > start_time:
        s = sun(CITY.observer, date=now + timedelta(days=1))
        sunset_time = s["sunset"]
        start_time = sunset_time - timedelta(minutes=20)
        end_time = sunset_time + timedelta(minutes=20)

    print(f"Next capture begins at {start_time.astimezone().strftime('%Y-%m-%d %H:%M:%S')}.")
    return start_time, sunset_time, end_time

def sunset_loop():
    start_time, sunset_time, end_time = get_next_sunset_timings()

    while True:
        now = datetime.now(timezone.utc)

        if now >= start_time:
            print("Sunset sequence started! Moving camera...")
            pantilthat.pan(0)
            pantilthat.tilt(-25)
            time.sleep(2)

            os.makedirs("sunset_data", exist_ok=True)
            main_photo_taken = False
            frame_count = 0
            horizon_angle = None # Reset the angle for the new day's sunset

            while datetime.now(timezone.utc) < end_time:
                current_now = datetime.now(timezone.utc)
                frame_name = f"sunset_data/frame_{frame_count:04d}.jpg"
                
                try:
                    take_photo(frame_name)
                    
                    # Calculate angle on the very first frame of the evening
                    if horizon_angle is None:
                        horizon_angle = calculate_horizon_angle(frame_name)
                        print(f"Detected horizon tilt: {horizon_angle:.2f}°. Applying to entire sunset.")
                        
                    # Rotate and crop the frame
                    align_image(frame_name, horizon_angle)
                    
                    frame_count += 1
                except Exception as e:
                    print(f"Missed a frame: {e}")

                if current_now >= sunset_time and not main_photo_taken:
                    shutil.copy(frame_name, "sunset_main.jpg")
                    main_photo_taken = True

                time.sleep(15) 

            if not main_photo_taken:
                shutil.copy(frame_name, "sunset_main.jpg")

            print("Capturing done. Stitching video...")
            output_video = "sunset_timelapse.mp4"
            ffmpeg_cmd = [
                "ffmpeg", "-y", "-framerate", "30", "-pattern_type", "glob",
                "-i", "sunset_data/*.jpg", "-c:v", "libx264", "-pix_fmt", "yuv4p",
                "-vf", "scale=1024:-2", output_video
            ]
            subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            post_to_channel("sunset_main.jpg", output_video)

            shutil.rmtree("sunset_data")
            if os.path.exists("sunset_main.jpg"): os.remove("sunset_main.jpg")
            if os.path.exists(output_video): os.remove(output_video)

            start_time, sunset_time, end_time = get_next_sunset_timings()

        time.sleep(60)

loop_thread = threading.Thread(target=sunset_loop, daemon=True)
loop_thread.start()

print("Sunset bot is running and listening for /test...")
bot.infinity_polling()
