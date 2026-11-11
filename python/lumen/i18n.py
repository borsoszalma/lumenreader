"""UI languages. Strings live in lumen/strings/<code>.json (same files as the C# version)."""
from __future__ import annotations

import json
import locale
import os
import sys
from functools import lru_cache

LANGUAGES = [
    ("en", "English"), ("de", "Deutsch"), ("fr", "Français"), ("es", "Español"), ("it", "Italiano"),
    ("pt", "Português"), ("nl", "Nederlands"), ("sv", "Svenska"), ("pl", "Polski"), ("cs", "Čeština"),
    ("sk", "Slovenčina"), ("hu", "Magyar"), ("ro", "Română"), ("hr", "Hrvatski"), ("sl", "Slovenščina"),
    ("sr-Latn", "Srpski (latinica)"), ("lt", "Lietuvių"), ("lv", "Latviešu"), ("et", "Eesti"),
]
_CODES = {c for c, _ in LANGUAGES}
_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "strings")


@lru_cache(maxsize=None)
def load(code: str) -> dict:
    try:
        with open(os.path.join(_DIR, f"{code}.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {} if code == "en" else load("en")


def system_language() -> str:
    """Windows display language mapped to a supported code; English when not available."""
    name = None
    if sys.platform == "win32":
        try:
            import ctypes
            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            name = locale.windows_locale.get(lcid)
        except Exception:
            name = None
    if not name:
        name = os.environ.get("LANG") or locale.getlocale()[0] or "en"
    lang = name.replace("-", "_").split("_")[0].lower()
    if lang == "sr":
        return "sr-Latn"
    return lang if lang in _CODES else "en"


def resolve(setting: str) -> str:
    return setting if setting in _CODES else system_language()
