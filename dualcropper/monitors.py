r"""dualcropper.monitors - enumerate physical monitors and their geometry.

Detection order on Windows (every step is written to dualcropper.log):

 1. ``EnumDisplayMonitors`` + ``GetMonitorInfoW`` - the canonical Win32 way to
    walk every *visible* display.  The callback receives each monitor's RECT in
    virtual-desktop coordinates.  Before enumerating we mark the process
    DPI-aware (per-monitor v2) so a panel running under Windows scaling still
    reports its true pixel size instead of scaled logical pixels.
    This pass also records which monitor is PRIMARY.
 2. ``IDesktopWallpaper::GetMonitorDevicePathCount / GetMonitorDevicePathAt``
    (COM, pure ctypes) - gives exactly the monitor-ID strings that
    ``SetWallpaper`` accepts.  Both lists are ordered left-to-right
    (ties top-to-bottom), so IDs are paired with rects positionally.
 3. If COM fails entirely we fall back to ``\\.\DISPLAYn`` names; per-monitor
    assignment then reports a clear error instead of guessing.

Why not the previous approaches (bug history):
 - Matching GDI device names against IDesktopWallpaper IDs never succeeds ->
   "second monitor not found" even with two real displays.
 - ``GetMonitorRECT`` / ``DESKTOP_MONITOR_INFO`` returned all-zero rects through
   naive ctypes structs -> monitors detected but sized 0x0.
 - Calling EnumDisplayMonitors without DPI awareness returns scaled logical
   pixels -> wrong sizes reported.

Monitor numbering rule used across the app:
    sorted by left edge (x), ties broken by top edge (y) -> index 0..N-1
    "Monitor 1" = leftmost, "Monitor 2" = next, ...
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import List

from .logger import get_logger

IS_WINDOWS = sys.platform.startswith("win")
log = get_logger("monitors")


@dataclass(frozen=True)
class MonitorInfo:
    """One physical display with its position in virtual-desktop coordinates."""
    number: int              # 1-based, left-to-right (ties: top-to-bottom)
    id_string: str           # IDesktopWallpaper monitor ID (or GDI name fallback)
    x: int
    y: int
    width: int
    height: int
    primary: bool = False
    diag_inch: float = 0.0   # real panel diagonal from EDID (0 = unknown)

    @property
    def label(self) -> str:
        return f"{self.number}:{self.width}x{self.height}@({self.x},{self.y})"


def _sort_key(m: "MonitorInfo"):
    return (m.x, m.y)


def _renumber(mons: List["MonitorInfo"]) -> List["MonitorInfo"]:
    mons.sort(key=_sort_key)
    return [MonitorInfo(i + 1, m.id_string, m.x, m.y, m.width, m.height,
                        m.primary, m.diag_inch)
            for i, m in enumerate(mons)]


def list_monitors() -> List[MonitorInfo]:
    """Return all detected monitors, numbered left-to-right."""
    if not IS_WINDOWS:
        mons = _enum_x11_monitors()
        log.info("X11 enumeration: %d monitor(s): %s",
                 len(mons), ", ".join(m.label for m in mons) or "-")
        return mons

    from . import _win_monitors as wm  # Windows-only implementation

    log.info("Windows monitor detection starting (python=%s)",
             sys.version.split()[0])
    dpi_ok = wm.make_process_dpi_aware()
    gdi = wm.enum_gdi_rects()
    log.info("GDI pass: %d visible monitor(s): %s",
             len(gdi), ", ".join(m.label for m in gdi) or "-")
    dw_ids = wm.dw_monitor_ids()
    log.info("IDesktopWallpaper pass: %d device path(s)", len(dw_ids))

    # Real physical sizes from the panels' EDID blocks (centimetres).  This is
    # the only trustworthy source of the diagonal; guessing it from pixels
    # assumes a 16:9 aspect *and* unscaled DPI - when either breaks the GUI
    # shows nonsense like a 32" panel reported as 120".
    diags: List[float] = []
    try:
        diags = wm.physical_diagonals_inch()
        log.info("EDID diagonals: %s",
                 ", ".join(f"{d:.1f}\"" if d else "?" for d in diags) or "-")
    except Exception as exc:
        log.warning("EDID diagonal read failed (%s); inch fields keep manual "
                    "values", exc)

    def attach_diag(mlist: List["MonitorInfo"]) -> List["MonitorInfo"]:
        out = []
        for m in mlist:
            d = 0.0
            if diags and 0 < m.number <= len(diags):
                d = diags[m.number - 1]
            elif diags and 0 < m.number - 1 < len(diags):
                d = diags[m.number - 1]
            out.append(MonitorInfo(m.number, m.id_string, m.x, m.y,
                                   m.width, m.height, m.primary, d))
        return out

    if gdi and dw_ids:
        # Positional match: IDesktopWallpaper enumerates device paths in the
        # same left-to-right display order we derived from the GDI rects.
        gdi_sorted = sorted(gdi, key=_sort_key)
        merged = [MonitorInfo(0, did, m.x, m.y, m.width, m.height, m.primary)
                  for m, did in zip(gdi_sorted, dw_ids)]
        if len(gdi_sorted) != len(dw_ids):
            log.warning("Count mismatch: GDI=%d DW=%d - pairing by index",
                        len(gdi), len(dw_ids))
        # Sanity check: cross-verify each DW rect against the GDI rect it is
        # paired with (GetMonitorRECT slot 13).  A large mismatch means the
        # positional pairing assumption broke down on this machine.
        try:
            dw_rects = wm.dw_monitor_rects()
            for i, (m, r) in enumerate(zip(merged, dw_rects)):
                if r is None:
                    continue
                if abs(r[2] - m.width) > 1 or abs(r[3] - m.height) > 1:
                    log.warning("Pairing check M%d: GDI rect %dx%d vs DW rect "
                                "%dx%d - positional pairing may be wrong",
                                i + 1, m.width, m.height, r[2], r[3])
            log.debug("DW rect cross-check done for %d monitor(s)",
                      min(len(merged), len(dw_rects)))
        except Exception as exc:
            log.debug("DW rect cross-check skipped: %s", exc)
        return attach_diag(_renumber(merged))
    if gdi:
        log.warning("No IDesktopWallpaper IDs available; SetWallpaper may fail. "
                    "Using GDI names.")
        if not dpi_ok:
            log.warning("DPI awareness was applied at runtime (not via manifest): "
                        "the GDI rects above may still be logical/scaled pixels")
        return attach_diag(_renumber(gdi))
    if dw_ids:
        log.warning("GDI returned no monitors but DW has %d IDs; using DW count only.",
                    len(dw_ids))
        return attach_diag(_renumber([MonitorInfo(0, d, 0, 0, 0, 0) for d in dw_ids]))
    log.error("Both enumeration passes returned nothing.")
    return []


def detect_count() -> int:
    return len(list_monitors())


# ------------------------------------------------------------------ non-Windows

def _enum_x11_monitors() -> List[MonitorInfo]:
    """Best-effort X11/xrandr enumeration (development/testing only)."""
    try:
        import re
        import subprocess
        txt = subprocess.run(["xrandr"], capture_output=True, text=True,
                             timeout=5).stdout
        out: List[MonitorInfo] = []
        for line in txt.splitlines():
            m = re.match(r"^(\S+) connected (?:primary )?(?:(\d+)x(\d+)\+(\d+)\+(\d+))?",
                         line)
            if not m or not m.group(2):
                continue
            name = m.group(1)
            w, h, px, py = (int(m.group(k)) for k in (2, 3, 4, 5))
            out.append(MonitorInfo(0, name, px, py, w, h))
        out.sort(key=_sort_key)
        return [MonitorInfo(i + 1, mo.id_string, mo.x, mo.y, mo.width, mo.height)
                for i, mo in enumerate(out)]
    except Exception as exc:
        log.warning("xrandr enumeration failed: %s", exc)
        return []
