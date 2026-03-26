#!/usr/bin/env python3
"""OSINT Investigation Suite — Desktop launcher.

Starts the web dashboard and opens the browser automatically.
Used as the entry point for PyInstaller builds.
"""

import os
import sys
import signal
import socket
import threading
import time
import webbrowser
from pathlib import Path


def get_base_path():
    """Get the base path for bundled resources (PyInstaller-aware)."""
    if getattr(sys, '_MEIPASS', None):
        return Path(sys._MEIPASS)
    return Path(__file__).parent


def find_free_port(start=8100, end=8200):
    """Find an available port."""
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    return start


def open_browser(port):
    """Open browser after a short delay to let server start."""
    time.sleep(1.5)
    webbrowser.open(f'http://127.0.0.1:{port}')


def main():
    # Set up environment for bundled mode
    base = get_base_path()
    os.environ.setdefault('OSINT_DATABASE_URL', f'sqlite+aiosqlite:///{Path.home() / ".osint-suite" / "osint.db"}')
    os.environ.setdefault('OSINT_DATABASE_URL_SYNC', f'sqlite:///{Path.home() / ".osint-suite" / "osint.db"}')
    os.environ.setdefault('OSINT_DEBUG', 'false')
    os.environ.setdefault('OSINT_WEB_HOST', '127.0.0.1')

    # Ensure data directory exists
    data_dir = Path.home() / '.osint-suite'
    data_dir.mkdir(exist_ok=True)

    port = find_free_port()
    os.environ['OSINT_WEB_PORT'] = str(port)

    print(f"""
╔══════════════════════════════════════════════╗
║    OSINT Investigation Suite v0.1.0          ║
║    http://127.0.0.1:{port:<5}                    ║
║    Press Ctrl+C to quit                      ║
╚══════════════════════════════════════════════╝
""")

    # Open browser in background thread
    threading.Thread(target=open_browser, args=(port,), daemon=True).start()

    # Handle Ctrl+C gracefully
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))

    # Start uvicorn
    import uvicorn
    uvicorn.run(
        'osintsuite.web.app:app',
        host='127.0.0.1',
        port=port,
        log_level='info',
    )


if __name__ == '__main__':
    main()
