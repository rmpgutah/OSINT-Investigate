#!/bin/bash
# Deploy OSINT Suite web server for remote access (Android + multi-platform sync)
# Run this on your cloud VPS or server
set -e

echo "=== OSINT Suite Server Deployment ==="

# Install Python if needed
if ! command -v python3 &> /dev/null; then
    echo "Installing Python..."
    sudo apt update && sudo apt install -y python3 python3-pip python3-venv
fi

# Clone or update
if [ ! -d "OSINT-Investigate" ]; then
    git clone https://github.com/JeremyEngram/OSINT-Investigate.git
    cd OSINT-Investigate
else
    cd OSINT-Investigate
    git pull
fi

# Setup venv
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Check for .env
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo ">>> IMPORTANT: Edit .env with your database credentials <<<"
    echo ">>> Then re-run this script <<<"
    exit 1
fi

# Run migrations
osint db init

# Start server (bind to all interfaces for remote access)
echo ""
echo "Starting OSINT Suite web server..."
echo "Dashboard: http://$(hostname -I | awk '{print $1}'):8000"
echo ""

OSINT_WEB_HOST=0.0.0.0 osint-web
