"""Windows app launcher — starts the web server and opens the browser."""

import os
import sys
import threading
import time
import webbrowser


def start_server():
    """Start the FastAPI web server."""
    os.environ.setdefault("OSINT_WEB_HOST", "127.0.0.1")
    os.environ.setdefault("OSINT_WEB_PORT", "8000")

    from osintsuite.web.app import main
    main()


def open_browser():
    """Open the dashboard in the default browser after a short delay."""
    time.sleep(2)
    webbrowser.open("http://127.0.0.1:8000")


if __name__ == "__main__":
    browser_thread = threading.Thread(target=open_browser, daemon=True)
    browser_thread.start()
    start_server()
