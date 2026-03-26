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
import traceback
import webbrowser
from pathlib import Path


# Force stdout/stderr to be unbuffered (critical for PyInstaller)
sys.stdout = open(sys.stdout.fileno(), 'w', buffering=1, closefd=False)
sys.stderr = open(sys.stderr.fileno(), 'w', buffering=1, closefd=False)


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
    time.sleep(2.0)
    webbrowser.open(f'http://127.0.0.1:{port}')


def main():
    try:
        # Set up environment for bundled mode
        base = get_base_path()

        # Ensure template directory is on the path for PyInstaller
        if getattr(sys, '_MEIPASS', None):
            sys.path.insert(0, sys._MEIPASS)

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
""", flush=True)

        # Open browser in background thread
        threading.Thread(target=open_browser, args=(port,), daemon=True).start()

        # Handle Ctrl+C gracefully
        signal.signal(signal.SIGINT, lambda *_: sys.exit(0))

        # Import and start uvicorn with direct app object
        # (string import paths like 'module:app' fail in PyInstaller frozen builds)
        print("Loading application...", flush=True)

        # In PyInstaller builds, fix the templates path
        if getattr(sys, '_MEIPASS', None):
            meipass = Path(sys._MEIPASS)
            tpl_dir = meipass / 'osintsuite' / 'web' / 'templates'
            print(f"  _MEIPASS: {meipass}", flush=True)
            print(f"  Templates exist: {tpl_dir.exists()}", flush=True)
            if tpl_dir.exists():
                print(f"  Templates: {list(tpl_dir.iterdir())}", flush=True)

        import uvicorn
        print("  uvicorn imported", flush=True)

        from osintsuite.web.app import create_app
        print("  create_app imported", flush=True)

        app = create_app()
        print(f"  app created: {app.title}", flush=True)
        print(f"Starting server on port {port}...", flush=True)

        config = uvicorn.Config(
            app,
            host='127.0.0.1',
            port=port,
            log_level='info',
        )
        server = uvicorn.Server(config)
        server.run()

    except Exception as e:
        print(f"\n[ERROR] Failed to start: {e}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        # Write error to a log file in the data dir
        log_path = Path.home() / '.osint-suite' / 'error.log'
        try:
            with open(log_path, 'a') as f:
                f.write(f"\n{'='*60}\n{time.ctime()}\n")
                traceback.print_exc(file=f)
            print(f"Error log written to: {log_path}", file=sys.stderr, flush=True)
        except Exception:
            pass
        input("Press Enter to exit...")
        sys.exit(1)


if __name__ == '__main__':
    main()
