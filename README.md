# TwitchLaser - Twitch-Controlled Laser Engraver

Automatically engrave Twitch subscriber names on your laser engraver!

## Features

- 🎮 **Twitch Integration** - Monitors channel for new subscriptions
- 🔥 **Automatic Engraving** - Queues and engraves subscriber names
- 🗺️ **Smart Placement** - Tracks used space and finds optimal locations
- 📹 **Live Streaming** - USB webcam support for streaming the engraving
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

### 1. Clone or Copy Files

```bash
cd ~
# Copy twitchlaser directory to Raspberry Pi
```

### 2. Run Installation Script

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

### 3. Configure Secrets

Edit the secrets file with your credentials:

```bash
nano secrets.py
```

Fill in:
- **WiFi credentials** (WIFI_SSID, WIFI_PSK)
- **Twitch API credentials** (get from https://dev.twitch.tv/console/apps)
- **FluidNC connection details** (hostname or IP)

### 4. Test FluidNC Connection

Make sure your BACHIN-3C-TA4 is powered on and connected to the network.

Test connection:
```bash
ping bachin-3c-ta4.local
```

Or use IP address if mDNS doesn't work.

### 5. Start Service

```bash
sudo systemctl start twitchlaser
```

Check status:
```bash
sudo systemctl status twitchlaser
```

View logs:
```bash
sudo journalctl -u twitchlaser -f
```

## Web Interface

Access the control panel at:
- `http://twitchlaser.local:5000`
- `http://<raspberry-pi-ip>:5000`

### Features:

- **Live View** - Camera feed of engraving area
- **Test Engraving** - Manually engrave test text
- **Queue Status** - View pending engravings
- **Settings** - Configure laser power, speed, text size
- **Laser Controls** - Home, unlock, send G-code commands
- **Twitch Controls** - Start/stop subscription monitoring
- **Placement Map** - Visual representation of engraved names

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

## Troubleshooting

### FluidNC Not Connected

Check:
- FluidNC powered on
- Network cable connected
- Correct hostname/IP in secrets.py
- Telnet port 23 accessible: `telnet bachin-3c-ta4.local 23`

### Twitch Not Working

Check:
- Valid API credentials in secrets.py
- Correct channel name
- Internet connection working
- Check logs: `sudo journalctl -u twitchlaser -f`

### Camera Not Working

Check:
- USB camera connected
- Camera permissions: `ls -l /dev/video0`
- Try different camera index in web interface

### Service Won't Start

Check logs:
```bash
sudo journalctl -u twitchlaser -n 50
```

Test manually:
```bash
cd ~/twitchlaser
source venv/bin/activate
python3 main.py
```

## File Structure

```
twitchlaser/
├── main.py                 # Main application
├── config.py              # Configuration management
├── secrets.py             # Credentials (create from .example)
├── laser_controller.py    # FluidNC communication
├── layout_manager.py      # Placement tracking
├── gcode_generator.py     # Text to G-code conversion
├── twitch_monitor.py      # Twitch API integration
├── camera_stream.py       # Webcam streaming
├── web_server.py          # Flask web interface
├── templates/
│   └── index.html         # Web UI
├── static/
│   ├── css/style.css      # Styles
│   └── js/app.js          # Client-side JavaScript
├── data/
│   ├── config.json        # Settings
│   └── placements.json    # Placement tracking
└── requirements.txt       # Python dependencies
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

## Safety

⚠️ **IMPORTANT SAFETY NOTES**:

- Never leave laser unattended while operating
- Ensure proper ventilation
- Use appropriate laser safety glasses
- Keep fire extinguisher nearby
- Test laser power on scrap material first
- Emergency stop button available in web interface

## License

MIT License - Use at your own risk

## Credits

Built for BACHIN-3C-TA4 running FluidNC firmware
