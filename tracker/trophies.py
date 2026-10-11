"""Trophäen-Überwachung über Exophase.

Exophase entdeckt neue PSN-Trophäenlisten selbstständig, oft schon vor Release (mit 0 Spielern).
Einmal täglich wird jedes PlayStation-Spiel im Tracker per Name gesucht; Meldung, wenn eine
Trophäenliste erstmals auftaucht oder die Anzahl steigt (DLC).
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
HEADERS = {'User-Agent': f'GameReleaseTracker/{config.APP_VERSION} (privat)'}
IGNORE = '-'          # exophase_id-Wert für "falscher Treffer, ignorieren"
MIN_RATIO = 0.92


def _normalize(name):
    """Wie igdb.normalize_name, entfernt zusätzlich Akzente ('Yōtei' → 'yotei')."""
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
        """Bester Exophase-Eintrag für ein Spiel oder None."""
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
        """Prüft alle PS-Spiele; gibt Meldungstexte für das Popup zurück."""
        messages = []
        changed = False

        for game in self.store.snapshot():
            if not has_playstation(game) or game.get('exophase_id') == IGNORE:
                continue
            try:
                entry = self.find(game)
            except Exception as e:
                log.warning("Exophase-Abfrage für %s fehlgeschlagen: %s", game['name'], e)
                continue
            if not entry:
                continue

            count = int(entry.get('total_awards') or 0)
            old_count = int(game.get('psn_trophy_count') or 0)
            name = title(game)
            days = days_until(game.get('release_date'))
            already_out = days is not None and days < 0

            if old_count == 0 and count > 0 and not already_out:
                text = f"{name}: Trophäenliste ist da ({count} Trophäen)"
                self.store.add_notification("🏆 Trophäenliste verfügbar", text, "trophy_added", name)
                messages.append(f"🏆 {text}")
            elif old_count and count > old_count:
                text = f"{name}: +{count - old_count} Trophäen (jetzt {count})"
                self.store.add_notification("🏆 Neue Trophäen", text, "trophy_added", name)
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
