"""Konstanten. Zugangsdaten kommen aus config.json (neben der exe), NIE aus dem Repo."""
import json
import os

from . import paths

try:
    from .version import VERSION as APP_VERSION  # wird vom GitHub-Build gesetzt
except ImportError:
    APP_VERSION = '0.0.0-dev'


def _load_secrets():
    try:
        with open(paths.CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


_secrets = _load_secrets()

# IGDB/Twitch OAuth
IGDB_CLIENT_ID = _secrets.get('igdb_client_id', '')
IGDB_CLIENT_SECRET = _secrets.get('igdb_client_secret', '')

# GitHub Gist für die Handy-PWA (ID ist öffentlich, Token nicht)
GIST_ID = '6223c6e0ef9f2d6f85060d161a4650e1'
GITHUB_TOKEN = _secrets.get('github_token', '')

# Repo, aus dessen Releases sich die App aktualisiert
UPDATE_REPO = 'demuHB/game-tracker'
UPDATE_URL = os.environ.get('GAMETRACKER_UPDATE_URL') or f'https://api.github.com/repos/{UPDATE_REPO}/releases/latest'
UPDATE_INTERVAL = 6 * 3600

# Entwicklungsmodus: kein Upload ins Gist
NO_CLOUD = os.environ.get('GAMETRACKER_NO_CLOUD') == '1'

APP_NAME = 'Game Release Tracker'

# Tägliches Auto-Update ab dieser Stunde
AUTO_UPDATE_HOUR = 6

COVER_URL = 'https://images.igdb.com/igdb/image/upload/{size}/{image_id}.jpg'


def missing_secrets():
    """Namen der fehlenden Einträge in config.json (für einen Hinweis in der App)."""
    return [name for name, value in (('igdb_client_id', IGDB_CLIENT_ID),
                                     ('igdb_client_secret', IGDB_CLIENT_SECRET),
                                     ('github_token', GITHUB_TOKEN)) if not value]
