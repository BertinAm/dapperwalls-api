"""WSGI entry point for Phusion Passenger (Namecheap cPanel "Setup Python App").

In cPanel's Setup Python App screen:
  * "Application root" is the directory containing this file.
  * "Application startup file" is this file (passenger_wsgi.py).
  * "Application Entry point" is `application`.

Environment variables (SECRET_KEY, DATABASE_URL, etc.) are set on that same
screen; Passenger does not read a shell profile. Click "Restart" after any
change to code or environment variables.
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from config.wsgi import application  # noqa: E402,F401
