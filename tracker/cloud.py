"""Upload von games.json ins GitHub-Gist (Datenquelle der Handy-PWA)."""
import json
import logging
import threading

import requests

from . import config

log = logging.getLogger(__name__)

RETRY_DELAYS = [30, 120, 600, 1800]   # Sekunden nach dem 1., 2., 3., ab 4. Fehlschlag


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
        failures = 0
        games = None
        while True:
            with self._cond:
                if games is None:
                    while self._pending is None:
                        self._cond.wait()
                    # kurz sammeln, falls direkt weitere Änderungen folgen
                    self._cond.wait(timeout=2)
                else:
                    # letzter Upload fehlgeschlagen: warten – kommt inzwischen ein neuerer Stand, wird der gesendet
                    delay = RETRY_DELAYS[min(failures - 1, len(RETRY_DELAYS) - 1)]
                    log.info("Gist-Upload: neuer Versuch in %d s", delay)
                    self._cond.wait(timeout=delay)
                if self._pending is not None:
                    games, self._pending = self._pending, None
            if self._send(games):
                failures, games = 0, None
            else:
                failures += 1

    @staticmethod
    def _send(games):
        """True bei Erfolg."""
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
                return True
            log.error("Gist-Upload fehlgeschlagen: %s", response.status_code)
            return response.status_code in (401, 403, 404)   # falscher Token o.Ä.: Wiederholen hilft nicht
        except Exception as e:
            log.error("Gist-Upload-Fehler: %s", e)
            return False
