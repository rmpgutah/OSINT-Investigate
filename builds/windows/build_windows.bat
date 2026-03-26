@echo off
REM Build OSINT Suite as a Windows executable using PyInstaller
echo === Building OSINT Suite for Windows ===

cd /d "%~dp0\..\.."

REM Ensure venv
if not exist ".venv" (
    python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -e . pyinstaller

REM Build executable
pyinstaller ^
    --name "OSINT-Suite" ^
    --onedir ^
    --windowed ^
    --icon builds\windows\icon.ico ^
    --add-data "src\osintsuite\web\templates;osintsuite\web\templates" ^
    --add-data "src\osintsuite\web\static;osintsuite\web\static" ^
    --add-data ".env.example;." ^
    --hidden-import osintsuite.cli.app ^
    --hidden-import osintsuite.web.app ^
    --hidden-import osintsuite.modules.person_search ^
    --hidden-import osintsuite.modules.web_scraper ^
    --hidden-import osintsuite.modules.email_intel ^
    --hidden-import osintsuite.modules.phone_lookup ^
    --hidden-import osintsuite.modules.domain_recon ^
    --hidden-import osintsuite.modules.social_media ^
    --hidden-import asyncpg ^
    --hidden-import psycopg ^
    --hidden-import uvicorn ^
    builds\windows\launcher.py

REM Create ZIP
echo Creating ZIP archive...
if not exist downloads mkdir downloads
powershell Compress-Archive -Path "dist\OSINT-Suite\*" -DestinationPath "downloads\OSINT-Suite-Windows.zip" -Force

echo === Windows build complete: downloads\OSINT-Suite-Windows.zip ===
pause
