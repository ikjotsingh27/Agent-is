"""Colors, fonts and small widget helpers, so every screen looks the same.

Dark is the default; light is one click in Settings. All colors live here and nowhere else.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

THEMES = {
    "dark": {
        "bg": "#15171c", "sidebar": "#1b1e24", "card": "#21252d", "border": "#2c313b", "field": "#191c22",
        "text": "#e9ebef", "muted": "#9aa1ad", "accent": "#7d9bff", "accent_hover": "#95adff",
        "on_accent": "#0f1115", "you": "#a9bcff", "ok": "#3ecf8e", "warn": "#f2b544", "bad": "#f06464",
        "nav_active": "#2a2f3a",
    },
    "light": {
        "bg": "#f5f6f8", "sidebar": "#eceef2", "card": "#ffffff", "border": "#dde1e7", "field": "#f8f9fb",
        "text": "#1c1f25", "muted": "#5d6470", "accent": "#3a5bd9", "accent_hover": "#2f4cc0",
        "on_accent": "#ffffff", "you": "#2f4cc0", "ok": "#178a5b", "warn": "#a86a00", "bad": "#c93636",
        "nav_active": "#dfe3ea",
    },
}

FONT = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 9)
FONT_BOLD = ("Segoe UI Semibold", 10)
FONT_TITLE = ("Segoe UI Semibold", 16)
FONT_BIG = ("Segoe UI Semibold", 22)


class Theme:
    def __init__(self, name: str = "dark"):
        self.name = name if name in THEMES else "dark"
        self.c = THEMES[self.name]

    def __getitem__(self, key):
        return self.c[key]

    # --- widget helpers -------------------------------------------------------------------------
    def frame(self, parent, bg="bg", **kw):
        return tk.Frame(parent, bg=self.c[bg], **kw)

    def card(self, parent, **kw):
        """A flat panel with a soft border and padding inside."""
        outer = tk.Frame(parent, bg=self.c["card"], highlightthickness=1,
                         highlightbackground=self.c["border"], **kw)
        inner = tk.Frame(outer, bg=self.c["card"])
        inner.pack(fill="both", expand=True, padx=18, pady=16)
        return outer, inner

    def label(self, parent, text="", bg="bg", fg="text", font=FONT, **kw):
        return tk.Label(parent, text=text, bg=self.c[bg], fg=self.c[fg], font=font, anchor="w", justify="left", **kw)

    def button(self, parent, text, command, kind="primary", bg="bg", **kw):
        if kind == "primary":
            colors = dict(bg=self.c["accent"], fg=self.c["on_accent"], activebackground=self.c["accent_hover"],
                          activeforeground=self.c["on_accent"])
        else:  # secondary: quiet button that sits on a card or the page
            colors = dict(bg=self.c["nav_active"], fg=self.c["text"], activebackground=self.c["border"],
                          activeforeground=self.c["text"])
        b = tk.Button(parent, text=text, command=command, relief="flat", bd=0, cursor="hand2",
                      font=FONT_BOLD, padx=16, pady=7, disabledforeground=self.c["muted"], **colors, **kw)
        return b

    def entry(self, parent, show=None, width=40):
        return tk.Entry(parent, show=show, width=width, relief="flat", font=FONT, bg=self.c["field"],
                        fg=self.c["text"], insertbackground=self.c["text"], highlightthickness=1,
                        highlightbackground=self.c["border"], highlightcolor=self.c["accent"])

    def style_ttk(self, root) -> None:
        """Dropdowns (ttk) need their colors set through a style."""
        style = ttk.Style(root)
        style.theme_use("clam")
        c = self.c
        style.configure("TCombobox", fieldbackground=c["field"], background=c["card"], foreground=c["text"],
                        arrowcolor=c["text"], bordercolor=c["border"], lightcolor=c["field"], darkcolor=c["field"],
                        selectbackground=c["field"], selectforeground=c["text"], padding=5)
        style.map("TCombobox", fieldbackground=[("readonly", c["field"])], foreground=[("readonly", c["text"])],
                  selectbackground=[("readonly", c["field"])], selectforeground=[("readonly", c["text"])])
        root.option_add("*TCombobox*Listbox.background", c["card"])
        root.option_add("*TCombobox*Listbox.foreground", c["text"])
        root.option_add("*TCombobox*Listbox.selectBackground", c["accent"])
        root.option_add("*TCombobox*Listbox.selectForeground", c["on_accent"])
        root.option_add("*TCombobox*Listbox.font", FONT)
        style.configure("Vertical.TScrollbar", background=c["card"], troughcolor=c["bg"], bordercolor=c["bg"],
                        arrowcolor=c["muted"], lightcolor=c["card"], darkcolor=c["card"])


def scroll_area(theme: Theme, parent):
    """A frame that scrolls with the mouse wheel when its content is taller than the window.

    Returns (outer, inner): pack the outer frame, put content in the inner one.
    """
    outer = theme.frame(parent)
    canvas = tk.Canvas(outer, bg=theme["bg"], highlightthickness=0, bd=0)
    inner = theme.frame(canvas)
    window = canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.pack(fill="both", expand=True)
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))

    def wheel(event):
        if inner.winfo_height() > canvas.winfo_height():
            canvas.yview_scroll(int(-event.delta / 120), "units")

    canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", wheel))
    canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
    return outer, inner


def paste_into(root, entry) -> bool:
    """Put the clipboard text into an entry. Returns False when the clipboard has no text."""
    try:
        text = root.clipboard_get()
    except tk.TclError:
        return False
    entry.delete(0, "end")
    entry.insert(0, text.strip())
    return True
