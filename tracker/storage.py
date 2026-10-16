"""Laden/Speichern aller JSON-Dateien – thread-safe und atomar."""
import json
import logging
import os
import shutil
import threading
from datetime import date, datetime

from . import paths
from .models import normalize_game

log = logging.getLogger(__name__)

MAX_NOTIFICATIONS = 50
BACKUP_DAYS = 7


def _read_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        log.error("Konnte %s nicht lesen: %s", path, e)
        return default


def _write_json(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


class Store:
    """Hält alle Daten im Speicher. Verändernde Zugriffe laufen über `lock`.

    `listeners` werden nach jeder Änderung aufgerufen (aus beliebigem Thread) –
    die UI leitet das selbst in ihren Hauptthread um.
    """

    def __init__(self, on_games_saved=None):
        self.lock = threading.RLock()
        self.on_games_saved = on_games_saved
        self.listeners = []
        self.games = [normalize_game(g) for g in _read_json(paths.GAMES_FILE, [])]
        self.config = _read_json(paths.CONFIG_FILE, {'last_update': ''})
        self.waitlist = _read_json(paths.WAITLIST_FILE, [])
        self.notifications = _read_json(paths.NOTIFICATIONS_FILE, [])
        self.backup_daily()

    def subscribe(self, callback):
        self.listeners.append(callback)

    def _changed(self):
        for cb in list(self.listeners):
            try:
                cb()
            except Exception:
                log.exception("Listener-Fehler")

    # --- Spiele ---
    def save_games(self, upload=True):
        with self.lock:
            snapshot = [dict(g) for g in self.games]
            _write_json(paths.GAMES_FILE, snapshot)
        if upload and self.on_games_saved:
            self.on_games_saved(snapshot)
        self._changed()

    def snapshot(self):
        with self.lock:
            return [dict(g) for g in self.games]

    def find(self, uid):
        with self.lock:
            return next((g for g in self.games if g.get('uid') == uid), None)

    def add_game(self, game):
        with self.lock:
            self.games.append(normalize_game(game))
        self.save_games()

    def update_game(self, uid, **fields):
        with self.lock:
            game = self.find(uid)
            if game is None:
                return False
            game.update(fields)
        self.save_games()
        return True

    def delete_game(self, uid):
        with self.lock:
            self.games = [g for g in self.games if g.get('uid') != uid]
        self.save_games()

    def set_archived(self, uid, archived):
        self.update_game(uid, archived=archived, archived_at=datetime.now().isoformat() if archived else '')

    # --- Sicherungen ---
    def backup_daily(self):
        """Einmal pro Tag eine Kopie der Daten nach backups/JJJJ-MM-TT/; nur die letzten 7 Tage behalten."""
        try:
            root = os.path.join(paths.DATA_DIR, 'backups')
            target = os.path.join(root, date.today().isoformat())
            if not os.path.isdir(target):
                os.makedirs(target)
                with self.lock:
                    for path in (paths.GAMES_FILE, paths.WAITLIST_FILE, paths.NOTIFICATIONS_FILE):
                        if os.path.exists(path):
                            shutil.copy2(path, target)
                log.info("Sicherung angelegt: %s", target)
            days = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))
            for old in days[:-BACKUP_DAYS]:
                shutil.rmtree(os.path.join(root, old), ignore_errors=True)
        except OSError as e:
            log.warning("Sicherung fehlgeschlagen: %s", e)

    def has_igdb_id(self, igdb_id):
        igdb_id = str(igdb_id)
        with self.lock:
            return any(g.get('igdb_id') == igdb_id for g in self.games)

    # --- Config ---
    def save_config(self):
        with self.lock:
            _write_json(paths.CONFIG_FILE, self.config)

    # --- Warteliste ---
    def save_waitlist(self):
        with self.lock:
            _write_json(paths.WAITLIST_FILE, self.waitlist)
        self._changed()

    def add_to_waitlist(self, name, known_games=None):
        with self.lock:
            self.waitlist.append({
                'name': name,
                'added_date': datetime.now().isoformat(),
                'known_games': list(known_games or []),
            })
        self.save_waitlist()

    def remove_from_waitlist(self, name):
        with self.lock:
            self.waitlist = [w for w in self.waitlist if w['name'] != name]
        self.save_waitlist()

    def ignore_waitlist_matches(self, name, game_names):
        with self.lock:
            for item in self.waitlist:
                if item['name'] == name:
                    known = item.setdefault('known_games', [])
                    known.extend(n for n in game_names if n not in known)
        self.save_waitlist()

    # --- Benachrichtigungen ---
    def save_notifications(self):
        with self.lock:
            _write_json(paths.NOTIFICATIONS_FILE, self.notifications)
        self._changed()

    def add_notification(self, title, message, notification_type, game_name=''):
        with self.lock:
            self.notifications.insert(0, {
                'title': title,
                'message': message,
                'type': notification_type,
                'game_name': game_name,
                'timestamp': datetime.now().isoformat(),
                'read': False,
            })
            del self.notifications[MAX_NOTIFICATIONS:]
        self.save_notifications()

    def mark_all_read(self):
        with self.lock:
            for n in self.notifications:
                n['read'] = True
        self.save_notifications()

    def clear_notifications(self):
        with self.lock:
            self.notifications = []
        self.save_notifications()

    def unread_count(self):
        with self.lock:
            return sum(1 for n in self.notifications if not n.get('read'))
