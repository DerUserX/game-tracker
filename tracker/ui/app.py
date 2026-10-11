"""App-Kern: EIN Tk-Root im Hauptthread, Tray-Icon in eigenem Thread, Task-Queue dazwischen."""
import logging
import queue
import threading
import time

import customtkinter as ctk
import pystray
from PIL import Image, ImageDraw

from .. import config, paths
from ..cloud import GistUploader
from ..covers import CoverCache
from ..igdb import IGDBClient
from ..selfupdate import SelfUpdater
from ..storage import Store
from ..trophies import ExophaseMonitor
from ..updater import Updater
from . import theme
from .add_dialog import AddDialog
from .edit_dialog import EditDialog
from .manager import ManagerWindow
from .notifications import NotificationsWindow
from .popup import Popup
from .waitlist import WaitlistMatchDialog, WaitlistWindow
from .widgets import CoverImages, MessagePopup

log = logging.getLogger(__name__)


class TrackerApp:
    def __init__(self):
        theme.setup()
        self.root = ctk.CTk()
        self.root.withdraw()
        self.root.title(config.APP_NAME)
        self._queue = queue.Queue()
        self._refresh_pending = False

        self.uploader = GistUploader()
        self.store = Store(on_games_saved=self.uploader.upload)
        self.igdb = IGDBClient()
        self.covers = CoverImages(CoverCache(), self.call)
        self.trophies = ExophaseMonitor(self.store)
        self.updater = Updater(
            self.store, self.igdb, self.trophies,
            notify=lambda t, msgs, icon: self.call(MessagePopup, self, t, msgs, icon),
            on_waitlist_match=lambda item, games: self.call(WaitlistMatchDialog, self, item, games),
        )
        self.store.subscribe(self._schedule_refresh)
        self.self_updater = SelfUpdater(self)

        self.popup = None
        self.windows = {}   # key → offenes Fenster (je Typ nur eins)
        self.icon = None

    # ------------------------------------------------------------- Threading
    def call(self, fn, *args, **kwargs):
        """Thread-sicher: führt fn im UI-Thread aus."""
        self._queue.put((fn, args, kwargs))

    def _poll(self):
        try:
            while True:
                fn, args, kwargs = self._queue.get_nowait()
                try:
                    fn(*args, **kwargs)
                except Exception:
                    log.exception("Fehler in UI-Aufgabe %s", getattr(fn, '__name__', fn))
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    def _schedule_refresh(self):
        # Viele Store-Änderungen kurz hintereinander → ein einziges Neuzeichnen
        if not self._refresh_pending:
            self._refresh_pending = True
            self.call(lambda: self.root.after(150, self._refresh_views))

    def _refresh_views(self):
        self._refresh_pending = False
        if self.popup is not None:
            self.popup.render()
        for key in ('manager', 'waitlist', 'notifications'):
            win = self.windows.get(key)
            if win is not None and win.winfo_exists():
                win.render()
        self._update_tray_title()

    # ------------------------------------------------------------- Popup
    def toggle_popup(self):
        if self.popup is not None and self.popup.is_open:
            self.popup.close()
        elif self.popup is not None and time.monotonic() - self.popup.closed_at < 0.5:
            pass  # Klick aufs Tray-Icon hat das Flyout gerade per Fokusverlust geschlossen
        else:
            self.show_popup()

    def show_popup(self):
        if self.popup is None:
            self.popup = Popup(self)
        self.popup.show()

    def _prebuild_popup(self):
        """Flyout im Hintergrund vorbauen, damit der erste Tray-Klick sofort reagiert."""
        if self.popup is None:
            self.popup = Popup(self)

    # ------------------------------------------------------------- Fenster
    def _open(self, key, factory, from_popup):
        """Öffnet ein Fenster (oder holt das offene nach vorn). from_popup: Flyout danach wieder zeigen."""
        win = self.windows.get(key)
        if win is not None and win.winfo_exists():
            win.deiconify()
            win.lift()
            win.focus_force()
            return
        if from_popup and self.popup is not None:
            self.popup.close()

        def on_close():
            self.windows.pop(key, None)
            if from_popup:
                self.root.after(100, self.show_popup)

        self.windows[key] = factory(on_close)

    def open_add_dialog(self, from_popup=False):
        self._open('add', lambda cb: AddDialog(self, on_close=cb), from_popup)

    def open_edit_dialog(self, uid, from_popup=False):
        if self.store.find(uid):
            self._open(f'edit:{uid}', lambda cb: EditDialog(self, uid, on_close=cb), from_popup)

    def open_manager(self):
        self._open('manager', lambda cb: ManagerWindow(self, on_close=cb), False)

    def open_waitlist(self, from_popup=False):
        self._open('waitlist', lambda cb: WaitlistWindow(self, on_close=cb), from_popup)

    def open_notifications(self, from_popup=False):
        self._open('notifications', lambda cb: NotificationsWindow(self, on_close=cb), from_popup)

    # ------------------------------------------------------------- Update
    def start_update(self):
        def work():
            self.updater.run_full_update()
            self._schedule_refresh()
        threading.Thread(target=work, daemon=True, name='manual-update').start()
        self._schedule_refresh()

    # ------------------------------------------------------------- Tray
    def _tray_image(self):
        icon_path = paths.resource('icon.ico')
        if icon_path:
            try:
                return Image.open(icon_path)
            except Exception:
                pass
        image = Image.new('RGB', (64, 64), theme.BG)
        dc = ImageDraw.Draw(image)
        dc.rounded_rectangle([8, 20, 56, 44], 10, fill=theme.ACCENT)
        dc.rectangle([18, 28, 22, 36], fill='white')
        dc.rectangle([15, 31, 25, 33], fill='white')
        dc.ellipse([38, 27, 43, 32], fill=theme.DANGER)
        dc.ellipse([44, 33, 49, 38], fill=theme.SUCCESS)
        return image

    def _update_tray_title(self):
        if self.icon is None:
            return
        unread = self.store.unread_count()
        self.icon.title = config.APP_NAME + (f" – {unread} neue Benachrichtigung(en)" if unread else "")

    def _start_tray(self):
        item = pystray.MenuItem
        menu = pystray.Menu(
            item('Spiele anzeigen', lambda: self.call(self.toggle_popup), default=True),
            item('Spiel hinzufügen', lambda: self.call(self.open_add_dialog)),
            item('Spiele verwalten', lambda: self.call(self.open_manager)),
            item('Warteliste', lambda: self.call(self.open_waitlist)),
            item('Benachrichtigungen', lambda: self.call(self.open_notifications)),
            item('Jetzt aktualisieren', lambda: self.call(self.start_update)),
            pystray.Menu.SEPARATOR,
            item('Beenden', lambda: self.call(self.quit)),
        )
        self.icon = pystray.Icon('game_tracker', self._tray_image(), config.APP_NAME, menu)
        self.icon.run_detached()
        self._update_tray_title()

    def quit(self):
        if self.icon is not None:
            self.icon.stop()
        self.root.quit()

    def _startup_notices(self, updated_to):
        if updated_to:
            text = f"Game Tracker was updated to version {updated_to}."
            self.store.add_notification("⬆️ Updated", text, "update")
            try:
                self.icon.notify(text, config.APP_NAME)  # Windows notification
            except Exception:
                pass
        missing = config.missing_secrets()
        if missing:
            MessagePopup(self, "Credentials missing in config.json",
                         [f"Missing entries: {', '.join(missing)}",
                          "Without them there are no IGDB updates or Gist uploads. "
                          "Copy config.json from the old PC or add the values (see README)."], "⚠️")

    def run(self, show_popup=False, updated_to=None):
        self._start_tray()
        self.updater.start_scheduler()
        self.self_updater.start()
        self.root.after(1500, lambda: self._startup_notices(updated_to))
        self.root.after(50, self._poll)
        self.root.after(500, self._prebuild_popup)
        if show_popup:
            self.root.after(300, self.show_popup)
        self.root.mainloop()
