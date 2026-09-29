"""Plain-language GUI startup errors, usable even when Tk cannot be imported."""

from __future__ import annotations

import sys
from pathlib import Path


def _linux_family():
    try:
        lines = Path("/etc/os-release").read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    values = {}
    for line in lines:
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"').lower()
    return " ".join((values.get("ID", ""), values.get("ID_LIKE", "")))


def dependency_message(error, *, system=None, linux_family=None):
    """Explain the failed component and the next command without importing Tk."""
    system = system or sys.platform
    detail = str(error)
    lowered = detail.lower()
    missing = getattr(error, "name", None)
    if ("libtk" in lowered or "libtcl" in lowered or missing in ("tkinter", "_tkinter")
            or "no module named 'tkinter'" in lowered or "no module named '_tkinter'" in lowered):
        if system.startswith("linux"):
            family = linux_family if linux_family is not None else _linux_family()
            if "omarchy" in family or "arch" in family:
                fix = "sudo pacman -Syu tk"
            elif "debian" in family or "ubuntu" in family:
                fix = "sudo apt install python3-tk"
            else:
                fix = "Install your distribution's Tk package for Python."
        elif system == "darwin":
            fix = "Install a Python build with working Tcl/Tk support."
        else:
            fix = "Install or repair Python with its Tcl/Tk component enabled."
        return ("The GUI cannot start because Tk is missing from this computer.\n"
                "A Python virtual environment alone will not provide this system library.\n\n"
                f"Fix: {fix}\n\nThen run: python findphotodates_gui.py\n\nDetails: {detail}")
    if missing == "customtkinter" or "no module named 'customtkinter'" in lowered:
        if system.startswith("win"):
            setup = ("py -m venv .venv\n"
                     ".venv\\Scripts\\python -m pip install -r requirements-gui.txt\n"
                     ".venv\\Scripts\\python findphotodates_gui.py")
        else:
            setup = ("python -m venv .venv\n"
                     "source .venv/bin/activate\n"
                     "python -m pip install -r requirements-gui.txt\n"
                     "python findphotodates_gui.py")
        return ("The GUI cannot start because CustomTkinter is not installed for this Python.\n\n"
                f"Set up a project virtual environment:\n{setup}\n\n"
                f"Details: {detail}")
    return ("The GUI could not load a required component.\n\n"
            "Try: python -m pip install -r requirements-gui.txt\n\n"
            f"Details: {detail}")
