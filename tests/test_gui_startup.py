"""Startup help must work before Tk or CustomTkinter can be imported."""

from findphotodates_gui_startup import dependency_message


def test_missing_tk_on_omarchy_points_to_system_package():
    message = dependency_message(
        ImportError("libtk8.6.so: cannot open shared object file"),
        system="linux", linux_family="omarchy arch",
    )
    assert "sudo pacman -Syu tk" in message
    assert "virtual environment alone will not" in message
    assert "pip install" not in message


def test_missing_customtkinter_points_to_python_dependency():
    error = ModuleNotFoundError("No module named 'customtkinter'")
    error.name = "customtkinter"
    message = dependency_message(error, system="linux", linux_family="omarchy arch")
    assert "python -m pip install -r requirements-gui.txt" in message
    assert "pacman" not in message


def test_missing_tk_on_ubuntu_uses_ubuntu_package():
    error = ModuleNotFoundError("No module named '_tkinter'")
    error.name = "_tkinter"
    message = dependency_message(error, system="linux", linux_family="ubuntu debian")
    assert "sudo apt install python3-tk" in message
