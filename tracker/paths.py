"""Pfade: Daten liegen neben der exe (bzw. neben dem Script), Ressourcen ggf. im PyInstaller-Bundle."""
import os
import sys


def _app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resource_dir():
    return getattr(sys, '_MEIPASS', _app_dir())


APP_DIR = _app_dir()
DATA_DIR = os.environ.get('GAMETRACKER_DATA_DIR') or APP_DIR
RESOURCE_DIR = _resource_dir()

GAMES_FILE = os.path.join(DATA_DIR, 'games.json')
CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')
WAITLIST_FILE = os.path.join(DATA_DIR, 'waitlist.json')
NOTIFICATIONS_FILE = os.path.join(DATA_DIR, 'notifications.json')
COVERS_DIR = os.path.join(DATA_DIR, 'covers')
LOG_FILE = os.path.join(DATA_DIR, 'gametracker.log')


def resource(name):
    """Sucht eine Ressource (z.B. icon.ico) erst im Bundle, dann neben der App."""
    for base in (RESOURCE_DIR, APP_DIR):
        path = os.path.join(base, name)
        if os.path.exists(path):
            return path
    return None
