"""IGDB-API (via Twitch OAuth): Suche per ID/Name, Suchvariationen, Namensvergleich."""
import logging
import re
import threading
import time
from datetime import datetime
from difflib import SequenceMatcher

import requests

from . import config

log = logging.getLogger(__name__)

GAMES_URL = 'https://api.igdb.com/v4/games'
TOKEN_URL = 'https://id.twitch.tv/oauth2/token'


class NetworkError(Exception):
    """IGDB nicht erreichbar – ein Update-Durchlauf sollte dann abbrechen statt jedes Spiel einzeln zu versuchen."""


FIELDS = 'fields name, first_release_date, platforms.name, rating, cover.image_id;'


class IGDBClient:
    def __init__(self):
        self.session = requests.Session()
        self.access_token = None
        self.token_expires_at = 0
        self._token_lock = threading.Lock()
        # IGDB erlaubt 4 Requests/Sekunde – ein Lock serialisiert parallele Aufrufe
        self._request_lock = threading.Lock()

    # --- Auth ---
    def _refresh_token(self):
        if not config.IGDB_CLIENT_ID or not config.IGDB_CLIENT_SECRET:
            log.error("IGDB-Zugangsdaten fehlen in config.json")
            return False
        try:
            response = self.session.post(TOKEN_URL, params={
                'client_id': config.IGDB_CLIENT_ID,
                'client_secret': config.IGDB_CLIENT_SECRET,
                'grant_type': 'client_credentials',
            }, timeout=10)
            if response.status_code == 200:
                data = response.json()
                self.access_token = data['access_token']
                self.token_expires_at = time.time() + data.get('expires_in', 5000000)
                log.info("IGDB Access Token geholt")
                return True
            log.error("Token-Abruf fehlgeschlagen: %s", response.status_code)
        except Exception as e:
            log.error("OAuth-Fehler: %s", e)
        return False

    def _ensure_token(self):
        with self._token_lock:
            if not self.access_token or time.time() > self.token_expires_at - 300:
                if not self._refresh_token():
                    raise NetworkError("Kein IGDB-Token (Twitch nicht erreichbar?)")
            return True

    # --- Requests ---
    def _query(self, body, label, max_retries=3):
        """Schickt eine Apicalypse-Query; gibt Liste oder None zurück."""
        self._ensure_token()

        for attempt in range(max_retries):
            try:
                with self._request_lock:
                    response = self.session.post(GAMES_URL, headers={
                        'Client-ID': config.IGDB_CLIENT_ID,
                        'Authorization': f'Bearer {self.access_token}',
                    }, data=body.encode('utf-8'), timeout=15)
                    time.sleep(0.26)

                if response.status_code == 200:
                    return response.json()
                if response.status_code == 429:
                    log.warning("Rate-Limit (%s), warte 2 Sek.", label)
                    time.sleep(2)
                    continue
                if response.status_code == 401:
                    with self._token_lock:
                        self._refresh_token()
                    continue
                log.error("IGDB-Fehler %s bei %s", response.status_code, label)
                return None
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                log.warning("Verbindungsfehler bei %s, Versuch %d/%d", label, attempt + 1, max_retries)
                if attempt == max_retries - 1:
                    raise NetworkError(f"IGDB nicht erreichbar ({label})")
                time.sleep((attempt + 1) * 2)
            except Exception as e:
                log.error("IGDB-Abfrage %s fehlgeschlagen: %s", label, e)
                return None
        return None

    @staticmethod
    def _parse(game):
        release_date = ''
        if game.get('first_release_date'):
            try:
                release_date = datetime.fromtimestamp(game['first_release_date']).strftime('%Y-%m-%d')
            except (OSError, ValueError):
                pass
        platforms = ', '.join(p.get('name', '') for p in game.get('platforms', []))
        rating = round(game['rating'] / 10.0, 1) if game.get('rating') else 0
        return {
            'name': game.get('name', 'Unbekannt'),
            'release_date': release_date,
            'platforms': platforms,
            'rating': rating,
            'igdb_id': str(game.get('id', '')),
            'cover_id': (game.get('cover') or {}).get('image_id', ''),
        }

    def by_id(self, igdb_id):
        igdb_id = str(igdb_id).strip()
        if not igdb_id.isdigit():
            return None
        data = self._query(f'{FIELDS} where id = {igdb_id};', f'ID {igdb_id}')
        return self._parse(data[0]) if data else None

    def cover_ids(self, igdb_ids):
        """{igdb_id: image_id} für mehrere Spiele in einer Abfrage."""
        ids = [str(i) for i in igdb_ids if str(i).isdigit()]
        if not ids:
            return {}
        data = self._query(f'fields cover.image_id; where id = ({",".join(ids)}); limit 500;', 'Cover') or []
        return {str(g['id']): g['cover']['image_id'] for g in data if g.get('cover', {}).get('image_id')}

    def search(self, name, limit=50):
        escaped = name.replace('\\', '\\\\').replace('"', '\\"')
        data = self._query(f'search "{escaped}"; {FIELDS} limit {limit};', f"'{name}'")
        return [self._parse(g) for g in data] if data else []

    def search_with_variations(self, name):
        """Sucht alle Variationen und liefert eindeutige Ergebnisse (nach ID)."""
        results, seen = [], set()
        for variation in search_variations(name):
            for result in self.search(variation):
                if result['igdb_id'] and result['igdb_id'] not in seen:
                    seen.add(result['igdb_id'])
                    results.append(result)
        return results


def search_variations(name):
    variations = [name]
    if ':' in name:
        variations.append(name.split(':')[0].strip())
    if '-' in name:
        variations.append(name.split('-')[0].strip())

    roman_to_arabic = {'I': '1', 'II': '2', 'III': '3', 'IV': '4', 'V': '5',
                       'VI': '6', 'VII': '7', 'VIII': '8', 'IX': '9', 'X': '10'}
    for roman, arabic in roman_to_arabic.items():
        if re.search(rf' {roman}\b', name, flags=re.IGNORECASE):
            variations.append(re.sub(rf' {roman}\b', f' {arabic}', name, flags=re.IGNORECASE))
        if re.search(rf' {arabic}\b', name):
            variations.append(re.sub(rf' {arabic}\b', f' {roman}', name))

    if name.lower().startswith('the '):
        variations.append(name[4:])

    unique = []
    for v in variations:
        if v and v not in unique:
            unique.append(v)
    return unique


def normalize_name(name):
    if not name:
        return ''
    name = re.sub(r'[^\w\s]', ' ', name.lower())
    name = f" {' '.join(name.split())} "
    roman_map = {' i ': ' 1 ', ' ii ': ' 2 ', ' iii ': ' 3 ', ' iv ': ' 4 ', ' v ': ' 5 ', ' vi ': ' 6 ',
                 ' vii ': ' 7 ', ' viii ': ' 8 ', ' ix ': ' 9 ', ' x ': ' 10 ', ' xi ': ' 11 ', ' xii ': ' 12 '}
    for roman, arabic in roman_map.items():
        name = name.replace(roman, arabic)
    return ' '.join(name.split())


def is_similar_name(name1, name2, threshold=0.65):
    norm1, norm2 = normalize_name(name1), normalize_name(name2)
    if not norm1 or not norm2:
        return False
    if norm1 == norm2 or norm1 in norm2 or norm2 in norm1:
        return True
    return SequenceMatcher(None, norm1, norm2).ratio() >= threshold
