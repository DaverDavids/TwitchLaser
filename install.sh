#!/bin/bash
# TwitchLaser Installation Script for Raspberry Pi Zero 2 W

set -e

echo "=================================="
echo "TwitchLaser Installation"
echo "=================================="

# Check if running as root
if [ "$EUID" -eq 0 ]; then 
    echo "Please do not run as root"
    exit 1
fi

# Update system
echo "Updating system..."
sudo apt-get update

# Install system dependencies
echo "Installing system dependencies..."
sudo apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    git \
    v4l-utils \
    avahi-daemon \
    avahi-utils

# Enable and start Avahi (mDNS)
echo "Configuring mDNS..."
sudo systemctl enable avahi-daemon
sudo systemctl start avahi-daemon

# Set hostname
HOSTNAME="twitchlaser"
echo "Setting hostname to $HOSTNAME..."
sudo hostnamectl set-hostname $HOSTNAME

# Update /etc/hosts
sudo sed -i "s/127.0.1.1.*/127.0.1.1\t$HOSTNAME/g" /etc/hosts

# Create virtual environment
echo "Creating Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
echo "Installing Python packages..."
pip install --upgrade pip
pip install -r requirements.txt

# Create secrets.py from template
if [ ! -f secrets.py ]; then
    echo "Creating secrets.py from template..."
    cp secrets.py.example secrets.py
    echo "⚠️  IMPORTANT: Edit secrets.py with your credentials!"
fi

# Create systemd service
echo "Creating systemd service..."
SERVICE_FILE="/etc/systemd/system/twitchlaser.service"

sudo tee $SERVICE_FILE > /dev/null <<EOF
[Unit]
Description=TwitchLaser - Twitch-controlled Laser Engraver
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$(pwd)
Environment="PATH=$(pwd)/venv/bin"
ExecStart=$(pwd)/venv/bin/python3 $(pwd)/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd
sudo systemctl daemon-reload

# Enable service
echo "Enabling twitchlaser service..."
sudo systemctl enable twitchlaser.service

echo ""
echo "=================================="
echo "Installation Complete!"
echo "=================================="
echo ""
echo "Next steps:"
echo "1. Edit secrets.py with your credentials:"
echo "   nano secrets.py"
echo ""
echo "2. Start the service:"
echo "   sudo systemctl start twitchlaser"
echo ""
echo "3. Check status:"
echo "   sudo systemctl status twitchlaser"
echo ""
echo "4. View logs:"
echo "   sudo journalctl -u twitchlaser -f"
echo ""
echo "5. Access web interface:"
echo "   http://twitchlaser.local:5000"
echo ""
echo "=================================="
