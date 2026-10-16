"""Datenaktualisierung (IGDB, Warteliste, Release-Erinnerungen, PSN) und Tages-Scheduler."""
import logging
import threading
import time
from datetime import datetime

from . import config, igdb
from .models import days_until, format_date_european, is_tba, title

log = logging.getLogger(__name__)


class Updater:
    """`notify(title, messages, icon)` zeigt ein Popup – wird aus dem Worker-Thread aufgerufen,
    die UI muss das selbst in ihren Hauptthread umleiten."""

    def __init__(self, store, igdb_client, trophy_monitor, notify, on_waitlist_match):
        self.store = store
        self.igdb = igdb_client
        self.trophies = trophy_monitor
        self.notify = notify
        self.on_waitlist_match = on_waitlist_match
        self._run_lock = threading.Lock()
        self.running = False
        self.last_error = ''

    # --- Einstieg ---
    def start_scheduler(self):
        threading.Thread(target=self._scheduler_loop, daemon=True, name='scheduler').start()

    def run_full_update(self):
        """Kompletter Durchlauf; blockiert. Parallelaufrufe werden ignoriert."""
        if not self._run_lock.acquire(blocking=False):
            log.info("Update läuft bereits")
            return None
        self.running = True
        try:
            log.info("Aktualisierung gestartet")
            self.last_error = ''
            count = self.update_games()
            self.check_waitlist()
            self.check_upcoming_releases()
            trophy_messages = self.trophies.check_all()
            if trophy_messages:
                self.notify("🏆 Trophäen-Neuigkeiten", trophy_messages, "🏆")
            self.store.config['last_update'] = datetime.now().isoformat()
            self.store.save_config()
            log.info("%d Spiel(e) aktualisiert", count)
            return count
        except igdb.NetworkError as e:
            log.error("Aktualisierung abgebrochen: %s", e)
            self.last_error = str(e)
            return None
        except Exception:
            log.exception("Fehler bei der Aktualisierung")
            return None
        finally:
            self.running = False
            self._run_lock.release()

    def _scheduler_loop(self):
        try:
            self.backfill_covers()
        except igdb.NetworkError as e:
            log.warning("Cover-Nachladen übersprungen: %s", e)
        except Exception:
            log.exception("Cover-Nachladen fehlgeschlagen")
        time.sleep(60)
        while True:
            try:
                self.store.backup_daily()
                if self._due():
                    self.run_full_update()
            except Exception:
                log.exception("Scheduler-Fehler")
            time.sleep(3600)

    def _due(self):
        last = self.store.config.get('last_update', '')
        if not last:
            return True
        try:
            last_dt = datetime.fromisoformat(last)
        except ValueError:
            return True
        now = datetime.now()
        return last_dt.date() < now.date() and now.hour >= config.AUTO_UPDATE_HOUR

    def backfill_covers(self):
        """Ergänzt fehlende cover_ids (z.B. bei Spielen aus Version 1.x) mit einer einzigen Abfrage."""
        missing = [g for g in self.store.snapshot() if g.get('igdb_id') and not g.get('cover_id')]
        if not missing:
            return
        found = self.igdb.cover_ids(g['igdb_id'] for g in missing)
        if not found:
            return
        with self.store.lock:
            for game in self.store.games:
                if not game.get('cover_id') and game.get('igdb_id') in found:
                    game['cover_id'] = found[game['igdb_id']]
        self.store.save_games()
        log.info("%d Cover-IDs ergänzt", len(found))

    # --- IGDB-Update ---
    def update_games(self):
        updated = 0
        changed = False
        messages = []

        for game in self.store.snapshot():
            if game.get('archived'):
                continue   # erschienen und archiviert – nur noch die Trophäen werden geprüft
            if game.get('igdb_id'):
                result = self.igdb.by_id(game['igdb_id'])
            else:
                results = []
                for variation in igdb.search_variations(game['name']):
                    results = self.igdb.search(variation)
                    if results:
                        break
                result = results[0] if results and igdb.is_similar_name(results[0]['name'], game['name']) else None

            if not result:
                continue

            fields, message = self._apply_result(game, result)
            if message:
                messages.append(message)
            if fields:
                with self.store.lock:
                    current = self.store.find(game['uid'])
                    if current:
                        current.update(fields)
                        changed = True
                        if 'release_date' in fields:
                            updated += 1

        if changed:
            self.store.save_games()
        if messages:
            self.notify("📅 Neue Release-Daten", messages, "📅")
        return updated

    def _apply_result(self, game, result):
        """Berechnet geänderte Felder für ein Spiel; erzeugt ggf. Benachrichtigung."""
        fields = {}
        message = None
        name = title(game)

        if result.get('platforms') and result['platforms'] != game.get('platforms'):
            fields['platforms'] = result['platforms']
        if result.get('cover_id') and result['cover_id'] != game.get('cover_id'):
            fields['cover_id'] = result['cover_id']
        if not game.get('igdb_id') and result.get('igdb_id'):
            fields['igdb_id'] = result['igdb_id']

        old_date = game.get('release_date', '')
        old_window = game.get('release_window', '')
        new_date = format_date_european(result.get('release_date', ''))
        new_window = result.get('release_window', '')
        if not new_date:
            return fields, message

        date_changed = new_date != old_date
        window_changed = new_window != old_window
        old_label, new_label = old_window or old_date, new_window or new_date

        if date_changed or window_changed:
            # Erstes Mal mit Zeitraum-Erkennung: gleicher Platzhalter, jetzt nur als „2027“/„Q4 2027“ erkannt → still
            first_detection = not old_window and new_window and not date_changed
            if not first_detection and old_label != new_label:
                if not old_label:
                    head, text = "📅 Neues Release-Datum", f"{name} - Erster Termin: {new_label}"
                    message = f"📅 {name}: {new_label} (neu!)"
                elif old_window and not new_window:
                    head, text = "📅 Genaues Datum", f"{name} - Genaues Datum: {new_date} (vorher: {old_window})"
                    message = f"📅 {name}: {new_date} (vorher: {old_window})"
                elif new_window and old_window:
                    head, text = "📆 Zeitraum präzisiert", f"{name} - {new_window} (vorher: {old_window})"
                    message = f"📆 {name}: {new_window} (vorher: {old_window})"
                else:
                    head, text = "📅 Neues Release-Datum", f"{name} - Neues Datum: {new_label} (vorher: {old_label})"
                    message = f"📅 {name}: {new_label} (vorher: {old_label})"
                self.store.add_notification(head, text, "date_change", name)
            fields.update(release_date=new_date, release_window=new_window, last_known_date=new_date,
                          rating=result.get('rating', game.get('rating', 0)))
            if date_changed:
                fields['manual_tba'] = False
        return fields, message

    # --- Warteliste ---
    def check_waitlist(self):
        for item in list(self.store.waitlist):
            results = self.igdb.search_ranked(item['name'])
            known = item.get('known_games', [])
            new_games = [r for r in results
                         if not any(igdb.is_similar_name(r['name'], k) for k in known)
                         and not self.store.has_igdb_id(r['igdb_id'])]
            if new_games:
                self.store.add_notification("🔔 Warteliste - Neue Treffer",
                                            f"{len(new_games)} neue(s) Spiel(e) für '{item['name']}' gefunden",
                                            "waitlist_match", item['name'])
                self.on_waitlist_match(item, new_games)

    # --- Erinnerungen ---
    def check_upcoming_releases(self):
        """Erinnert genau 7 Tage vorher und am Releasetag."""
        notified = self.store.config.setdefault('notified_releases', {})
        messages = []

        for game in self.store.snapshot():
            if is_tba(game) or game.get('archived'):
                continue
            days = days_until(game['release_date'])
            if days not in (7, 0):
                continue
            name = title(game)
            key = f"{name}|{game['release_date']}|{days}"
            if key in notified:
                continue
            if days == 0:
                self.store.add_notification("🎉 Release HEUTE!", f"{name} erscheint heute!", "upcoming_release", name)
                messages.append(f"🎉 {name} erscheint HEUTE!")
            else:
                self.store.add_notification("⏰ Release in 7 Tagen", f"{name} erscheint in 7 Tagen!",
                                            "upcoming_release", name)
                messages.append(f"⏰ {name} erscheint in 7 Tagen ({game['release_date']})")
            notified[key] = datetime.now().isoformat()

        # Alte Einträge (Release > 7 Tage her) aufräumen
        for key in list(notified):
            parts = key.split('|')
            days_left = days_until(parts[1]) if len(parts) > 1 else None
            if days_left is not None and days_left < -7:
                del notified[key]
        self.store.save_config()

        if messages:
            self.notify("⏰ Anstehende Releases", messages, "🎮")
