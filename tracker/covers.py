"""IGDB-Cover herunterladen und lokal cachen."""
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from . import config, paths

log = logging.getLogger(__name__)

SIZE = 't_cover_small'  # 90x128
RETRY_AFTER = 1800


class CoverCache:
    def __init__(self):
        os.makedirs(paths.COVERS_DIR, exist_ok=True)
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='cover')
        self._inflight = set()
        self._failed = {}  # image_id → Zeitpunkt des Fehlschlags (erst nach RETRY_AFTER erneut versuchen)
        self._lock = threading.Lock()

    @staticmethod
    def path(image_id):
        return os.path.join(paths.COVERS_DIR, f'{image_id}.jpg')

    def cached(self, image_id):
        """Pfad, falls bereits lokal vorhanden, sonst None."""
        if not image_id:
            return None
        p = self.path(image_id)
        return p if os.path.exists(p) else None

    def fetch(self, image_id, callback):
        """Lädt asynchron; callback(path) wird im Worker-Thread aufgerufen."""
        if not image_id:
            return
        with self._lock:
            if image_id in self._inflight or time.time() - self._failed.get(image_id, 0) < RETRY_AFTER:
                return
            self._inflight.add(image_id)
        self._pool.submit(self._download, image_id, callback)

    def _download(self, image_id, callback):
        try:
            target = self.path(image_id)
            if not os.path.exists(target):
                url = config.COVER_URL.format(size=SIZE, image_id=image_id)
                for attempt in range(3):
                    try:
                        response = requests.get(url, timeout=15)
                        break
                    except requests.exceptions.ConnectionError:
                        if attempt == 2:
                            raise
                        time.sleep(1.5)
                response.raise_for_status()
                with open(target + '.tmp', 'wb') as f:
                    f.write(response.content)
                os.replace(target + '.tmp', target)
            callback(target)
        except Exception as e:
            log.warning("Cover %s nicht ladbar: %s", image_id, e)
            with self._lock:
                self._failed[image_id] = time.time()
        finally:
            with self._lock:
                self._inflight.discard(image_id)
