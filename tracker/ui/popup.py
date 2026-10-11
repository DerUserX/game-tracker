"""Tray-Flyout: die Spieleliste unten rechts über der Taskleiste.

Wird einmal gebaut und danach nur ein-/ausgeblendet; die Kartenliste wird nur neu
aufgebaut, wenn sich die Spieldaten (oder das Tagesdatum) geändert haben.
"""
import json
import time
from datetime import date, datetime

import customtkinter as ctk

from ..models import days_between, is_tba, sort_key_dated, sort_key_tba
from . import theme
from .widgets import GameCard, GapSeparator, empty_state

WIDTH, HEIGHT = 480, 620


class Popup(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__(app.root, fg_color=theme.BORDER)
        self.app = app
        self.page = 0
        self.withdraw()
        self.overrideredirect(True)
        self.attributes('-topmost', True)
        self._position()

        # 1px-Rahmen über den äußeren Hintergrund
        self.shell = ctk.CTkFrame(self, fg_color=theme.BG, corner_radius=0)
        self.shell.pack(fill='both', expand=True, padx=1, pady=1)

        self._build_header()
        self._build_tabs()
        self._build_footer()
        self.body = ctk.CTkFrame(self.shell, fg_color='transparent')
        self.body.pack(fill='both', expand=True)
        self.lists = [None, None]
        self._data = ([], [])
        self._signature = None
        self.closed_at = 0.0
        self.render()

        self.bind('<Escape>', lambda e: self.close())
        self.bind('<FocusOut>', lambda e: self.after(200, self._check_focus))
        self.bind('<FocusIn>', lambda e: setattr(self, '_had_focus', True))
        self._had_focus = False

    def _position(self):
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{WIDTH}x{HEIGHT}+{sw - WIDTH - 12}+{sh - HEIGHT - 56}")

    @property
    def is_open(self):
        return self.winfo_exists() and self.state() == 'normal'

    def show(self):
        self.render()
        self._position()
        self._had_focus = False
        self.deiconify()
        self.after(30, self._grab_focus)

    # ------------------------------------------------------------- Aufbau
    def _build_header(self):
        head = ctk.CTkFrame(self.shell, fg_color=theme.SURFACE, corner_radius=0)
        head.pack(fill='x')
        left = ctk.CTkFrame(head, fg_color='transparent')
        left.pack(side='left', padx=16, pady=12)
        ctk.CTkLabel(left, text="Meine Hype Games", font=theme.H1, text_color=theme.TEXT).pack(anchor='w')
        self.subtitle = ctk.CTkLabel(left, text="", font=theme.SMALL, text_color=theme.MUTED, height=16)
        self.subtitle.pack(anchor='w')

        btns = ctk.CTkFrame(head, fg_color='transparent')
        btns.pack(side='right', padx=12)

        def icon_btn(text, command, kind='subtle', tip=None):
            b = ctk.CTkButton(btns, text=text, command=command, width=36, height=36, corner_radius=9,
                              font=(theme.EMOJI_FAMILY, 15), **{k: v for k, v in theme.button_style(kind).items()
                                                                 if k in ('fg_color', 'hover_color', 'text_color')})
            b.pack(side='left', padx=3)
            return b

        self.add_btn = icon_btn("＋", lambda: self.app.open_add_dialog(from_popup=True), 'success')
        self.refresh_btn = icon_btn("⟳", self._refresh)
        bell_wrap = ctk.CTkFrame(btns, fg_color='transparent')
        bell_wrap.pack(side='left')
        self.bell_btn = ctk.CTkButton(bell_wrap, text="🔔", width=36, height=36, corner_radius=9,
                                      font=(theme.EMOJI_FAMILY, 14), fg_color=theme.CARD, hover_color=theme.CARD_HOVER,
                                      command=lambda: self.app.open_notifications(from_popup=True))
        self.bell_btn.pack(padx=3)
        self.badge = ctk.CTkLabel(bell_wrap, text="", font=theme.font(9, 'bold'), fg_color=theme.DANGER,
                                  text_color='white', corner_radius=8, width=18, height=16)
        close = ctk.CTkButton(btns, text="✕", width=30, height=30, corner_radius=8, font=theme.BODY_BOLD,
                              fg_color='transparent', hover_color=theme.DANGER_DARK, text_color=theme.MUTED,
                              command=self.close)
        close.pack(side='left', padx=(6, 0))

    def _build_tabs(self):
        bar = ctk.CTkFrame(self.shell, fg_color=theme.SURFACE, corner_radius=0)
        bar.pack(fill='x')
        self.tabs = ctk.CTkSegmentedButton(bar, values=["a", "b"], command=self._on_tab, height=32,
                                           font=theme.SMALL_BOLD, corner_radius=8,
                                           fg_color=theme.BG, unselected_color=theme.BG,
                                           unselected_hover_color=theme.CARD, selected_color=theme.ACCENT,
                                           selected_hover_color=theme.ACCENT_HOVER, text_color=theme.TEXT)
        self.tabs.pack(fill='x', padx=14, pady=(0, 10))

    def _build_footer(self):
        foot = ctk.CTkFrame(self.shell, fg_color=theme.SURFACE, corner_radius=0, height=34)
        foot.pack(fill='x', side='bottom')
        self.footer_label = ctk.CTkLabel(foot, text="", font=theme.TINY, text_color=theme.MUTED)
        self.footer_label.pack(side='left', padx=14, pady=6)
        self.waitlist_link = ctk.CTkLabel(foot, text="", font=theme.font(10, 'bold'), text_color=theme.ACCENT,
                                          cursor='hand2')
        self.waitlist_link.bind('<Button-1>', lambda e: self.app.open_waitlist(from_popup=True))

    # ------------------------------------------------------------- Inhalt
    def render(self):
        store = self.app.store
        games = store.snapshot()
        dated = sorted((g for g in games if not is_tba(g)), key=sort_key_dated)
        tba = sorted((g for g in games if is_tba(g)), key=sort_key_tba)

        self.subtitle.configure(text=f"{len(games)} Spiele · {sum(1 for g in games if g.get('hyped'))} gehypt")
        labels = [f"📅  Mit Datum ({len(dated)})", f"📭  Ohne Datum ({len(tba)})"]
        self.tabs.configure(values=labels)
        self.tabs.set(labels[self.page])

        unread = store.unread_count()
        if unread:
            self.badge.configure(text=str(unread))
            self.badge.place(relx=1.0, x=-2, y=0, anchor='ne')
            self.bell_btn.configure(fg_color=theme.WARNING, hover_color='#d68c22')
        else:
            self.badge.place_forget()
            self.bell_btn.configure(fg_color=theme.CARD, hover_color=theme.CARD_HOVER)

        last = store.config.get('last_update', '')
        try:
            text = f"Aktualisiert {datetime.fromisoformat(last).strftime('%d.%m. %H:%M')}"
        except ValueError:
            text = "Klick auf ein Spiel zum Bearbeiten"
        if self.app.updater.running:
            text = "Aktualisiere…"
        elif self.app.updater.last_error:
            text = "⚠️ Update fehlgeschlagen – offline?"
        self.footer_label.configure(text=text)
        if store.waitlist:
            self.waitlist_link.configure(text=f"📋 {len(store.waitlist)} auf Warteliste")
            self.waitlist_link.pack(side='right', padx=14)
        else:
            self.waitlist_link.pack_forget()
        self.refresh_btn.configure(state='disabled' if self.app.updater.running else 'normal')

        # Kartenliste nur bei echten Datenänderungen neu bauen
        signature = json.dumps(games, sort_keys=True) + date.today().isoformat()
        if signature != self._signature:
            self._signature = signature
            self._data = (dated, tba)
            for i, lst in enumerate(self.lists):
                if lst is not None:
                    lst.destroy()
                    self.lists[i] = None
            self._show_page()

    def _show_page(self):
        """Zeigt die aktive Seite; baut sie beim ersten Bedarf."""
        if self.lists[self.page] is None:
            dated, tba = self._data
            self.lists[self.page] = (self._build_list(dated, with_gaps=True, empty="Keine Spiele mit Datum")
                                     if self.page == 0 else
                                     self._build_list(tba, with_gaps=False, empty="Keine Spiele ohne Datum"))
        self.lists[self.page].pack(fill='both', expand=True)

    def _build_list(self, games, with_gaps, empty):
        lst = ctk.CTkScrollableFrame(self.body, fg_color='transparent', scrollbar_button_color=theme.BORDER,
                                     scrollbar_button_hover_color=theme.SUBTLE)
        if not games:
            empty_state(lst, "📭", empty, "Klicke auf ＋ um Spiele hinzuzufügen")
            return lst
        for i, game in enumerate(games):
            if with_gaps and i > 0:
                gap = days_between(games[i - 1], game)
                if gap:
                    GapSeparator(lst, gap).pack(fill='x', pady=1)
            GameCard(lst, game, self.app.covers, self._open_game).pack(fill='x', padx=(10, 4), pady=4)
        return lst

    def _on_tab(self, value):
        new_page = 0 if value.startswith("📅") else 1
        if new_page == self.page:
            return
        if self.lists[self.page] is not None:
            self.lists[self.page].pack_forget()
        self.page = new_page
        self._show_page()

    # ------------------------------------------------------------- Aktionen
    def _open_game(self, uid):
        self.app.open_edit_dialog(uid, from_popup=True)

    def _refresh(self):
        self.refresh_btn.configure(state='disabled')
        self.footer_label.configure(text="Aktualisiere…")
        self.app.start_update()

    def _grab_focus(self):
        if self.winfo_exists():
            self.lift()
            self.focus_force()

    def _check_focus(self):
        """Schließt das Flyout, sobald der Fokus woanders hingeht (Klick außerhalb).

        Nur wenn es vorher wirklich den Fokus hatte – Windows verweigert Hintergrundprozessen
        manchmal den Vordergrund, dann bleibt es offen bis ✕/Esc/Tray-Klick.
        """
        if not self.is_open or not self._had_focus:
            return
        try:
            focused = self.focus_get()
        except (KeyError, AttributeError):
            focused = None
        if focused is None or focused.winfo_toplevel() is not self:
            x, y = self.winfo_pointerxy()
            under = self.winfo_containing(x, y)
            if under is None or under.winfo_toplevel() is not self:
                self.close()

    def close(self):
        if self.is_open:
            self.closed_at = time.monotonic()
            self.withdraw()
