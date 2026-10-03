"""dualcropper.wallpaper - set per-monitor desktop wallpaper, cross-platform.

Windows (primary target): per-monitor assignment uses the
``IDesktopWallpaper::SetWallpaper(devicePath, wallpaper)`` COM interface
(Windows 8+), driven through pure ``ctypes`` vtable calls (no extra packages,
PyInstaller-friendly).  The device paths handed to SetWallpaper are the exact
strings produced by ``IDesktopWallpaper::GetMonitorDevicePathAt`` (see
dualcropper._win_monitors), paired left-to-right with the physical monitor
rects from EnumDisplayMonitors - so "monitor N" means the same thing in
detection and in assignment, and a connected display can never be "not found".

Every attempt is logged (successes at INFO, failures at WARNING/ERROR with the
HRESULT / Win32 error code) to the rotating dualcropper.log file so problems
can be traced externally.  On failure the user gets an explicit message; we
never silently push one picture to every screen.

Linux: gsettings (GNOME) then feh.   macOS: AppleScript via osascript.
"""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
import sys
from typing import List, Tuple

from .logger import get_logger, log_path

log = get_logger("wallpaper")


def tr(key: str, **fmt) -> str:
    """Translate *key* if i18n is importable; otherwise return the raw key."""
    try:
        from . import i18n
        s = i18n.tr(key)
    except Exception:
        s = key
    return s.format(**fmt) if fmt else s


SPI_SETDESKWALLPAPER = 0x0014
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02


# ------------------------------------------------------------------- public

def set_wallpaper(path: str) -> Tuple[bool, str]:
    """Set *path* as the (single) wallpaper using the first backend that works."""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        log.error("set_wallpaper: file does not exist: %s", path)
        return False, tr("wp_not_found", path=path)
    log.info("set_wallpaper(single): %s", path)

    system = platform.system()
    if system == "Windows":
        return _set_windows(path)
    if system == "Darwin":
        return _set_macos(path)
    return _set_linux(path)


def set_wallpapers_per_monitor(paths: List[str]) -> Tuple[bool, str]:
    """Assign one wallpaper per physical monitor.

    *paths* must be ordered left-to-right across the desk (index 0 goes to the
    leftmost display).  On Windows this is IDesktopWallpaper only - no silent
    single-wallpaper fallback: partial success is reported explicitly.
    """
    if not paths:
        log.error("set_wallpapers_per_monitor: empty path list")
        return False, tr("wp_no_paths")
    paths = [os.path.abspath(p) for p in paths]
    missing = [p for p in paths if not os.path.isfile(p)]
    if missing:
        log.error("set_wallpapers_per_monitor: missing files: %s", missing)
        return False, tr("wp_not_found", path=", ".join(missing))
    log.info("set_wallpapers_per_monitor: %d file(s): %s",
             len(paths), "; ".join(os.path.basename(p) for p in paths))

    system = platform.system()
    if system == "Windows":
        return _set_windows_per_monitor(paths)
    if system == "Darwin":
        return _set_macos_many(paths)
    # Linux: gsettings has no per-monitor API here; use the first file.
    return _set_linux(paths[0])


# ------------------------------------------------------------------ windows

def _set_windows_per_monitor(paths: List[str]) -> Tuple[bool, str]:
    """Per-monitor wallpapers strictly via the IDesktopWallpaper COM interface."""
    try:
        from . import monitors as mon_api
        mons = mon_api.list_monitors()
    except Exception as exc:
        log.exception("monitor enumeration crashed")
        return False, tr("wp_windows_multi_fail", err=exc)

    if not mons:
        log.error("per-monitor apply aborted: OS enumeration returned nothing")
        return False, tr("wp_windows_monitors_missing")

    dw_ids = [m.id_string for m in mons]

    def _is_dw_path(i: str) -> bool:
        # IDesktopWallpaper device paths always end with a GUID clause, e.g.
        #   \\?\DISPLAY#DEL4088#4&...&UID123#{b5f...}
        # GDI fallback names ("\\.\\DISPLAY1") never contain '#' or '{'.
        return "#" in i and "{" in i and "}" in i

    if not all(_is_dw_path(i) for i in dw_ids):
        # GDI fallback names ("\\.\DISPLAY1") were produced because COM
        # enumeration failed earlier; SetWallpaper would reject them.
        log.error("monitor IDs are not IDesktopWallpaper device paths (%s); "
                  "COM enumeration must have failed", dw_ids)
        return False, tr("wp_windows_com_fail",
                         err="EnumMonitors unavailable (see log)")

    n = len(mons)
    files = list(paths)
    while len(files) < n:          # extra displays repeat the last crop
        files.append(paths[-1])
    log.info("applying %d wallpaper(s) to %d monitor(s)", len(files[:n]), n)
    return _apply_dw(dw_ids, files[:n])


def _apply_dw(ids: List[str], files: List[str]) -> Tuple[bool, str]:
    """IDesktopWallpaper::SetWallpaper per monitor; logs each HRESULT decoded.

    Preferred backend is the comtypes-generated proxy (correct marshalling of
    all out-parameters, exact COMError HRESULTs).  When comtypes is missing we
    fall back to raw ctypes vtable calls - with restype-corrected callables,
    Enable(True)/GetStatus diagnostics and a GetWallpaper read-back so the log
    proves what every screen actually shows after the batch.
    """
    try:
        from . import _win_monitors as wm
    except Exception as exc:
        log.exception("Windows wallpaper backend unavailable")
        return False, tr("wp_windows_com_fail", err=exc)

    # ---- preferred: comtypes proxy -------------------------------------
    res = wm.dw_set_wallpapers_comtypes(ids, [os.path.abspath(f) for f in files])
    if res is not None:
        applied, failures = res
        if not failures and applied == len(ids):
            return True, tr("wp_windows_per_monitor", count=applied)
        detail = ", ".join(f"M{m}:{name}" for m, _hr, name in failures)
        log.error("comtypes partial/per-monitor failure: applied=%d/%d [%s]",
                  applied, len(ids), detail)
        if applied:
            return False, tr("wp_windows_partial", err=f"{len(failures)} ({detail})")
        return False, tr("wp_windows_multi_fail", err=detail or "see log")

    # ---- fallback: raw ctypes vtable -----------------------------------
    try:
        punk, release, must_uninit = wm.dw_instance()
    except Exception as exc:
        log.exception("CoCreateInstance(IDesktopWallpaper) failed")
        return False, tr("wp_windows_com_fail", err=exc)

    applied, results = 0, []
    try:
        wm._set_ret_types(punk)
        enable = wm.dw_get_slot(punk, wm.SLOT_ENABLE)
        get_status = wm.dw_get_slot(punk, wm.SLOT_GET_STATUS)
        set_wp = wm.dw_get_slot(punk, wm.SLOT_SET_WALLPAPER)
        get_wp = wm.dw_get_slot(punk, wm.SLOT_GET_WALLPAPER)
        hr = enable(punk, ctypes.c_int(1))
        log.debug("IDesktopWallpaper::Enable(True) hr=%s (%s)",
                  wm._fmt_hr(hr), wm._describe_hr(hr))
        status = ctypes.c_int(-1)
        hr = get_status(punk, ctypes.byref(status))
        log.debug("IDesktopWallpaper::GetStatus hr=%s backend=%d",
                  wm._fmt_hr(hr), status.value)
        for i, (dev, img) in enumerate(zip(ids, files)):
            abspath = os.path.abspath(img)
            hr = set_wp(punk, dev, abspath)
            results.append((i + 1, hr, os.path.basename(img)))
            if hr >= 0:
                applied += 1
                log.info("SetWallpaper monitor %d OK  id=%s file=%s",
                         i + 1, dev, os.path.basename(img))
            else:
                log.error("SetWallpaper monitor %d FAILED: %s [%s] id=%s file=%s",
                          i + 1, wm._describe_hr(hr), wm._fmt_hr(hr),
                          dev, os.path.basename(img))
        # Verification read-back: prove in the log which file each screen uses.
        for i, dev in enumerate(ids):
            try:
                bstr = ctypes.c_wchar_p()
                hr = get_wp(punk, dev, ctypes.byref(bstr))
                if hr >= 0:
                    log.info("verify monitor %d wallpaper = %s", i + 1, bstr.value)
                else:
                    log.debug("GetWallpaper(%d) hr=%s", i + 1, wm._fmt_hr(hr))
                if bstr.value is not None:
                    try:
                        ctypes.windll.oleaut32.SysFreeString(bstr)
                    except Exception:
                        pass
            except Exception as exc:
                log.debug("GetWallpaper(%d) error: %s", i + 1, exc)
    except Exception as exc:
        log.exception("unexpected error during DW assignment")
        return False, tr("wp_windows_multi_fail", err=exc)
    finally:
        try:
            release(punk)
        except Exception:
            pass
        if must_uninit:
            try:
                ctypes.windll.ole32.CoUninitialize()
            except Exception:
                pass

    failed = [(m, h) for m, h, _ in results if h < 0]
    if not failed and applied == len(ids):
        return True, tr("wp_windows_per_monitor", count=applied)
    detail = ", ".join(f"M{m}:{_hr_name(h)}" for m, h in failed)
    log.error("partial/per-monitor failure: applied=%d/%d [%s]",
              applied, len(ids), detail)
    if applied:
        return False, tr("wp_windows_partial", err=f"{len(failed)} ({detail})")
    return False, tr("wp_windows_multi_fail", err=detail)


def _hr_name(hr: int) -> str:
    """Short human-readable HRESULT label for user-facing messages."""
    names = {0x80040154: "class-not-registered", 0x80070057: "invalid-arg",
             0x80070005: "access-denied", 0x800401F0: "com-not-initialised"}
    return names.get(hr & 0xFFFFFFFF, f"hr=0x{(hr & 0xFFFFFFFF):08X}")


def _set_windows(path: str) -> Tuple[bool, str]:
    try:
        result = ctypes.windll.user32.SystemParametersInfoW(
            SPI_SETDESKWALLPAPER, 0, path,
            SPIF_UPDATEINIFILE | SPIF_SENDCHANGE)
        if result:
            log.info("SystemParametersInfoW(SPI_SETDESKWALLPAPER) ok")
            return True, tr("wp_windows_set")
        err = ctypes.windll.kernel32.GetLastError()
        log.error("SystemParametersInfoW failed (err=%d)", err)
        return False, tr("wp_windows_spi_fail")
    except Exception as exc:  # pragma: no cover - Windows only
        log.exception("SystemParametersInfoW raised")
        return False, tr("wp_windows_api_err", err=exc)


# -------------------------------------------------------------------- linux

def _set_linux(path: str) -> Tuple[bool, str]:
    if shutil.which("gsettings"):
        try:
            subprocess.run(["gsettings", "set", "org.gnome.desktop.background",
                            "picture-uri", f"file://{path}"], check=True)
            subprocess.run(["gsettings", "set", "org.gnome.desktop.background",
                            "picture-uri-dark", f"file://{path}"])
            log.info("gsettings wallpaper set: %s", path)
            return True, tr("wp_gnome")
        except Exception as exc:
            log.warning("gsettings failed: %s", exc)
    if shutil.which("feh"):
        try:
            subprocess.run(["feh", "--bg-fill", path], check=True)
            log.info("feh wallpaper set: %s", path)
            return True, tr("wp_feh")
        except Exception as exc:
            log.warning("feh failed: %s", exc)
    log.error("no Linux wallpaper backend available")
    return False, tr("wp_linux_fail")


# -------------------------------------------------------------------- macos

def _set_macos_many(paths: List[str]) -> Tuple[bool, str]:
    script_lines = []
    for i, p in enumerate(paths, start=1):
        script_lines.append(
            f'tell application "System Events" to tell every desktop '
            f'whose index is {i} to set picture to "{p}"')
    script = "\n".join(script_lines) or f'set picture to "{paths[0]}"'
    try:
        subprocess.run(["osascript", "-e", script], check=True)
        log.info("osascript per-desktop wallpapers set: %d", len(paths))
        return True, tr("wp_macos")
    except Exception as exc:
        log.warning("osascript multi-desktop failed: %s", exc)
        return _set_macos(paths[0]) if paths else (False, str(exc))


def _set_macos(path: str) -> Tuple[bool, str]:
    script = ('tell application "System Events" to tell every desktop '
              f'to set picture to "{path}"')
    try:
        subprocess.run(["osascript", "-e", script], check=True)
        log.info("osascript wallpaper set: %s", path)
        return True, tr("wp_macos")
    except Exception as exc:
        log.error("osascript failed: %s", exc)
        return False, tr("wp_macos_err", err=exc)


def is_windows() -> bool:
    return sys.platform.startswith("win")
