"""Warteliste: Verwaltung und Treffer-Dialog."""
from datetime import datetime

import customtkinter as ctk

from ..models import format_date_european, make_game, platform_list, short_platform
from . import theme
from .widgets import ScrollFrame, BaseWindow, ConfirmDialog, empty_state

COVER = (40, 54)


def _fmt(iso, fmt='%d.%m.%Y'):
    try:
        return datetime.fromisoformat(iso).strftime(fmt)
    except (TypeError, ValueError):
        return '?'


class WaitlistWindow(BaseWindow):
    def __init__(self, app, on_close=None):
        super().__init__(app, "Warteliste", 700, 620, on_close=on_close)
        self.header("Warteliste", "")
        self.list = ScrollFrame(self, fg_color=theme.SURFACE, corner_radius=theme.RADIUS)
        self.list.pack(fill='both', expand=True, padx=20, pady=20)
        self.render()

    def render(self):
        for child in self.list.winfo_children():
            child.destroy()
        items = list(self.app.store.waitlist)
        self.subtitle_label.configure(
            text=f"{len(items)} Einträge · werden täglich automatisch neu gesucht")
        if not items:
            empty_state(self.list, "📋", "Warteliste ist leer",
                        "Spiele landen hier, wenn die Suche nichts Passendes findet.")
            return
        for item in items:
            self._row(item)

    def _row(self, item):
        card = ctk.CTkFrame(self.list, fg_color=theme.CARD, corner_radius=8)
        card.pack(fill='x', padx=6, pady=3)
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text=item['name'], font=theme.BODY_BOLD, text_color=theme.TEXT, anchor='w'
                     ).grid(row=0, column=0, sticky='ew', padx=14, pady=(10, 0))
        known = item.get('known_games', [])
        meta = f"Hinzugefügt {_fmt(item.get('added_date'))}" + (f"  ·  {len(known)} ignorierte Treffer" if known else "")
        ctk.CTkLabel(card, text=meta, font=theme.SMALL, text_color=theme.MUTED, anchor='w'
                     ).grid(row=1, column=0, sticky='ew', padx=14, pady=(0, 10))

        details = ctk.CTkFrame(card, fg_color='transparent')
        if known:
            for name in known[:30]:
                ctk.CTkLabel(details, text=f"•  {name}", font=theme.SMALL, text_color=theme.SUBTLE, anchor='w'
                             ).pack(fill='x', padx=14)

            def toggle():
                if details.winfo_ismapped():
                    details.grid_forget()
                else:
                    details.grid(row=2, column=0, columnspan=3, sticky='ew', pady=(0, 10))
            ctk.CTkButton(card, text="Details", width=80, command=toggle, **theme.button_style('ghost')
                          ).grid(row=0, column=1, rowspan=2, padx=(0, 6))

        ctk.CTkButton(card, text="Entfernen", width=90, command=lambda: self._confirm_remove(item['name']),
                      **theme.button_style('danger')).grid(row=0, column=2, rowspan=2, padx=(0, 12))

    def _confirm_remove(self, name):
        ConfirmDialog(self.app, "Von Warteliste entfernen?", f"„{name}“ wird nicht mehr automatisch gesucht.",
                      confirm_text="Entfernen", on_confirm=lambda: self.app.store.remove_from_waitlist(name))


class WaitlistMatchDialog(BaseWindow):
    """Erscheint, wenn die tägliche Suche neue Treffer für einen Wartelisten-Eintrag findet."""

    def __init__(self, app, item, new_games):
        super().__init__(app, "Warteliste – neue Treffer", 720, 600)
        self.item = item
        self.new_games = new_games
        self.attributes('-topmost', True)
        self.header(f"🔔  Neue Treffer für „{item['name']}“", f"Gefunden am {datetime.now():%d.%m.%Y %H:%M}")

        buttons = ctk.CTkFrame(self, fg_color=theme.SURFACE, corner_radius=0)
        buttons.pack(fill='x', side='bottom')
        ctk.CTkButton(buttons, text="Treffer ignorieren", command=self._ignore,
                      **theme.button_style('ghost')).pack(side='right', padx=(6, 20), pady=12)
        ctk.CTkButton(buttons, text="Von Warteliste entfernen", command=self._remove,
                      **theme.button_style('danger')).pack(side='left', padx=20, pady=12)

        lst = ScrollFrame(self, fg_color=theme.SURFACE, corner_radius=theme.RADIUS)
        lst.pack(fill='both', expand=True, padx=20, pady=16)
        for game in new_games:
            self._row(lst, game)

    def _row(self, parent, result):
        row = ctk.CTkFrame(parent, fg_color=theme.CARD, corner_radius=8)
        row.pack(fill='x', padx=6, pady=3)
        row.grid_columnconfigure(1, weight=1)
        cover = ctk.CTkLabel(row, text='')
        cover.grid(row=0, column=0, rowspan=2, padx=10, pady=8)

        def set_cover():
            if cover.winfo_exists():
                cover.configure(image=self.app.covers.get(result['cover_id'], COVER, result['name']))
        cover.configure(image=self.app.covers.get(result['cover_id'], COVER, result['name'], on_ready=set_cover))

        ctk.CTkLabel(row, text=result['name'], font=theme.BODY_BOLD, anchor='w', text_color=theme.TEXT
                     ).grid(row=0, column=1, sticky='ew', pady=(8, 0))
        plats = ', '.join(short_platform(p) for p in platform_list(result['platforms'])[:5])
        meta = f"{format_date_european(result['release_date']) or 'TBA'}   ·   ID {result['igdb_id']}"
        if plats:
            meta += f"   ·   {plats}"
        ctk.CTkLabel(row, text=meta, font=theme.SMALL, text_color=theme.MUTED, anchor='w'
                     ).grid(row=1, column=1, sticky='ew', pady=(0, 8))
        ctk.CTkButton(row, text="Hinzufügen", width=100, command=lambda: self._add(result),
                      **theme.button_style('success')).grid(row=0, column=2, rowspan=2, padx=12)

    def _add(self, result):
        self.app.store.add_game(make_game(result))
        self.app.store.remove_from_waitlist(self.item['name'])
        self.close()

    def _ignore(self):
        self.app.store.ignore_waitlist_matches(self.item['name'], [g['name'] for g in self.new_games])
        self.close()

    def _remove(self):
        self.app.store.remove_from_waitlist(self.item['name'])
        self.close()
