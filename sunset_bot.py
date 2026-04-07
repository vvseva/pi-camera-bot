import os
import time
import subprocess
import threading
import shutil
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

def take_photo(filename="sunset.jpg"):
    # Using the fully updated command with flips and Pi 5 fixes
    subprocess.run(["rpicam-still", "-o", filename, "-t", "2000", "--vflip", "--hflip", "--camera", "0", "--autofocus-mode", "auto", "--awb", "auto"], check=True)

def post_to_channel(photo_file, video_file=None, is_test=False):
    print("Posting to Telegram channel...")
    try:
        caption = f"Norrköping Sunset 🌅\n{datetime.now().strftime('%Y-%m-%d')}"
        if is_test:
            caption = "🛠 **Test Photo & Video** 🛠\n" + caption
        
        # 1. Send the static picture
        with open(photo_file, "rb") as photo:
            bot.send_photo(chat_id=CHANNEL_ID, photo=photo, caption=caption, parse_mode="Markdown")
            
        # 2. Send the timelapse video
        if video_file and os.path.exists(video_file):
            with open(video_file, "rb") as video:
                bot.send_video(chat_id=CHANNEL_ID, video=video)
                
        print("Successfully posted both!")
    except Exception as e:
        print(f"Failed to post: {e}")

# --- TELEGRAM COMMANDS ---
@bot.message_handler(commands=['test'])
def handle_test(message):
    bot.reply_to(message, "Running channel post test... taking a photo and generating a 5-second mini-timelapse 📸")
    try:
        # Move camera for the test
        pantilthat.pan(0)
        pantilthat.tilt(-25)
        time.sleep(2)
        
        # Take a quick burst of 5 photos for the test video
        os.makedirs("test_data", exist_ok=True)
        for i in range(5):
            take_photo(f"test_data/frame_{i:04d}.jpg")
            
        # Copy the first frame to act as our "main" static picture
        shutil.copy("test_data/frame_0000.jpg", "test_main.jpg")

        # Compile the mini-video
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-framerate", "5", "-pattern_type", "glob",
            "-i", "test_data/*.jpg", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-vf", "scale=1024:-2", "test_video.mp4"
        ]
        subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        post_to_channel("test_main.jpg", "test_video.mp4", is_test=True)
        bot.reply_to(message, "✅ Test picture and video successfully posted to the channel!")

    except Exception as e:
        bot.reply_to(message, f"❌ Failed during test: {e}")
        
    finally:
        # Cleanup test files
        if os.path.exists("test_data"): shutil.rmtree("test_data")
        if os.path.exists("test_main.jpg"): os.remove("test_main.jpg")
        if os.path.exists("test_video.mp4"): os.remove("test_video.mp4")

# --- BACKGROUND SUNSET LOOP ---
def get_next_sunset_timings():
    """Calculates the exact start, sunset, and end times."""
    now = datetime.now(timezone.utc)
    s = sun(CITY.observer, date=now)
    sunset_time = s["sunset"]
    
    # Start 20 mins before, end 10 mins after
    start_time = sunset_time - timedelta(minutes=20)
    end_time = sunset_time + timedelta(minutes=10)

    # If we missed today's start time, calculate for tomorrow
    if now > start_time:
        s = sun(CITY.observer, date=now + timedelta(days=1))
        sunset_time = s["sunset"]
        start_time = sunset_time - timedelta(minutes=20)
        end_time = sunset_time + timedelta(minutes=10)

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

            # Timelapse loop (Runs for 30 minutes)
            while datetime.now(timezone.utc) < end_time:
                current_now = datetime.now(timezone.utc)
                frame_name = f"sunset_data/frame_{frame_count:04d}.jpg"
                
                try:
                    take_photo(frame_name)
                    frame_count += 1
                except Exception as e:
                    print(f"Missed a frame: {e}")

                # If we just hit the exact sunset time, save this frame as the main photo
                if current_now >= sunset_time and not main_photo_taken:
                    shutil.copy(frame_name, "sunset_main.jpg")
                    main_photo_taken = True

                time.sleep(15) # Wait 15 seconds between frames

            # Fallback just in case
            if not main_photo_taken:
                shutil.copy(frame_name, "sunset_main.jpg")

            print("Capturing done. Stitching video...")
            output_video = "sunset_timelapse.mp4"
            ffmpeg_cmd = [
                "ffmpeg", "-y", "-framerate", "15", "-pattern_type", "glob",
                "-i", "sunset_data/*.jpg", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-vf", "scale=1024:-2", output_video
            ]
            subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            post_to_channel("sunset_main.jpg", output_video)

            # Cleanup real files
            shutil.rmtree("sunset_data")
            if os.path.exists("sunset_main.jpg"): os.remove("sunset_main.jpg")
            if os.path.exists(output_video): os.remove(output_video)

            # Calculate times for tomorrow
            start_time, sunset_time, end_time = get_next_sunset_timings()

        # Tick method: Sleep for 60 seconds, check the time, repeat. 
        # Prevents thread-locking so /test still works!
        time.sleep(60)

# Start the sunset time-checker in a separate background thread
loop_thread = threading.Thread(target=sunset_loop, daemon=True)
loop_thread.start()

print("Sunset bot is running and listening for /test...")
bot.infinity_polling()
