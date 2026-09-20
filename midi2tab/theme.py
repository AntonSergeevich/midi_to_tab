"""
Тёмная тема оформления.

tkinter не умеет тёмный режим сам, поэтому палитра задаётся вручную --
и для ttk-виджетов через Style, и для классических (Text, Listbox),
которые Style не затрагивает вовсе.
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk

# Палитра: глубокий тёмный фон, тёплый янтарный акцент под дерево гитары
BG = "#15161c"          # фон окна
PANEL = "#1d1f27"       # панели
PANEL_HI = "#252833"    # поля ввода, выделение
BORDER = "#333744"
TEXT = "#e6e8ee"
TEXT_MUTED = "#8b90a3"
ACCENT = "#d99a4e"      # янтарный -- кнопки действия, лады в табах
ACCENT_HOVER = "#e8ad63"
ACCENT_DIM = "#8a6432"
BLUE = "#5b8dee"
GREEN = "#4caf7d"
RED = "#e0605f"

FONT = ("Segoe UI", 10) if sys.platform == "win32" else ("DejaVu Sans", 10)
FONT_BOLD = (FONT[0], 10, "bold")
FONT_TITLE = (FONT[0], 11, "bold")
FONT_SMALL = (FONT[0], 9)
MONO = ("Consolas", 10) if sys.platform == "win32" else ("DejaVu Sans Mono", 10)


def apply(root: tk.Tk) -> ttk.Style:
    """Навесить тёмное оформление на всё окно."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")  # единственная встроенная тема, поддающаяся перекраске
    except tk.TclError:
        pass

    root.configure(bg=BG)

    style.configure(".", background=BG, foreground=TEXT, font=FONT, borderwidth=0)
    style.configure("TFrame", background=BG)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
    style.configure("Muted.TLabel", background=BG, foreground=TEXT_MUTED, font=FONT_SMALL)
    style.configure("PanelMuted.TLabel", background=PANEL, foreground=TEXT_MUTED, font=FONT_SMALL)
    style.configure("Title.TLabel", background=BG, foreground=TEXT, font=FONT_TITLE)
    style.configure("Step.TLabel", background=BG, foreground=ACCENT, font=FONT_BOLD)
    style.configure("Ok.TLabel", background=BG, foreground=GREEN, font=FONT_SMALL)
    style.configure("Warn.TLabel", background=BG, foreground=RED, font=FONT_SMALL)

    style.configure(
        "TLabelframe", background=BG, bordercolor=BORDER, relief="solid", borderwidth=1
    )
    style.configure("TLabelframe.Label", background=BG, foreground=ACCENT, font=FONT_BOLD)

    # Кнопки
    style.configure(
        "TButton",
        background=PANEL_HI,
        foreground=TEXT,
        bordercolor=BORDER,
        focuscolor=BORDER,
        borderwidth=1,
        padding=(12, 6),
        relief="flat",
    )
    style.map(
        "TButton",
        background=[("pressed", BORDER), ("active", BORDER), ("disabled", PANEL)],
        foreground=[("disabled", TEXT_MUTED)],
    )
    style.configure(
        "Accent.TButton",
        background=ACCENT,
        foreground="#1a1206",
        font=FONT_BOLD,
        padding=(18, 8),
    )
    style.map(
        "Accent.TButton",
        background=[("pressed", ACCENT_DIM), ("active", ACCENT_HOVER), ("disabled", PANEL_HI)],
        foreground=[("disabled", TEXT_MUTED)],
    )

    # Поля ввода
    for widget in ("TEntry", "TSpinbox", "TCombobox"):
        style.configure(
            widget,
            fieldbackground=PANEL_HI,
            background=PANEL_HI,
            foreground=TEXT,
            bordercolor=BORDER,
            insertcolor=ACCENT,
            arrowcolor=TEXT_MUTED,
            selectbackground=ACCENT_DIM,
            selectforeground=TEXT,
            padding=5,
        )
        style.map(
            widget,
            bordercolor=[("focus", ACCENT)],
            fieldbackground=[("disabled", PANEL), ("readonly", PANEL_HI)],
            foreground=[("disabled", TEXT_MUTED)],
        )

    style.configure("TCheckbutton", background=BG, foreground=TEXT, focuscolor=BG)
    style.map(
        "TCheckbutton",
        background=[("active", BG)],
        indicatorcolor=[("selected", ACCENT), ("!selected", PANEL_HI)],
        foreground=[("disabled", TEXT_MUTED)],
    )
    style.configure("Panel.TCheckbutton", background=PANEL, foreground=TEXT, focuscolor=PANEL)
    style.map("Panel.TCheckbutton", background=[("active", PANEL)],
              indicatorcolor=[("selected", ACCENT), ("!selected", PANEL_HI)])

    style.configure("TRadiobutton", background=BG, foreground=TEXT, focuscolor=BG)
    style.map("TRadiobutton", background=[("active", BG)],
              indicatorcolor=[("selected", ACCENT), ("!selected", PANEL_HI)])

    # Вкладки
    style.configure("TNotebook", background=BG, bordercolor=BORDER, tabmargins=(2, 4, 2, 0))
    style.configure(
        "TNotebook.Tab",
        background=PANEL,
        foreground=TEXT_MUTED,
        padding=(14, 7),
        bordercolor=BORDER,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", PANEL_HI)],
        foreground=[("selected", ACCENT)],
        expand=[("selected", (0, 0, 0, 0))],
    )

    # Полосы прокрутки и прогресс
    style.configure(
        "TScrollbar",
        background=PANEL_HI,
        troughcolor=PANEL,
        bordercolor=PANEL,
        arrowcolor=TEXT_MUTED,
        relief="flat",
    )
    style.map("TScrollbar", background=[("active", BORDER)])
    style.configure(
        "TProgressbar", background=ACCENT, troughcolor=PANEL, bordercolor=PANEL, thickness=6
    )
    style.configure("TScale", background=BG, troughcolor=PANEL_HI, bordercolor=BORDER)
    style.configure("TSeparator", background=BORDER)

    # Выпадающие списки Combobox -- отдельные окна, Style их не достаёт
    root.option_add("*TCombobox*Listbox.background", PANEL_HI)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_DIM)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    root.option_add("*TCombobox*Listbox.font", FONT)
    return style


def style_listbox(widget: tk.Listbox) -> None:
    widget.configure(
        background=PANEL_HI,
        foreground=TEXT,
        selectbackground=ACCENT_DIM,
        selectforeground=TEXT,
        highlightthickness=1,
        highlightbackground=BORDER,
        highlightcolor=ACCENT,
        borderwidth=0,
        font=FONT_SMALL,
        activestyle="none",
    )


def style_text(widget: tk.Text) -> None:
    widget.configure(
        background=PANEL,
        foreground=TEXT,
        insertbackground=ACCENT,
        selectbackground=ACCENT_DIM,
        selectforeground=TEXT,
        highlightthickness=1,
        highlightbackground=BORDER,
        highlightcolor=BORDER,
        borderwidth=0,
        font=MONO,
        padx=10,
        pady=8,
    )
    # Подсветка содержимого табулатуры
    widget.tag_configure("fret", foreground=ACCENT)
    widget.tag_configure("string", foreground=BLUE)
    widget.tag_configure("head", foreground=TEXT_MUTED)
    widget.tag_configure("good", foreground=GREEN)
    widget.tag_configure("bad", foreground=RED)
