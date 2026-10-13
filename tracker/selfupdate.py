"""Self-Update der exe aus GitHub-Releases.

Ablauf: neuestes Release prüfen → wenn neuer, GameTracker.exe als GameTracker.new.exe laden →
(sobald kein Dialog offen ist) laufende exe in GameTracker.old.exe umbenennen, neue an ihre Stelle,
neuen Prozess mit --updated <version> starten, selbst beenden. Der neue Prozess löscht .old.exe.
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

from . import config

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


def cleanup_old():
    """Löscht Reste des letzten Updates (mit kurzer Wiederholung, der alte Prozess beendet sich evtl. noch)."""
    if not getattr(sys, 'frozen', False):
        return
    _, new, old = exe_paths()
    for path in (old, new):
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


class SelfUpdater:
    def __init__(self, app):
        self.app = app
        self.pending_version = None
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
            response = requests.get(url, headers={**headers, 'Authorization': f'Bearer {config.GITHUB_TOKEN}'},
                                    timeout=15)
            if response.status_code not in (401, 403):
                response.raise_for_status()
                return response.json()
            log.warning("Update-Abfrage mit Token abgelehnt (%s) – versuche ohne", response.status_code)
        response = requests.get(url, headers=headers, timeout=15)
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
        try:
            if os.path.exists(source):
                shutil.copyfile(source, part)
            else:
                with requests.get(source, stream=True, timeout=60) as response:
                    response.raise_for_status()
                    with open(part, 'wb') as f:
                        for chunk in response.iter_content(1 << 16):
                            f.write(chunk)
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
            env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1')  # neuer onefile-Prozess entpackt frisch
            subprocess.Popen([exe, '--updated', self.pending_version], env=env, close_fds=True,
                             creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
        except Exception as e:
            log.error("Update-Installation fehlgeschlagen: %s", e)
            if not os.path.exists(exe) and os.path.exists(old):
                os.replace(old, exe)   # zurückrollen
            self.app.store.add_notification("⚠️ Update fehlgeschlagen",
                                            f"Version {self.pending_version} konnte nicht installiert werden: {e}",
                                            "update")
            self.pending_version = None
            return
        log.info("Update auf %s gestartet – beende mich", self.pending_version)
        self.app.quit()
