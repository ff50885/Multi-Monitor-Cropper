"""dualcropper.logger - diagnostic logging for DualCropper.

Every detection / cropping / wallpaper-assignment step is written to a rotating
log file so failures can be traced precisely.

Log location (created on first use):
    Windows : %LOCALAPPDATA%\\DualCropper\\logs\\dualcropper.log
    macOS   : ~/Library/Logs/DualCropper/dualcropper.log
    other   : ~/.local/state/dualcropper/logs/dualcropper.log

Usage:
    from .logger import get_logger, log_path
    log = get_logger()
    log.info("...")
    log.exception("...")          # includes traceback
    print(log_path())             # shown in the GUI / copied by the user
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_MAX_BYTES = 1_000_000     # 1 MB per file
_BACKUP_COUNT = 3          # keep dualcropper.log.1 .. .3
_FMT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_configured = False


def log_dir() -> str:
    """Directory that holds the log files (best-effort, never raises)."""
    try:
        if sys.platform.startswith("win"):
            base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
            return os.path.join(base, "DualCropper", "logs")
        if sys.platform == "darwin":
            return os.path.expanduser("~/Library/Logs/DualCropper")
        return os.path.join(os.path.expanduser("~"), ".local", "state",
                            "dualcropper", "logs")
    except Exception:
        return os.path.join(os.getcwd(), "logs")


def log_path() -> str:
    return os.path.join(log_dir(), "dualcropper.log")


def get_logger(name: str = "dualcropper") -> logging.Logger:
    """Return the application logger, configuring the file handler once."""
    global _configured
    root = logging.getLogger("dualcropper")
    if not _configured:
        _configured = True
        root.setLevel(logging.DEBUG)
        root.propagate = False
        try:
            d = log_dir()
            os.makedirs(d, exist_ok=True)
            fh = RotatingFileHandler(log_path(), maxBytes=_MAX_BYTES,
                                     backupCount=_BACKUP_COUNT,
                                     encoding="utf-8")
            fh.setFormatter(logging.Formatter(_FMT, _DATEFMT))
            root.addHandler(fh)
        except Exception as exc:  # pragma: no cover - disk/permission issues
            try:
                sys.stderr.write(f"[dualcropper] cannot open log file: {exc}\n")
            except Exception:
                pass
    if name == "dualcropper":
        return root
    return root.getChild(name.split(".")[-1]) if name.startswith("dualcropper.") else root
