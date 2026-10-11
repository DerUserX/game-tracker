"""Game Release Tracker – Einstiegspunkt.

Start:  pythonw gametracker.pyw            (nur Tray)
        python  gametracker.pyw --show     (Spieleliste direkt öffnen)
"""
import ctypes
import logging
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

from tracker import paths


def setup_logging():
    handlers = [RotatingFileHandler(paths.LOG_FILE, maxBytes=500_000, backupCount=1, encoding='utf-8')]
    if sys.stdout is not None:  # pythonw/exe haben keine Konsole
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')


def already_running(wait_seconds=0):
    """Verhindert, dass die App doppelt läuft (z.B. Autostart + manueller Start).

    Nach einem Self-Update wartet die neue Version kurz, bis die alte beendet ist.
    """
    kernel32 = ctypes.windll.kernel32
    deadline = time.time() + wait_seconds
    while True:
        try:
            handle = kernel32.CreateMutexW(None, False, 'GameReleaseTracker.SingleInstance')
            if kernel32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
                return False
            kernel32.CloseHandle(handle)
        except Exception:
            return False
        if time.time() >= deadline:
            return True
        time.sleep(0.5)


def updated_version():
    """Version aus '--updated <version>' (nach einem Self-Update), sonst None."""
    if '--updated' in sys.argv:
        i = sys.argv.index('--updated')
        return sys.argv[i + 1] if i + 1 < len(sys.argv) else '?'
    return None


def main():
    setup_logging()
    updated = updated_version()
    if already_running(wait_seconds=15 if updated else 0):
        logging.info("Game Tracker läuft bereits – Abbruch")
        return
    try:
        # Eigene Taskleisten-Identität statt "python.exe"; scharfe Darstellung bei Skalierung
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('GameReleaseTracker.App')
    except Exception:
        pass

    from tracker import config, selfupdate
    from tracker.ui.app import TrackerApp
    logging.info("Starte Game Release Tracker %s (Daten: %s)", config.APP_VERSION, paths.DATA_DIR)
    threading.Thread(target=selfupdate.cleanup_old, daemon=True).start()
    TrackerApp().run(show_popup='--show' in sys.argv, updated_to=updated)


if __name__ == '__main__':
    main()
