"""dualcropper.i18n - JSON-backed localisation for the DualCropper GUI.

Language files live in ``locales/*.json`` next to this module (or inside a
PyInstaller bundle).  Every user-visible string is looked up through
:func:`tr`; missing keys fall back to English, and untranslated values are
returned verbatim so custom option labels always round-trip correctly.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Dict, List

DEFAULT_LANG = "en"

# Bundled languages shown in the UI language picker (code -> native name).
LANGUAGES = [
    ("en", "English"),
    ("tr", "Türkçe"),
    ("de", "Deutsch"),
    ("es", "Español"),
    ("fr", "Français"),
    ("ru", "Русский"),
]

_loaded: Dict[str, Dict[str, str]] = {}
_current_code: str = DEFAULT_LANG
_strings: Dict[str, str] = {}
_fallback: Dict[str, str] = {}


def _locale_dir() -> str:
    """Directory containing the JSON locale files (PyInstaller aware)."""
    if getattr(sys, "frozen", False):  # running from a .exe bundle
        return os.path.join(getattr(sys, "_MEIPASS", "."), "locales")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")


def load_locale(code: str) -> Dict[str, str]:
    if code not in _loaded:
        path = os.path.join(_locale_dir(), f"{code}.json")
        data: Dict[str, str] = {}
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
            data = {k: v for k, v in raw.items() if isinstance(v, str)}
        except Exception:
            data = {}
        _loaded[code] = data
    return _loaded[code]


def available_codes() -> List[str]:
    """Codes of bundled locales that actually have a JSON file on disk."""
    found = []
    for code, _name in LANGUAGES:
        if os.path.isfile(os.path.join(_locale_dir(), f"{code}.json")):
            found.append(code)
    return found or [DEFAULT_LANG]


def language_names() -> List[str]:
    return [name for _code, name in LANGUAGES]


def code_for_name(name: str) -> str:
    for code, n in LANGUAGES:
        if n == name:
            return code
    return DEFAULT_LANG


def set_language(code: str) -> None:
    global _current_code, _strings, _fallback
    _fallback = load_locale(DEFAULT_LANG)
    _current_code = code
    _strings = load_locale(code)
    if not _strings:  # broken/missing file -> English
        _current_code = DEFAULT_LANG
        _strings = _fallback


def current_language() -> str:
    return _current_code


def tr(key: str) -> str:
    """Translate *key*; falls back to English, then to the key itself."""
    return _strings.get(key) or _fallback.get(key) or key
