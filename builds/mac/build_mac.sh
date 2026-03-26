#!/bin/bash
# Build OSINT Suite as a macOS .app bundle using PyInstaller
set -e

echo "=== Building OSINT Suite for macOS ==="

cd "$(dirname "$0")/../.."

# Ensure venv and dependencies
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install -e . pyinstaller

# Build the CLI + Web launcher
pyinstaller \
    --name "OSINT-Suite" \
    --onedir \
    --windowed \
    --icon builds/mac/icon.icns \
    --add-data "src/osintsuite/web/templates:osintsuite/web/templates" \
    --add-data "src/osintsuite/web/static:osintsuite/web/static" \
    --add-data ".env.example:.env.example" \
    --hidden-import osintsuite.cli.app \
    --hidden-import osintsuite.web.app \
    --hidden-import osintsuite.modules.person_search \
    --hidden-import osintsuite.modules.web_scraper \
    --hidden-import osintsuite.modules.email_intel \
    --hidden-import osintsuite.modules.phone_lookup \
    --hidden-import osintsuite.modules.domain_recon \
    --hidden-import osintsuite.modules.social_media \
    --hidden-import asyncpg \
    --hidden-import psycopg \
    --hidden-import uvicorn \
    builds/mac/launcher.py

# Create DMG
echo "Creating DMG..."
mkdir -p downloads
hdiutil create -volname "OSINT Suite" \
    -srcfolder dist/OSINT-Suite.app \
    -ov -format UDZO \
    downloads/OSINT-Suite-macOS.dmg

echo "=== macOS build complete: downloads/OSINT-Suite-macOS.dmg ==="
