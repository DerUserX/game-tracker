"""Constants. Credentials come from config.json (next to the exe) and NEVER from the repo."""
import json
import os

from . import paths

try:
    from .version import VERSION as APP_VERSION  # set by the GitHub build
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

# GitHub Gist for the phone PWA (the ID is public, the token isn't)
GIST_ID = '6223c6e0ef9f2d6f85060d161a4650e1'
GITHUB_TOKEN = _secrets.get('github_token', '')

# Repo whose releases the app updates itself from
UPDATE_REPO = 'DerUserX/game-tracker'
UPDATE_URL = os.environ.get('GAMETRACKER_UPDATE_URL') or f'https://api.github.com/repos/{UPDATE_REPO}/releases/latest'
UPDATE_INTERVAL = 6 * 3600

# Development mode: no upload to the Gist
NO_CLOUD = os.environ.get('GAMETRACKER_NO_CLOUD') == '1'

APP_NAME = 'Game Release Tracker'

# Daily auto-update from this hour on
AUTO_UPDATE_HOUR = 6

COVER_URL = 'https://images.igdb.com/igdb/image/upload/{size}/{image_id}.jpg'


def missing_secrets():
    """Names of the missing entries in config.json (for a notice in the app)."""
    return [name for name, value in (('igdb_client_id', IGDB_CLIENT_ID),
                                     ('igdb_client_secret', IGDB_CLIENT_SECRET),
                                     ('github_token', GITHUB_TOKEN)) if not value]
