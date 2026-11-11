"""User preferences in %LOCALAPPDATA%\\LumenReaderPy\\settings.json."""
from __future__ import annotations

import json
import os
import sys


def data_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") if sys.platform == "win32" else None
    base = base or os.path.join(os.path.expanduser("~"), ".local", "share")
    path = os.path.join(base, "LumenReaderPy")
    os.makedirs(path, exist_ok=True)
    return path


def _file() -> str:
    return os.path.join(data_dir(), "settings.json")


def system_prefers_dark() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def load() -> dict:
    settings = {"theme": None, "language": "auto", "tocVisible": True, "tocWidth": 290, "lastFolder": None,
                "reopenLast": True}
    try:
        with open(_file(), encoding="utf-8") as f:
            settings.update(json.load(f))
    except (OSError, ValueError):
        pass
    if settings.get("theme") not in ("light", "dark", "reading"):
        settings["theme"] = "dark" if system_prefers_dark() else "light"
    return settings


def save(settings: dict) -> None:
    try:
        with open(_file(), "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
