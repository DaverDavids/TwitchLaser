# TwitchLaser Quick Start Guide

## Prerequisites

- Raspberry Pi Zero 2 W with Raspberry Pi OS installed
- BACHIN T-A4 laser engraver with FluidNC running
- Both devices on same network
- Twitch developer account

## Step-by-Step Setup

### 1. Get Twitch API Credentials

1. Go to https://dev.twitch.tv/console/apps
2. Click "Register Your Application"
3. Fill in:
   - Name: TwitchLaser
   - OAuth Redirect URLs: http://localhost
   - Category: Other
4. Save **Client ID** and **Client Secret**

### 2. Install on Raspberry Pi

```bash
# Copy twitchlaser folder to Pi
cd ~/twitchlaser

# Run installer
./install.sh
```

### 3. Configure Secrets

```bash
nano secrets.py
```

Update these values:
```python
WIFI_SSID = "YourWiFiName"
WIFI_PSK = "YourWiFiPassword"

TWITCH_CLIENT_ID = "your_client_id_here"
TWITCH_CLIENT_SECRET = "your_client_secret_here"
TWITCH_CHANNEL_NAME = "your_twitch_username"

FLUIDNC_HOST = "bachin-3c-ta4.local"  # or IP address
FLUIDNC_PORT = 23
```

Save and exit (Ctrl+X, Y, Enter)

### 4. Test FluidNC Connection

```bash
# Test ping
ping bachin-3c-ta4.local

# If that doesn't work, find IP:
sudo arp-scan --localnet | grep -i bachin
```

### 5. Start Service

```bash
sudo systemctl start twitchlaser
sudo systemctl status twitchlaser
```

### 6. Open Web Interface

In browser:
```
http://twitchlaser.local:5000
```

Or use IP address:
```
http://<raspberry-pi-ip>:5000
```

### 7. Initial Configuration

In web interface:

1. **Laser Settings**
   - Start with 50% power
   - Speed: 1000 mm/min
   - Test on scrap first!

2. **Text Settings**
   - Initial height: 5mm
   - Minimum height: 2mm

3. **Engraving Area**
   - Width: 200mm (or your workpiece size)
   - Height: 298mm (or your workpiece size)

4. **Home Laser**
   - Click "Home" button
   - Wait for homing to complete

5. **Test Engraving**
   - Enter "TEST" in test field
   - Click "Engrave Test"
   - Watch it engrave!

### 8. Connect Camera (Optional)

```bash
# Check camera
ls /dev/video*

# Should show /dev/video0
```

Camera will auto-start if detected.

### 9. Enable Twitch Monitoring

1. In web interface, click "Start Monitoring"
2. Green LED should light up
3. System now watches for subscriptions

### 10. Streaming to Twitch/OBS

Add browser source in OBS:
```
URL: http://twitchlaser.local:5000/video_feed
Width: 640
Height: 480
```

## Testing Workflow

### Test 1: Manual G-code

1. Go to "Laser Controls"
2. Enter: `G0 X10 Y10`
3. Click "Send Command"
4. Laser should move

### Test 2: Test Engraving

1. Place scrap material on bed
2. Home laser if needed
3. Enter text: "TEST"
4. Click "Engrave Test"
5. Watch it work!

### Test 3: Placement Tracking

1. Engrave multiple names
2. Check "Placement Map"
3. Should show each engraving location

## Troubleshooting

### Can't Access Web Interface

```bash
# Check service status
sudo systemctl status twitchlaser

# View logs
sudo journalctl -u twitchlaser -f

# Find Pi IP address
hostname -I
```

### FluidNC Won't Connect

```bash
# Check FluidNC is on network
ping bachin-3c-ta4.local

# Try telnet
telnet bachin-3c-ta4.local 23

# Check secrets.py has correct hostname
cat secrets.py | grep FLUIDNC
```

### Laser Not Moving

1. Check FluidNC is homed: Click "Home"
2. Check laser is unlocked: Click "Unlock"
3. Check power is on
4. Try manual command: `G0 X0 Y0`

### Twitch Not Working

Check logs:
```bash
sudo journalctl -u twitchlaser -f | grep -i twitch
```

Verify credentials:
```bash
cat secrets.py | grep TWITCH
```

## Daily Operation

### Starting Up

1. Power on laser engraver
2. Power on Raspberry Pi
3. Service auto-starts in ~30 seconds
4. Open web interface
5. Home laser
6. Start Twitch monitoring
7. Ready!

### Shutting Down

1. Stop Twitch monitoring in web interface
2. Let any active engraving finish
3. Power off laser
4. Shutdown Pi: `sudo shutdown -h now`

## Maintenance

### View Logs

```bash
# Real-time logs
sudo journalctl -u twitchlaser -f

# Last 100 lines
sudo journalctl -u twitchlaser -n 100
```

### Restart Service

```bash
sudo systemctl restart twitchlaser
```

### Update Code

```bash
cd ~/twitchlaser
# Copy new files
sudo systemctl restart twitchlaser
```

### Backup Placements

```bash
cp ~/twitchlaser/data/placements.json ~/placements_backup.json
```

### Clear All Placements

In web interface: Click "Clear All Placements" button

Or manually:
```bash
rm ~/twitchlaser/data/placements.json
sudo systemctl restart twitchlaser
```

## Support

Check logs first:
```bash
sudo journalctl -u twitchlaser -n 200
```

Common issues:
- Network connectivity
- Wrong credentials in secrets.py
- FluidNC not responding
- Camera not detected

Test each component separately using the web interface.
