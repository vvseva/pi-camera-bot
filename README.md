# Sunset Catcher & Interactive Pi Camera Bot

An automated and interactive camera system built for the Raspberry Pi 5. This project uses two synchronized Telegram bots to achieve two goals:
1. **The Brother Dusk Bot:** Runs in the background, calculates the exact time of sunset for a specific location, moves the camera to the optimal angle, takes an auto-focused photo, and publishes it to a Telegram channel.
2. **The Brother Day Bot:** Provides a game-controller-style keyboard inside Telegram to manually pan, tilt, and snap photos on demand.

![images/controls.png]

## 🛠 Prerequisites

### Hardware
- **Raspberry Pi 5** (This project utilizes the Pi 5's specific active cooling and hardware handling, but should work with older models).
- **Raspberry Pi Camera Module 3** (Required for hardware autofocus, but sunsets don't move much, so older cameras modules would be ok).
- **Pimoroni Pan-Tilt HAT** (OPTIONAL, but fun).
- **22-pin to 15-pin FPC MIPI Cable** (Important: The standard Camera 3 cable does not fit the Pi 5. You must use the Pi 5 specific adapter cable).
- **USB-C Raspberry Pi Charger** (if you want this thing to run 24/7)

### Software & Accounts
- Raspberry Pi OS (Bookworm)
- Two Telegram Bot Tokens (generated via [@BotFather](https://t.me/botfather))
- A Telegram Channel to post the sunset photos

---

## ⚙️ Hardware Setup

1. **Mount the HAT:** Attach the Pimoroni Pan-Tilt HAT to the Pi's GPIO pins.
2. **Connect the Camera:** * **Pi 5 Board:** Plug the 22-pin end into `CAM/DISP 0`. Ensure the silver metal contacts face **inward** toward the center of the Pi motherboard.
   * **Camera Board:** Plug the 15-pin end into the camera. Ensure the metal contacts face the green circuit board.
3. **Enable I2C:** Run `sudo raspi-config`, navigate to **Interface Options**, and enable **I2C**. Reboot the Pi.

---

## 💻 Software Setup

Because Raspberry Pi OS (Bookworm) restricts system-wide `pip` installations, this project must run inside a properly configured Virtual Environment that still has access to system-level hardware drivers.

1. Install System Dependencies
```bash
sudo apt update
sudo apt install python3-smbus i2c-tools
```

2. Set Up the Virtual Environment
Navigate to your project directory and create the environment:

```bash
cd ~/pi_bots
python3 -m venv bot_env
```

Hardware Access Fix: To allow the virtual environment to communicate with the physical Pan-Tilt HAT via smbus, you must edit the environment's configuration file:
```bash
nano bot_env/pyvenv.cfg
```
Change include-system-site-packages = false to true. Save and exit.


3. Install Python Libraries
Activate the environment and install the required packages:

```bash
source bot_env/bin/activate
pip install pyTelegramBotAPI astral requests pantilthat python-dotenv
```

## 🔐 Configuration (.env)
Never hardcode your Telegram API tokens! 
Create a hidden .env file in the root of your project directory:

```bash
nano .env
Add your specific credentials (no spaces around the equals signs, no quotes):
```

Add your specific credentials (no spaces around the equals signs, no quotes):

```Ini, TOML
SUNSET_BOT_TOKEN=your_sunset_bot_token_here
INTERACTIVE_BOT_TOKEN=your_interactive_bot_token_here
CHANNEL_ID=@your_channel_username
```

Optional: Run chmod 600 .env to secure the file so only your user can read it.

## 🚀 Running the Bots Automatically (Systemd)
To ensure the bots start automatically on boot and restart if they crash, this project uses systemd daemons. The service files are stored inside the repository (in the system_setup folder) and are symlinked to the system folder for easy updating.

1. Create the Symlinks
Run these commands to create shortcuts from the OS systemd folder to your local Git repository:

```bash
sudo ln -s /home/vvseva/pi_bots/system_setup/sunset_bot.service /etc/systemd/system/sunset_bot.service
sudo ln -s /home/vvseva/pi_bots/system_setup/interactive_bot.service /etc/systemd/system/interactive_bot.service
```

2. Enable and Start the Services
Reload the daemon list to recognize the new links, enable them to start on boot, and start them immediately:

```bash
sudo systemctl daemon-reload
sudo systemctl enable sunset_bot.service interactive_bot.service
sudo systemctl start sunset_bot.service interactive_bot.service
```
Useful Systemd Commands:

- Check status: systemctl status interactive_bot.service

- View live logs: journalctl -u sunset_bot.service -f

- Restart after code changes: sudo systemctl restart interactive_bot.service

## 🎮 Usage
The Interactive Bot
1. Send /control to your Interactive Bot in Telegram.
2. Your standard keyboard will be replaced by a permanent Reply Keyboard.
3. Use the D-Pad to move the camera (limited to safe hardware angles of ±75°).
4. Press 📸 Take Photo to trigger the rpicam-still hardware autofocus.

The Sunset Bot
- The bot runs silently, calculating the next sunset time locally.
- At sunset, it moves the camera to Pan 0, Tilt -25, waits 2 seconds for physical stabilization, snaps the photo, and pushes it to your configured Telegram Channel.
- Testing: Send /test to the Sunset Bot via direct message to instantly trigger the camera movement and push a description "Test Photo" to the channel to verify connectivity.
