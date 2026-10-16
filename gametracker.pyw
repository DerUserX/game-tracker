"""Game Release Tracker – Einstiegspunkt.

Start:  pythonw gametracker.pyw            (nur Tray)
        python  gametracker.pyw --show     (Spieleliste direkt öffnen)
        GameTracker.exe --selftest         (Start-Test für den GitHub-Build: Oberfläche aufbauen, beenden)
"""
import ctypes
import logging
import os
import sys
import tempfile
import threading
from logging.handlers import RotatingFileHandler

if '--selftest' in sys.argv:
    # Start-Test mit leeren Daten in einem Temp-Ordner, ohne Gist-Upload – muss vor dem Import von paths passieren
    os.environ['GAMETRACKER_DATA_DIR'] = tempfile.mkdtemp(prefix='gametracker-selftest-')
    os.environ['GAMETRACKER_NO_CLOUD'] = '1'

from tracker import paths, singleinstance  # noqa: E402


def setup_logging():
    handlers = [RotatingFileHandler(paths.LOG_FILE, maxBytes=500_000, backupCount=1, encoding='utf-8')]
    if sys.stdout is not None:  # pythonw/exe haben keine Konsole
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')


def arg_value(flag):
    """Wert nach einem Schalter, z.B. '--updated 2.1.15' → '2.1.15'; None, wenn nicht vorhanden."""
    if flag in sys.argv:
        i = sys.argv.index(flag)
        return sys.argv[i + 1] if i + 1 < len(sys.argv) else '?'
    return None


def selftest():
    """Baut App und Spieleliste einmal auf und beendet sich: Exit-Code 0 = startfähig."""
    from tracker.ui.app import TrackerApp
    from tracker.ui.popup import Popup
    app = TrackerApp()
    popup = Popup(app)
    popup.render()
    app.root.update()
    popup.destroy()
    app.root.destroy()
    logging.info("Selbsttest erfolgreich")


def main():
    setup_logging()
    if '--selftest' in sys.argv:
        try:
            selftest()
        except Exception:
            logging.exception("Selbsttest fehlgeschlagen")
            sys.exit(1)
        sys.exit(0)

    updated = arg_value('--updated')
    rolled_back = arg_value('--rolled-back')
    # nach einem Update/Rückweg kurz warten, bis die vorige Instanz die Sperre freigegeben hat
    if not singleinstance.acquire(wait_seconds=15 if (updated or rolled_back) else 0):
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
    threading.Thread(target=selfupdate.cleanup_old, kwargs={'updated': bool(updated)}, daemon=True).start()
    app = TrackerApp()
    app.run(show_popup='--show' in sys.argv, updated_to=updated, rolled_back=rolled_back)

    # Self-Update: Oberfläche ist zu, jetzt auf die Rückmeldung der neuen Version warten (sonst Rückweg)
    if app.self_updater.handover:
        selfupdate.watch_handover(app.self_updater.handover)


if __name__ == '__main__':
    main()
