"""„Spiele verwalten“: durchsuchbare Gesamtliste."""
import customtkinter as ctk

from ..models import is_tba, sort_key_dated, title
from . import theme
from .widgets import ScrollFrame, BaseWindow, ConfirmDialog, add_context_menu, bind_hover_click, empty_state

COVER = (30, 40)


class ManagerWindow(BaseWindow):
    def __init__(self, app, on_close=None):
        super().__init__(app, "Spiele verwalten", 820, 720, on_close=on_close)
        self.header("Spiele verwalten", "")

        bar = ctk.CTkFrame(self, fg_color='transparent')
        bar.pack(fill='x', padx=20, pady=(14, 8))
        self.filter = ctk.CTkEntry(bar, placeholder_text="🔎  Filtern…", **theme.entry_style())
        self.filter.pack(side='left', fill='x', expand=True)
        self.filter.bind('<KeyRelease>', lambda e: self.render())
        add_context_menu(self.filter)
        ctk.CTkButton(bar, text="＋  Hinzufügen", width=140, command=lambda: app.open_add_dialog(),
                      **theme.button_style('success')).pack(side='left', padx=(8, 0))

        self.list = ScrollFrame(self, fg_color=theme.SURFACE, corner_radius=theme.RADIUS)
        self.list.pack(fill='both', expand=True, padx=20, pady=(0, 20))
        self.render()

    def render(self):
        for child in self.list.winfo_children():
            child.destroy()
        games = sorted(self.app.store.snapshot(), key=sort_key_dated)
        self.subtitle_label.configure(text=f"{len(games)} Spiele · Klick auf eine Zeile zum Bearbeiten")
        needle = self.filter.get().strip().lower()
        if needle:
            games = [g for g in games if needle in title(g).lower() or needle in g.get('name', '').lower()]
        if not games:
            empty_state(self.list, "🎮", "Keine Spiele", "Nichts gefunden" if needle else "Füge dein erstes Spiel hinzu")
            return
        for game in games:
            self._row(game)

    def _row(self, game):
        row = ctk.CTkFrame(self.list, fg_color=theme.CARD, corner_radius=8, cursor='hand2')
        row.pack(fill='x', padx=6, pady=2)
        row.grid_columnconfigure(1, weight=1)

        cover = ctk.CTkLabel(row, text='')
        cover.grid(row=0, column=0, padx=(8, 10), pady=6)

        def set_cover():
            if cover.winfo_exists():
                cover.configure(image=self.app.covers.get(game.get('cover_id'), COVER, title(game)))
        cover.configure(image=self.app.covers.get(game.get('cover_id'), COVER, title(game), on_ready=set_cover))

        name = ('★ ' if game.get('hyped') else '') + title(game)
        ctk.CTkLabel(row, text=name, font=theme.BODY_BOLD, anchor='w',
                     text_color=theme.GOLD if game.get('hyped') else theme.TEXT).grid(row=0, column=1, sticky='ew')
        date = 'TBA' if is_tba(game) else game['release_date']
        meta = f"{date}" + (f"   ·   ID {game['igdb_id']}" if game.get('igdb_id') else "")
        if game.get('psn_trophy_count'):
            meta += f"   ·   🏆 {game['psn_trophy_count']}"
        ctk.CTkLabel(row, text=meta, font=theme.SMALL, text_color=theme.MUTED).grid(row=0, column=2, padx=10)

        delete = ctk.CTkButton(row, text="🗑", width=32, height=28, fg_color='transparent',
                               hover_color=theme.DANGER_DARK, text_color=theme.MUTED, font=(theme.EMOJI_FAMILY, 12),
                               command=lambda: self._confirm_delete(game))
        delete.grid(row=0, column=3, padx=(0, 8))

        bind_hover_click(row, lambda: row.configure(fg_color=theme.CARD_HOVER),
                         lambda: row.configure(fg_color=theme.CARD),
                         lambda uid=game['uid']: self.app.open_edit_dialog(uid), exclude=(delete,))

    def _confirm_delete(self, game):
        ConfirmDialog(self.app, "Spiel löschen?", f"„{title(game)}“ wird aus dem Tracker entfernt.",
                      on_confirm=lambda: self.app.store.delete_game(game['uid']))
