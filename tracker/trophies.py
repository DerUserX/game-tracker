"""Trophy monitoring via Exophase.

Exophase discovers new PSN trophy lists on its own, often before release (shown with 0 players).
Once a day, every PlayStation game in the tracker is searched for by name; an alert is raised
when a trophy list appears for the first time or the trophy count goes up (DLC).
"""
import logging
import time
import unicodedata
from difflib import SequenceMatcher

import requests

from . import config, igdb
from .models import days_until, has_playstation, title

log = logging.getLogger(__name__)

SEARCH_URL = 'https://api.exophase.com/public/archive/platform/psn/page/1'
HEADERS = {'User-Agent': f'GameReleaseTracker/{config.APP_VERSION} (private use)'}
IGNORE = '-'          # exophase_id value for "wrong match, ignore"
MIN_RATIO = 0.92


def _normalize(name):
    """Like igdb.normalize_name, but also strips diacritics ('Yōtei' → 'yotei')."""
    ascii_name = unicodedata.normalize('NFKD', name or '').encode('ascii', 'ignore').decode()
    return igdb.normalize_name(ascii_name)


def is_match(a, b):
    na, nb = _normalize(a), _normalize(b)
    return bool(na) and (na == nb or SequenceMatcher(None, na, nb).ratio() >= MIN_RATIO)


def _platform_rank(entry):
    names = [p.get('name', '') for p in entry.get('platforms') or []]
    return 2 if 'PS5' in names else 1 if 'PS4' in names else 0


class ExophaseMonitor:
    def __init__(self, store):
        self.store = store
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def search(self, query):
        response = self.session.get(SEARCH_URL, params={'q': query, 'sort': 'added'}, timeout=15)
        response.raise_for_status()
        games = response.json().get('games')
        return games.get('list', []) if isinstance(games, dict) else []

    def find(self, game):
        """Best Exophase entry for a game, or None."""
        known_id = game.get('exophase_id')
        names = [n for n in (game.get('name'), game.get('display_name')) if n]
        for name in dict.fromkeys(names):
            results = self.search(name)
            time.sleep(1.5)
            if known_id:
                hit = next((r for r in results if str(r.get('master_id')) == known_id), None)
                if hit:
                    return hit
            candidates = [r for r in results if any(is_match(r.get('title', ''), n) for n in names)]
            if candidates:
                return max(candidates, key=lambda r: (_platform_rank(r), r.get('total_awards', 0)))
        return None

    def check_all(self):
        """Checks all PS games; returns message texts for the popup."""
        messages = []
        changed = False

        for game in self.store.snapshot():
            if not has_playstation(game) or game.get('exophase_id') == IGNORE:
                continue
            try:
                entry = self.find(game)
            except Exception as e:
                log.warning("Exophase query for %s failed: %s", game['name'], e)
                continue
            if not entry:
                continue

            count = int(entry.get('total_awards') or 0)
            old_count = int(game.get('psn_trophy_count') or 0)
            name = title(game)
            days = days_until(game.get('release_date'))
            already_out = days is not None and days < 0

            if old_count == 0 and count > 0 and not already_out:
                text = f"{name}: trophy list is here ({count} trophies)"
                self.store.add_notification("🏆 Trophy list available", text, "trophy_added", name)
                messages.append(f"🏆 {text}")
            elif old_count and count > old_count:
                text = f"{name}: +{count - old_count} trophies (now {count})"
                self.store.add_notification("🏆 New trophies", text, "trophy_added", name)
                messages.append(f"🏆 {text}")

            fields = {
                'exophase_id': str(entry.get('master_id', '')),
                'exophase_title': entry.get('title', ''),
                'exophase_url': entry.get('endpoint_awards', ''),
                'psn_trophy_count': count,
            }
            if any(game.get(k) != v for k, v in fields.items()):
                with self.store.lock:
                    current = self.store.find(game['uid'])
                    if current:
                        current.update(fields)
                        changed = True

        if changed:
            self.store.save_games()
        return messages
