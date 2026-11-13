#!/usr/bin/env python3
"""Simple desktop front end for findphotodates' interactive workflow."""

from __future__ import annotations

import datetime as _datetime
import os
import queue
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

from findphotodates_gui_startup import dependency_message

try:
    from tkinter import Menu, TclError, filedialog, ttk
    from tkinter import font as tkfont

    import customtkinter as ctk

    import findphotodates_gui_dialogs as messagebox
except (ImportError, OSError) as exc:
    if __name__ == "__main__":
        message = dependency_message(exc)
        print(message, file=sys.stderr)
        try:
            import tkinter
            root = tkinter.Tk()
            root.withdraw()
            import tkinter.messagebox
            tkinter.messagebox.showerror("Find Photo Dates", message)
            root.destroy()
        except Exception:
            pass  # Tk itself may be unavailable; the terminal message still explains the fix.
        raise SystemExit(1) from None
    raise

import findphotodates as fp
import findphotodates_coverage as coverage
from findphotodates_drives import unmount_command
from findphotodates_search import (
    FolderResult,
    InventorySearchIndex,
    SearchQueryError,
    normalize_photo_day,
)

APP_TITLE = f"Find Photo Dates {fp.__version__}"
GUI_SCALE_CHOICES = {
    "Text: 100%": 1.0,
    "Text: 125%": 1.25,
    "Text: 150%": 1.5,
    "Text: 175%": 1.75,
    "Text: 200%": 2.0,
}


def _closest_scale_label(scale):
    return min(GUI_SCALE_CHOICES, key=lambda label: abs(GUI_SCALE_CHOICES[label] - scale))


def worker_command(arguments):
    """Launch a console helper beside the frozen app, or its source module."""
    if getattr(sys, "frozen", False):
        name = "FindPhotoDatesWorker.exe" if os.name == "nt" else "FindPhotoDatesWorker"
        return [str(Path(sys.executable).with_name(name)), *arguments]
    return [sys.executable, "-u", str(Path(__file__).with_name("findphotodates_gui_worker.py")), *arguments]


def scan_arguments(settings, directory, output, list_dir, *, whole_drive=False):
    args = ["scan", "--directory", str(directory), "--output", str(output),
            "--list-dir", str(list_dir), "--hash", settings.hash_mode,
            "--workers", str(settings.workers), "--min-image-size", str(settings.min_image_size),
            "--path-style", settings.path_style, "--archives", settings.archives]
    if settings.only_media:
        args.append("--only-media")
    if settings.locate:
        args.append("--locate")
    if settings.retry_blank_exif:
        args.append("--retry-blank-exif")
    if whole_drive:
        args.append("--whole-drive")
    return args


def progress_fraction(line):
    match = re.search(r"([\d,]+)\s*/\s*([\d,]+)\s*\(([\d.]+)%\)", line)
    if match:
        return min(1.0, max(0.0, float(match.group(3)) / 100))
    return None


_CHANGE_GROUPS = (("New files", "new"), ("Changed files", "changed"),
                  ("Moved or renamed", "moved"), ("Removed", "removed"))


_READ_PROBLEMS = ((r"([\d,]+) folder\(s\) couldn't be opened", "folders couldn't be opened"),
                  (r"([\d,]+) file\(s\) couldn't be read", "files couldn't be read"),
                  (r"([\d,]+) photo\(s\)/video\(s\) couldn't be read",
                   "photos/videos couldn't be read for dates"))


def note_summary_line(line, summary):
    """Remember the parts of a job's output worth repeating once it finishes."""
    line = line.strip()
    match = re.search(r"\(([\d,]+) files listed\)", line)
    if match:
        summary["files"] = int(match.group(1).replace(",", ""))
        return
    match = re.match(r"Files indexed: [\d,]+ \(([\d,]+) with EXIF dates\)", line)
    if match:
        summary["dated"] = int(match.group(1).replace(",", ""))
        return
    if line == "Nothing new, changed or removed.":
        summary["changes"] = {}
        return
    if "no earlier scan to compare with" in line:
        summary["first_scan"] = True
        return
    for prefix, word in _CHANGE_GROUPS:
        match = re.match(re.escape(prefix) + r"[^:]*: ([\d,]+)$", line)
        if match:
            summary.setdefault("changes", {})[word] = int(match.group(1).replace(",", ""))
            return
    if line.startswith("Reports saved in: "):
        summary["reports"] = True
        return
    for pattern, word in _READ_PROBLEMS:
        match = re.match(pattern, line)
        if match:
            summary.setdefault("read_problems", {})[word] = int(match.group(1).replace(",", ""))
            return


def summary_text(summary):
    """One short line describing a finished job, or None if there is nothing to say."""
    parts = []
    if "files" in summary:
        parts.append(f"{summary['files']:,} files in the file list")
        if summary.get("dated"):
            parts[-1] += f" ({summary['dated']:,} with photo dates)"
    if summary.get("first_scan"):
        parts.append("first scan of this drive")
    elif "changes" in summary:
        changes = summary["changes"]
        if changes:
            parts.append(", ".join(f"{changes[word]:,} {word}" for _, word in _CHANGE_GROUPS
                                   if word in changes) + " since the last scan")
        else:
            parts.append("no changes since the last scan")
    if summary.get("reports"):
        parts.append("backup reports are ready")
    problems = summary.get("read_problems")
    if problems:
        # Lead with this so it isn't lost behind the counts.
        parts.insert(0, "Read problems: " + ", ".join(
            f"{problems[word]:,} {word}" for _, word in _READ_PROBLEMS if word in problems) + " (see details)")
    if not parts:
        return None
    text = " · ".join(parts)
    return text[0].upper() + text[1:]


def open_folder(path):
    if os.name == "nt":
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        # The file manager is a separate program: don't hand it this Python
        # environment (its own Python add-ons then fail to load) or let its
        # start-up chatter land in our terminal.
        env = {key: value for key, value in os.environ.items()
               if key not in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH")}
        if sys.prefix != sys.base_prefix:
            ours = os.path.join(sys.prefix, "bin")
            env["PATH"] = os.pathsep.join(part for part in env.get("PATH", "").split(os.pathsep)
                                          if part.rstrip("/") != ours)
        subprocess.Popen(["xdg-open", str(path)], env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# One search-index update at a time, whichever window asked for it; two
# writing at once can hit "database is locked".
_SEARCH_INDEX_LOCK = threading.Lock()
LIST_CHECK_SECONDS = 5


class _IndexState:
    """Shared by every index update in this program, whichever window asked."""

    request = 0  # the newest update asked for; older ones still waiting give way
    version = 0  # goes up whenever an update may have changed what is indexed
    guard = threading.Lock()


def _new_index_request():
    with _IndexState.guard:
        _IndexState.request += 1
        return _IndexState.request


def _refresh_search_index(index, request, paths, progress=None):
    """Run one index update, or return None when a newer request replaced it
    while it waited (an update for lists since replaced would undo the newer
    one's work if it ran last)."""
    with _SEARCH_INDEX_LOCK:
        if request != _IndexState.request:
            return None
        changed = True  # assume so if it fails part-way: some lists may be done
        try:
            changed, total = index.refresh(paths, progress=progress)
        finally:
            if changed:
                with _IndexState.guard:
                    _IndexState.version += 1
        return changed, total


def search_index():
    """The saved search index every window shares."""
    return InventorySearchIndex(fp.DEFAULT_CACHE_DIR / "inventory_search.sqlite3")


def list_signatures(file_lists):
    """What tells one saved version of each file list from another."""
    found = []
    for file_list in file_lists:
        try:
            stat = os.stat(file_list.path)
            found.append((str(file_list.path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            found.append((str(file_list.path), None, None))
    return found


def update_search_index_in_background(file_lists):
    """Bring the search index up to date before the search window is opened,
    so searching doesn't start with a wait."""
    paths = [file_list.path for file_list in file_lists]
    if not paths:
        return None

    request = _new_index_request()

    def work():
        try:
            _refresh_search_index(search_index(), request, paths)
        except Exception:
            pass  # the search window reports index problems when it opens
    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread


class HoverHelp:
    """Delayed, readable help that also remains available by clicking ?."""

    def __init__(self, root, widget, title, description):
        self.root = root
        self.widget = widget
        self.title = title
        self.description = description
        self.pending = None
        self.popup = None
        widget.bind("<Enter>", self._schedule, add=True)
        widget.bind("<Leave>", self.hide, add=True)
        widget.bind("<ButtonPress>", self.hide, add=True)

    def _schedule(self, _event=None):
        self.hide()
        self.pending = self.root.after(500, self.show)

    def show(self):
        self.pending = None
        if not self.widget.winfo_exists():
            return
        popup = ctk.CTkToplevel(self.root)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        panel = ctk.CTkFrame(popup, corner_radius=8, border_width=1)
        panel.pack(fill="both", expand=True)
        ctk.CTkLabel(panel, text=self.title, anchor="w",
                     font=ctk.CTkFont(size=16, weight="bold"))\
            .pack(fill="x", padx=14, pady=(11, 3))
        ctk.CTkLabel(panel, text=self.description, anchor="w", justify="left",
                     wraplength=420, font=ctk.CTkFont(size=14))\
            .pack(fill="both", padx=14, pady=(0, 12))
        popup.update_idletasks()
        pointer_x = self.widget.winfo_pointerx()
        pointer_y = self.widget.winfo_pointery()
        x = pointer_x + 16
        y = pointer_y + 16
        if x + popup.winfo_reqwidth() > self.root.winfo_screenwidth() - 12:
            x = pointer_x - popup.winfo_reqwidth() - 16
        if y + popup.winfo_reqheight() > self.root.winfo_screenheight() - 12:
            y = pointer_y - popup.winfo_reqheight() - 16
        popup.geometry(f"+{max(0, x)}+{max(0, y)}")
        popup.deiconify()
        self.popup = popup

    def hide(self, _event=None):
        if self.pending is not None:
            self.root.after_cancel(self.pending)
            self.pending = None
        if self.popup is not None:
            self.popup.destroy()
            self.popup = None


def _scrollbar(parent, orientation, command):
    """A scrollbar in the window's theme, easy to see in light and dark
    mode (Tk's own is pale gray on pale gray)."""
    return ctk.CTkScrollbar(parent, orientation=orientation, command=command,
                            button_color=("gray55", "gray50"),
                            button_hover_color=("gray35", "gray70"))


class FileSearchDialog(ctk.CTkToplevel):
    """Responsive search window backed by a persistent inventory index."""

    RESULT_LIMIT = 500

    def __init__(self, parent, file_lists):
        super().__init__(parent)
        self.parent = parent
        self.file_lists = list(file_lists)
        self.index = search_index()
        self.events = queue.Queue()
        self.results = {}
        self.generation = 0
        self.index_ready = False
        # One index refresh at a time; events from an older refresh are ignored.
        self.index_generation = 0
        self._index_lock = _SEARCH_INDEX_LOCK
        self._list_signatures = None   # the lists as last handed to the index
        self._unsettled_signatures = None
        self._last_list_check = time.monotonic()
        self._searched = False
        self._search_version = None  # index version the shown results came from
        self._index_error = ""  # why the latest index update failed, if it did
        self.pending_search = False
        self.offset = 0
        self._more = False      # the search has more file results to load
        self._loading = False   # a search is running
        self._after_id = None
        self._closed = False

        self.title("Search file lists")
        self.geometry("1120x690")
        self.minsize(760, 480)
        self.transient(parent)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self.protocol("WM_DELETE_WINDOW", self._close)

        self._build_controls()
        self._build_results()
        self._build_details()
        self._after_id = self.after(75, self._drain_events)
        # What an earlier run indexed can be searched straight away; lists
        # that changed since are brought up to date in the background.
        # (Counted off the window's thread, in case the index is busy.)
        threading.Thread(target=lambda: self.events.put(("indexed", self.index.indexed_total())),
                         daemon=True).start()
        self._start_index()

    def _build_controls(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(15, 5))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Search every file list", anchor="w",
                     font=ctk.CTkFont(size=22, weight="bold")).grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(head, text="Search help", width=100, fg_color="transparent",
                      border_width=1, command=self._show_help).grid(row=0, column=1, padx=(8, 0))

        controls = ctk.CTkFrame(self, corner_radius=8)
        controls.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 8))
        controls.grid_columnconfigure(0, weight=1)
        self.query_var = ctk.StringVar()
        self.query_entry = ctk.CTkEntry(
            controls, textvariable=self.query_var, height=38,
            placeholder_text='Optional: filename, folder, place, or filters such as ext:jpg size>10MB stack:stacked',
        )
        self.query_entry.grid(row=0, column=0, sticky="ew", padx=(10, 7), pady=(10, 5))
        self.query_entry.bind("<Return>", self._search)
        self.query_entry.bind("<KeyRelease>", self._schedule_search)
        self.search_btn = ctk.CTkButton(controls, text="Search", width=92, height=38,
                                        command=self._search, state="disabled")
        self.search_btn.grid(row=0, column=1, padx=(0, 7), pady=(10, 5))

        self.list_var = ctk.StringVar(value="All lists")
        self.list_menu = ctk.CTkOptionMenu(controls, variable=self.list_var, values=["All lists"],
                                           width=220, command=lambda _value: self._search())
        self.list_menu.grid(row=1, column=2, padx=(0, 10), pady=(5, 5))
        self._set_list_choices()

        date_row = ctk.CTkFrame(controls, fg_color="transparent")
        date_row.grid(row=1, column=0, columnspan=2, sticky="w", padx=11, pady=(5, 5))
        ctk.CTkLabel(date_row, text="Photos taken on:",
                     font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=(0, 7))
        self.date_var = ctk.StringVar()
        self.date_entry = ctk.CTkEntry(date_row, textvariable=self.date_var, width=150,
                                       placeholder_text="YYYY-MM-DD")
        self.date_entry.grid(row=0, column=1)
        self.date_entry.bind("<Return>", self._search)
        self.date_entry.bind("<KeyRelease>", self._schedule_search)
        self.parent._text_button(date_row, "Today", self._use_today).grid(row=0, column=2, padx=(7, 0))
        self.parent._text_button(date_row, "Clear", self._clear_date).grid(row=0, column=3, padx=(3, 0))
        self.folders_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(date_row, text="Folders only", variable=self.folders_var,
                        command=self._folders_toggled).grid(row=0, column=4, padx=(24, 0))
        self.status_var = ctk.StringVar(value="Preparing the search index…")
        ctk.CTkLabel(controls, textvariable=self.status_var, anchor="w",
                     text_color=("gray35", "gray72"))\
            .grid(row=2, column=0, columnspan=3, sticky="ew", padx=11, pady=(0, 8))
        self.index_var = ctk.StringVar()
        self.index_label = ctk.CTkLabel(controls, textvariable=self.index_var, anchor="w",
                                        text_color=("gray35", "gray72"))
        self.index_label.grid(row=3, column=0, columnspan=3, sticky="ew", padx=11, pady=(0, 8))
        self.index_label.grid_remove()

    def _set_index_note(self, text):
        """Show what the index is doing in the background, or nothing."""
        self.index_var.set(text)
        (self.index_label.grid if text else self.index_label.grid_remove)()

    def _build_results(self):
        frame = ctk.CTkFrame(self, corner_radius=8)
        frame.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 8))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)

        self.tree_style = ttk.Style(self)
        dark = ctk.get_appearance_mode() == "Dark"
        self.tree_style.configure("FileSearch.Treeview",
                                  background="#242424" if dark else "#ffffff",
                                  fieldbackground="#242424" if dark else "#ffffff",
                                  foreground="#f2f2f2" if dark else "#202020")
        self.tree_style.map("FileSearch.Treeview",
                            background=[("selected", "#1f6aa5")],
                            foreground=[("selected", "#ffffff")])
        self.tree_style.configure("FileSearch.Treeview.Heading",
                                  background="#333333" if dark else "#e6e6e6",
                                  foreground="#f2f2f2" if dark else "#202020")
        columns = ("path", "date", "size", "modified", "location", "stack", "list")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings",
                                 style="FileSearch.Treeview", selectmode="browse")
        self.tree_columns = (("path", "File path", 390), ("date", "Photo date", 145),
                             ("size", "Size", 85), ("modified", "Modified", 145),
                             ("location", "Place", 170), ("stack", "Stack", 190),
                             ("list", "File list", 170))
        for column, title, width in self.tree_columns:
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, minwidth=60, stretch=column in ("path", "location"))
        self.tree_font = tkfont.nametofont("TkDefaultFont").copy()
        self.tree_heading_font = tkfont.nametofont("TkHeadingFont").copy()
        self.apply_scale(self.parent.gui_scale)
        vertical = _scrollbar(frame, "vertical", self.tree.yview)
        horizontal = _scrollbar(frame, "horizontal", self.tree.xview)

        def scrolled(first, last):
            vertical.set(first, last)
            # Reaching the end of the results loads the next batch.
            if float(first) > 0 and float(last) >= 0.999:
                self._load_more()
        self.tree.configure(yscrollcommand=scrolled, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=(8, 0))
        vertical.grid(row=0, column=1, sticky="ns", padx=(2, 6), pady=(8, 0))
        horizontal.grid(row=1, column=0, sticky="ew", padx=(8, 0), pady=(2, 6))
        self.tree.bind("<<TreeviewSelect>>", self._selection_changed)
        self.tree.bind("<Double-1>", self._open_containing_folder)
        self.result_menu = Menu(self, tearoff=0, font=self.tree_font,
                                background="#2b2b2b" if dark else "#ffffff",
                                foreground="#f2f2f2" if dark else "#202020",
                                activebackground="#1f6aa5", activeforeground="#ffffff")
        self.result_menu.add_command(label="Copy", command=self._copy_path)
        right_clicks = ("<Button-2>", "<Button-3>", "<Control-Button-1>") \
            if sys.platform == "darwin" else ("<Button-3>",)
        for sequence in right_clicks:
            self.tree.bind(sequence, self._show_result_menu)

    def _show_result_menu(self, event):
        item = self.tree.identify_row(event.y)
        if not item:
            return
        self.tree.selection_set(item)
        self.tree.focus(item)
        try:
            self.result_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.result_menu.grab_release()

    def _build_details(self):
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 14))
        footer.grid_columnconfigure(0, weight=1)
        self.detail_var = ctk.StringVar(value="Select a result to see its GPS coordinates and content hash.")
        ctk.CTkLabel(footer, textvariable=self.detail_var, anchor="w", justify="left",
                     wraplength=720).grid(row=0, column=0, sticky="ew")
        self.more_btn = ctk.CTkButton(footer, text="Load more", width=90,
                                      fg_color="transparent", border_width=1,
                                      command=self._load_more)
        self.more_btn.grid(row=0, column=1, padx=(8, 0))
        self.more_btn.grid_remove()
        self.copy_btn = ctk.CTkButton(footer, text="Copy path", width=90,
                                      fg_color="transparent", border_width=1,
                                      command=self._copy_path, state="disabled")
        self.copy_btn.grid(row=0, column=2, padx=(8, 0))
        self.open_btn = ctk.CTkButton(footer, text="Open folder", width=100,
                                      command=self._open_containing_folder, state="disabled")
        self.open_btn.grid(row=0, column=3, padx=(8, 0))

    def _set_list_choices(self):
        labels = ["All lists"]
        self.list_choices = {"All lists": None}
        for file_list in self.file_lists:
            label = file_list.path.name
            if label in self.list_choices:
                label = str(file_list.path)
            labels.append(label)
            self.list_choices[label] = file_list.path
        self.list_menu.configure(values=labels)
        if self.list_var.get() not in self.list_choices:
            self.list_var.set("All lists")

    def use_lists(self, file_lists):
        """Follow the main window's lists after drives were refreshed."""
        self.file_lists = list(file_lists)
        self._set_list_choices()
        if self.index_ready:
            self._set_index_note("Checking for changed file lists…")
        else:
            self.status_var.set("Updating the search index…")
        self._start_index()

    def _show_help(self):
        messagebox.showinfo(
            "Search file lists",
            "To find photos from one day, enter it in Photos taken on using YYYY-MM-DD. "
            "You can combine the date with a filename, folder, place, or another filter.\n\n"
            "Type ordinary words to search paths, dates, GPS, places, hashes, and list names. "
            "Words are combined with AND. Put phrases in quotes.\n\n"
            "Field filters:\n"
            "  path:vacation   name:IMG_1234   ext:jpg|png\n"
            "  date:2024-06-30   date>=2020-01-01   date:missing\n"
            "  size>10MB   modified<2024-06-30\n"
            "  lat>35   lon<-120   location:\"New York\"\n"
            "  hash:abc123   list:Archive\n"
            "  stack:bracketing   stack:stacked   (Olympus / OM System cameras)\n\n"
            "Use : for contains, = for an exact value, * and ? as wildcards, | for alternatives, "
            "and a leading - to exclude a word or filter (for example -ext:tmp). "
            "Use missing or present to test empty fields.\n\n"
            "Folders only lists the folders that hold matches, with how many files match in "
            "each. Searching for a folder's name lists that folder once, not every folder "
            "inside it.",
            parent=self,
        )

    def _start_index(self):
        self.index_generation = generation = _new_index_request()
        self._index_error = ""
        paths = [file_list.path for file_list in self.file_lists]
        self._list_signatures = list_signatures(self.file_lists)
        self._unsettled_signatures = None

        def progress(number, total, name, rows):
            self.events.put(("progress", generation, number, total, name, rows))

        def work():
            # Two refreshes writing the same index at once can hit "database
            # is locked", so a newer one waits for the one running to finish.
            try:
                outcome = _refresh_search_index(self.index, generation, paths, progress)
            except Exception as exc:
                self.events.put(("error", generation, str(exc)))
                return
            if outcome is not None:  # None: the lists changed again; a newer refresh follows
                self.events.put(("ready", generation, *outcome, list(self.index.skipped)))
        threading.Thread(target=work, daemon=True).start()

    def _check_lists_changed(self):
        """Re-index when a file list was saved again while this window is open
        (a scan finished, say).  A list still being written is left until its
        size and time stop moving."""
        current = list_signatures(self.file_lists)
        if current == self._list_signatures:
            self._unsettled_signatures = None
        elif current == self._unsettled_signatures:
            if self.index_ready:
                self._set_index_note("A file list changed; updating the search index…")
            self._start_index()
        else:
            self._unsettled_signatures = current

    def _schedule_search(self, event=None):
        if event and event.keysym in ("Return", "Up", "Down", "Left", "Right", "Shift_L", "Shift_R"):
            return
        self.generation += 1
        generation = self.generation

        def later():
            if generation == self.generation and (self.query_var.get().strip()
                                                   or self.date_var.get().strip()):
                self._search()
        self.after(350, later)

    def _search(self, _event=None, *, append=False):
        query = self.query_var.get().strip()
        raw_day = self.date_var.get().strip()
        if raw_day:
            try:
                day = normalize_photo_day(raw_day)
            except SearchQueryError as exc:
                self.status_var.set(str(exc))
                return
            if day != raw_day:
                self.date_var.set(day)
            query = f"{query} date:{day}".strip()
        if not self.index_ready:
            self.pending_search = True
            return
        self.generation += 1
        generation = self.generation
        self._searched = True
        self._search_version = _IndexState.version
        if not append:
            self.offset = 0
        selected = self.list_choices.get(self.list_var.get())
        sources = [selected] if selected else None
        self.search_btn.configure(state="disabled")
        self.more_btn.configure(state="disabled")
        self._loading = True
        folders = self.folders_var.get()
        self.status_var.set("Finding folders…" if folders else
                            "Loading more…" if append else "Searching…")

        def work():
            try:
                if folders:
                    found = self.index.search_folders(
                        query, source_paths=sources,
                        should_stop=lambda: generation != self.generation)
                    if found is not None:
                        self.events.put(("folders", generation, query, found))
                    return
                rows, more = self.index.search(query, source_paths=sources,
                                               limit=self.RESULT_LIMIT, offset=self.offset)
                self.events.put(("results", generation, query, append, rows, more))
            except (SearchQueryError, sqlite3.Error) as exc:
                self.events.put(("search_error", generation, str(exc)))
            except Exception as exc:
                self.events.put(("search_error", generation, f"Search failed: {exc}"))
        threading.Thread(target=work, daemon=True).start()

    def _use_today(self):
        self.date_var.set(_datetime.date.today().isoformat())
        self._search()

    def _clear_date(self):
        self.date_var.set("")
        self._search()

    def apply_scale(self, scale):
        """Scale the native ttk result table along with CustomTkinter widgets."""
        if not hasattr(self, "tree"):
            return
        self.tree_font.configure(size=max(10, round(11 * scale)))
        self.tree_heading_font.configure(size=max(10, round(11 * scale)), weight="bold")
        self.tree_style.configure("FileSearch.Treeview", rowheight=round(27 * scale),
                                  font=self.tree_font)
        self.tree_style.configure("FileSearch.Treeview.Heading", font=self.tree_heading_font)
        self._set_columns()

    # In "Folders only" the table shows folders: Place becomes the number of
    # matching files, and the other columns sum up the files in each folder.
    FOLDER_COLUMNS = (("path", "Folder", 390), ("location", "Files", 90),
                      ("size", "Total size", 100), ("date", "Photo dates", 210),
                      ("modified", "Newest change", 145), ("list", "File list", 170))

    def _set_columns(self):
        folders = self.folders_var.get() if hasattr(self, "folders_var") else False
        layout = self.FOLDER_COLUMNS if folders else self.tree_columns
        scale = self.parent.gui_scale
        self.tree.configure(displaycolumns=[column for column, _title, _width in layout])
        for column, title, width in layout:
            self.tree.heading(column, text=title, anchor="e" if column == "location" and folders
                              else "center")
            self.tree.column(column, width=round(width * scale), minwidth=round(60 * scale),
                             anchor="e" if column == "location" and folders else "w",
                             stretch=column == "path" or (column == "location" and not folders))

    def _folders_toggled(self):
        self._set_columns()
        self._clear_results()
        if self._searched or self.query_var.get().strip() or self.date_var.get().strip():
            self._search()

    def _load_more(self):
        if not self._more or self._loading:
            return
        self.offset = len(self.results)
        self._search(append=True)

    @staticmethod
    def _human_size(size):
        return fp._human_size(size) or "0 B"

    @staticmethod
    def _modified(value):
        if not value:
            return ""
        try:
            return _datetime.datetime.fromtimestamp(value / 1_000_000_000).strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError, OverflowError):
            return ""

    def _clear_results(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.results.clear()
        self._more = False
        self.more_btn.grid_remove()
        self.copy_btn.configure(state="disabled")
        self.open_btn.configure(state="disabled")
        self.detail_var.set("Select a folder to see what it holds." if self.folders_var.get()
                            else "Select a result to see its GPS coordinates and content hash.")

    FOLDER_LIMIT = 2000  # more is slow to show and too many to look through

    def _show_folders(self, query, folders):
        self._clear_results()
        for folder in folders[:self.FOLDER_LIMIT]:
            dates = folder.first_date if folder.first_date == folder.last_date \
                else f"{folder.first_date} to {folder.last_date}"
            item = self.tree.insert("", "end", values=(
                folder.filepath, dates, self._human_size(folder.size_bytes),
                self._modified(folder.newest_mtime_ns), f"{folder.files:,}", "",
                Path(folder.source_path).name,
            ))
            self.results[item] = folder
        files = sum(folder.files for folder in folders)
        count = len(folders)
        label = "all indexed files" if not query else "your search"
        shown = (f"; showing the first {self.FOLDER_LIMIT:,}, so add words to narrow it down"
                 if count > self.FOLDER_LIMIT else "")
        self.status_var.set(f"{count:,} folder{'s' if count != 1 else ''} with "
                            f"{files:,} file{'s' if files != 1 else ''} matching {label}{shown}")
        self._loading = False
        self.search_btn.configure(state="normal")

    def _show_results(self, query, append, rows, more):
        if not append:
            self._clear_results()
        for result in rows:
            item = self.tree.insert("", "end", values=(
                result.filepath, result.date_taken, self._human_size(result.size_bytes),
                self._modified(result.mtime_ns), result.location, result.stack,
                Path(result.source_path).name,
            ))
            self.results[item] = result
        count = len(self.results)
        suffix = " so far; scroll to the end for more" if more else ""
        label = "all indexed files" if not query else "your search"
        self.status_var.set(f"{count:,} result{'s' if count != 1 else ''} for {label}{suffix}")
        self._more = more
        self._loading = False
        (self.more_btn.grid if more else self.more_btn.grid_remove)()
        self.more_btn.configure(state="normal")
        self.search_btn.configure(state="normal")

    def _selected_result(self):
        selected = self.tree.selection()
        return self.results.get(selected[0]) if selected else None

    def _selection_changed(self, _event=None):
        result = self._selected_result()
        if not result:
            return
        self.copy_btn.configure(state="normal")
        self.open_btn.configure(state="normal")
        if isinstance(result, FolderResult):
            dates = result.first_date if result.first_date == result.last_date \
                else f"{result.first_date} to {result.last_date}"
            self.detail_var.set(f"{result.files:,} matching files, "
                                f"{self._human_size(result.size_bytes)}"
                                + (f"; photos taken {dates}" if dates else ""))
            return
        gps = ", ".join(part for part in (result.gps_lat, result.gps_lon) if part) or "none"
        content_hash = result.content_hash or "none"
        self.detail_var.set(f"GPS: {gps}    Hash: {content_hash}")
        self.copy_btn.configure(state="normal")
        self.open_btn.configure(state="normal")

    def _resolved_path(self, result):
        local = Path(fp._resolve_local_inventory_path(result.filepath))
        if local.exists():
            return local
        file_list = next((item for item in self.file_lists
                          if str(item.path.resolve()) == result.source_path), None)
        if file_list and file_list.drive and file_list.root:
            key = fp._normalize_cache_key(result.filepath)
            prefix = fp._root_cache_prefix(file_list.root)
            if key.startswith(prefix):
                return Path(file_list.drive.mount, *key[len(prefix):].split("/"))
        return local if local.is_absolute() else None

    def _copy_path(self):
        result = self._selected_result()
        if result:
            self.clipboard_clear()
            self.clipboard_append(result.filepath)
            self.status_var.set("Path copied to the clipboard.")

    def _open_containing_folder(self, _event=None):
        result = self._selected_result()
        if not result:
            return
        path = self._resolved_path(result)
        if path is None:
            messagebox.showinfo("Drive not connected",
                                "The file is in the list, but its containing folder is not available right now.\n\n"
                                + result.filepath, parent=self)
            return
        folder = path if path.is_dir() else path.parent
        if not folder.is_dir():
            # A file inside a .zip: its path continues past the archive.
            archive = next((parent for parent in path.parents if parent.is_file()), None)
            if archive is None:
                messagebox.showinfo("Drive not connected",
                                    "The file is in the list, but its containing folder is not available right now.\n\n"
                                    + result.filepath, parent=self)
                return
            folder = archive.parent
            self.status_var.set(f"That file is inside {archive.name}; showing the folder that "
                                "holds the archive.")
        try:
            open_folder(folder)
        except Exception as exc:
            messagebox.showerror("Open folder", str(exc), parent=self)

    def _drain_events(self):
        if self._closed:
            return
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind in ("progress", "ready", "error") and event[1] != self.index_generation:
                    continue  # from a refresh of lists since replaced
                if kind == "indexed":
                    if event[1] is not None and not self.index_ready:
                        self.index_ready = True
                        self.search_btn.configure(state="normal")
                        self.status_var.set(f"{event[1]:,} files ready to search.")
                        # The update may already have failed; keep saying so.
                        self._set_index_note(self._index_error
                                             or "Checking for changed file lists…")
                        if self.pending_search:
                            self.pending_search = False
                            self._search()
                elif kind == "progress":
                    _, _generation, number, total, name, rows = event
                    detail = f" — {rows:,} files" if rows is not None else ""
                    if not self.index_ready:
                        self.status_var.set(f"Indexing {name} ({number}/{total}){detail}")
                    elif rows is not None:  # this list changed; the rest stay searchable
                        self._set_index_note(f"Updating {name} in the background "
                                             f"({number}/{total}){detail}. Results come from "
                                             "the earlier version until it finishes.")
                elif kind == "ready":
                    _, _generation, changed, total, skipped = event
                    was_ready = self.index_ready
                    self.index_ready = True
                    self.search_btn.configure(state="normal")
                    self._set_index_note("")
                    action = f"Updated {changed} list{'s' if changed != 1 else ''}; " if changed else ""
                    problem = (" Couldn't read " + ", ".join(name for name, _ in skipped) + "."
                               if skipped else "")
                    # Results on screen are out of date if any update (this
                    # one, an earlier one it replaced, or the main window's)
                    # changed the index after they were fetched.
                    stale = self._searched and self._search_version != _IndexState.version
                    if changed or not was_ready or skipped or not self._searched:
                        self.status_var.set(f"{action}{total:,} files ready to search.{problem}")
                    if self.pending_search or stale or (not was_ready and (
                            self.query_var.get().strip() or self.date_var.get().strip())):
                        self.pending_search = False
                        self._search()
                elif kind == "error":
                    self._index_error = "Could not update the search index: " + event[2]
                    if self.index_ready:
                        self._set_index_note(self._index_error)
                    else:
                        self.status_var.set("Could not build the search index: " + event[2])
                elif kind == "results":
                    _, generation, query, append, rows, more = event
                    if generation == self.generation:
                        self._show_results(query, append, rows, more)
                elif kind == "folders":
                    _, generation, query, folders = event
                    if generation == self.generation:
                        self._show_folders(query, folders)
                elif kind == "search_error":
                    _, generation, error = event
                    if generation == self.generation:
                        self._loading = False
                        self.status_var.set(error)
                        self.more_btn.configure(state="normal")
                        self.search_btn.configure(state="normal")
        except queue.Empty:
            pass
        if time.monotonic() - self._last_list_check >= LIST_CHECK_SECONDS:
            self._last_list_check = time.monotonic()
            self._check_lists_changed()
        self._after_id = self.after(75, self._drain_events)

    def _close(self):
        self._closed = True
        if self._after_id is not None:
            self.after_cancel(self._after_id)
        if getattr(self.parent, "search_dialog", None) is self:
            self.parent.search_dialog = None
        self.destroy()


class CoverageDialog(ctk.CTkToplevel):
    """Which photos and videos have a second copy, drive by drive."""

    COLUMNS = (("drive", "Drive", 190), ("connected", "Connected", 90),
               ("role", "Role", 150), ("backblaze", "Backblaze", 125),
               ("total", "Photos & videos", 130), ("only", "Only copy", 120),
               ("weak", "Only on weak drives", 150), ("verdict", "Summary", 330))

    def __init__(self, parent, file_lists):
        super().__init__(parent)
        self.parent = parent
        self.file_lists = list(file_lists)
        self.events = queue.Queue()
        self.drives = []
        self.settings = fp._coverage_settings()
        self.generation = 0
        self._after_id = None
        self._closed = False

        self.title("Coverage")
        self.geometry("1180x720")
        self.minsize(820, 520)
        self.transient(parent)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._build_controls()
        self._build_table()
        self._build_details()
        self._after_id = self.after(75, self._drain_events)
        self._calculate()

    def _build_controls(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(15, 5))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Do your photos and videos have a second copy?", anchor="w",
                     font=ctk.CTkFont(size=22, weight="bold")).grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(head, text="Help", width=80, fg_color="transparent", border_width=1,
                      command=self._show_help).grid(row=0, column=1, padx=(8, 0))
        controls = ctk.CTkFrame(self, corner_radius=8)
        controls.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 8))
        controls.grid_columnconfigure(3, weight=1)
        ctk.CTkLabel(controls, text="Backblaze version history:",
                     font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=(12, 7), pady=10)
        self.history_var = ctk.StringVar(
            value=coverage.BACKBLAZE_HISTORY_CHOICES[self.settings.backblaze_history_days])
        ctk.CTkOptionMenu(controls, variable=self.history_var, width=110,
                          values=list(coverage.BACKBLAZE_HISTORY_CHOICES.values()),
                          command=self._set_history).grid(row=0, column=1, pady=10)
        self.recalc_btn = ctk.CTkButton(controls, text="Recalculate", width=110,
                                        fg_color="transparent", border_width=1,
                                        command=self._calculate)
        self.recalc_btn.grid(row=0, column=2, padx=(12, 0), pady=10)
        self.status_var = ctk.StringVar(value="")
        ctk.CTkLabel(controls, textvariable=self.status_var, anchor="w",
                     text_color=("gray35", "gray72")).grid(row=0, column=3, sticky="ew", padx=12)
        self.progress = ctk.CTkProgressBar(controls, width=160)
        self.progress.grid(row=0, column=4, padx=(0, 12))
        self.progress.set(0)

    def _build_table(self):
        frame = ctk.CTkFrame(self, corner_radius=8)
        frame.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 8))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        style = ttk.Style(self)
        dark = ctk.get_appearance_mode() == "Dark"
        scale = self.parent.gui_scale
        self.tree_font = tkfont.nametofont("TkDefaultFont").copy()
        self.tree_font.configure(size=max(10, round(11 * scale)))
        heading_font = tkfont.nametofont("TkHeadingFont").copy()
        heading_font.configure(size=max(10, round(11 * scale)), weight="bold")
        style.configure("Coverage.Treeview", rowheight=round(27 * scale), font=self.tree_font,
                        background="#242424" if dark else "#ffffff",
                        fieldbackground="#242424" if dark else "#ffffff",
                        foreground="#f2f2f2" if dark else "#202020")
        style.configure("Coverage.Treeview.Heading", font=heading_font,
                        background="#333333" if dark else "#e6e6e6",
                        foreground="#f2f2f2" if dark else "#202020")
        style.map("Coverage.Treeview", background=[("selected", "#1f6aa5")],
                  foreground=[("selected", "#ffffff")])
        self.tree = ttk.Treeview(frame, columns=[c[0] for c in self.COLUMNS], show="headings",
                                 style="Coverage.Treeview", selectmode="browse")
        for column, title, width in self.COLUMNS:
            self.tree.heading(column, text=title)
            self.tree.column(column, width=round(width * scale), minwidth=round(60 * scale),
                             stretch=column == "verdict")
        self.tree.tag_configure("risk", foreground="#e0603a" if dark else "#b3381a")
        self.tree.tag_configure("gone", foreground="gray55")
        vertical = _scrollbar(frame, "vertical", self.tree.yview)
        self.tree.configure(yscrollcommand=vertical.set)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        vertical.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=8)
        self.tree.bind("<<TreeviewSelect>>", self._selection_changed)

    def _build_details(self):
        frame = ctk.CTkFrame(self, corner_radius=8)
        frame.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 14))
        frame.grid_columnconfigure(0, weight=1)
        self.detail = ctk.CTkTextbox(frame, height=190, wrap="none",
                                     font=ctk.CTkFont(family="Courier", size=14))
        self.detail.grid(row=0, column=0, rowspan=4, sticky="nsew", padx=(10, 8), pady=10)
        self.detail.insert("1.0", "Select a drive to see where its only copies are.")
        self.detail.configure(state="disabled")
        side = ctk.CTkFrame(frame, fg_color="transparent")
        side.grid(row=0, column=1, sticky="n", padx=(0, 12), pady=10)
        ctk.CTkLabel(side, text="This drive is:", anchor="w",
                     font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, sticky="w")
        self.role_var = ctk.StringVar(value="")
        self.role_menu = ctk.CTkOptionMenu(
            side, variable=self.role_var, width=200,
            values=[coverage.ROLE_LABELS[r] for r in coverage.ROLES],
            command=self._set_role, state="disabled")
        self.role_menu.grid(row=1, column=0, sticky="w", pady=(3, 8))
        self.backblaze_var = ctk.BooleanVar(value=False)
        self.backblaze_box = ctk.CTkCheckBox(side, text="Backed up by Backblaze",
                                             variable=self.backblaze_var,
                                             command=self._set_backblaze, state="disabled")
        self.backblaze_box.grid(row=2, column=0, sticky="w", pady=(0, 8))
        self.cleanup_btn = ctk.CTkButton(side, text="Clean up duplicates…", width=200,
                                         state="disabled", command=self._open_cleanup)
        self.cleanup_btn.grid(row=3, column=0, sticky="w", pady=(0, 8))
        ctk.CTkButton(side, text="Copy report", width=200, fg_color="transparent",
                      border_width=1, command=self._copy_report).grid(row=4, column=0, sticky="w")

    def _show_help(self):
        messagebox.showinfo(
            "Coverage",
            "This compares all your file lists, including lists of drives that aren't "
            "connected. A photo or video has a copy when another drive has a file with the "
            "same name and size, and the same date taken when both have one. Lightroom's "
            "date prefix (YYYYMMDD-) is ignored.\n\n"
            "Roles:\n"
            "  Automatic: kept, unless its last health check found bad sectors or read "
            "errors.\n"
            "  Keep: a drive you rely on.\n"
            "  Weak: failing or about to be retired. Copies on it don't count as safe.\n"
            "  Gone: wiped or lost. Its list is ignored.\n\n"
            "Backblaze keeps a disconnected drive's backup for about as long as your "
            "version history (30 days, or 1 year with the upgrade). Connect each external "
            "drive before then so Backblaze keeps its backup.\n\n"
            "Placeholders, such as phone photos shown by Windows Phone Link, aren't really "
            "stored on the drive and never count as copies.",
            parent=self,
        )

    def _calculate(self):
        self.generation += 1
        generation = self.generation
        self.recalc_btn.configure(state="disabled")
        self.status_var.set("Comparing every file list…")
        self.progress.set(0)
        settings = fp._coverage_settings()
        health = fp.read_health_log(self.parent.list_dir)
        # Use the main window's latest lists: drives may have been plugged in or
        # out since this window opened, and cleanup needs to know which are connected.
        # An empty list counts: the main window now shows a folder with no lists.
        if getattr(self.parent, "lists", None) is not None:
            self.file_lists = list(self.parent.lists)
        lists = self.file_lists

        def progress(step, total, name):
            self.events.put(("progress", generation, step / total, name))

        def work():
            try:
                drives = coverage.build_coverage(lists, settings, health, progress=progress)
                self.events.put(("done", generation, drives, settings))
            except Exception as exc:
                self.events.put(("error", generation, str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def _render(self):
        selected = self._selected()
        selected_path = selected.list_path if selected else None
        self.tree.delete(*self.tree.get_children())
        for number, drive in enumerate(self.drives):
            if drive.role == coverage.ROLE_GONE:
                tags = ("gone",)
            elif (drive.at_risk.files and drive.role == coverage.ROLE_WEAK) or \
                    drive.backblaze_status in ("expired", "unprotected", "soon"):
                tags = ("risk",)
            else:
                tags = ()
            gone = drive.role == coverage.ROLE_GONE
            values = (
                drive.name,
                "Yes" if drive.connected else "No",
                coverage.ROLE_LABELS[drive.role],
                {"ok": "OK", "soon": "Connect soon", "expired": "Expired",
                 "unprotected": "Not backed up"}.get(drive.backblaze_status,
                                                     "Yes" if drive.setting.backblaze else "No"),
                "" if gone else f"{drive.total.files:,}",
                "" if gone else coverage.human_size(drive.only_copy.bytes) if drive.only_copy.files else "",
                "" if gone else coverage.human_size(drive.weak_only.bytes) if drive.weak_only.files else "",
                coverage.verdict(drive),
            )
            self.tree.insert("", "end", iid=str(number), values=values, tags=tags)
            if drive.list_path == selected_path:
                self.tree.selection_set(str(number))

    def _selected(self):
        selection = self.tree.selection()
        if not selection or int(selection[0]) >= len(self.drives):
            return None
        return self.drives[int(selection[0])]

    def _selection_changed(self, _event=None):
        drive = self._selected()
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        if drive is None:
            self.role_menu.configure(state="disabled")
            self.backblaze_box.configure(state="disabled")
            self.cleanup_btn.configure(state="disabled")
            self.detail.configure(state="disabled")
            return
        self.detail.insert("1.0", coverage.format_drive(drive, folders=20))
        self.detail.configure(state="disabled")
        self.role_var.set(coverage.ROLE_LABELS[drive.setting.role])
        self.backblaze_var.set(drive.setting.backblaze)
        self.role_menu.configure(state="normal")
        self.backblaze_box.configure(state="normal")
        can_clean = drive.connected and drive.role != coverage.ROLE_GONE
        self.cleanup_btn.configure(state="normal" if can_clean else "disabled")

    def _update_settings(self, change):
        """Save one change, keeping changes made meanwhile elsewhere (such as
        the dates drives were last connected)."""
        self.settings = fp.update_coverage_settings(change)
        for drive in self.drives:
            drive.setting = self.settings.drive(drive.list_path.name)

    def _refresh_backblaze(self):
        today = _datetime.date.today()
        for drive in self.drives:
            drive.backblaze_status = drive.backblaze_message = ""
            coverage.backblaze_status(drive, self.settings.backblaze_history_days, today)
        self._render()
        self._selection_changed()

    def _set_history(self, label):
        days = next(d for d, text in coverage.BACKBLAZE_HISTORY_CHOICES.items() if text == label)
        self._update_settings(lambda s: setattr(s, "backblaze_history_days", days))
        self._refresh_backblaze()

    def _set_role(self, label):
        drive = self._selected()
        if drive is None:
            return
        role = next(r for r, text in coverage.ROLE_LABELS.items() if text == label)
        name = drive.list_path.name
        self._update_settings(lambda s: setattr(s.drive(name), "role", role))
        self._calculate()  # which copies count as safe depends on every drive's role

    def _set_backblaze(self):
        drive = self._selected()
        if drive is None:
            return
        name, value = drive.list_path.name, bool(self.backblaze_var.get())
        self._update_settings(lambda s: setattr(s.drive(name), "backblaze", value))
        self._refresh_backblaze()

    def _open_cleanup(self):
        drive = self._selected()
        if drive is None or not drive.connected:
            return
        CleanupDialog(self.parent, self, self.file_lists, drive)

    def _copy_report(self):
        if not self.drives:
            return
        self.clipboard_clear()
        self.clipboard_append(coverage.format_report(self.drives, self.settings, 20))
        self.status_var.set("Report copied.")

    def _drain_events(self):
        if self._closed:
            return
        try:
            while True:
                event = self.events.get_nowait()
                kind, generation = event[0], event[1]
                if generation != self.generation:
                    continue
                if kind == "progress":
                    _, _, fraction, name = event
                    self.progress.set(fraction)
                    self.status_var.set(f"Reading {name}…")
                elif kind == "done":
                    _, _, self.drives, self.settings = event
                    self.progress.set(1)
                    self.recalc_btn.configure(state="normal")
                    retire = [d.name for d in self.drives
                              if d.role == coverage.ROLE_WEAK and d.can_retire]
                    attention = [d for d in self.drives if d.backblaze_status in
                                 ("soon", "expired", "unprotected")]
                    parts = [f"{len(self.drives)} drives compared."]
                    if retire:
                        parts.append("Safe to retire: " + ", ".join(retire) + ".")
                    if attention:
                        parts.append(f"{len(attention)} need attention for Backblaze.")
                    self.status_var.set(" ".join(parts))
                    self._render()
                    self._selection_changed()
                elif kind == "error":
                    self.recalc_btn.configure(state="normal")
                    self.status_var.set("Couldn't compare the lists: " + event[2])
        except queue.Empty:
            pass
        self._after_id = self.after(100, self._drain_events)

    def _close(self):
        self._closed = True
        if self._after_id is not None:
            self.after_cancel(self._after_id)
        if getattr(self.parent, "coverage_dialog", None) is self:
            self.parent.coverage_dialog = None
        self.destroy()


class CleanupDialog(ctk.CTkToplevel):
    """Find and delete photos and videos already kept on another drive."""

    def __init__(self, parent, coverage_dialog, file_lists, target):
        super().__init__(parent)
        self.parent = parent
        self.coverage_dialog = coverage_dialog
        self.file_lists = list(file_lists)
        self.target = target  # DriveCoverage of the drive to clean
        self.events = queue.Queue()
        self.plan = None
        self.stop_event = threading.Event()
        self.busy = False
        self.log_path = None
        self._after_id = None
        self._closed = False
        self.settings = fp._coverage_settings()

        self.title(f"Clean up duplicates on {target.name}")
        self.geometry("980x600")
        self.minsize(720, 520)
        self.transient(parent)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._build()
        self._after_id = self.after(100, self._drain_events)

    def _build(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(15, 5))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text=f"Clean up duplicates on {self.target.name}", anchor="w",
                     font=ctk.CTkFont(size=22, weight="bold")).grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            head, anchor="w", justify="left", wraplength=920,
            text=("Finds photos and videos on this drive that already have a copy in a keep "
                  "folder on another drive you keep (same name, size and date taken). Files "
                  "in keep folders on this drive are never deleted. Nothing is deleted until "
                  "you confirm, and every deletion is logged."),
            text_color=("gray35", "gray72")).grid(row=1, column=0, sticky="ew", pady=(4, 0))

        options = ctk.CTkFrame(self, corner_radius=8)
        options.grid(row=1, column=0, sticky="ew", padx=16, pady=(6, 8))
        options.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(options, text="Keep folders:", font=ctk.CTkFont(weight="bold"))\
            .grid(row=0, column=0, sticky="w", padx=(12, 7), pady=(10, 4))
        self.folders_var = ctk.StringVar(value=", ".join(self.settings.keep_folders))
        ctk.CTkEntry(options, textvariable=self.folders_var,
                     placeholder_text="Top-level folders, separated by commas")\
            .grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=(10, 4))
        self.library_var = ctk.BooleanVar(value=self.settings.keep_library_days)
        ctk.CTkCheckBox(options, text="Library day folders (YYYY/YYYY-MM-DD) are keep folders too",
                        variable=self.library_var)\
            .grid(row=1, column=0, columnspan=2, sticky="w", padx=12, pady=4)
        self.trust_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(options, variable=self.trust_var,
                        text="Trust the file lists for drives that aren't connected "
                             "(otherwise those duplicates are kept)")\
            .grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=4)
        self.compare_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(options, variable=self.compare_var,
                        text="Also compare a few small pieces of each file with its kept copy "
                             "(a little slower)")\
            .grid(row=3, column=0, columnspan=2, sticky="w", padx=12, pady=(4, 10))

        frame = ctk.CTkFrame(self, corner_radius=8)
        frame.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 8))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(frame, columns=("folder", "files", "size"), show="headings",
                                 style="Coverage.Treeview", selectmode="none")
        scale = self.parent.gui_scale
        for column, title, width in (("folder", "Folder", 560), ("files", "Files", 110),
                                     ("size", "Size", 120)):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=round(width * scale), stretch=column == "folder")
        vertical = _scrollbar(frame, "vertical", self.tree.yview)
        self.tree.configure(yscrollcommand=vertical.set)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        vertical.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=8)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 14))
        footer.grid_columnconfigure(0, weight=1)
        self.status_var = ctk.StringVar(value="Choose Find duplicates to see what can be deleted.")
        ctk.CTkLabel(footer, textvariable=self.status_var, anchor="w", justify="left",
                     wraplength=900).grid(row=0, column=0, columnspan=5, sticky="ew",
                                          pady=(0, 8))
        self.progress = ctk.CTkProgressBar(footer)
        self.progress.grid(row=1, column=0, sticky="ew")
        self.progress.set(0)
        self.find_btn = ctk.CTkButton(footer, text="Find duplicates", width=130,
                                      command=self._find)
        self.find_btn.grid(row=1, column=2, padx=(8, 0))
        self.delete_btn = ctk.CTkButton(footer, text="Delete", width=150, state="disabled",
                                        fg_color=("#b3381a", "#a33a20"),
                                        hover_color=("#8f2c14", "#7f2d18"),
                                        command=self._delete)
        self.delete_btn.grid(row=1, column=3, padx=(8, 0))
        self.log_btn = ctk.CTkButton(footer, text="Open log", width=90, fg_color="transparent",
                                     border_width=1, command=self._open_log)
        self.log_btn.grid(row=1, column=4, padx=(8, 0))
        self.log_btn.grid_remove()

    def _keep_folders(self):
        return [f.strip() for f in self.folders_var.get().split(",") if f.strip()]

    def _find(self):
        folders, library_days = self._keep_folders(), bool(self.library_var.get())

        def change(saved):
            saved.keep_folders = folders
            saved.keep_library_days = library_days

        self.settings = fp.update_coverage_settings(change)
        self.busy = True
        self.plan = None
        self.find_btn.configure(state="disabled")
        self.delete_btn.configure(state="disabled", text="Delete")
        self.tree.delete(*self.tree.get_children())
        self.status_var.set("Reading the file lists…")
        self.progress.set(0)
        settings = self.settings
        health = fp.read_health_log(self.parent.list_dir)

        def progress(step, total, name):
            self.events.put(("progress", step / total, f"Reading {name}…"))

        def work():
            try:
                plan = coverage.plan_cleanup(
                    self.file_lists, settings, self.target.list_path,
                    settings.keep_folders, settings.keep_library_days, health, progress)
                self.events.put(("plan", plan))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def _show_plan(self, plan):
        self.plan = plan
        for folder, size in plan.folders.most_common():
            self.tree.insert("", "end", values=(folder, f"{plan.folder_files[folder]:,}",
                                                coverage.human_size(size)))
        count = len(plan.items)
        if not count:
            self.status_var.set("No duplicates found. Every photo and video here is either in "
                                "a keep folder or has no kept copy elsewhere.")
            return
        offline = sum(1 for item in plan.items if not any(c for _, c in item.kept))
        note = (f" {offline:,} of them have their kept copy on a drive that isn't connected."
                if offline else "")
        self.status_var.set(
            f"{count:,} duplicates ({coverage.human_size(plan.total_bytes)}) can be deleted. "
            f"{plan.kept_without_copy:,} photos and videos have no kept copy and stay.{note}")
        self.delete_btn.configure(state="normal", text=f"Delete {count:,} files")

    def _delete(self):
        plan = self.plan
        if not plan or not plan.items:
            return
        trust = bool(self.trust_var.get())
        compare = bool(self.compare_var.get())
        if not messagebox.askyesno(
                "Delete duplicates",
                f"Permanently delete {len(plan.items):,} files "
                f"({coverage.human_size(plan.total_bytes)}) from {self.target.name}?\n\n"
                "Each one's kept copy is checked on disk first when its drive is connected"
                + ("; for drives that aren't connected, the file lists are trusted."
                   if trust else "; duplicates whose kept copy's drive isn't connected are kept.")
                + (" Each file's contents are also compared with its copy." if compare else "")
                + " Deleted files don't go to the trash.",
                icon="warning", parent=self):
            return
        stamp = _datetime.date.today().isoformat()
        name = re.sub(r'[<>:"/\\|?*]', "_", self.target.name)
        self.log_path = (Path(self.parent.list_dir) / "Cleanup logs"
                         / f"{name} duplicates - delete log {stamp}.tsv")
        self.busy = True
        self.stop_event.clear()
        self.find_btn.configure(state="disabled")
        self.delete_btn.configure(text="Stop", command=self.stop_event.set)
        self.status_var.set("Deleting…")

        def progress(done, total, freed):
            self.events.put(("progress", done / total,
                             f"Deleted {done:,} of {total:,} — {coverage.human_size(freed)} freed"))

        def work():
            try:
                counts, freed = coverage.run_cleanup(plan, self.log_path, trust, progress,
                                                     self.stop_event, compare_contents=compare)
                self.events.put(("deleted", counts, freed))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def _open_log(self):
        if self.log_path:
            self.parent._open_path(self.log_path.parent)

    def _drain_events(self):
        if self._closed:
            return
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "progress":
                    self.progress.set(event[1])
                    self.status_var.set(event[2])
                elif kind == "plan":
                    self.busy = False
                    self.progress.set(1)
                    self.find_btn.configure(state="normal")
                    self._show_plan(event[1])
                elif kind == "deleted":
                    _, counts, freed = event
                    self.busy = False
                    self.plan = None
                    self.find_btn.configure(state="normal")
                    self.delete_btn.configure(state="disabled", text="Delete",
                                              command=self._delete)
                    parts = [f"{v:,} {k}" for k, v in counts.most_common()]
                    stopped = " Stopped before the end." if self.stop_event.is_set() else ""
                    self.status_var.set(f"Done: {coverage.human_size(freed)} freed. "
                                        + "; ".join(parts) + "." + stopped
                                        + " Update this drive's file list to keep it current.")
                    self.log_btn.grid()
                elif kind == "error":
                    self.busy = False
                    self.find_btn.configure(state="normal")
                    self.delete_btn.configure(state="disabled", text="Delete",
                                              command=self._delete)
                    self.status_var.set("Problem: " + event[1])
        except queue.Empty:
            pass
        self._after_id = self.after(100, self._drain_events)

    def _close(self):
        if self.busy and self.plan is not None:
            if not messagebox.askyesno("Stop deleting?", "Stop deleting and close?", parent=self):
                return
        self.stop_event.set()
        self._closed = True
        if self._after_id is not None:
            self.after_cancel(self._after_id)
        self.destroy()


class FindPhotoDatesGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        config = fp.load_config()
        if sys.platform.startswith("linux"):
            screen_height = self.winfo_screenheight()
            default_scale = 1.75 if screen_height >= 1800 else (1.5 if screen_height >= 1200 else 1.25)
        else:
            # CustomTkinter already follows native DPI scaling on macOS and Windows.
            default_scale = 1.0
        try:
            requested_scale = float(config.get("gui_scale", default_scale))
        except (TypeError, ValueError):
            requested_scale = default_scale
        self.gui_scale_label = _closest_scale_label(requested_scale)
        self.gui_scale = GUI_SCALE_CHOICES[self.gui_scale_label]
        ctk.set_widget_scaling(self.gui_scale)
        ctk.set_window_scaling(self.gui_scale)
        self._dialog_font_sizes = {}
        self._scale_dialog_fonts()
        self.title(APP_TITLE)
        self.geometry("820x560")
        self.minsize(700, 480)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.settings = fp._InteractiveSettings()
        self.list_dir = fp.default_inventory_dir()
        self.drives = []
        self.lists = []
        self.list_for = {}
        self.selected = {}
        self.events = queue.Queue()
        self.process = None
        self.running = False
        self.stopping = False
        self.refreshing = False
        self.advanced_open = False
        self._log_open = False
        self._lines = []
        self._summary = {}
        self._pending_scans = []
        self._job_label = ""
        self._last_report_dir = None
        self.exiftool_ready = False
        self.unmounted = []  # plugged-in drives the system didn't mount
        # A drive being mounted or unmounted: no jobs start until the drive
        # list has been refreshed afterwards.
        self.drive_busy = None
        self._drive_op = 0              # counts mounts and unmounts
        self._drive_op_finished = True  # whether the latest one has reported back
        self._advanced_auto_height = None
        self._compact_height = None
        self._hover_help = []
        self.search_dialog = None
        self.coverage_dialog = None

        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.grid(row=0, column=0, sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        self._build_header()
        self._build_drives()
        self._build_actions()
        self._build_activity()
        self._build_advanced()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(100, self._drain_events)
        self.after(150, self.refresh)

    def _text_button(self, parent, title, command):
        return ctk.CTkButton(parent, text=title, command=command, width=1,
                             fg_color="transparent", text_color=("#1f6aa5", "#5aa7df"),
                             hover_color=("gray82", "gray28"))

    def _label(self, parent, text, row, *, bold=False, color=None, padx=0, pady=0):
        label = ctk.CTkLabel(parent, text=text, anchor="w", justify="left",
                             font=ctk.CTkFont(weight="bold") if bold else None,
                             text_color=color)
        label.grid(row=row, column=0, sticky="ew", padx=padx, pady=pady)
        return label

    def _build_header(self):
        frame = ctk.CTkFrame(self.body, fg_color="transparent")
        frame.grid(row=0, column=0, sticky="ew", padx=22, pady=(18, 10))
        frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(frame, text="File lists for your drives", anchor="w",
                     font=ctk.CTkFont(size=27, weight="bold")).grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(frame, text=APP_TITLE, text_color=("gray45", "gray62"),
                     font=ctk.CTkFont(size=14)).grid(row=0, column=1, sticky="e")
        self.scale_var = ctk.StringVar(value=self.gui_scale_label)
        ctk.CTkOptionMenu(frame, variable=self.scale_var, values=list(GUI_SCALE_CHOICES),
                          width=125, command=self._set_gui_scale).grid(row=0, column=2,
                                                                       sticky="e", padx=(10, 0))
        ctk.CTkLabel(frame, text="Choose a connected drive to make or update its file list. "
                     "Later scans read only new or changed files. Lists stay in Documents so you "
                     "can see what is on drives even while they are unplugged.",
                     wraplength=740, anchor="w", justify="left",
                     font=ctk.CTkFont(size=14),
                     text_color=("gray35", "gray70")).grid(row=1, column=0, columnspan=3, sticky="ew", pady=(5, 0))
        self.exif_var = ctk.StringVar(value="Checking ExifTool…")
        exif_row = ctk.CTkFrame(frame, fg_color="transparent")
        exif_row.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        exif_row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(exif_row, textvariable=self.exif_var, anchor="w",
                     text_color=("gray35", "gray72"), font=ctk.CTkFont(size=14)).grid(row=0, column=0, sticky="ew")
        # Shown only once the check finds ExifTool missing or out of date.
        self.exif_link = self._text_button(
            exif_row, "Get ExifTool", lambda: __import__("webbrowser").open("https://exiftool.org/"))

    def _scale_dialog_fonts(self):
        """Message boxes on Linux are drawn by Tk in its own fonts, which
        CustomTkinter's scaling doesn't touch; without this they stay tiny
        on a high-resolution screen."""
        if not sys.platform.startswith("linux"):
            return  # Windows and macOS show their own, already scaled, dialogs
        for name in ("TkDefaultFont", "TkCaptionFont", "TkTextFont", "TkMenuFont"):
            try:
                font = tkfont.nametofont(name)
                base = self._dialog_font_sizes.setdefault(name, font.cget("size"))
                size = round(base * self.gui_scale)  # negative sizes are pixels
                font.configure(size=size or (-1 if base < 0 else 1))
            except TclError:
                pass

    def _set_gui_scale(self, label):
        scale = GUI_SCALE_CHOICES[label]
        self.gui_scale_label = label
        self.gui_scale = scale
        ctk.set_widget_scaling(scale)
        ctk.set_window_scaling(scale)
        self._scale_dialog_fonts()
        if self.search_dialog is not None and self.search_dialog.winfo_exists():
            self.search_dialog.apply_scale(scale)
        fp.update_config(lambda config: config.update(gui_scale=scale))

    def _build_drives(self):
        outer = ctk.CTkFrame(self.body, corner_radius=10)
        outer.grid(row=1, column=0, sticky="ew", padx=22, pady=(2, 10))
        outer.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(outer, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 0))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Connected drives", anchor="w",
                     font=ctk.CTkFont(size=18, weight="bold")).grid(row=0, column=0, sticky="ew")
        self.refresh_btn = self._text_button(head, "Refresh drives", self.refresh)
        self.refresh_btn.grid(row=0, column=1)
        self.drive_frame = ctk.CTkFrame(outer, fg_color="transparent")
        self.drive_frame.grid(row=1, column=0, sticky="ew", padx=16, pady=(8, 10))
        self.drive_frame.grid_columnconfigure(0, weight=1)
        self.drive_status = ctk.StringVar(value="Looking for drives and file lists…")
        ctk.CTkLabel(self.drive_frame, textvariable=self.drive_status, anchor="w",
                     font=ctk.CTkFont(size=15)).grid(row=0, column=0, sticky="ew")
        footer = ctk.CTkFrame(outer, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 12))
        footer.grid_columnconfigure(0, weight=1)
        self.list_dir_var = ctk.StringVar(value=f"File lists: {self.list_dir}")
        ctk.CTkLabel(footer, textvariable=self.list_dir_var, anchor="w",
                     text_color=("gray35", "gray72"), wraplength=530,
                     font=ctk.CTkFont(size=14)).grid(row=0, column=0, sticky="ew")
        self._text_button(footer, "Open folder", lambda: self._open_path(self.list_dir)).grid(row=0, column=1)
        self._text_button(footer, "Search files", self._show_search).grid(row=0, column=2)
        self._text_button(footer, "Coverage", self._show_coverage).grid(row=0, column=3)
        self._text_button(footer, "Show all lists", self._show_lists).grid(row=0, column=4)

    def _build_actions(self):
        frame = ctk.CTkFrame(self.body, fg_color="transparent")
        frame.grid(row=2, column=0, sticky="ew", padx=22, pady=(3, 12))
        frame.grid_columnconfigure(2, weight=1)
        self.start_btn = ctk.CTkButton(frame, text="Make or update selected lists", height=40,
                                       command=self._start_selected)
        self.start_btn.grid(row=0, column=0, sticky="w")
        self.health_btn = ctk.CTkButton(frame, text="Check drive health", height=40,
                                        fg_color="transparent", border_width=1,
                                        text_color=("gray15", "gray88"), command=self._health)
        self.health_btn.grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.advanced_btn = self._text_button(frame, "Advanced ▾", self._toggle_advanced)
        self.advanced_btn.grid(row=0, column=3, sticky="e")

    def _build_activity(self):
        frame = ctk.CTkFrame(self.body, corner_radius=10)
        frame.grid(row=3, column=0, sticky="ew", padx=22, pady=(0, 12))
        frame.grid_columnconfigure(0, weight=1)
        self.activity = frame
        self.activity.grid_remove()
        self.phase_var = ctk.StringVar(value="Preparing…")
        ctk.CTkLabel(frame, textvariable=self.phase_var, anchor="w",
                     font=ctk.CTkFont(size=18, weight="bold")).grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 0))
        self.progress = ctk.CTkProgressBar(frame, mode="indeterminate")
        self.progress.grid(row=1, column=0, sticky="ew", padx=16, pady=(12, 0))
        self.detail_var = ctk.StringVar(value="")
        ctk.CTkLabel(frame, textvariable=self.detail_var, anchor="w", justify="left",
                     wraplength=740, font=ctk.CTkFont(size=14)).grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 0))
        buttons = ctk.CTkFrame(frame, fg_color="transparent")
        buttons.grid(row=3, column=0, sticky="ew", padx=12, pady=(7, 9))
        self.stop_btn = self._text_button(buttons, "Stop and save progress", self._stop)
        self.stop_btn.grid(row=0, column=0)
        self.log_btn = self._text_button(buttons, "Show details", self._toggle_log)
        self.log_btn.grid(row=0, column=1, padx=(5, 0))
        self.reports_btn = self._text_button(buttons, "Open backup reports",
                                             lambda: self._open_path(self._last_report_dir))
        self.reports_btn.grid(row=0, column=2, padx=(5, 0))
        self.reports_btn.grid_remove()
        self.log = ctk.CTkTextbox(frame, height=210, font=ctk.CTkFont(family="Courier", size=14))
        self.log.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 14))
        self.log.grid_remove()

    def _build_advanced(self):
        self.advanced = ctk.CTkFrame(self.body, corner_radius=10)
        self.advanced.grid(row=4, column=0, sticky="ew", padx=22, pady=(0, 22))
        self.advanced.grid_columnconfigure(0, weight=1)
        self.advanced.grid_remove()
        ctk.CTkLabel(self.advanced, text="Advanced options", anchor="w",
                     font=ctk.CTkFont(size=18, weight="bold")).grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 3))
        self.media_var = ctk.BooleanVar(value=False)
        self.locate_var = ctk.BooleanVar(value=False)
        self.retry_var = ctk.BooleanVar(value=False)
        self.hash_var = ctk.StringVar(value="Off")
        self.workers_var = ctk.StringVar(value="4")
        self.min_var = ctk.StringVar(value=str(fp.MIN_EXIFTOOL_IMAGE_BYTES // 1000))
        self.path_var = ctk.StringVar(value="Automatic")
        self.archives_var = ctk.StringVar(value=fp._ARCHIVE_LABELS[fp.DEFAULT_ARCHIVE_MODE])
        options = ctk.CTkFrame(self.advanced, fg_color="transparent")
        options.grid(row=1, column=0, sticky="ew", padx=16)
        options.grid_columnconfigure(1, weight=1)
        checkbox = ctk.CTkCheckBox(options, text="Photos and videos only", variable=self.media_var)
        checkbox.grid(row=0, column=0, sticky="w", pady=6)
        self._attach_help(checkbox, "include")
        self._help(options, "include", 0)
        checkbox = ctk.CTkCheckBox(options, text="Look up place names from GPS", variable=self.locate_var)
        checkbox.grid(row=1, column=0, sticky="w", pady=6)
        self._attach_help(checkbox, "locate")
        self._help(options, "locate", 1)
        checkbox = ctk.CTkCheckBox(options, text="Re-check files with no photo date", variable=self.retry_var)
        checkbox.grid(row=2, column=0, sticky="w", pady=6)
        self._attach_help(checkbox, "retry")
        self._help(options, "retry", 2)
        self._option_row(options, 3, "File fingerprints", self.hash_var, ("Off", "Quick", "Full"), "hash")
        self._option_row(options, 4, "Path style", self.path_var, ("Automatic", "Linux style", "Windows style"), "path_style")
        self._option_row(options, 5, "Look inside archives", self.archives_var,
                         tuple(fp._ARCHIVE_LABELS.values()), "archives")
        self._entry_row(options, 6, "Photos read at the same time (1–32)", self.workers_var, "workers")
        self._entry_row(options, 7, "Skip images smaller than (KB; 0 = off)", self.min_var, "min_size")
        ctk.CTkLabel(self.advanced, text="Other tasks", anchor="w",
                     font=ctk.CTkFont(size=16, weight="bold")).grid(row=2, column=0, sticky="ew", padx=16, pady=(16, 5))
        tasks = ctk.CTkFrame(self.advanced, fg_color="transparent")
        tasks.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 15))
        for row, (title, action, help_key) in enumerate((
            ("Make a list for one folder…", self._folder_scan, "folder_scan"),
            ("Check which files are backed up…", self._backup_check, "check"),
            ("Import file lists…", self._import_lists, "import"),
            ("Change file lists folder…", self._change_list_dir, "list_dir"),
            ("Copy equivalent command", self._copy_command, "command"),
            ("Quick drive health check", lambda: self._health(False), None),
            ("Extended drive health check", lambda: self._health(True), None),
            ("Show drive health history", self._health_history, None),
        )):
            button = self._text_button(tasks, title, action)
            button.grid(row=row, column=0, sticky="w", pady=1)
            if help_key:
                self._attach_help(button, help_key)
                self._help(tasks, help_key, row)

    def _attach_help(self, widget, key):
        title, description = fp._ADVANCED_HELP[key]
        self._hover_help.append(HoverHelp(self, widget, title, description))

    def _help(self, parent, key, row):
        title, description = fp._ADVANCED_HELP[key]
        button = self._text_button(parent, "?", lambda: messagebox.showinfo(title, description))
        button.grid(row=row, column=2, padx=(8, 0))
        self._attach_help(button, key)

    def _option_row(self, parent, row, title, variable, values, key):
        label = ctk.CTkLabel(parent, text=title, anchor="w")
        label.grid(row=row, column=0, sticky="w", pady=6)
        control = ctk.CTkOptionMenu(parent, variable=variable, values=list(values), width=165)
        control.grid(row=row, column=1, sticky="w", padx=(16, 0))
        self._attach_help(label, key)
        self._attach_help(control, key)
        self._help(parent, key, row)

    def _entry_row(self, parent, row, title, variable, key):
        label = ctk.CTkLabel(parent, text=title, anchor="w")
        label.grid(row=row, column=0, sticky="w", pady=6)
        control = ctk.CTkEntry(parent, textvariable=variable, width=90)
        control.grid(row=row, column=1, sticky="w", padx=(16, 0))
        self._attach_help(label, key)
        self._attach_help(control, key)
        self._help(parent, key, row)

    def _toggle_advanced(self):
        for help_popup in self._hover_help:
            help_popup.hide()
        self.advanced_open = not self.advanced_open
        self.advanced_btn.configure(text="Advanced ▴" if self.advanced_open else "Advanced ▾")
        (self.advanced.grid if self.advanced_open else self.advanced.grid_remove)()
        # CTk's geometry() uses logical dimensions; winfo_height() reports
        # scaled pixels on high DPI desktops, so compare logical values here.
        width, height = (int(part) for part in self.geometry().split("+")[0].split("x"))
        if self.advanced_open:
            target = min(760, self._reverse_window_scaling(self.winfo_screenheight()) - 80)
            if height < target - 20:
                self._compact_height = height
                self._advanced_auto_height = target
                self.geometry(f"{width}x{target}")
        elif self._advanced_auto_height is not None:
            if abs(height - self._advanced_auto_height) < 40:
                self.geometry(f"{width}x{self._compact_height}")
            self._advanced_auto_height = None

    def _toggle_log(self):
        self._log_open = not self._log_open
        self.log_btn.configure(text="Hide details" if self._log_open else "Show details")
        (self.log.grid if self._log_open else self.log.grid_remove)()

    def refresh(self):
        if self.running or self.refreshing:
            return
        self.refreshing = True
        self.refresh_btn.configure(state="disabled")
        self.drive_status.set("Looking for drives and file lists…")
        # Only a refresh started after the mount or unmount reported back ends it.
        busy = (self._drive_op, self._drive_op_finished)

        def work():
            try:
                drives = fp.detect_drives()
                lists = fp.find_file_lists(self.list_dir)
                fp.match_lists_to_drives(lists, drives)
                fp.remember_connected_drives(lists)
                try:
                    unmounted = fp.unmounted_drives()
                except Exception:
                    unmounted = []  # only a convenience; never stop a refresh
                self.events.put(("refresh", drives, lists, None, unmounted, busy))
            except Exception as exc:
                self.events.put(("refresh", [], [], str(exc), [], busy))
        threading.Thread(target=work, daemon=True).start()
        threading.Thread(target=self._check_exiftool, daemon=True).start()

    def _check_exiftool(self):
        self.events.put(("exiftool", fp._exiftool_status(fp._exiftool_version())))

    def _render_drives(self):
        for child in self.drive_frame.winfo_children():
            child.destroy()
        self.selected.clear()
        if not self.drives:
            self.drive_status.set("No connected drives found. Plug one in and refresh, or use Advanced to list a folder.")
            ctk.CTkLabel(self.drive_frame, textvariable=self.drive_status, anchor="w",
                         wraplength=720, font=ctk.CTkFont(size=15)).grid(row=0, column=0, sticky="ew")
            self._render_unmounted(1)
            return
        widths = (32, 220, 78, 100, 145)
        header = ctk.CTkFrame(self.drive_frame, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        header.grid_columnconfigure(1, weight=1)
        for column, title in enumerate(("", "Drive", "Size", "Filesystem", "Updated")):
            ctk.CTkLabel(header, text=title, width=widths[column], anchor="w",
                         font=ctk.CTkFont(size=14, weight="bold"))\
                .grid(row=0, column=column, sticky="ew", padx=(7, 0))
        unmount_width = 96
        ctk.CTkLabel(header, text="", width=unmount_width)\
            .grid(row=0, column=5, padx=(7, 8))  # keeps the titles over their columns
        for index, drive in enumerate(self.drives):
            var = ctk.BooleanVar(value=False)
            self.selected[drive.mount] = var
            row = ctk.CTkFrame(self.drive_frame, corner_radius=5, border_width=1,
                               border_color=("gray82", "gray32"),
                               fg_color=("gray94", "gray19") if index % 2 == 0
                               else ("gray89", "gray24"))
            row.grid(row=index + 1, column=0, sticky="ew", pady=2)
            row.grid_columnconfigure(1, weight=1)
            ctk.CTkCheckBox(row, text="", variable=var, width=widths[0])\
                .grid(row=0, column=0, rowspan=3, sticky="w", padx=(8, 0))
            fl = self.list_for.get(drive.mount)
            if fl:
                try:
                    updated = fp._last_updated_label(fl.path.stat().st_mtime)
                except OSError:
                    updated = "Unknown"
                description = fl.path.name
            else:
                updated = "Never"
                description = f"New: {fp.canonical_list_name(drive)}"
            fields = (drive.display_name, fp._human_size(drive.size_bytes) or "—",
                      drive.fstype or "—", updated)
            for column, value in enumerate(fields, 1):
                ctk.CTkLabel(row, text=value, width=widths[column], anchor="w",
                             justify="left", wraplength=widths[column],
                             font=ctk.CTkFont(size=15, weight="bold") if column == 1
                             else ctk.CTkFont(size=14))\
                    .grid(row=0, column=column, sticky="ew", padx=(7, 0), pady=(9, 0))
            for line, value in enumerate((f"Location: {drive.mount}",
                                          f"File list: {description}"), 1):
                ctk.CTkLabel(row, text=value, anchor="w", justify="left",
                             wraplength=680, font=ctk.CTkFont(size=14),
                             text_color=("gray32", "gray76"))\
                    .grid(row=line, column=1, columnspan=4, sticky="ew", padx=(7, 8),
                          pady=(0, 8 if line == 2 else 0))
            if unmount_command(drive.mount):
                button = ctk.CTkButton(row, text="Unmount", width=unmount_width,
                                       fg_color="transparent", border_width=1,
                                       text_color=("gray10", "gray90"))
                button.configure(command=lambda drive=drive, button=button:
                                 self._unmount(drive, button))
                button.grid(row=0, column=5, rowspan=3, padx=(7, 8))
            else:
                ctk.CTkLabel(row, text="", width=unmount_width)\
                    .grid(row=0, column=5, rowspan=3, padx=(7, 8))
        self.drive_status.set("")
        self._render_unmounted(len(self.drives) + 1)

    def _render_unmounted(self, first_row):
        """Plugged-in drives that aren't mounted, so they can't be scanned yet;
        a red ✗ marks one the system refused because it wasn't shut down cleanly."""
        for index, drive in enumerate(self.unmounted):
            row = ctk.CTkFrame(self.drive_frame, corner_radius=5, border_width=1,
                               border_color=("#d9a49a", "#7a3b30") if drive.dirty
                               else ("gray82", "gray32"),
                               fg_color=("gray94", "gray19"))
            row.grid(row=first_row + index, column=0, sticky="ew", pady=2)
            row.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(row, text="✗" if drive.dirty else "○", width=32,
                         text_color=("#c0392b", "#ff6b5b") if drive.dirty else ("gray45", "gray62"),
                         font=ctk.CTkFont(size=22, weight="bold"))\
                .grid(row=0, column=0, rowspan=2, padx=(8, 0))
            size = fp._human_size(drive.size_bytes)
            title = f"{drive.display_name}  ({', '.join(filter(None, (size, drive.fstype)))})"
            ctk.CTkLabel(row, text=title, anchor="w", font=ctk.CTkFont(size=15, weight="bold"))\
                .grid(row=0, column=1, sticky="ew", padx=(7, 0), pady=(9, 0))
            if drive.dirty:
                note = ("Plugged in, but not mounted: it wasn't shut down cleanly, so it's marked "
                        "as needing a check. Mount it read-only to scan it. To fix it, plug it "
                        "into Windows and run chkdsk on it (chkdsk X: /f, with its drive letter).")
            else:
                note = f"Plugged in, but not mounted ({drive.device}). Mount it to scan it."
            if not fp.mount_command(drive.device):
                note += (" (udisksctl isn't installed, so mount it from your file manager, or with "
                         f"sudo mount{' -o ro' if drive.dirty else ''} {drive.device} <folder>.)")
            ctk.CTkLabel(row, text=note, anchor="w", justify="left", wraplength=640,
                         font=ctk.CTkFont(size=14), text_color=("gray32", "gray76"))\
                .grid(row=1, column=1, sticky="ew", padx=(7, 8), pady=(0, 8))
            command = fp.mount_command(drive.device, read_only=drive.dirty)
            if command:
                button = ctk.CTkButton(row, text="Mount read-only" if drive.dirty else "Mount",
                                       width=130, fg_color="transparent", border_width=1,
                                       text_color=("gray10", "gray90"))
                button.configure(command=lambda drive=drive, button=button, command=command:
                                 self._mount(drive, button, command))
                button.grid(row=0, column=2, rowspan=2, padx=(7, 8))

    def _drive_op_allowed(self, title):
        """One mount or unmount at a time: drive_busy then covers exactly one."""
        if self.drive_busy:
            messagebox.showinfo(title, f"Wait until {self.drive_busy} has finished.")
            return False
        return True

    def _begin_drive_op(self, description):
        self._drive_op += 1
        self._drive_op_finished = False
        self.drive_busy = description

    def _mount(self, drive, button, command):
        if self.running:
            messagebox.showinfo("Mount", "Wait for the current job to finish first.")
            return
        if not self._drive_op_allowed("Mount"):
            return
        button.configure(state="disabled", text="Mounting…")
        self._begin_drive_op(f"mounting {drive.display_name}")

        def work():
            try:
                result = subprocess.run(command, capture_output=True, text=True,
                                        stdin=subprocess.DEVNULL, timeout=120)
                error = "" if result.returncode == 0 else (
                    (result.stderr or result.stdout).strip() or "udisksctl failed.")
            except (OSError, subprocess.SubprocessError) as exc:
                error = str(exc)
            self.events.put(("mounted", drive.display_name, error))
        threading.Thread(target=work, daemon=True).start()

    def _unmount(self, drive, button):
        """Unmount a drive so it can be unplugged; its file list is kept."""
        if self.running:
            messagebox.showinfo("Unmount", "Wait for the current job to finish, or cancel it, "
                                           "before unmounting a drive.")
            return
        command = unmount_command(drive.mount)
        if not command or not self._drive_op_allowed("Unmount"):
            return
        button.configure(state="disabled", text="Unmounting…")
        name = drive.display_name
        self._begin_drive_op(f"unmounting {name}")

        def work():
            try:
                # Unsaved data is written out first, which can take a while.
                result = subprocess.run(command, capture_output=True, text=True,
                                        stdin=subprocess.DEVNULL, timeout=300)
                error = "" if result.returncode == 0 else (
                    (result.stderr or result.stdout).strip() or f"{command[0]} failed.")
            except subprocess.TimeoutExpired:
                error = "The drive is still busy after 5 minutes."
            except OSError as exc:
                error = str(exc)
            self.events.put(("unmounted", name, error))
        threading.Thread(target=work, daemon=True).start()

    def _sync_settings(self):
        try:
            workers = int(self.workers_var.get())
            minimum = int(self.min_var.get())
            if not 1 <= workers <= 32 or not 0 <= minimum <= 1000000:
                raise ValueError
        except ValueError:
            messagebox.showerror("Advanced options", "Readers must be 1–32 and the image size must be 0–1,000,000 KB.")
            return False
        self.settings.only_media = self.media_var.get()
        self.settings.locate = self.locate_var.get()
        self.settings.retry_blank_exif = self.retry_var.get()
        self.settings.hash_mode = {"Off": "off", "Quick": "sample", "Full": "full"}[self.hash_var.get()]
        self.settings.path_style = {"Automatic": "auto", "Linux style": "linux", "Windows style": "windows"}[self.path_var.get()]
        self.settings.archives = next(mode for mode, label in fp._ARCHIVE_LABELS.items()
                                      if label == self.archives_var.get())
        self.settings.workers = workers
        self.settings.min_image_size = minimum * 1000
        return True

    def _start_selected(self):
        if self.running or not self._sync_settings():
            return
        if not self.exiftool_ready:
            messagebox.showerror("ExifTool needed", fp._exiftool_install_hint())
            return
        chosen = [drive for drive in self.drives if self.selected[drive.mount].get()]
        if not chosen:
            messagebox.showinfo("Choose a drive", "Select at least one connected drive.")
            return
        descriptions = [f"{drive.display_name}: {'update' if drive.mount in self.list_for else 'make'} its list" for drive in chosen]
        question = "Run these scans in order?\n\n" + "\n".join(descriptions)
        if any(drive.mount not in self.list_for for drive in chosen):
            # Updating an existing list reuses what it already knows and is quick.
            question += "\n\nThe first scan of a large drive can take hours."
        if not messagebox.askyesno("Make or update file lists", question):
            return
        self._pending_scans = []
        assigned = set()  # new lists don't exist until scanned; keep same-named drives apart
        for drive in chosen:
            fl = self.list_for.get(drive.mount)
            output = fl.path if fl else fp._unused_path(self.list_dir / fp.canonical_list_name(drive), assigned)
            assigned.add(Path(output))
            self._pending_scans.append((f"Scanning {drive.display_name}",
                                        scan_arguments(self.settings, drive.mount, output, self.list_dir,
                                                       whole_drive=True)))
        self._next_scan()

    def _next_scan(self):
        if self._pending_scans:
            name, args = self._pending_scans.pop(0)
            self._start_job(name, args)
        else:
            self.refresh()

    def _start_job(self, label, args):
        if self.running:
            return
        if self.drive_busy:
            messagebox.showinfo(label, f"Wait until {self.drive_busy} has finished, then try again.")
            self._pending_scans = []
            return
        self._job_label = label
        self.running = True
        self.stopping = False
        self._lines.clear()
        self._summary = {}
        self._last_report_dir = None
        self.reports_btn.grid_remove()
        self.activity.grid()
        self.phase_var.set(label)
        self.detail_var.set("Starting…")
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        self.stop_btn.configure(state="normal")
        self.stop_btn.grid()
        self.start_btn.configure(state="disabled")
        self.health_btn.configure(state="disabled")
        self.refresh_btn.configure(state="disabled")
        def work():
            try:
                flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                proc = subprocess.Popen(worker_command(args), stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                        text=True, encoding="utf-8", errors="replace", bufsize=1,
                                        creationflags=flags)
                self.events.put(("process", proc))
                # The scan prints carriage-return progress. Reading one character
                # at a time lets the status update without waiting for a newline.
                current = []
                while True:
                    char = proc.stdout.read(1)
                    if not char:
                        break
                    if char in "\r\n":
                        if current:
                            self.events.put(("line", "".join(current)))
                            current.clear()
                    else:
                        current.append(char)
                        if len(current) > 20000:
                            self.events.put(("line", "".join(current)))
                            current.clear()
                if current:
                    self.events.put(("line", "".join(current)))
                self.events.put(("done", proc.wait()))
            except Exception as exc:
                self.events.put(("line", f"Could not start the job: {exc}"))
                self.events.put(("done", 1))
        threading.Thread(target=work, daemon=True).start()

    def _append_line(self, line):
        self._lines.append(line)
        if len(self._lines) > 1000:
            self._lines = self._lines[-1000:]
            self.log.delete("1.0", "end")
            self.log.insert("end", "\n".join(self._lines) + "\n")
        else:
            self.log.insert("end", line + "\n")
        self.log.see("end")
        if line.strip():
            self.detail_var.set(line[:250])
        note_summary_line(line, self._summary)
        fraction = progress_fraction(line)
        if fraction is not None:
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress.set(fraction)
        match = re.search(r"Reports saved in: (.+)", line)
        if match:
            self._last_report_dir = Path(match.group(1))
            if self._last_report_dir.is_dir():
                self.reports_btn.grid()

    def _drain_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "refresh":
                    _, self.drives, self.lists, error, self.unmounted, busy = event
                    self.refreshing = False
                    if busy == (self._drive_op, True):
                        self.drive_busy = None  # the list now shows the drives as they are
                    self.refresh_btn.configure(state="normal")
                    self.list_for = {fl.drive.mount: fl for fl in self.lists if fl.drive}
                    self._render_drives()
                    self.list_dir_var.set(f"File lists: {self.list_dir}  ({len(self.lists)} lists)")
                    if self.search_dialog is not None and self.search_dialog.winfo_exists():
                        if self.search_dialog.file_lists != self.lists:
                            self.search_dialog.use_lists(self.lists)
                    else:
                        update_search_index_in_background(self.lists)
                    if error:
                        messagebox.showerror("Refresh drives", error)
                elif kind == "mounted":
                    _, name, error = event
                    self._drive_op_finished = True
                    if error:
                        messagebox.showerror("Mount", f"{name} could not be mounted.\n\n{error}")
                    self.refreshing = False  # show the drives as they are now, either way
                    self.refresh()
                elif kind == "unmounted":
                    _, name, error = event
                    self._drive_op_finished = True
                    if error:
                        messagebox.showerror(
                            "Unmount", f"{name} could not be unmounted. Close any programs "
                                       f"or windows using it and try again.\n\n{error}")
                    else:
                        messagebox.showinfo("Unmount", f"{name} is unmounted and safe to unplug.")
                    self.refreshing = False  # show the drives as they are now, either way
                    self.refresh()
                elif kind == "exiftool":
                    usable, up_to_date, message = event[1]
                    self.exiftool_ready = usable
                    self.exif_var.set(message)
                    if up_to_date:
                        self.exif_link.grid_remove()
                    else:
                        self.exif_link.grid(row=0, column=1, sticky="e")
                    self.start_btn.configure(state="normal" if usable and not self.running else "disabled")
                elif kind == "process":
                    self.process = event[1]
                    if self.stopping:
                        self._signal_stop()
                elif kind == "line":
                    self._append_line(event[1])
                elif kind == "done":
                    self._job_done(event[1])
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _job_done(self, code):
        self.running = False
        self.process = None
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress.set(1 if code == 0 and not self.stopping else 0)
        self.stop_btn.grid_remove()  # nothing left to stop; a greyed-out button looked like the job was still running
        self.start_btn.configure(state="normal" if self.exiftool_ready else "disabled")
        self.health_btn.configure(state="normal")
        self.refresh_btn.configure(state="normal")
        label = self._job_label
        summary = summary_text(self._summary)
        if self.stopping:
            self.detail_var.set("Start the same scan again to continue where it left off.")
        elif code == 0:
            self.detail_var.set(summary or "Finished.")
            if self._summary.get("read_problems") and not self._log_open:
                self._toggle_log()
        if self.stopping:
            self.phase_var.set("Stopped — saved progress can be resumed")
            self._pending_scans.clear()
        elif code == 0:
            self.phase_var.set(f"{label} — done")
            if self._pending_scans:
                self.after(300, self._next_scan)
                return
        else:
            self.phase_var.set(f"{label} — needs attention")
            self._pending_scans.clear()
            if not self._log_open:
                self._toggle_log()
        self.refresh()

    def _signal_stop(self):
        proc = self.process
        if not proc or proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                proc.send_signal(signal.SIGINT)
        except (OSError, ValueError):
            proc.terminate()

    def _stop(self):
        if not self.running or self.stopping:
            return
        self.stopping = True
        self._pending_scans.clear()
        self.phase_var.set("Stopping after saving progress…")
        self.stop_btn.grid_remove()
        self._signal_stop()

    def _health(self, extended=None):
        if self.running:
            return
        if extended is None:
            extended = messagebox.askyesnocancel("Drive health", "Run an extended surface check?\n\n"
                                                 "Yes: extended (can take hours)\nNo: quick health check")
            if extended is None:
                return
        selected = [drive for drive in self.drives if self.selected.get(drive.mount) and self.selected[drive.mount].get()]
        if extended and not messagebox.askyesno("Extended health check", "Extended checks can take hours and may read every file "
                                             "on drives that cannot self-test. Start now?"):
            return
        suffix = ["--extended"] if extended else []
        self._pending_scans = []
        if selected:
            self._pending_scans = [(
                f"{'Extended' if extended else 'Quick'} health: {drive.display_name}",
                ["health", "--directory", drive.mount, *suffix],
            ) for drive in selected]
            self._next_scan()
        else:
            self._start_job("Extended drive health" if extended else "Drive health", ["health", *suffix])

    def _health_history(self):
        rows = fp.read_health_log(self.list_dir)
        if not rows:
            messagebox.showinfo("Drive health history", "No health checks have been recorded yet.")
            return
        lines = [f"{row.get('date', '')}  •  {row.get('drive', '')}  •  "
                 f"{row.get('check', '')}  •  {row.get('health', '')}\n"
                 f"  {row.get('notes', '')}" for row in reversed(rows)]
        dialog = ctk.CTkToplevel(self)
        dialog.title("Drive health history")
        dialog.geometry("700x500")
        dialog.transient(self)
        box = ctk.CTkTextbox(dialog, wrap="word")
        box.pack(fill="both", expand=True, padx=15, pady=15)
        box.insert("1.0", "\n\n".join(lines))
        box.configure(state="disabled")

    def _folder_scan(self):
        if self.running or not self._sync_settings():
            return
        if not self.exiftool_ready:
            messagebox.showerror("ExifTool needed", fp._exiftool_install_hint())
            return
        directory = filedialog.askdirectory(title="Choose a folder to list")
        if not directory:
            return
        from tkinter import simpledialog
        default = fp._safe_filename(Path(directory).name or "Folder")
        name = simpledialog.askstring("Name this file list", "File list name:", initialvalue=default, parent=self)
        if name is None:
            return
        name = fp._safe_filename(name.strip() or default)
        output = self.list_dir / (name + ".tsv")
        if output.exists() and not messagebox.askyesno("Update file list", f"Update the existing list {output.name}?"):
            return
        self._start_job(f"Scanning {Path(directory).name}", scan_arguments(self.settings, directory, output, self.list_dir))

    def _backup_check(self):
        if self.running:
            return
        if not fp.find_file_lists(self.list_dir):
            messagebox.showinfo("Check backups", "Make at least one file list first.")
            return
        directory = filedialog.askdirectory(title="Choose a folder to check against your file lists")
        if directory:
            self._start_job("Checking backups", ["backup", "--directory", directory, "--list-dir", str(self.list_dir)])

    def _import_lists(self):
        if self.running:
            return
        folder = filedialog.askdirectory(title="Choose a folder containing file lists")
        if not folder:
            return
        paths = fp.find_legacy_lists(folder)
        if not paths and Path(folder) != self.list_dir:
            paths = [fl.path for fl in fp.find_file_lists(folder)]
        paths = [p for p in paths if Path(p).parent != self.list_dir]
        if not paths:
            messagebox.showinfo("Import file lists", "No file lists were found in that folder.")
            return
        if messagebox.askyesno("Import file lists", f"Move {len(paths)} file list(s) into {self.list_dir}?"):
            try:
                moved = fp.import_file_lists(paths, self.list_dir, self.drives)
                messagebox.showinfo("Import file lists", f"Imported {len(moved)} file list(s).")
                self.refresh()
            except Exception as exc:
                messagebox.showerror("Import file lists", str(exc))

    def _change_list_dir(self):
        if self.running:
            return
        folder = filedialog.askdirectory(title="Choose where file lists are kept", initialdir=str(self.list_dir))
        if not folder or Path(folder) == self.list_dir:
            return
        new_dir = Path(folder)
        existing = fp.find_file_lists(self.list_dir)
        move = bool(existing) and messagebox.askyesno("Move file lists?", f"Move {len(existing)} existing list(s) to the new folder?")
        try:
            new_dir.mkdir(parents=True, exist_ok=True)
            if move:
                import shutil
                for fl in existing:
                    shutil.move(str(fl.path), str(fp._unused_path(new_dir / fl.path.name)))
            fp.update_config(lambda config: config.update(inventory_dir=str(new_dir)))
            self.list_dir = new_dir
            self.refresh()
        except Exception as exc:
            messagebox.showerror("Change file lists folder", str(exc))

    def _copy_command(self):
        if not self._sync_settings():
            return
        selected = next((d for d in self.drives if self.selected.get(d.mount) and self.selected[d.mount].get()), None)
        if selected:
            directory = selected.mount
            fl = self.list_for.get(directory)
            output = fl.path if fl else self.list_dir / fp.canonical_list_name(selected)
        else:
            directory = "/path/to/drive" if os.name != "nt" else "D:\\"
            output = self.list_dir / "My Drive.tsv"
        command = self.settings.command_line(directory, output)
        self.clipboard_clear()
        self.clipboard_append(command)
        messagebox.showinfo("Equivalent command copied", command)

    def _show_lists(self):
        if not self.lists:
            messagebox.showinfo("File lists", "No file lists yet.")
            return
        lines = []
        for fl in self.lists:
            try:
                date = fp._last_updated_label(fl.path.stat().st_mtime)
                count = fp._count_rows(fl.path)
                where = fl.drive.mount if fl.drive else "drive disconnected or folder list"
                lines.append(f"{fl.path.name}\n  {count:,} files  •  {date}  •  {where}")
            except OSError:
                continue
        dialog = ctk.CTkToplevel(self)
        dialog.title("All file lists")
        dialog.geometry("680x480")
        dialog.transient(self)
        box = ctk.CTkTextbox(dialog, wrap="word")
        box.pack(fill="both", expand=True, padx=15, pady=15)
        box.insert("1.0", "\n\n".join(lines))
        box.configure(state="disabled")

    def _show_search(self):
        if not self.lists:
            messagebox.showinfo("Search file lists", "Make at least one file list first.")
            return
        if self.search_dialog is not None and self.search_dialog.winfo_exists():
            self.search_dialog.deiconify()
            self.search_dialog.lift()
            self.search_dialog.date_entry.focus_set()
            if self.search_dialog.file_lists != self.lists:  # drives changed meanwhile
                self.search_dialog.use_lists(self.lists)
            return
        self.search_dialog = FileSearchDialog(self, self.lists)
        self.search_dialog.date_entry.focus_set()

    def _show_coverage(self):
        if not self.lists:
            messagebox.showinfo("Coverage", "Make at least one file list first.")
            return
        if self.coverage_dialog is not None and self.coverage_dialog.winfo_exists():
            self.coverage_dialog.deiconify()
            self.coverage_dialog.lift()
            if self.coverage_dialog.file_lists != self.lists:  # drives changed meanwhile
                self.coverage_dialog._calculate()
            return
        self.coverage_dialog = CoverageDialog(self, self.lists)

    def _open_path(self, path):
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
            open_folder(path)
        except Exception as exc:
            messagebox.showerror("Open folder", str(exc))

    def _close(self):
        if self.running:
            if not messagebox.askyesno("Stop current job?", "Stop the current job, save its progress, and close the window?"):
                return
            self._stop()
            def await_exit():
                if self.running:
                    self.after(200, await_exit)
                else:
                    self.destroy()
            await_exit()
        else:
            self.destroy()


if __name__ == "__main__":
    try:
        app = FindPhotoDatesGUI()
    except SystemExit as exc:
        if not isinstance(exc.code, str):
            raise
        # e.g. a damaged settings file: say so instead of closing silently.
        print(exc.code, file=sys.stderr)
        try:
            messagebox.showerror("Find Photo Dates", exc.code)
        except Exception:
            pass
        raise SystemExit(1) from None
    except TclError as exc:
        if "display" not in str(exc).lower():
            raise
        print("The GUI needs a graphical desktop session. Run it from your desktop "
              "terminal, or use python findphotodates.py for the text menu.\n\n"
              f"Details: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    app.mainloop()
