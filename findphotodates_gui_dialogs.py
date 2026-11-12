"""Message boxes drawn with customtkinter, so they match the rest of the app.

Drop-in replacement for the parts of ``tkinter.messagebox`` the GUI uses:
``showinfo``, ``showwarning``, ``showerror``, ``askyesno`` and
``askyesnocancel`` take the same arguments and return the same values.
"""

from __future__ import annotations

import tkinter
from tkinter import TclError

import customtkinter as ctk

# (icon character, circle colour (light, dark)) for each kind of message.
_ICONS = {
    "info": ("i", ("#1f6aa5", "#2f7fc1")),
    "question": ("?", ("#1f6aa5", "#2f7fc1")),
    "warning": ("!", ("#c47f00", "#d99a1e")),
    "error": ("!", ("#c0392b", "#d9534f")),
}
_HEADLINE_MAX = 90
_SCROLL_AFTER_LINES = 18


def split_message(title, message):
    """Return (headline, body) for a dialog.

    A short first paragraph becomes the headline ("Run these scans in order?"),
    with the rest underneath. Otherwise the window title is the headline.
    """
    message = (message or "").strip()
    first, sep, rest = message.partition("\n\n")
    if first and "\n" not in first and len(first) <= _HEADLINE_MAX:
        return first, rest.strip() if sep else ""
    return title or "", message


class _Dialog(ctk.CTkToplevel):
    def __init__(self, parent, kind, title, message, buttons, cancel_value):
        super().__init__(parent)
        self.result = cancel_value
        self._cancel_value = cancel_value
        self.title(title or "")
        self.resizable(False, False)
        self.grid_columnconfigure(1, weight=1)
        if parent is not None:
            try:
                self.transient(parent)
            except TclError:
                pass

        glyph, color = _ICONS[kind]
        ctk.CTkLabel(self, text=glyph, width=38, height=38, corner_radius=19, fg_color=color,
                     text_color="white", font=ctk.CTkFont(size=20, weight="bold")
                     ).grid(row=0, column=0, sticky="n", padx=(24, 14), pady=(24, 0))

        headline, body = split_message(title, message)
        ctk.CTkLabel(self, text=headline, anchor="w", justify="left", wraplength=420,
                     font=ctk.CTkFont(size=17, weight="bold")
                     ).grid(row=0, column=1, sticky="ew", padx=(0, 26), pady=(28, 0))
        if body:
            if body.count("\n") >= _SCROLL_AFTER_LINES or len(body) > 1500:
                box = ctk.CTkTextbox(self, width=440, height=300, wrap="word",
                                     font=ctk.CTkFont(size=14))
                box.insert("1.0", body)
                box.configure(state="disabled")
                box.grid(row=1, column=1, sticky="ew", padx=(0, 26), pady=(10, 0))
            else:
                ctk.CTkLabel(self, text=body, anchor="w", justify="left", wraplength=420,
                             text_color=("gray30", "gray75"), font=ctk.CTkFont(size=14)
                             ).grid(row=1, column=1, sticky="ew", padx=(0, 26), pady=(8, 0))

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=2, column=0, columnspan=2, sticky="e", padx=22, pady=(22, 20))
        primary = None
        # buttons: [(text, value, is_primary)], shown left to right.
        for column, (text, value, is_primary) in enumerate(buttons):
            if is_primary:
                button = ctk.CTkButton(row, text=text, width=96, height=34, corner_radius=8,
                                       command=lambda v=value: self._finish(v))
                primary = (button, value)
            else:
                button = ctk.CTkButton(row, text=text, width=96, height=34, corner_radius=8,
                                       fg_color="transparent", border_width=1,
                                       border_color=("gray65", "gray40"),
                                       text_color=("gray15", "gray90"),
                                       hover_color=("gray85", "gray25"),
                                       command=lambda v=value: self._finish(v))
            button.grid(row=0, column=column, padx=(8, 0))

        self.protocol("WM_DELETE_WINDOW", lambda: self._finish(self._cancel_value))
        self.bind("<Escape>", lambda _e: self._finish(self._cancel_value))
        if primary is not None:
            self.bind("<Return>", lambda _e: self._finish(primary[1]))
            self.bind("<KP_Enter>", lambda _e: self._finish(primary[1]))
        self._place_over(parent)

    def _place_over(self, parent):
        self.update_idletasks()
        width, height = self.winfo_reqwidth(), self.winfo_reqheight()
        try:
            if parent is None or not parent.winfo_viewable():
                raise TclError
            x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - height) // 3
        except TclError:
            x = (self.winfo_screenwidth() - width) // 2
            y = (self.winfo_screenheight() - height) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _finish(self, value):
        self.result = value
        try:
            self.grab_release()
        except TclError:
            pass
        self.destroy()

    def run(self):
        try:
            self.wait_visibility()
            self.grab_set()
        except TclError:
            pass  # still usable without a grab (e.g. another window holds it)
        self.lift()
        self.focus_force()
        self.wait_window()
        return self.result


def _parent(options):
    parent = options.get("parent") or tkinter._default_root
    if parent is not None:
        try:
            parent = parent.winfo_toplevel()
        except TclError:
            parent = None
    return parent


def _show(kind, title, message, buttons, cancel_value, options):
    parent = _parent(options)
    if parent is None:  # no window to attach to: fall back to the plain dialog
        from tkinter import messagebox
        fallback = {"info": messagebox.showinfo, "warning": messagebox.showwarning,
                    "error": messagebox.showerror}
        if kind in fallback:
            return fallback[kind](title, message)
        if len(buttons) == 3:
            return messagebox.askyesnocancel(title, message)
        return messagebox.askyesno(title, message)
    return _Dialog(parent, kind, title, message, buttons, cancel_value).run()


def showinfo(title=None, message=None, **options):
    return _show("info", title, message, [("OK", "ok", True)], "ok", options)


def showwarning(title=None, message=None, **options):
    return _show("warning", title, message, [("OK", "ok", True)], "ok", options)


def showerror(title=None, message=None, **options):
    return _show("error", title, message, [("OK", "ok", True)], "ok", options)


def askyesno(title=None, message=None, **options):
    return _show("question", title, message,
                 [("No", False, False), ("Yes", True, True)], False, options)


def askyesnocancel(title=None, message=None, **options):
    return _show("question", title, message,
                 [("Cancel", None, False), ("No", False, False), ("Yes", True, True)],
                 None, options)
