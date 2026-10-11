"""Zentrales Design: Farben, Schriften, Abstände."""
import customtkinter as ctk

# Flächen
BG = '#12162a'
SURFACE = '#1a1f36'
CARD = '#232a45'
CARD_HOVER = '#2b3354'
INPUT = '#1e2440'
BORDER = '#2f3757'

# Text
TEXT = '#e8ecf8'
MUTED = '#8f97b5'
SUBTLE = '#5f6788'

# Akzente
ACCENT = '#4a90e2'
ACCENT_HOVER = '#3b7cd0'
SUCCESS = '#2ecc71'
SUCCESS_DARK = '#23a35a'
DANGER = '#e5534b'
DANGER_DARK = '#c2413a'
WARNING = '#f0a030'
GOLD = '#f5c542'
HYPE_BG = '#2c2816'
HYPE_HOVER = '#38321b'
HYPE_BORDER = '#d9a520'
TROPHY = '#5aa9ff'
UNREAD_BG = '#1f3340'

# Countdown-Stufen: (Hintergrund, Text)
PILL_TODAY = ('#1f4d33', '#5ee08f')
PILL_WEEK = ('#4d2323', '#ff7b72')
PILL_MONTH = ('#4a3714', '#f5b14a')
PILL_LATER = ('#1d3352', '#6cb0ff')
PILL_PAST = ('#2a3048', '#9aa3c0')
PILL_NONE = ('#2a3048', '#8f97b5')

FONT_FAMILY = 'Segoe UI'
EMOJI_FAMILY = 'Segoe UI Emoji'


def font(size=12, weight='normal'):
    return (FONT_FAMILY, size, weight)


H1 = font(17, 'bold')
H2 = font(14, 'bold')
BODY = font(12)
BODY_BOLD = font(12, 'bold')
SMALL = font(11)
SMALL_BOLD = font(11, 'bold')
TINY = font(10)

RADIUS = 10
PAD = 12


def setup():
    ctk.set_appearance_mode('dark')
    ctk.set_default_color_theme('blue')


def button_style(kind='primary'):
    """Gemeinsame Button-Parameter je Variante."""
    styles = {
        'primary': dict(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color='white'),
        'success': dict(fg_color=SUCCESS_DARK, hover_color='#1d8a4c', text_color='white'),
        'danger': dict(fg_color=DANGER_DARK, hover_color='#a33730', text_color='white'),
        'ghost': dict(fg_color='transparent', hover_color=CARD_HOVER, text_color=TEXT,
                      border_width=1, border_color=BORDER),
        'subtle': dict(fg_color=CARD, hover_color=CARD_HOVER, text_color=TEXT),
        'warning': dict(fg_color='#c98516', hover_color='#ad7212', text_color='white'),
    }
    return dict(corner_radius=8, font=BODY_BOLD, height=36, **styles[kind])


def entry_style():
    return dict(fg_color=INPUT, border_color=BORDER, border_width=1, text_color=TEXT,
                placeholder_text_color=SUBTLE, corner_radius=8, height=38, font=BODY)
