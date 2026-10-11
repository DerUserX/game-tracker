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


class SelfUpdater:
    def __init__(self, app):
        self.app = app
        self.pending_version = None

    @property
    def enabled(self):
        return getattr(sys, 'frozen', False) and parse_version(config.APP_VERSION) is not None

    def start(self):
        if not self.enabled:
            log.info("Self-Update inaktiv (Version %s)", config.APP_VERSION)
            return
        threading.Thread(target=self._loop, daemon=True, name='selfupdate').start()

    def _loop(self):
        time.sleep(30)
        while True:
            try:
                self.check_and_download()
            except Exception as e:
                log.warning("Update-Prüfung fehlgeschlagen: %s", e)
            time.sleep(config.UPDATE_INTERVAL)

    # --- Download ---
    @staticmethod
    def _fetch_release():
        url = config.UPDATE_URL
        if os.path.exists(url):  # lokales Test-Release
            import json
            with open(url, encoding='utf-8') as f:
                return json.load(f)
        response = requests.get(url, headers={'Accept': 'application/vnd.github+json'}, timeout=15)
        response.raise_for_status()
        return response.json()

    def check_and_download(self):
        if self.pending_version:
            return
        release = self._fetch_release()
        latest = parse_version(release.get('tag_name'))
        current = parse_version(config.APP_VERSION)
        if not latest or latest <= current:
            log.info("Kein Update (installiert %s, neuestes %s)", config.APP_VERSION, release.get('tag_name'))
            return
        asset = next((a for a in release.get('assets', []) if a.get('name') == ASSET_NAME), None)
        if not asset:
            log.warning("Release %s hat kein %s", release.get('tag_name'), ASSET_NAME)
            return

        _, new, _ = exe_paths()
        source = asset['browser_download_url']
        log.info("Lade Update %s …", release['tag_name'])
        if os.path.exists(source):
            shutil.copyfile(source, new)
        else:
            with requests.get(source, stream=True, timeout=60) as response:
                response.raise_for_status()
                with open(new + '.part', 'wb') as f:
                    for chunk in response.iter_content(1 << 16):
                        f.write(chunk)
            os.replace(new + '.part', new)
        if asset.get('size') and os.path.getsize(new) != asset['size']:
            os.remove(new)
            raise IOError("Download unvollständig")

        self.pending_version = release['tag_name'].lstrip('v')
        self.app.call(self.install_when_idle)

    # --- Installation (UI-Thread) ---
    def install_when_idle(self):
        if any(w.winfo_exists() for w in self.app.windows.values()) or self.app.updater.running:
            self.app.root.after(60_000, self.install_when_idle)  # offenen Dialog nicht unterbrechen
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
