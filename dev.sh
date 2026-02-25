#!/bin/bash
# Development and Testing Helper Script

case "$1" in
    start)
        echo "Starting TwitchLaser in development mode..."
        source venv/bin/activate
        python3 main.py
        ;;

    logs)
        echo "Viewing service logs (Ctrl+C to exit)..."
        sudo journalctl -u twitchlaser -f
        ;;

    status)
        echo "Service Status:"
        sudo systemctl status twitchlaser
        echo ""
        echo "Processes:"
        ps aux | grep -i twitchlaser | grep -v grep
        ;;

    restart)
        echo "Restarting service..."
        sudo systemctl restart twitchlaser
        echo "Waiting for startup..."
        sleep 2
        sudo systemctl status twitchlaser
        ;;

    stop)
        echo "Stopping service..."
        sudo systemctl stop twitchlaser
        ;;

    test-fluidnc)
        echo "Testing FluidNC connection..."
        source venv/bin/activate
        python3 << 'EOF'
import sys
sys.path.append('.')
from laser_controller import LaserController
from config import debug_print

laser = LaserController()
if laser.connected:
    print("✓ Connected to FluidNC")
    success, response = laser.send_command("?")
    print(f"Status: {response}")
else:
    print("✗ Failed to connect to FluidNC")
    print("Check:")
    print("  1. FluidNC is powered on")
    print("  2. Network connection")
    print("  3. Hostname/IP in secrets.py")
EOF
        ;;

    test-camera)
        echo "Testing camera..."
        ls -l /dev/video* 2>/dev/null
        if [ $? -eq 0 ]; then
            echo "✓ Camera device(s) found"
        else
            echo "✗ No camera devices found"
        fi
        ;;

    test-twitch)
        echo "Testing Twitch API connection..."
        source venv/bin/activate
        python3 << 'EOF'
import sys
sys.path.append('.')
from twitch_monitor import TwitchMonitor

def dummy_callback(name, source):
    print(f"Would engrave: {name}")

monitor = TwitchMonitor(dummy_callback)
if monitor.get_access_token():
    print("✓ Successfully authenticated with Twitch")
    user_id = monitor.get_user_id()
    if user_id:
        print(f"✓ Found channel ID: {user_id}")
    else:
        print("✗ Failed to get channel ID")
        print("Check channel name in secrets.py")
else:
    print("✗ Failed to authenticate with Twitch")
    print("Check API credentials in secrets.py")
EOF
        ;;

    backup)
        echo "Backing up data..."
        timestamp=$(date +%Y%m%d_%H%M%S)
        mkdir -p backups
        cp -r data backups/data_$timestamp
        cp secrets.py backups/secrets_$timestamp.py 2>/dev/null || true
        echo "✓ Backup saved to backups/data_$timestamp"
        ;;

    clear-placements)
        echo "⚠️  This will delete all placement data!"
        read -p "Are you sure? (yes/no): " confirm
        if [ "$confirm" = "yes" ]; then
            rm -f data/placements.json
            echo "✓ Placements cleared"
        else
            echo "Cancelled"
        fi
        ;;

    install-dev)
        echo "Installing development dependencies..."
        source venv/bin/activate
        pip install ipython pytest
        echo "✓ Development tools installed"
        ;;

    *)
        echo "TwitchLaser Development Helper"
        echo ""
        echo "Usage: ./dev.sh [command]"
        echo ""
        echo "Commands:"
        echo "  start              - Run in development mode (foreground)"
        echo "  logs              - View live service logs"
        echo "  status            - Check service and process status"
        echo "  restart           - Restart the service"
        echo "  stop              - Stop the service"
        echo "  test-fluidnc      - Test FluidNC connection"
        echo "  test-camera       - Test camera detection"
        echo "  test-twitch       - Test Twitch API connection"
        echo "  backup            - Backup data and settings"
        echo "  clear-placements  - Delete all placement data"
        echo "  install-dev       - Install development tools"
        echo ""
        ;;
esac
