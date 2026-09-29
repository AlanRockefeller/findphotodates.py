#!/usr/bin/env python3
"""Simple desktop front end for findphotodates' interactive workflow."""

from __future__ import annotations

import os
import queue
import re
import signal
import subprocess
import sys
import threading
from pathlib import Path

from findphotodates_gui_startup import dependency_message

try:
    from tkinter import TclError, filedialog, messagebox
    import customtkinter as ctk
except (ImportError, OSError) as exc:
    if __name__ == "__main__":
        message = dependency_message(exc)
        print(message, file=sys.stderr)
        try:
            import tkinter
            root = tkinter.Tk()
            root.withdraw()
            tkinter.messagebox.showerror("Find Photo Dates", message)
            root.destroy()
        except Exception:
            pass  # Tk itself may be unavailable; the terminal message still explains the fix.
        raise SystemExit(1) from None
    raise

import findphotodates as fp


APP_TITLE = f"Find Photo Dates {fp.__version__}"


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
            "--path-style", settings.path_style]
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


def open_folder(path):
    if os.name == "nt":
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


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


class FindPhotoDatesGUI(ctk.CTk):
    def __init__(self):
        # CustomTkinter detects display DPI on macOS and Windows, but Linux
        # needs an application-level boost on dense screens.
        if sys.platform.startswith("linux"):
            ctk.set_widget_scaling(1.2)
        super().__init__()
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
        self._pending_scans = []
        self._job_label = ""
        self._last_report_dir = None
        self.exiftool_ready = False
        self._advanced_auto_height = None
        self._compact_height = None
        self._hover_help = []

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
        ctk.CTkLabel(frame, text="Choose a connected drive to make or update its file list. "
                     "Later scans read only new or changed files. Lists stay in Documents so you "
                     "can see what is on drives even while they are unplugged.",
                     wraplength=740, anchor="w", justify="left",
                     font=ctk.CTkFont(size=14),
                     text_color=("gray35", "gray70")).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        self.exif_var = ctk.StringVar(value="Checking ExifTool…")
        exif_row = ctk.CTkFrame(frame, fg_color="transparent")
        exif_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        exif_row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(exif_row, textvariable=self.exif_var, anchor="w",
                     text_color=("gray35", "gray72"), font=ctk.CTkFont(size=14)).grid(row=0, column=0, sticky="ew")
        self._text_button(exif_row, "Get ExifTool", lambda: __import__("webbrowser").open("https://exiftool.org/"))\
            .grid(row=0, column=1, sticky="e")

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
        self._text_button(footer, "Show all lists", self._show_lists).grid(row=0, column=2)

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
        self._entry_row(options, 5, "Photos read at the same time (1–32)", self.workers_var, "workers")
        self._entry_row(options, 6, "Skip images smaller than (KB; 0 = off)", self.min_var, "min_size")
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
        def work():
            try:
                drives = fp.detect_drives()
                lists = fp.find_file_lists(self.list_dir)
                fp.match_lists_to_drives(lists, drives)
                self.events.put(("refresh", drives, lists, None))
            except Exception as exc:
                self.events.put(("refresh", [], [], str(exc)))
        threading.Thread(target=work, daemon=True).start()
        threading.Thread(target=self._check_exiftool, daemon=True).start()

    def _check_exiftool(self):
        self.events.put(("exiftool", fp._exiftool_available()))

    def _render_drives(self):
        for child in self.drive_frame.winfo_children():
            child.destroy()
        self.selected.clear()
        if not self.drives:
            self.drive_status.set("No connected drives found. Plug one in and refresh, or use Advanced to list a folder.")
            ctk.CTkLabel(self.drive_frame, textvariable=self.drive_status, anchor="w",
                         wraplength=720, font=ctk.CTkFont(size=15)).grid(row=0, column=0, sticky="ew")
            return
        widths = (32, 220, 78, 100, 145)
        header = ctk.CTkFrame(self.drive_frame, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        header.grid_columnconfigure(1, weight=1)
        for column, title in enumerate(("", "Drive", "Size", "Filesystem", "Updated")):
            ctk.CTkLabel(header, text=title, width=widths[column], anchor="w",
                         font=ctk.CTkFont(size=14, weight="bold"))\
                .grid(row=0, column=column, sticky="ew", padx=(7, 0))
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
        self.drive_status.set("")

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
        if not messagebox.askyesno("Make or update file lists", "Run these scans in order?\n\n" + "\n".join(descriptions)
                                   + "\n\nThe first scan of a large drive can take hours."):
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
        self._job_label = label
        self.running = True
        self.stopping = False
        self._lines.clear()
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
                    _, self.drives, self.lists, error = event
                    self.refreshing = False
                    self.refresh_btn.configure(state="normal")
                    self.list_for = {fl.drive.mount: fl for fl in self.lists if fl.drive}
                    self._render_drives()
                    self.list_dir_var.set(f"File lists: {self.list_dir}  ({len(self.lists)} lists)")
                    if error:
                        messagebox.showerror("Refresh drives", error)
                elif kind == "exiftool":
                    self.exiftool_ready = event[1]
                    self.exif_var.set("ExifTool is ready." if event[1] else
                                      "ExifTool is required to read photo dates. Install it before scanning.")
                    self.start_btn.configure(state="normal" if event[1] and not self.running else "disabled")
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
        self.stop_btn.configure(state="disabled")
        self.start_btn.configure(state="normal" if self.exiftool_ready else "disabled")
        self.health_btn.configure(state="normal")
        self.refresh_btn.configure(state="normal")
        label = self._job_label
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
        self.stop_btn.configure(state="disabled")
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
            config = fp.load_config()
            config["inventory_dir"] = str(new_dir)
            fp.save_config(config)
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
    except TclError as exc:
        if "display" not in str(exc).lower():
            raise
        print("The GUI needs a graphical desktop session. Run it from your desktop "
              "terminal, or use python findphotodates.py for the text menu.\n\n"
              f"Details: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    app.mainloop()
