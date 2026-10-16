"""Self-Update der exe aus GitHub-Releases.

Ablauf: neuestes Release prüfen → wenn neuer, GameTracker.exe als GameTracker.new.exe laden →
(sobald kein Dialog offen ist) laufende exe in GameTracker.old.exe umbenennen, neue an ihre Stelle,
neuen Prozess mit --updated <version> starten.

Rückweg bei kaputtem Update: Die alte Version beendet nur ihre Oberfläche und wartet, bis die neue
„läuft sauber“ meldet (update_health.json). Stürzt die neue ab oder meldet sich nicht innerhalb von
HEALTH_TIMEOUT Sekunden, stellt die alte sich wieder her, startet sich neu und merkt sich die kaputte
Version, damit sie nicht erneut geladen wird. Erst nach der Rückmeldung löscht die neue .old.exe.
Nur in der gebauten exe aktiv – beim Start per Python passiert nichts.
"""
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time

import requests

from . import config, singleinstance

log = logging.getLogger(__name__)

ASSET_NAME = 'GameTracker.exe'


def parse_version(text):
    """'v2.1.12' → (2, 1, 12); Dev-Builds/unbekannt → None."""
    if not text or 'dev' in text:
        return None
    numbers = re.findall(r'\d+', text)
    return tuple(int(n) for n in numbers[:3]) if numbers else None


def exe_paths():
    exe = sys.executable
    folder = os.path.dirname(exe)
    return exe, os.path.join(folder, 'GameTracker.new.exe'), os.path.join(folder, 'GameTracker.old.exe')


HEALTH_TIMEOUT = int(os.environ.get('GAMETRACKER_HEALTH_TIMEOUT', '90'))   # Sekunden
HEALTHY_AFTER = int(os.environ.get('GAMETRACKER_HEALTHY_AFTER', '15'))    # so lange muss die neue Version laufen


def _side_file(name):
    return os.path.join(os.path.dirname(sys.executable), name)


def _read_json(path):
    import json
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_json(path, data):
    import json
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f)


def health_file():
    return _side_file('update_health.json')


def skip_file():
    return _side_file('update_skip.json')


def skipped_version():
    """Version, die nach einem fehlgeschlagenen Start übersprungen wird."""
    return _read_json(skip_file()).get('version', '')


def cleanup_old(updated=False, keep_health=False):
    """Löscht Reste des letzten Updates (mit kurzer Wiederholung, der alte Prozess beendet sich evtl. noch).

    Direkt nach einem Update (updated=True) bleibt .old.exe stehen – sie wird für den Rückweg gebraucht,
    bis mark_healthy() die neue Version bestätigt.
    """
    if not getattr(sys, 'frozen', False):
        return
    _, new, old = exe_paths()
    paths_to_remove = [new] if updated else [old, new, _side_file('GameTracker.failed.exe')]
    if not updated and not keep_health and os.path.exists(health_file()):
        os.remove(health_file())
    for path in paths_to_remove:
        for _ in range(20):
            if not os.path.exists(path):
                break
            try:
                os.remove(path)
            except OSError:
                time.sleep(0.5)


# Wartezeiten nach einer fehlgeschlagenen Prüfung (Sekunden); per Env für Tests überschreibbar
RETRY_DELAYS = [int(x) for x in os.environ.get('GAMETRACKER_UPDATE_RETRY', '120,300,900,1800').split(',')]
FIRST_DELAY = int(os.environ.get('GAMETRACKER_UPDATE_FIRST_DELAY', '10'))
MAX_WAIT_FOR_DIALOGS = 10   # Minuten, danach Hinweis „Update bereit“
QUICK_RETRY_PAUSES = [3, 10]   # Sekunden zwischen Sofort-Wiederholungen bei Verbindungsabbruch


def _with_retries(action, what):
    """Führt action() aus; bei Verbindungsabbruch/Zeitüberschreitung bis zu 3 Versuche mit kurzen Pausen.

    Nötig, weil manche Netze/Firewalls Verbindungen zu GitHub gelegentlich mitten im Aufbau kappen
    (ConnectionResetError 10054) – ein zweiter Versuch Sekunden später klappt dann meist.
    """
    attempts = len(QUICK_RETRY_PAUSES) + 1
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if attempt == attempts:
                raise
            pause = QUICK_RETRY_PAUSES[attempt - 1]
            log.warning("%s: Verbindung abgebrochen (%s) – Versuch %d/%d in %d s",
                        what, type(e).__name__, attempt + 1, attempts, pause)
            time.sleep(pause)


class SelfUpdater:
    def __init__(self, app):
        self.app = app
        self.pending_version = None
        self.handover = None   # nach install(): Daten für watch_handover() in der alten Version
        self._check_lock = threading.Lock()
        self._wait_minutes = 0
        self.status = {'last_check': None, 'last_error': '', 'latest': ''}

    @property
    def enabled(self):
        return getattr(sys, 'frozen', False) and parse_version(config.APP_VERSION) is not None

    def start(self):
        if not self.enabled:
            log.info("Self-Update inaktiv (Version %s)", config.APP_VERSION)
            return
        threading.Thread(target=self._loop, daemon=True, name='selfupdate').start()

    def _loop(self):
        """Prüft kurz nach dem Start, danach alle 6 h. Nach Fehlern schnell erneut (2, 5, 15, 30 Min. …)."""
        time.sleep(FIRST_DELAY)
        failures = 0
        while True:
            try:
                self.check_and_download()
                failures = 0
                delay = config.UPDATE_INTERVAL
            except Exception as e:
                delay = RETRY_DELAYS[min(failures, len(RETRY_DELAYS) - 1)]
                failures += 1
                log.warning("Update-Prüfung fehlgeschlagen (%s) – nächster Versuch in %d s", e, delay)
            time.sleep(delay)

    def check_manual(self):
        """Vom Tray-Menü: sofort prüfen und das Ergebnis als Windows-Benachrichtigung melden."""
        if not self.enabled:
            self.app.notify_tray(f"Entwicklungsversion {config.APP_VERSION} – kein automatisches Update.")
            return

        def work():
            try:
                result = self.check_and_download()
            except Exception as e:
                self.app.notify_tray(f"Update-Prüfung fehlgeschlagen: {e}")
                return
            if result == 'latest':
                self.app.notify_tray(f"Du hast die neueste Version ({config.APP_VERSION}).")
            elif result == 'downloaded':
                self.app.notify_tray(f"Update {self.pending_version} wird installiert – die App startet gleich neu.")
            elif result == 'pending':
                self.app.notify_tray(f"Update {self.pending_version} ist bereit und wird gleich installiert.")

        threading.Thread(target=work, daemon=True, name='selfupdate-manual').start()

    # --- Download ---
    @staticmethod
    def _fetch_release():
        url = config.UPDATE_URL
        if os.path.exists(url):  # lokales Test-Release
            import json
            with open(url, encoding='utf-8') as f:
                return json.load(f)
        headers = {'Accept': 'application/vnd.github+json'}
        if config.GITHUB_TOKEN:
            # mit Token: 5000 statt 60 Anfragen/Std. (das 60er-Limit teilen sich alle Geräte am Anschluss)
            response = _with_retries(lambda: requests.get(
                url, headers={**headers, 'Authorization': f'Bearer {config.GITHUB_TOKEN}'}, timeout=15),
                "Update-Abfrage")
            if response.status_code not in (401, 403):
                response.raise_for_status()
                return response.json()
            log.warning("Update-Abfrage mit Token abgelehnt (%s) – versuche ohne", response.status_code)
        response = _with_retries(lambda: requests.get(url, headers=headers, timeout=15), "Update-Abfrage")
        response.raise_for_status()
        return response.json()

    def check_and_download(self):
        """Gibt 'latest', 'downloaded' oder 'pending' zurück; Fehler werden als Exception weitergereicht."""
        with self._check_lock:
            if self.pending_version:
                return 'pending'
            self.status['last_check'] = time.time()
            try:
                release = self._fetch_release()
                latest = parse_version(release.get('tag_name'))
                current = parse_version(config.APP_VERSION)
                self.status['latest'] = release.get('tag_name', '')
                if not latest or latest <= current:
                    log.info("Kein Update (installiert %s, neuestes %s)", config.APP_VERSION, release.get('tag_name'))
                    self.status['last_error'] = ''
                    return 'latest'
                if release.get('tag_name', '').lstrip('v') == skipped_version():
                    log.info("Version %s wird übersprungen (startete beim letzten Versuch nicht)",
                             release.get('tag_name'))
                    self.status['last_error'] = ''
                    return 'latest'
                asset = next((a for a in release.get('assets', []) if a.get('name') == ASSET_NAME), None)
                if not asset:
                    raise IOError(f"Release {release.get('tag_name')} hat kein {ASSET_NAME}")
                self._download(release['tag_name'], asset)
            except Exception as e:
                self.status['last_error'] = str(e)
                raise
            self.status['last_error'] = ''
            self.pending_version = release['tag_name'].lstrip('v')
            self._wait_minutes = 0
            self.app.call(self.install_when_idle)
            return 'downloaded'

    @staticmethod
    def _download(tag, asset):
        _, new, _ = exe_paths()
        source = asset['browser_download_url']
        log.info("Lade Update %s …", tag)
        part = new + '.part'

        def fetch():   # bei Abbruch beginnt der Download von vorn
            with requests.get(source, stream=True, timeout=60) as response:
                response.raise_for_status()
                with open(part, 'wb') as f:
                    for chunk in response.iter_content(1 << 16):
                        f.write(chunk)

        try:
            if os.path.exists(source):
                shutil.copyfile(source, part)
            else:
                _with_retries(fetch, "Update-Download")
            if asset.get('size') and os.path.getsize(part) != asset['size']:
                raise IOError("Download unvollständig")
            os.replace(part, new)
        finally:
            if os.path.exists(part):
                os.remove(part)

    # --- Installation (UI-Thread) ---
    def install_when_idle(self):
        """Installiert sofort – außer ein Dialog ist offen (dann jede Minute erneut prüfen).

        Auf das tägliche Daten-Update wird bewusst NICHT gewartet: Es startet nach dem Neustart ohnehin wieder.
        """
        if any(w.winfo_exists() for w in self.app.windows.values()):
            self._wait_minutes += 1
            if self._wait_minutes == MAX_WAIT_FOR_DIALOGS:
                self.app.notify_tray(f"Update {self.pending_version} ist bereit – es wird installiert, "
                                     "sobald alle Fenster geschlossen sind.")
            self.app.root.after(60_000, self.install_when_idle)
            return
        self.install()

    def install(self):
        exe, new, old = exe_paths()
        try:
            if os.path.exists(old):
                os.remove(old)
            os.replace(exe, old)          # laufende exe darf umbenannt werden
            os.replace(new, exe)
            _write_json(health_file(), {'version': self.pending_version, 'ok': False})
            singleinstance.release()      # die neue Version darf starten, während wir auf ihre Rückmeldung warten
            proc = _start(exe, ['--updated', self.pending_version])
            self.handover = {'proc': proc, 'version': self.pending_version, 'previous': config.APP_VERSION}
        except Exception as e:
            log.error("Update-Installation fehlgeschlagen: %s", e)
            if not os.path.exists(exe) and os.path.exists(old):
                os.replace(old, exe)   # zurückrollen
            self.app.store.add_notification("⚠️ Update fehlgeschlagen",
                                            f"Version {self.pending_version} konnte nicht installiert werden: {e}",
                                            "update")
            self.pending_version = None
            return
        log.info("Update auf %s gestartet – warte auf Rückmeldung der neuen Version", self.pending_version)
        self.app.quit()

    def mark_healthy(self):
        """Von der NEUEN Version aufgerufen, sobald sie eine Weile sauber läuft: Update bestätigen, Altlasten löschen."""
        data = _read_json(health_file())
        if not data:
            return
        data['ok'] = True
        _write_json(health_file(), data)
        log.info("Update auf %s bestätigt", data.get('version'))

        def later():
            # erst warten, bis die alte Version die Rückmeldung gelesen und sich beendet hat; die
            # Rückmeldedatei bleibt bis zum nächsten normalen Start stehen (sonst Fehlalarm beim Lesen)
            time.sleep(10)
            cleanup_old(keep_health=True)
        threading.Thread(target=later, daemon=True).start()


def _start(exe, args):
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1')  # neuer onefile-Prozess entpackt frisch
    return subprocess.Popen([exe] + args, env=env, close_fds=True,
                            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)


def watch_handover(handover):
    """Läuft in der ALTEN Version nach dem Beenden der Oberfläche: wartet auf die Rückmeldung der neuen.

    Bleibt sie aus (Absturz oder Zeitüberschreitung), wird die alte Version wiederhergestellt und neu gestartet.
    """
    proc, version = handover['proc'], handover['version']
    deadline = time.time() + HEALTH_TIMEOUT
    while time.time() < deadline:
        if _read_json(health_file()).get('ok'):
            log.info("Neue Version %s läuft – alte Version beendet sich", version)
            return
        if proc.poll() is not None:
            log.error("Neue Version %s hat sich beendet (Code %s), bevor sie bereit war", version, proc.returncode)
            break
        time.sleep(0.5)
    else:
        log.error("Neue Version %s hat sich nicht innerhalb von %d s gemeldet", version, HEALTH_TIMEOUT)
    rollback(proc, version, handover['previous'])


def rollback(proc, version, previous):
    exe, _, old = exe_paths()
    if proc.poll() is None:
        try:
            proc.kill()
            proc.wait(timeout=10)
        except Exception:
            pass
    for _ in range(20):   # die abgestürzte exe ist evtl. noch kurz gesperrt
        try:
            failed = _side_file('GameTracker.failed.exe')
            if os.path.exists(failed):
                os.remove(failed)
            if os.path.exists(exe):
                os.replace(exe, failed)
            os.replace(old, exe)
            break
        except OSError:
            time.sleep(0.5)
    else:
        log.error("Rückweg fehlgeschlagen – bitte GameTracker.old.exe von Hand in GameTracker.exe umbenennen")
        return
    _write_json(skip_file(), {'version': version})
    if os.path.exists(health_file()):
        os.remove(health_file())
    log.warning("Update %s fehlgeschlagen – zurück auf %s", version, previous)
    _start(exe, ['--rolled-back', version])
