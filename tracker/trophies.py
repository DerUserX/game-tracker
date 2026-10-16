"""Trophäen-Überwachung über Exophase.

Exophase entdeckt neue Trophäen-/Erfolgslisten selbstständig, oft schon vor Release (mit 0 Spielern).
Einmal täglich wird jedes Spiel mit PlayStation, PC oder Xbox im Tracker per Name gesucht – zuerst die
PlayStation-Liste, sonst Steam, sonst Xbox (die Erfolge sind dort bis auf Platin gleich und tauchen oft
früher auf). Meldung, wenn erstmals eine Liste auftaucht, wenn später die PlayStation-Liste dazukommt
oder wenn die Anzahl steigt (DLC).
"""
import logging
import time
import unicodedata
from difflib import SequenceMatcher

import requests

from . import config, igdb
from .models import days_until, has_achievements, title

log = logging.getLogger(__name__)

SEARCH_URL = 'https://api.exophase.com/public/archive/platform/{platform}/page/1'
PLATFORMS = ['psn', 'steam', 'xbox']           # Reihenfolge = Vorrang
PLATFORM_NAMES = {'psn': 'PlayStation', 'steam': 'Steam', 'xbox': 'Xbox'}
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


def award_label(platform, count):
    """'35 Trophäen' (PlayStation) bzw. '35 Erfolge (Steam)'."""
    if platform in ('', 'psn'):
        return f"{count} {'Trophäe' if count == 1 else 'Trophäen'}"
    return f"{count} {'Erfolg' if count == 1 else 'Erfolge'} ({PLATFORM_NAMES.get(platform, platform)})"


def stored_platform(game):
    """Plattform der gespeicherten Liste; ältere Einträge (vor Steam/Xbox) stammen immer von PlayStation."""
    if game.get('exophase_platform'):
        return game['exophase_platform']
    return 'psn' if game.get('exophase_id') not in ('', None, IGNORE) else ''


class ExophaseMonitor:
    def __init__(self, store):
        self.store = store
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def search(self, platform, query):
        response = self.session.get(SEARCH_URL.format(platform=platform), params={'q': query, 'sort': 'added'},
                                    timeout=15)
        response.raise_for_status()
        games = response.json().get('games')
        return games.get('list', []) if isinstance(games, dict) else []

    def find(self, game):
        """(Exophase-Eintrag, Plattform) – PlayStation vor Steam vor Xbox – oder (None, None)."""
        known_id = game.get('exophase_id')
        known_platform = stored_platform(game)
        names = list(dict.fromkeys(n for n in (game.get('name'), game.get('display_name')) if n))
        for platform in PLATFORMS:
            for name in names:
                results = self.search(platform, name)
                time.sleep(1.5)
                if known_id and platform == known_platform:
                    hit = next((r for r in results if str(r.get('master_id')) == known_id), None)
                    if hit:
                        return hit, platform
                candidates = [r for r in results if any(is_match(r.get('title', ''), n) for n in names)]
                if candidates:
                    return max(candidates, key=lambda r: (_platform_rank(r), r.get('total_awards', 0))), platform
        return None, None

    def check_all(self):
        """Prüft alle Spiele mit PS/PC/Xbox; gibt Meldungstexte für das Popup zurück."""
        messages = []
        changed = False

        def notify(title_text, text, name):
            self.store.add_notification(title_text, text, "trophy_added", name)
            messages.append(f"🏆 {text}")

        for game in self.store.snapshot():
            if not has_achievements(game) or game.get('exophase_id') == IGNORE:
                continue
            try:
                entry, platform = self.find(game)
            except Exception as e:
                log.warning("Exophase-Abfrage für %s fehlgeschlagen: %s", game['name'], e)
                continue
            if not entry:
                continue

            count = int(entry.get('total_awards') or 0)
            old_count = int(game.get('psn_trophy_count') or 0)
            old_platform = stored_platform(game)
            name = title(game)
            days = days_until(game.get('release_date'))
            already_out = days is not None and days < 0

            if old_count == 0 and count > 0:
                if not already_out:   # Ersterfassung bereits erschienener Spiele nur speichern
                    notify("🏆 Trophäenliste verfügbar", f"{name}: Liste ist da – {award_label(platform, count)}", name)
            elif platform == 'psn' and old_platform not in ('', 'psn'):
                # vorher nur Steam/Xbox: kein „+1“ wegen Platin, sondern eigener Hinweis
                if not already_out:
                    notify("🎮 PlayStation-Liste jetzt auch da", f"{name}: PlayStation-Trophäenliste ist jetzt auch da "
                                                                 f"({count} Trophäen)", name)
            elif platform == old_platform and count > old_count:
                notify("🏆 Neue Trophäen", f"{name}: +{count - old_count} – jetzt {award_label(platform, count)}", name)

            fields = {
                'exophase_id': str(entry.get('master_id', '')),
                'exophase_title': entry.get('title', ''),
                'exophase_url': entry.get('endpoint_awards', ''),
                'exophase_platform': platform,
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
