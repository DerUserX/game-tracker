"""Upload von games.json ins GitHub-Gist (Datenquelle der Handy-PWA)."""
import json
import logging
import threading

import requests

from . import config

log = logging.getLogger(__name__)


class GistUploader:
    """Lädt im Hintergrund hoch; schnelle Folge-Speicherungen werden zu einem Upload zusammengefasst."""

    def __init__(self):
        self._pending = None
        self._cond = threading.Condition()
        threading.Thread(target=self._worker, daemon=True, name='gist-upload').start()

    def upload(self, games):
        if config.NO_CLOUD:
            log.info("Cloud-Upload übersprungen (GAMETRACKER_NO_CLOUD)")
            return
        if not config.GITHUB_TOKEN:
            log.warning("Cloud-Upload übersprungen: github_token fehlt in config.json")
            return
        with self._cond:
            self._pending = games
            self._cond.notify()

    def _worker(self):
        while True:
            with self._cond:
                while self._pending is None:
                    self._cond.wait()
                # kurz sammeln, falls direkt weitere Änderungen folgen
                self._cond.wait(timeout=2)
                games, self._pending = self._pending, None
            self._send(games)

    @staticmethod
    def _send(games):
        try:
            response = requests.patch(
                f'https://api.github.com/gists/{config.GIST_ID}',
                headers={
                    'Authorization': f'token {config.GITHUB_TOKEN}',
                    'Accept': 'application/vnd.github+json',
                },
                json={'files': {'games.json': {'content': json.dumps(games, indent=2, ensure_ascii=False)}}},
                timeout=15,
            )
            if response.status_code == 200:
                log.info("Spieleliste ins Gist hochgeladen")
            else:
                log.error("Gist-Upload fehlgeschlagen: %s", response.status_code)
        except Exception as e:
            log.error("Gist-Upload-Fehler: %s", e)
