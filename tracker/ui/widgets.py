"""Wiederverwendbare UI-Bausteine."""
import ctypes
import logging
import sys
import tkinter
import weakref

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont, ImageTk

from .. import paths
from ..models import days_until, format_weeks, platform_list, short_platform
from . import theme

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- Bildschirm
class _Rect(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long), ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_ulong), ('rcMonitor', _Rect), ('rcWork', _Rect), ('dwFlags', ctypes.c_ulong)]


class _Point(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]


def monitor_at_cursor(widget):
    """(Arbeitsbereich, Skalierung) des Monitors unter dem Mauszeiger.

    Arbeitsbereich = Monitor ohne Taskleiste, in echten Pixeln: (links, oben, rechts, unten).
    Skalierung = Windows-Anzeigeskalierung dieses Monitors (1.0 = 100 %, 1.5 = 150 % …).
    Wichtig bei mehreren Monitoren mit unterschiedlicher Auflösung (z.B. 1080p und 4K).
    """
    try:
        user32 = ctypes.windll.user32
        point = _Point()
        user32.GetCursorPos(ctypes.byref(point))
        user32.MonitorFromPoint.restype = ctypes.c_void_p
        monitor = user32.MonitorFromPoint(point, 2)  # MONITOR_DEFAULTTONEAREST
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        user32.GetMonitorInfoW(ctypes.c_void_p(monitor), ctypes.byref(info))
        dpi_x, dpi_y = ctypes.c_uint(), ctypes.c_uint()
        ctypes.windll.shcore.GetDpiForMonitor(ctypes.c_void_p(monitor), 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
        r = info.rcWork
        return (r.left, r.top, r.right, r.bottom), (dpi_x.value + dpi_y.value) / (2 * 96)
    except (AttributeError, OSError):
        return (0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight() - 48), \
            ctk.ScalingTracker.get_window_scaling(widget)


def sync_scaling(window, scale):
    """Stellt die CTk-Skalierung eines Fensters sofort auf `scale` um (statt auf CTks 100-ms-Prüfung zu warten).

    Muss aufgerufen werden, NACHDEM das Fenster auf den Ziel-Monitor verschoben wurde – sonst setzt
    CTks eigene Prüfung den Wert gleich wieder zurück.
    """
    tracker = ctk.ScalingTracker
    if window in tracker.window_dpi_scaling_dict and tracker.window_dpi_scaling_dict[window] != scale:
        tracker.window_dpi_scaling_dict[window] = scale
        tracker.update_scaling_callbacks_for_window(window)


# ---------------------------------------------------------------- Scrollen
_WHEEL_TARGETS = weakref.WeakValueDictionary()   # Canvas-Pfad → Objekt mit _on_wheel()


def _wheel_dispatch(event):
    """Einziger globaler Mausrad-Handler: gibt das Ereignis an die scrollbare Fläche unter dem Zeiger."""
    widget = event.widget
    while widget is not None and not isinstance(widget, str):
        target = _WHEEL_TARGETS.get(str(widget))
        if target is not None:
            target._on_wheel(event)
            return
        widget = getattr(widget, 'master', None)


class SmoothWheel:
    """Weiches Mausrad-Scrollen für ein Canvas (yscrollincrement=1 → 1 unit = 1 px).

    Nutzer setzen self._wheel_canvas und rufen _register_wheel() auf.
    """

    STEP = 70          # Pixel pro Mausrad-Raste (bei 100 % Skalierung)

    def _register_wheel(self, canvas):
        self._wheel_canvas = canvas
        self._pending = 0.0
        self._animating = False
        canvas.configure(yscrollincrement=1)
        _WHEEL_TARGETS[str(canvas)] = self
        # ohne add → ersetzt alle bisherigen Handler (auch die von CTk angelegten)
        canvas.bind_all('<MouseWheel>', _wheel_dispatch)

    def _on_wheel(self, event):
        canvas = self._wheel_canvas
        if not canvas.winfo_exists() or canvas.yview() == (0.0, 1.0):
            return
        scale = ctk.ScalingTracker.get_widget_scaling(canvas)
        self._pending += -event.delta / 120 * self.STEP * scale
        if not self._animating:
            self._animating = True
            self._animate()

    def _animate(self):
        """Gleitet den Rest in kleinen Schritten ab (weicher als ein harter Sprung)."""
        canvas = self._wheel_canvas
        if not canvas.winfo_exists() or abs(self._pending) < 1:
            self._pending = 0.0
            self._animating = False
            return
        step = int(self._pending * 0.35) or (1 if self._pending > 0 else -1)
        canvas.yview_scroll(step, 'units')
        self._pending -= step
        canvas.after(10, self._animate)


class ScrollFrame(SmoothWheel, ctk.CTkScrollableFrame):
    """CTkScrollableFrame mit leichterem, weichem Mausrad-Scrollen.

    CTk scrollt unter Windows nur 20 px pro Raste und meldet jede Instanz per bind_all an,
    ohne sie beim Zerstören wieder abzumelden. Hier gibt es stattdessen EINEN globalen Handler.
    """

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        if sys.platform.startswith('win'):
            for seq in ('<KeyPress-Shift_L>', '<KeyPress-Shift_R>',
                        '<KeyRelease-Shift_L>', '<KeyRelease-Shift_R>'):
                self.unbind_all(seq)
            self._register_wheel(self._parent_canvas)


# ---------------------------------------------------------------- Cover-Bilder
class CoverImages:
    """Erzeugt abgerundete Cover-Bilder aus dem Cover-Cache; Platzhalter, solange nichts da ist.

    get() liefert CTkImages (für CTk-Widgets), get_tk() PhotoImages in echten Pixeln (für die
    gezeichnete Spieleliste). Muss im UI-Thread benutzt werden. Fehlende Cover werden asynchron
    geladen und über on_ready gemeldet (vom App-Objekt in den UI-Thread geleitet).
    """

    def __init__(self, cache, call_in_ui):
        self.cache = cache
        self.call_in_ui = call_in_ui
        self._images = {}
        self._waiting = {}

    def _open(self, image_id, on_ready):
        """PIL-Bild des Covers oder None (dann wird es im Hintergrund geladen)."""
        path = self.cache.cached(image_id)
        if path:
            try:
                return Image.open(path)
            except Exception as e:
                log.warning("Cover %s defekt: %s", image_id, e)
        if image_id and on_ready:
            waiting = self._waiting.setdefault(image_id, [])
            waiting.append(on_ready)
            del waiting[:-20]  # alte Callbacks (zerstörte Widgets) nicht ewig sammeln
            self.cache.fetch(image_id, lambda _p, i=image_id: self.call_in_ui(self._loaded, i))
        return None

    def get(self, image_id, size, name='', on_ready=None):
        key = (image_id, size)
        if image_id and key in self._images:
            return self._images[key]
        pil = self._open(image_id, on_ready)
        if pil is None:
            return self.placeholder(name, size)
        img = self._make(pil, size)
        self._images[key] = img
        return img

    def get_tk(self, image_id, size_px, name='', on_ready=None):
        """ImageTk.PhotoImage in genau size_px (echte Pixel), mit transparenten runden Ecken."""
        key = ('tk', image_id, size_px)
        if image_id and key in self._images:
            return self._images[key]
        pil = self._open(image_id, on_ready)
        is_placeholder = pil is None
        if is_placeholder:
            letter = name[:1].upper() if name else '?'
            key = ('tk', '__ph__' + letter, size_px)
            if key in self._images:
                return self._images[key]
            pil = self._placeholder_pil(letter, size_px)
        radius = max(4, size_px[0] // 6)
        rounded = self._rounded(pil, size_px, radius).resize(size_px, Image.LANCZOS)
        img = ImageTk.PhotoImage(rounded)
        self._images[key] = img
        return img

    def _loaded(self, image_id):
        for callback in self._waiting.pop(image_id, []):
            try:
                callback()
            except Exception:
                pass  # Widget wurde inzwischen zerstört

    @staticmethod
    def _rounded(img, size, radius=8):
        scale = 2  # für glatte Kanten in doppelter Auflösung zeichnen
        w, h = size[0] * scale, size[1] * scale
        img = img.convert('RGB').resize((w, h), Image.LANCZOS)
        mask = Image.new('L', (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius * scale, fill=255)
        out = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        out.paste(img, (0, 0), mask)
        return out

    def _make(self, pil_img, size):
        return ctk.CTkImage(dark_image=self._rounded(pil_img, size), size=size)

    @staticmethod
    def _placeholder_pil(letter, size):
        w, h = size[0] * 2, size[1] * 2
        img = Image.new('RGB', (w, h), theme.CARD_HOVER)
        draw = ImageDraw.Draw(img)
        for y in range(h):  # dezenter Verlauf
            shade = int(43 + 18 * y / h)
            draw.line([(0, y), (w, y)], fill=(shade, shade + 6, shade + 30))
        try:
            fnt = ImageFont.truetype('segoeuib.ttf', int(w * 0.45))
        except OSError:
            fnt = ImageFont.load_default()
        draw.text((w / 2, h / 2), letter, font=fnt, fill=(140, 150, 185), anchor='mm')
        return img

    def placeholder(self, name, size):
        letter = name[:1].upper() if name else '?'
        key = ('__ph__' + letter, size)
        if key not in self._images:
            self._images[key] = self._make(self._placeholder_pil(letter, size), size)
        return self._images[key]


# ---------------------------------------------------------------- Eingabefelder
def add_context_menu(entry):
    """Rechtsklick-Menü (Ausschneiden/Kopieren/Einfügen/Alles auswählen) für ein CTkEntry."""
    inner = getattr(entry, '_entry', entry)   # CTkEntry kapselt ein tk.Entry
    menu = tkinter.Menu(inner, tearoff=0, bg=theme.SURFACE, fg=theme.TEXT, activebackground=theme.ACCENT,
                        activeforeground='white', bd=0)
    menu.add_command(label="Ausschneiden", command=lambda: inner.event_generate('<<Cut>>'))
    menu.add_command(label="Kopieren", command=lambda: inner.event_generate('<<Copy>>'))
    def paste():
        try:
            text = inner.clipboard_get().strip()   # Zeilenumbrüche aus kopierten Namen weg
        except tkinter.TclError:
            return
        if hasattr(entry, '_deactivate_placeholder'):
            entry._deactivate_placeholder()       # grauen Platzhaltertext erst entfernen
        if inner.selection_present():
            inner.delete('sel.first', 'sel.last')
        inner.insert('insert', text)
        inner.focus_set()

    menu.add_command(label="Einfügen", command=paste)
    menu.add_separator()
    menu.add_command(label="Alles auswählen", command=lambda: (inner.focus_set(), inner.select_range(0, 'end'),
                                                               inner.icursor('end')))

    def show(event):
        inner.focus_set()
        # Pixelgröße passend zur aktuellen Skalierung (Punktgrößen wachsen auf 4K nicht mit)
        scale = ctk.ScalingTracker.get_widget_scaling(entry)
        menu.configure(font=(theme.FONT_FAMILY, -round(14 * scale)))
        has_selection = bool(inner.selection_present())
        try:
            has_clipboard = bool(inner.clipboard_get())
        except tkinter.TclError:
            has_clipboard = False
        # Nicht verfügbare Einträge nur grau färben statt sperren – gesperrte Einträge zeichnet Windows
        # mit einem Prägeeffekt; ohne Markierung/Zwischenablage tun die Befehle ohnehin nichts.
        for label, available in (("Ausschneiden", has_selection), ("Kopieren", has_selection),
                                 ("Einfügen", has_clipboard)):
            menu.entryconfigure(label, foreground=theme.TEXT if available else theme.SUBTLE,
                                activebackground=theme.ACCENT if available else theme.SURFACE)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    inner.bind('<Button-3>', show)
    return entry


# ---------------------------------------------------------------- Kleinteile
def countdown_info(game):
    """(Text, (bg, fg), Kontur) für die Countdown-Pill.

    Farben immer passend zur Karte (Hype gold, sonst blau); die Kontur zeigt die Dringlichkeit:
    heute grün, bis 7 Tage orange, sonst keine (None).
    """
    colors = theme.PILL_HYPE if game.get('hyped') else theme.PILL_STANDARD
    if game.get('manual_tba'):
        last = game.get('last_known_date')
        return (f"Platzhalter ignoriert: {last}" if last else "Datum unbekannt"), colors, None
    days = days_until(game.get('release_date')) if game.get('release_date') else None
    if days is None:
        return "Datum unbekannt", colors, None
    date = game['release_date']
    if days < 0:
        return f"Erschienen vor {-days} {'Tag' if days == -1 else 'Tagen'} · {date}", colors, None
    if days == 0:
        return f"🎉 Heute! · {date}", colors, theme.OUTLINE_TODAY
    if days <= theme.SOON_DAYS:
        return f"🔥 Noch {days} {'Tag' if days == 1 else 'Tage'} · {date}", colors, theme.OUTLINE_SOON
    return f"Noch {days} Tage ({format_weeks(days)}) · {date}", colors, None


class Pill(ctk.CTkFrame):
    """Kleiner abgerundeter Kasten mit Text; optional farbige Kontur."""

    def __init__(self, master, text, colors, font=theme.SMALL_BOLD, outline=None, **kw):
        bg, fg = colors
        super().__init__(master, fg_color=bg, corner_radius=7, border_width=2 if outline else 0,
                         border_color=outline or bg, **kw)
        ctk.CTkLabel(self, text=text, fg_color='transparent', text_color=fg, font=font, height=18 if outline else 22,
                     padx=6).pack(padx=2 if outline else 2, pady=2 if outline else 0)


def chip_row(master, platforms_str, bg):
    """Frame mit kleinen Plattform-Chips – None, wenn keine Plattformen bekannt sind.

    (Ein leerer CTkFrame wäre sonst 200×200 px groß.)
    """
    platforms = platform_list(platforms_str)
    if not platforms:
        return None
    row = ctk.CTkFrame(master, fg_color='transparent')
    for p in platforms:
        is_ps = 'playstation' in p.lower()
        ctk.CTkLabel(row, text=short_platform(p), font=theme.TINY, height=18, corner_radius=5, padx=6,
                     fg_color='#1d3557' if is_ps else theme.BORDER,
                     text_color='#9cc7ff' if is_ps else theme.MUTED).pack(side='left', padx=(0, 4))
    return row


def _is_descendant(widget, ancestor):
    while widget is not None:
        if widget == ancestor:
            return True
        widget = getattr(widget, 'master', None)
    return False


def bind_hover_click(container, on_enter, on_leave, on_click, exclude=()):
    """Hover/Klick auf einen Container samt allen Kindern (ohne Flackern beim Wechsel zwischen Kindern).

    Bindet bewusst über tkinter.Misc.bind an jedes einzelne (auch CTk-interne) Widget,
    damit Ereignisse nicht doppelt ausgelöst werden.
    """
    def enter(_e):
        on_enter()

    def leave(_e):
        x, y = container.winfo_pointerxy()
        under = container.winfo_containing(x, y)
        if not _is_descendant(under, container):
            on_leave()

    def bind_all(w):
        if w in exclude:
            return
        tkinter.Misc.bind(w, '<Enter>', enter, '+')
        tkinter.Misc.bind(w, '<Leave>', leave, '+')
        if on_click:
            tkinter.Misc.bind(w, '<Button-1>', lambda e: on_click(), '+')
        for child in w.winfo_children():
            bind_all(child)

    bind_all(container)


# ---------------------------------------------------------------- Fenster-Basis
class BaseWindow(ctk.CTkToplevel):
    """Zentriertes Fenster mit App-Icon und ESC zum Schließen.

    Wird erst sichtbar, wenn der Inhalt fertig aufgebaut ist (kein weißes Aufblitzen):
    CTkToplevel zeigt neue Fenster sonst schon nach 5 ms an. maximized=True öffnet maximiert.
    """

    def __init__(self, app, title_text, width, height, on_close=None, resizable=True, maximized=False):
        super().__init__(app.root, fg_color=theme.BG)
        self.withdraw()   # CTk merkt sich das und zeigt das Fenster nach dem Titelbalken-Trick nicht wieder an
        self.app = app
        self._on_close = on_close
        self._maximized = maximized
        self.title(title_text)
        self.resizable(resizable, resizable)
        icon = paths.resource('icon.ico')
        if icon:
            self.iconbitmap(icon)   # sofort setzen → CTk setzt kein eigenes Icon mehr
        self._center(width, height)
        self.minsize(min(width, 420), min(height, 300))
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda e: self.close())
        self.after_idle(self._reveal)   # läuft erst, wenn die Unterklasse ihren Inhalt gebaut hat

    def _center(self, width, height):
        """Mittig auf dem Monitor unter dem Mauszeiger. CTk skaliert bei geometry() auch die Position –
        daher Größe über CTk, Position unskaliert in echten Pixeln.

        Läuft, bevor die Unterklasse ihre Widgets baut: Fenster zuerst auf den Ziel-Monitor schieben und
        dessen Skalierung übernehmen, damit alle Widgets gleich in der richtigen Größe entstehen.
        """
        (left, top, right, bottom), scale = monitor_at_cursor(self)
        tkinter.Wm.wm_geometry(self, f"+{left + 20}+{top + 20}")
        sync_scaling(self, scale)
        height = min(height, int((bottom - top) / scale) - 60)
        self.geometry(f"{width}x{height}")
        x = left + (right - left - int(width * scale)) // 2
        y = top + max(10, (bottom - top - int(height * scale)) // 2)
        tkinter.Wm.wm_geometry(self, f"+{x}+{y}")

    def _reveal(self):
        if not self.winfo_exists():
            return
        self.update_idletasks()
        if self._maximized:
            self.state('zoomed')
        else:
            self.deiconify()
        self.lift()
        self.focus_force()

    def close(self):
        callback, self._on_close = self._on_close, None
        self.destroy()
        if callback:
            callback()

    def header(self, text, subtitle=None):
        bar = ctk.CTkFrame(self, fg_color=theme.SURFACE, corner_radius=0)
        bar.pack(fill='x')
        ctk.CTkLabel(bar, text=text, font=theme.H1, text_color=theme.TEXT).pack(anchor='w', padx=20, pady=(14, 0 if subtitle is not None else 14))
        self.subtitle_label = ctk.CTkLabel(bar, text=subtitle or '', font=theme.SMALL, text_color=theme.MUTED)
        if subtitle is not None:
            self.subtitle_label.pack(anchor='w', padx=20, pady=(0, 12))
        return bar


def empty_state(master, icon, title_text, text):
    frame = ctk.CTkFrame(master, fg_color='transparent')
    frame.pack(expand=True, fill='both', pady=50)
    ctk.CTkLabel(frame, text=icon, font=(theme.EMOJI_FAMILY, 40)).pack()
    ctk.CTkLabel(frame, text=title_text, font=theme.H2, text_color=theme.MUTED).pack(pady=(8, 2))
    ctk.CTkLabel(frame, text=text, font=theme.SMALL, text_color=theme.SUBTLE, justify='center').pack()
    return frame


# ---------------------------------------------------------------- Dialoge
class ConfirmDialog(BaseWindow):
    def __init__(self, app, title_text, message, confirm_text="Löschen", on_confirm=None, danger=True):
        super().__init__(app, title_text, 420, 210, resizable=False)
        self.on_confirm = on_confirm
        self.attributes('-topmost', True)
        body = ctk.CTkFrame(self, fg_color='transparent')
        body.pack(fill='both', expand=True, padx=24, pady=20)
        ctk.CTkLabel(body, text=title_text, font=theme.H2, text_color=theme.TEXT, anchor='w').pack(fill='x')
        ctk.CTkLabel(body, text=message, font=theme.BODY, text_color=theme.MUTED, anchor='w',
                     justify='left', wraplength=370).pack(fill='x', pady=(6, 0))
        buttons = ctk.CTkFrame(body, fg_color='transparent')
        buttons.pack(side='bottom', fill='x')
        ctk.CTkButton(buttons, text=confirm_text, command=self._confirm,
                      **theme.button_style('danger' if danger else 'primary')).pack(side='right')
        ctk.CTkButton(buttons, text="Abbrechen", command=self.close, width=110,
                      **theme.button_style('ghost')).pack(side='right', padx=(0, 8))
        self.bind('<Return>', lambda e: self._confirm())

    def _reveal(self):
        super()._reveal()
        if self.winfo_exists():
            self.grab_set()   # modal – geht erst, wenn das Fenster sichtbar ist

    def _confirm(self):
        callback = self.on_confirm
        self.close()
        if callback:
            callback()


class MessagePopup(BaseWindow):
    """Hinweis-Popup mit Liste von Meldungen (ersetzt die alten tk.Tk()-Popups)."""

    def __init__(self, app, title_text, messages, icon="🔔"):
        messages = [messages] if isinstance(messages, str) else list(messages)
        super().__init__(app, "Game Release Tracker", 500, min(220 + len(messages) * 64, 640))
        self.attributes('-topmost', True)
        ctk.CTkLabel(self, text=icon, font=(theme.EMOJI_FAMILY, 34)).pack(pady=(18, 0))
        ctk.CTkLabel(self, text=title_text, font=theme.H2, text_color=theme.TEXT).pack(pady=(4, 10))
        ctk.CTkButton(self, text="OK", command=self.close, width=140,
                      **theme.button_style('primary')).pack(side='bottom', pady=(8, 18))
        lst = ScrollFrame(self, fg_color='transparent')
        lst.pack(fill='both', expand=True, padx=20)
        for msg in messages:
            card = ctk.CTkFrame(lst, fg_color=theme.CARD, corner_radius=8)
            card.pack(fill='x', pady=3)
            ctk.CTkLabel(card, text=msg, font=theme.BODY, text_color=theme.TEXT, anchor='w', justify='left',
                         wraplength=410).pack(fill='x', padx=12, pady=9)
        self.bind('<Return>', lambda e: self.close())
        self.bell()
