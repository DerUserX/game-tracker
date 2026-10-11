"""Wiederverwendbare UI-Bausteine."""
import logging
import sys
import tkinter
import weakref
import webbrowser

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont

from .. import paths
from ..models import days_until, format_weeks, platform_list, short_platform, title
from . import theme

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- Scrollen
class ScrollFrame(ctk.CTkScrollableFrame):
    """CTkScrollableFrame mit leichterem, weichem Mausrad-Scrollen.

    CTk scrollt unter Windows nur 20 px pro Raste und meldet jede Instanz per bind_all an,
    ohne sie beim Zerstören wieder abzumelden – bei oft neu gebauten Listen laufen dann bei jeder
    Raste die Handler aller alten Listen mit. Hier gibt es stattdessen EINEN globalen Handler,
    der das Ereignis an die Liste unter dem Mauszeiger weitergibt.
    """

    STEP = 70          # Pixel pro Mausrad-Raste (bei 100 % Skalierung)
    _frames = weakref.WeakValueDictionary()   # Canvas-Pfad → ScrollFrame

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self._pending = 0.0
        self._animating = False
        if sys.platform.startswith('win'):
            # CTk-eigene globale Bindings ersetzen (sie gelten für alle Instanzen)
            for seq in ('<KeyPress-Shift_L>', '<KeyPress-Shift_R>',
                        '<KeyRelease-Shift_L>', '<KeyRelease-Shift_R>'):
                self.unbind_all(seq)
            ScrollFrame._frames[str(self._parent_canvas)] = self
            # ohne add → ersetzt alle bisherigen Handler (auch den gerade von CTk angelegten)
            self.bind_all('<MouseWheel>', ScrollFrame._dispatch)

    @classmethod
    def _dispatch(cls, event):
        widget = event.widget
        while widget is not None and not isinstance(widget, str):
            frame = cls._frames.get(str(widget))
            if frame is not None:
                frame._on_wheel(event)
                return
            widget = getattr(widget, 'master', None)

    def _on_wheel(self, event):
        if not self.winfo_exists() or self._parent_canvas.yview() == (0.0, 1.0):
            return
        scale = ctk.ScalingTracker.get_widget_scaling(self)
        self._pending += -event.delta / 120 * self.STEP * scale
        if not self._animating:
            self._animating = True
            self._animate()

    def _animate(self):
        """Gleitet den Rest in kleinen Schritten ab (weicher als ein harter Sprung)."""
        if not self.winfo_exists() or abs(self._pending) < 1:
            self._pending = 0.0
            self._animating = False
            return
        step = int(self._pending * 0.35) or (1 if self._pending > 0 else -1)
        self._parent_canvas.yview_scroll(step, 'units')   # 1 unit = 1 px (CTk-Einstellung unter Windows)
        self._pending -= step
        self.after(10, self._animate)


# ---------------------------------------------------------------- Cover-Bilder
class CoverImages:
    """Erzeugt CTkImages (abgerundet) aus dem Cover-Cache; Platzhalter, solange nichts da ist.

    Muss im UI-Thread benutzt werden. Fehlende Cover werden asynchron geladen und
    über `on_loaded(image_id)` gemeldet (vom App-Objekt in den UI-Thread geleitet).
    """

    def __init__(self, cache, call_in_ui):
        self.cache = cache
        self.call_in_ui = call_in_ui
        self._images = {}
        self._waiting = {}

    def get(self, image_id, size, name='', on_ready=None):
        key = (image_id, size)
        if image_id and key in self._images:
            return self._images[key]

        path = self.cache.cached(image_id)
        if path:
            try:
                img = self._make(Image.open(path), size)
                self._images[key] = img
                return img
            except Exception as e:
                log.warning("Cover %s defekt: %s", image_id, e)

        if image_id and on_ready:
            waiting = self._waiting.setdefault(image_id, [])
            waiting.append(on_ready)
            del waiting[:-20]  # alte Callbacks (zerstörte Widgets) nicht ewig sammeln
            self.cache.fetch(image_id, lambda _p, i=image_id: self.call_in_ui(self._loaded, i))
        return self.placeholder(name, size)

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

    def placeholder(self, name, size):
        key = ('__ph__' + (name[:1].upper() if name else '?'), size)
        if key in self._images:
            return self._images[key]
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
        letter = key[0][-1]
        draw.text((w / 2, h / 2), letter, font=fnt, fill=(140, 150, 185), anchor='mm')
        result = self._make(img, size)
        self._images[key] = result
        return result


# ---------------------------------------------------------------- Kleinteile
def countdown_info(game):
    """(Text, (bg, fg)) für die Countdown-Pill."""
    if game.get('manual_tba'):
        last = game.get('last_known_date')
        return (f"Platzhalter ignoriert: {last}" if last else "Datum unbekannt"), theme.PILL_NONE
    days = days_until(game.get('release_date')) if game.get('release_date') else None
    if days is None:
        return "Datum unbekannt", theme.PILL_NONE
    date = game['release_date']
    if days < 0:
        return f"Erschienen vor {-days} {'Tag' if days == -1 else 'Tagen'} · {date}", theme.PILL_PAST
    if days == 0:
        return f"🎉 Heute! · {date}", theme.PILL_TODAY
    if days < 7:
        return f"🔥 Noch {days} {'Tag' if days == 1 else 'Tage'} · {date}", theme.PILL_WEEK
    if days < 30:
        return f"Noch {days} Tage ({format_weeks(days)}) · {date}", theme.PILL_MONTH
    return f"Noch {days} Tage ({format_weeks(days)}) · {date}", theme.PILL_LATER


class Pill(ctk.CTkLabel):
    def __init__(self, master, text, colors, font=theme.SMALL_BOLD, **kw):
        bg, fg = colors
        super().__init__(master, text=text, fg_color=bg, text_color=fg, corner_radius=6,
                         font=font, height=22, padx=8, **kw)


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


class GapSeparator(ctk.CTkFrame):
    def __init__(self, master, days):
        super().__init__(master, fg_color='transparent', height=18)
        ctk.CTkFrame(self, fg_color=theme.BORDER, height=1).pack(side='left', fill='x', expand=True, padx=(8, 6))
        ctk.CTkLabel(self, text=f"{days} {'Tag' if days == 1 else 'Tage'}", font=theme.TINY,
                     text_color=theme.SUBTLE, height=16).pack(side='left')
        ctk.CTkFrame(self, fg_color=theme.BORDER, height=1).pack(side='left', fill='x', expand=True, padx=(6, 8))


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


# ---------------------------------------------------------------- Spielkarte
class GameCard(ctk.CTkFrame):
    COVER_SIZE = (46, 62)

    def __init__(self, master, game, covers, on_click, on_link=None):
        hyped = game.get('hyped')
        self.bg = theme.HYPE_BG if hyped else theme.CARD
        self.bg_hover = theme.HYPE_HOVER if hyped else theme.CARD_HOVER
        super().__init__(master, fg_color=self.bg, corner_radius=theme.RADIUS,
                         border_width=2 if hyped else 0, border_color=theme.HYPE_BORDER, cursor='hand2')
        self.game = game

        self.grid_columnconfigure(1, weight=1)
        name = title(game)

        self.cover = ctk.CTkLabel(self, text='', image=covers.get(game.get('cover_id'), self.COVER_SIZE, name,
                                                                  on_ready=self._reload_cover))
        self.cover.grid(row=0, column=0, rowspan=3, padx=(10, 10), pady=10, sticky='n')
        self._covers = covers

        # Zeile 1: Name + Badges
        top = ctk.CTkFrame(self, fg_color='transparent')
        top.grid(row=0, column=1, sticky='ew', pady=(9, 0), padx=(0, 10))
        ctk.CTkLabel(top, text=('★ ' if hyped else '') + name, font=theme.font(13, 'bold'), anchor='w',
                     text_color=theme.GOLD if hyped else theme.TEXT, wraplength=300, justify='left'
                     ).pack(side='left', fill='x', expand=True)
        trophy_btn = None
        if game.get('psn_trophy_count') and game.get('exophase_url'):
            # Klick öffnet die Trophäenliste auf Exophase (statt den Bearbeiten-Dialog)
            trophy_btn = ctk.CTkButton(top, text=f"🏆 {game['psn_trophy_count']}", width=46, height=22,
                                       corner_radius=6, font=theme.font(10, 'bold'), fg_color='#1b2f4d',
                                       hover_color='#27446e', text_color=theme.TROPHY,
                                       command=lambda: self._open_link(game['exophase_url'], on_link))
            trophy_btn.pack(side='right', padx=(4, 0))
        elif game.get('psn_trophy_count'):
            Pill(top, f"🏆 {game['psn_trophy_count']}", ('#1b2f4d', theme.TROPHY), font=theme.TINY).pack(side='right', padx=(4, 0))
        if game.get('manual_tba'):
            Pill(top, "TBA", (theme.BORDER, theme.MUTED), font=theme.TINY).pack(side='right', padx=(4, 0))

        # Zeile 2: Plattformen
        chips = chip_row(self, game.get('platforms'), self.bg)
        if chips:
            chips.grid(row=1, column=1, sticky='w', pady=(3, 0))

        # Zeile 3: Countdown
        text, colors = countdown_info(game)
        Pill(self, text, colors).grid(row=2, column=1, sticky='w', pady=(5, 10))

        bind_hover_click(self, lambda: self.configure(fg_color=self.bg_hover),
                         lambda: self.configure(fg_color=self.bg), lambda: on_click(game['uid']),
                         exclude=(trophy_btn,) if trophy_btn else ())

    @staticmethod
    def _open_link(url, on_link):
        webbrowser.open(url)
        if on_link:
            on_link()   # z.B. Flyout schließen

    def _reload_cover(self):
        if self.winfo_exists():
            self.cover.configure(image=self._covers.get(self.game.get('cover_id'), self.COVER_SIZE, title(self.game)))


# ---------------------------------------------------------------- Fenster-Basis
class BaseWindow(ctk.CTkToplevel):
    """Zentriertes Fenster mit App-Icon und ESC zum Schließen."""

    def __init__(self, app, title_text, width, height, on_close=None, resizable=True):
        super().__init__(app.root, fg_color=theme.BG)
        self.app = app
        self._on_close = on_close
        self.title(title_text)
        self.resizable(resizable, resizable)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        height = min(height, sh - 80)
        self.geometry(f"{width}x{height}+{(sw - width) // 2}+{max(20, (sh - height) // 2 - 20)}")
        self.minsize(min(width, 420), min(height, 300))
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda e: self.close())
        icon = paths.resource('icon.ico')
        if icon:
            # CTkToplevel setzt sein eigenes Icon verzögert – danach überschreiben
            self.after(250, lambda: self.winfo_exists() and self.iconbitmap(icon))
        self.after(50, self._bring_to_front)

    def _bring_to_front(self):
        if self.winfo_exists():
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
        self.after(100, lambda: self.winfo_exists() and self.grab_set())

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
