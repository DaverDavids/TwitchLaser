* Disclaimer: the code in this repo was created/assisted by AI.

# TwitchLaser - Twitch-Controlled Laser Engraver

Automatically engrave Twitch subscriber names on your laser engraver!

## Features

- 🎮 **Twitch Integration** - Monitors channel for new subscriptions
- 🔥 **Automatic Engraving** - Queues and engraves subscriber names
- 🗺️ **Smart Placement** - Tracks used space and finds optimal locations
- 📹 **Live Streaming** - USB webcam support for streaming the engraving
- 🎬 **OBS Studio Integration** - Controls scenes, sources, and hotkeys via WebSocket
- 📋 **Job Management** - Robust queueing system ensures no events are lost
- 🌐 **Web Interface** - Full control panel accessible via browser
- ⚙️ **FluidNC Integration** - Direct control of BACHIN-3C-TA4 laser engraver
- 💾 **Persistent Storage** - Remembers all placements and settings
- 📏 **Adaptive Sizing** - Shrinks text when space runs out

## Hardware Requirements

- Raspberry Pi Zero 2 W
- BACHIN T-A4 3-axis CNC with FluidNC firmware
- Laser module on gantry
- USB webcam (optional)
- Network connection (WiFi)

## Installation

### Dependencies

- Python 3.7+
- Flask
- OpenCV
- Twitch API credentials
- FluidNC firmware on laser controller
- OBS WebSocket (`obsws-python` for OBS integration)

### Run Installation Script

```bash
cd twitchlaser
chmod +x install.sh
./install.sh
```

This will:
- Install system dependencies
- Set up Python virtual environment
- Install required Python packages
- Configure mDNS (hostname: twitchlaser.local)
- Create systemd service for auto-start
- Enable service on boot

### Configure Secrets

Edit the secrets file with your credentials. Fill in:
- **WiFi credentials** (WIFI_SSID, WIFI_PSK)
- **Twitch API credentials** (get from https://dev.twitch.tv/console/apps)
- **FluidNC connection details** (hostname or IP)

### Start Service

```bash
sudo systemctl start twitchlaser
```

## Web Interface

Access the control panel at:
- `http://twitchlaser.local:5000`
- `http://<raspberry-pi-ip>:5000`

## Configuration

All settings are stored in `data/config.json` and can be edited via web interface:

### Laser Settings
- Power (1-100%)
- Speed (mm/min)
- Number of passes

### Text Settings
- Initial text height (mm)
- Minimum text height (mm)
- Font style

### Engraving Area
- Width (mm) - default 200
- Height (mm) - default 298

### OBS Integration
Configurable via config settings. Allows automatic scene switching, text overlay updates (e.g., "Now Engraving: {name}"), and source toggling when an engraving job starts and finishes.

## Usage

### Automatic Mode

1. Start Twitch monitoring via web interface
2. When someone subscribes, their name is automatically queued
3. System finds optimal placement and engraves
4. Placement is recorded to avoid overlap

### Manual Mode

1. Enter text in "Test Engraving" section
2. Click "Engrave Test"
3. System will find space and engrave immediately

### Streaming Setup

The built-in camera feed can be used with OBS:
1. Add Browser Source
2. URL: `http://twitchlaser.local:5000/video_feed`
3. Crop and position as desired

## File Structure

```
twitchlaser/
├── main.py                 # Main application
├── config.py               # Configuration management
├── secrets.py              # Credentials (create from .example)
├── laser_controller.py     # FluidNC communication
├── layout_manager.py       # Placement tracking
├── gcode_generator.py      # Text to G-code conversion
├── twitch_monitor.py       # Twitch API integration
├── job_manager.py          # Engraving queue management
├── obs_controller.py       # OBS WebSocket integration
├── camera_stream.py        # Webcam streaming
├── web_server.py           # Flask web interface
├── templates/
│   └── index.html          # Web UI
├── static/
│   ├── css/style.css       # Styles
│   └── js/app.js           # Client-side JavaScript
├── data/
│   ├── config.json         # Settings
│   └── placements.json     # Placement tracking
└── requirements.txt        # Python dependencies
```

## API Endpoints

- `GET /` - Web interface
- `GET /api/status` - System status
- `GET /api/config` - Get configuration
- `POST /api/config` - Update configuration
- `POST /api/test_engrave` - Test engraving
- `POST /api/laser_command` - Send G-code command
- `POST /api/laser_home` - Home laser
- `POST /api/laser_unlock` - Unlock after alarm
- `POST /api/laser_stop` - Emergency stop
- `POST /api/clear_placements` - Clear placement data
- `GET /api/placements` - Get all placements
- `POST /api/twitch_toggle` - Start/stop Twitch monitoring
- `GET /api/queue` - Get engraving queue
- `GET /video_feed` - MJPEG camera stream
