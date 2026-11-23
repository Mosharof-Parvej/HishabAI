"""Vercel serverless entry: exposes the Django WSGI app as `app`."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hishabai.settings")

from hishabai.wsgi import application as app  # noqa: E402,F401
