"""Spiel hinzufügen: Suche nach Name oder IGDB-ID – läuft im Hintergrund, UI bleibt bedienbar."""
import threading

import customtkinter as ctk

from ..igdb import NetworkError
from ..models import format_date_european, make_game, platform_list, short_platform
from . import theme
from .widgets import ScrollFrame, BaseWindow, add_context_menu, empty_state

COVER = (40, 54)


class AddDialog(BaseWindow):
    def __init__(self, app, on_close=None):
        super().__init__(app, "Spiel hinzufügen", 760, 760, on_close=on_close, maximized=True)
        self._search_id = 0
        self.header("Spiel hinzufügen", "Suche in der IGDB-Datenbank nach Name oder direkt per ID")

        top = ctk.CTkFrame(self, fg_color='transparent')
        top.pack(fill='x', padx=20, pady=(16, 6))

        self.mode = ctk.CTkSegmentedButton(top, values=["Nach Name", "Per IGDB-ID"], command=self._mode_changed,
                                           font=theme.SMALL_BOLD, height=30, selected_color=theme.ACCENT,
                                           selected_hover_color=theme.ACCENT_HOVER, unselected_color=theme.CARD,
                                           unselected_hover_color=theme.CARD_HOVER, fg_color=theme.CARD)
        self.mode.set("Nach Name")
        self.mode.pack(anchor='w')

        row = ctk.CTkFrame(top, fg_color='transparent')
        row.pack(fill='x', pady=(10, 0))
        self.entry = ctk.CTkEntry(row, placeholder_text="Spielname, z.B. Ghost of Yotei", **theme.entry_style())
        self.entry.pack(side='left', fill='x', expand=True)
        self.entry.bind('<Return>', lambda e: self.search())
        add_context_menu(self.entry)
        self.search_btn = ctk.CTkButton(row, text="Suchen", width=110, command=self.search,
                                        **theme.button_style('primary'))
        self.search_btn.pack(side='left', padx=(8, 0))

        switches = ctk.CTkFrame(top, fg_color='transparent')
        switches.pack(fill='x', pady=(12, 0))
        self.hyped = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(switches, text="★  Besonders gehypt", variable=self.hyped, font=theme.BODY_BOLD,
                      text_color=theme.GOLD, progress_color=theme.HYPE_BORDER).pack(side='left')
        self.manual_tba = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(switches, text="Datum ist nur ein Platzhalter (TBA)", variable=self.manual_tba,
                      font=theme.BODY_BOLD, text_color=theme.TEXT, progress_color=theme.DANGER_DARK
                      ).pack(side='left', padx=(28, 0))

        self.status = ctk.CTkLabel(self, text="", font=theme.SMALL, text_color=theme.MUTED, anchor='w')
        self.status.pack(fill='x', padx=22, pady=(8, 0))

        self.bottom = ctk.CTkFrame(self, fg_color='transparent')
        self.bottom.pack(fill='x', side='bottom', padx=20, pady=14)

        self.results = ScrollFrame(self, fg_color=theme.SURFACE, corner_radius=theme.RADIUS)
        self.results.pack(fill='both', expand=True, padx=20, pady=(6, 0))
        empty_state(self.results, "🔍", "Noch keine Suche", "Gib einen Namen ein und drücke Enter")

        self.after(150, lambda: self.winfo_exists() and self.entry.focus_set())

    def _mode_changed(self, value):
        self.entry.delete(0, 'end')
        self.entry.configure(placeholder_text="IGDB-ID, z.B. 119171" if value == "Per IGDB-ID"
                             else "Spielname, z.B. Ghost of Yotei")
        self.entry.focus_set()

    # ------------------------------------------------------------- Suche
    def search(self):
        query = self.entry.get().strip()
        if not query:
            return
        by_id = self.mode.get() == "Per IGDB-ID"
        if by_id and not query.isdigit():
            self.status.configure(text="⚠️ Die IGDB-ID besteht nur aus Ziffern.", text_color=theme.WARNING)
            return

        self._search_id += 1
        search_id = self._search_id
        self.search_btn.configure(state='disabled', text="Suche…")
        self.status.configure(text="Suche läuft…", text_color=theme.MUTED)

        def work():
            try:
                if by_id:
                    result = self.app.igdb.by_id(query)
                    results = [result] if result else []
                else:
                    results = self.app.igdb.search_ranked(query)
            except NetworkError:
                self.app.call(self._show_error, search_id)
                return
            self.app.call(self._show_results, search_id, query, results, by_id)

        threading.Thread(target=work, daemon=True).start()

    def _show_error(self, search_id):
        if not self.winfo_exists() or search_id != self._search_id:
            return
        self.search_btn.configure(state='normal', text="Suchen")
        self.status.configure(text="⚠️ IGDB ist nicht erreichbar – Internetverbindung prüfen.", text_color=theme.WARNING)

    def _show_results(self, search_id, query, results, by_id):
        if not self.winfo_exists() or search_id != self._search_id:
            return
        self.search_btn.configure(state='normal', text="Suchen")
        for child in self.results.winfo_children():
            child.destroy()
        for child in self.bottom.winfo_children():
            child.destroy()

        if not results:
            self.status.configure(text="")
            if by_id:
                empty_state(self.results, "😕", "Keine Treffer", f"Kein Spiel mit der ID {query} gefunden.")
                return
            empty_state(self.results, "😕", "Keine Treffer",
                        "Du kannst das Spiel auf die Warteliste setzen –\n"
                        "es wird täglich neu gesucht und du wirst benachrichtigt.")
            self._waitlist_button(query, [])
            return

        self.status.configure(text=f"{len(results)} Treffer – klicke auf „Hinzufügen“", text_color=theme.MUTED)
        for result in results:
            self._result_row(result)
        if not by_id:
            ctk.CTkLabel(self.bottom, text="Nicht das richtige Spiel dabei?", font=theme.SMALL,
                         text_color=theme.MUTED).pack(side='left')
            self._waitlist_button(query, [r['name'] for r in results])

    def _waitlist_button(self, query, known):
        def add():
            self.app.store.add_to_waitlist(query, known)
            self.close()
        ctk.CTkButton(self.bottom, text="📋  Zur Warteliste hinzufügen", command=add,
                      **theme.button_style('warning')).pack(side='right')

    def _result_row(self, result):
        row = ctk.CTkFrame(self.results, fg_color=theme.CARD, corner_radius=8)
        row.pack(fill='x', padx=6, pady=3)
        row.grid_columnconfigure(1, weight=1)

        cover = ctk.CTkLabel(row, text='')
        cover.grid(row=0, column=0, rowspan=2, padx=10, pady=8)

        def set_cover():
            if cover.winfo_exists():
                cover.configure(image=self.app.covers.get(result['cover_id'], COVER, result['name']))
        cover.configure(image=self.app.covers.get(result['cover_id'], COVER, result['name'], on_ready=set_cover))

        ctk.CTkLabel(row, text=result['name'], font=theme.BODY_BOLD, text_color=theme.TEXT, anchor='w',
                     wraplength=430, justify='left').grid(row=0, column=1, sticky='ew', pady=(8, 0))
        date = result.get('release_window') or format_date_european(result['release_date']) or 'TBA'
        plats = ', '.join(short_platform(p) for p in platform_list(result['platforms'])[:5])
        meta = f"{date}   ·   ID {result['igdb_id']}" + (f"   ·   {plats}" if plats else '')
        ctk.CTkLabel(row, text=meta, font=theme.SMALL, text_color=theme.MUTED, anchor='w'
                     ).grid(row=1, column=1, sticky='ew', pady=(0, 8))

        if self.app.store.has_igdb_id(result['igdb_id']):
            ctk.CTkLabel(row, text="✓ Im Tracker", font=theme.SMALL_BOLD, text_color=theme.SUCCESS
                         ).grid(row=0, column=2, rowspan=2, padx=12)
        else:
            ctk.CTkButton(row, text="Hinzufügen", width=100, command=lambda: self._add(result),
                          **theme.button_style('success')).grid(row=0, column=2, rowspan=2, padx=12)

    def _add(self, result):
        self.app.store.add_game(make_game(result, hyped=self.hyped.get(), manual_tba=self.manual_tba.get()))
        self.close()
