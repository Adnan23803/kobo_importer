"""Palette et reglages visuels communs.

Chaque couleur est donnee sous la forme (clair, sombre) : customtkinter choisit
seul la variante selon le mode d'apparence.
"""

import customtkinter as ctk

PAGE_BG = ("#f4f6f8", "#111827")
SIDEBAR_BG = ("#e7edf3", "#18212f")
CARD_BG = ("#ffffff", "#1f2937")
CARD_BORDER = ("#dbe3ec", "#334155")

TEXT = ("#14213d", "#f1f5f9")
TEXT_MUTED = ("#5c728a", "#9fb4c9")
TEXT_ON_ACCENT = ("#ffffff", "#ffffff")

PRIMARY = ("#1f4e79", "#3b82f6")
PRIMARY_HOVER = ("#163a5a", "#2563eb")

SUCCESS = ("#15803d", "#4ade80")
SUCCESS_BG = ("#dcfce7", "#14532d")
WARNING = ("#b45309", "#fbbf24")
WARNING_BG = ("#fef3c7", "#78350f")
DANGER = ("#b91c1c", "#f87171")
DANGER_BG = ("#fee2e2", "#7f1d1d")
NEUTRAL_BG = ("#e2e8f0", "#334155")

DANGER_BUTTON = ("#c81e1e", "#b91c1c")
DANGER_BUTTON_HOVER = ("#a11616", "#991b1b")

TONES = {
    "success": (SUCCESS, SUCCESS_BG),
    "warning": (WARNING, WARNING_BG),
    "error": (DANGER, DANGER_BG),
    "info": (PRIMARY, NEUTRAL_BG),
    "muted": (TEXT_MUTED, NEUTRAL_BG),
}

RADIUS = 12
CARD_RADIUS = 16


def font(size=13, weight="normal", slant="roman"):
    return ctk.CTkFont(size=size, weight=weight, slant=slant)


def title_font():
    return ctk.CTkFont(size=21, weight="bold")


def heading_font():
    return ctk.CTkFont(size=15, weight="bold")


def body_font():
    return ctk.CTkFont(size=13)


def small_font():
    return ctk.CTkFont(size=11)


def mono_font():
    return ctk.CTkFont(family="Consolas", size=12)
