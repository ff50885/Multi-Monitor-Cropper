r"""dualcropper._win_monitors - Windows-only monitor enumeration primitives.

Imported lazily (only when sys.platform starts with "win") by
``dualcropper.monitors`` so the rest of the package stays importable on
Linux/macOS.  Every ctypes call is made *inside* functions; nothing at module
level touches windll, which keeps PyInstaller's analysis safe.

Public helpers:
    make_process_dpi_aware()  -> request per-monitor-v2 DPI awareness
    enum_gdi_rects()          -> List[MonitorInfo] via EnumDisplayMonitors
    dw_monitor_ids()          -> IDesktopWallpaper device-path IDs via COM
    dw_instance()             -> shared COM-object factory (used by wallpaper)
    vtable_slot(obj, idx)     -> callable for a COM vtable slot
"""

from __future__ import annotations

import ctypes
import math
import os
import sys
from typing import List, Optional, Tuple

from .logger import get_logger
from .monitors import MonitorInfo

log = get_logger("win_monitors")

# --------------------------------------------------------- DPI awareness

def make_process_dpi_aware() -> bool:
    """Ask Windows for per-monitor-v2 DPI awareness.

    Returns True only when the process was *already* DPI-aware before this
    call (via the embedded application manifest - see main.py / the exe
    build).  When it returns False the awareness was switched on mid-session:
    every window handle created earlier keeps its old logical-coordinate
    mapping and EnumDisplayMonitors may still report stale/scaled rects.
    That is exactly why an earlier build logged "ok" yet produced bogus
    diagonals: the GUI window existed before the first Detect press.

    The correct fix is declaring dpiAwareness in the PE manifest so the value
    is in effect from process start; runtime calls remain as a best-effort
    fallback for plain `python main.py` runs without a rebuilt manifest.
    """
    already = False
    try:
        user32 = ctypes.windll.user32
        ctx = user32.GetProcessDpiAwarenessContext()
        if ctx:
            # DPI_AWARENESS_CONTEXT values are negative sentinels (-1..-4).
            v = ctypes.cast(ctx, ctypes.c_int).value if isinstance(
                ctx, int) else ctypes.c_long(ctx & 0xFFFFFFFF).value
            try:
                v = ctypes.c_long(v).value
            except Exception:
                pass
            if v in (-1, -2, -3, -4):
                already = True
                log.debug("process already DPI-aware via manifest (ctx=%d)", v)
    except Exception as exc:
        log.debug("GetProcessDpiAwarenessContext probe failed: %s", exc)

    if already:
        return True

    try:
        try:
            hr = ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_V2
            if hr == 0:
                log.warning("shcore.SetProcessDpiAwareness(PER_MONITOR_V2) applied "
                            "at runtime; rects may be stale for windows created "
                            "earlier (embed a DPI manifest for reliable results)")
                return False
            log.debug("shcore.SetProcessDpiAwareness returned %s", _fmt_hr(hr))
        except Exception as exc:
            log.debug("shcore.SetProcessDpiAwareness raised: %s", exc)
        # Win10 1607+: SetProcessDpiAwarenessContext(-4 = PER_MONITOR_V2)
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            log.warning("SetProcessDpiAwarenessContext(PER_MONITOR_V2) applied "
                        "at runtime; rects may be stale for earlier windows")
            return False
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-3)):
            log.warning("SetProcessDpiAwarenessContext(SYSTEM_AWARE) applied "
                        "at runtime")
            return False
        user32.SetProcessDPIAware()
        log.warning("SetProcessDPIAware() fallback called at runtime")
        return False
    except Exception as exc:
        log.error("DPI awareness unavailable (%s); rects may be scaled", exc)
        return False


# ------------------------------------------------------------------ EDID size
# Real physical panel size lives in the monitor's EDID block (bytes 21/22 of
# the CEA extension = width/height in cm).  Reading it fixes two long-standing
# complaints:
#   - diagonal guessed from pixels alone assumes a 16:9 aspect AND unscaled
#     DPI; either assumption being false yields nonsense like "120\"".
#   - non-16:9 panels (16:10, 21:9) were mis-sized by the pixel guess.

class _DEVICE_ID(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("Id", ctypes.c_uint32),
                ("Data", ctypes.c_ubyte * 1)]


class _DISPLAY_DEVICEW(ctypes.Structure):
    # Bu sınıf C tarafındaki yapı ile birebir eşleşmelidir, eksik alanlar bellek kaymasına yol açar.
    _fields_ = [("cb", ctypes.c_uint32), ("DeviceName", ctypes.c_wchar * 32),
                ("DeviceString", ctypes.c_wchar * 128),
                ("StateFlags", ctypes.c_uint32), 
                ("DeviceID", ctypes.c_wchar * 128), 
                ("DeviceIDOffset", ctypes.c_uint32)]


_DISPLAY_DEVICE_ACTIVE = 0x00000001
_DISPLAY_DEVICE_PRIMARY_DEVICE = 0x00000004


def enum_display_devices() -> List[Tuple[str, str, bool]]:
    """(device_name, instance_id, is_primary) for every active display device."""
    user32 = ctypes.windll.user32
    out: List[Tuple[str, str, bool]] = []
    i = 0
    while True:
        dd = _DISPLAY_DEVICEW()
        dd.cb = ctypes.sizeof(dd)
        if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
            break
        i += 1
        if dd.StateFlags & _DISPLAY_DEVICE_ACTIVE:
            out.append((dd.DeviceName, dd.DeviceID,
                        bool(dd.StateFlags & _DISPLAY_DEVICE_PRIMARY_DEVICE)))
            log.debug("display device: %s id=%s primary=%s flags=0x%X",
                      dd.DeviceName, dd.DeviceID,
                      bool(dd.StateFlags & _DISPLAY_DEVICE_PRIMARY_DEVICE),
                      dd.StateFlags)
    return out


def read_edid(device_instance_id: str) -> Optional[bytes]:
    """Query DISPLAY_CONFIGEDID for one device instance path; bytes or None."""
    import re as _re
    m = _re.search(r"(UID[0-9A-Fa-f]+)", device_instance_id or "")
    if not m:
        return None
    adapter = device_instance_id.split("\\")[0]      # e.g. \\.\DISPLAY
    uid = m.group(1)
    guid = getattr(ctypes.windll.user32, "GUID_CLASS_MONITOR", None)
    if guid is None:
        # GUID_DEVCLASS_MONITOR {4d36e960-e325-11ce-bfc1-08002be10318}
        buf = ctypes.create_unicode_buffer(
            "{4d36e960-e325-11ce-bfc1-08002be10318}")
        g = GUID()
        if ctypes.windll.ole32.CLSIDFromString(buf, ctypes.byref(g)) != 0:
            return None
    else:
        g = guid
    q = ctypes.create_unicode_buffer(f"{adapter}\\{uid}")
    QueryDisplayConfig = ctypes.windll.user32.QueryDisplayConfig
    QueryDisplayConfig.restype = ctypes.c_long
    sz = ctypes.c_uint32(0)
    rc = QueryDisplayConfig(0x00000002, None, None, None, None,
                            ctypes.byref(sz), ctypes.byref(g), q, None,
                            ctypes.byref(sz), None)
    if rc != 0 or sz.value <= 4:
        log.debug("QueryDisplayConfig(EDID,%s) rc=%s size=%d", uid,
                  _fmt_hr(rc), sz.value)
        return None
    buf = (ctypes.c_char * sz.value)()
    rc = QueryDisplayConfig(0x00000002, None, None, None, None,
                            ctypes.byref(sz), ctypes.byref(g), q, buf,
                            ctypes.byref(sz), None)
    if rc != 0:
        log.debug("QueryDisplayConfig(EDID,%s) fetch rc=%s", uid, _fmt_hr(rc))
        return None
    data = bytes(buf)[:sz.value]
    log.debug("EDID raw for %s: %d byte(s) %s%s", uid, len(data),
              data[:32].hex(), "..." if len(data) > 32 else "")
    return data


def edid_physical_cm(edid: bytes):
    """Parse EDID -> (width_cm, height_cm) real panel size, or None."""
    def base_cm(b: bytes):
        w, h = b[18], b[19]
        if 0 < w < 128 and 0 < h < 128:
            return float(w), float(h)
        return None

    if not edid or len(edid) < 128:
        return None
    result = None
    # Descriptor block 0x80 carries the established/base native resolution.
    for off in range(54, 126, 18):
        blk = edid[off:off + 18]
        if blk[0] == 0 and blk[1] == 0 and blk[2] == 0 and blk[3] == 0x11:
            result = base_cm(blk)
            log.debug("EDID base-timing descriptor @0x%02X -> %s cm", off, result)
            break
    # CEA extension (byte 120 count, tag code 2 = detailed timings).
    if len(edid) >= 193 and edid[120] >= 2 and edid[124] == 0x02:
        w, h = edid[145], edid[146]
        if 0 < w < 128 and 0 < h < 128:
            result = (float(w), float(h))
            log.debug("EDID CEA max-size bytes -> %s cm", result)
    return result


def physical_diagonals_inch() -> List[float]:
    """Real diagonal (inches) per active display device, EnumDisplayDevices order."""
    diags: List[float] = []
    for name, dev_id, primary in enum_display_devices():
        d = None
        try:
            edid = read_edid(dev_id)
            if edid:
                cm = edid_physical_cm(edid)
                if cm:
                    d = math.hypot(cm[0], cm[1]) / 2.54
        except Exception as exc:
            log.debug("EDID read failed for %s: %s", name, exc)
        diags.append(d if d else 0.0)
        log.info("EDID diagonal for %s (%s): %s", name,
                 "primary" if primary else "secondary",
                 f"{d:.1f}\"" if d else "unknown")
    return diags


# ------------------------------------------------------------ GDI rects

class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", ctypes.c_uint32)]


_MONITOR_PRIMARYFLAG = 1  # MONITORINFOF_PRIMARY


def enum_gdi_rects() -> List[MonitorInfo]:
    """Every visible monitor's physical rect via EnumDisplayMonitors."""
    user32 = ctypes.windll.user32
    out: List[MonitorInfo] = []

    def _cb(hmon, hdc, lprc, data):
        mi = _MONINFO()
        mi.cbSize = ctypes.sizeof(_MONINFO)
        ok = user32.GetMonitorInfoW(ctypes.c_void_p(hmon), ctypes.byref(mi))
        r = mi.rcMonitor
        w, h = r.right - r.left, r.bottom - r.top
        if not ok or w <= 0 or h <= 0:
            log.warning("GetMonitorInfoW ok=%s rect=(%d,%d,%d,%d) -> skipped",
                        bool(ok), r.left, r.top, r.right, r.bottom)
            return True
        name = f"\\\\.\\DISPLAY{len(out) + 1}"
        out.append(MonitorInfo(0, name, r.left, r.top, w, h,
                               primary=bool(mi.dwFlags & _MONITOR_PRIMARYFLAG)))
        log.debug("GDI monitor: %s primary=%s flags=%d",
                  out[-1].label, out[-1].primary, mi.dwFlags)
        return True

    MonEnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                                     ctypes.c_void_p, ctypes.POINTER(_RECT),
                                     ctypes.c_void_p)
    cb = MonEnumProc(_cb)
    if not user32.EnumDisplayMonitors(None, None, cb, None):
        log.warning("EnumDisplayMonitors failed (err=%d)",
                    ctypes.windll.kernel32.GetLastError())
    return out


# ------------------------------------------- IDesktopWallpaper COM plumbing

# CLSID of the DesktopWallpaper coclass.  NOTE: this is NOT the IID of the
# IDesktopWallpaper interface (they are different GUIDs!).  Passing the IID as
# the CLSID to CoCreateInstance yields REGDB_E_CLASSNOTREG (0x80040154), which
# is exactly what earlier builds did - the log line
#   "CoCreateInstance(hr=-0x7ffbfeac)"  ==  -0x80040154 signed -> class not found
# was a wrong-CLSID bug, not a missing Windows feature.
_CLSID_DW = "{C2CF3110-460E-4fc1-B9D0-8A1C0C9CC4BD}"
_IID_DW = "{B92B56A9-8B55-4E14-9A89-0199BBB6F93B}"


def _fmt_hr(hr: int) -> str:
    """HRESULT in unsigned hex (e.g. 0x80040154) regardless of sign convention."""
    return f"0x{(hr & 0xFFFFFFFF):08X}"

# IDesktopWallpaper vtable (documented, stable Win8..Win11):
#   0 QueryInterface  1 AddRef  2 Release
#   3 Enable           4 GetStatus
#  11 GetMonitorDevicePathCount
#  12 GetMonitorDevicePathAt(UINT index, out BSTR)
#  13 GetMonitorRECT(UINT index, out RECT)   <- used only as a cross-check of
#      the positional GDI<->DW pairing; primary rects come from GDI.
#  17 SetWallpaper(PCWSTR devicePath, PCWSTR wallpaper)
#  18 GetWallpaper(PCWSTR devicePath, out BSTR)      <- verification read-back
#  20 SetBackgroundColor(COLORREF)
#  21 GetBackgroundColor(out COLORREF)
#  22 SetWallpaperPosition(DW_WALLPAPER)
#  23 Apply()
SLOT_RELEASE = 2
SLOT_SET_WALLPAPER = 3
SLOT_GET_WALLPAPER = 4
SLOT_GET_PATH_AT = 5
SLOT_GET_COUNT = 6
SLOT_GET_MONITOR_RECT = 7
SLOT_SET_BACKGROUND_COLOR = 8
SLOT_SET_WALLPAPER_POSITION = 10
SLOT_GET_STATUS = 17
SLOT_ENABLE = 18

DW_BACKEND_SOLIDCOLOR = 0
DW_BACKEND_STRETCH = 6


def _set_ret_types(punk) -> None:
    """Give the raw vtable callables correct restypes.

    Without argtypes/restype, ctypes assumes the function returns a C *int*.
    Out-pointer slots such as GetMonitorDevicePathCount (returns HRESULT and
    writes the count through the pointer) then look like huge/invalid values -
    e.g. logged as "reports 3221225472 monitor(s)" - and every later step
    fails.  Setting restype=c_long makes HRESULT sign interpretation exact.

    IMPORTANT: the returned callable from vtable_slot() is a NEW ctypes object
    each time; mutating its restype has no effect on later lookups unless the
    callable is kept alive.  We therefore cache one configured callable per
    (object, slot) pair here and callers must use dw_get_slot().
    """
    cache = getattr(punk, "_dw_slots", None)
    if cache is None:
        cache = {}
        try:
            punk._dw_slots = cache
        except Exception:
            pass

    def _slot(idx):
        if idx not in cache:
            f = vtable_slot(punk, idx)
            f.restype = ctypes.c_long
            cache[idx] = f
        return cache[idx]

    for idx in (SLOT_ENABLE, SLOT_GET_STATUS, SLOT_GET_COUNT,
                SLOT_GET_PATH_AT, SLOT_GET_MONITOR_RECT, SLOT_SET_WALLPAPER,
                SLOT_SET_BACKGROUND_COLOR, SLOT_SET_WALLPAPER_POSITION):
        try:
            _slot(idx)
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("restype normalisation skipped for slot %d: %s", idx, exc)
    return cache


def dw_get_slot(punk, idx: int):
    """Return the cached, correctly-typed callable for COM vtable slot *idx*."""
    cache = getattr(punk, "_dw_slots", None)
    if cache is not None and idx in cache:
        return cache[idx]
    f = vtable_slot(punk, idx)
    f.restype = ctypes.c_long
    return f


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def make_guid(text: str) -> GUID:
    guid = GUID()
    if ctypes.windll.ole32.CLSIDFromString(text, ctypes.byref(guid)) != 0:
        raise OSError(f"CLSIDFromString failed for {text}")
    return guid


def vtable_slot(obj_ptr, index, restype=ctypes.c_long):
    """Return a callable for slot *index* of a COM object's vtable."""
    vt = ctypes.cast(ctypes.cast(obj_ptr, ctypes.POINTER(ctypes.c_void_p))[0],
                     ctypes.POINTER(ctypes.c_void_p))
    return ctypes.cast(vt[index], ctypes.WINFUNCTYPE(restype))


def dw_instance() -> Tuple[ctypes.c_void_p, "callable", bool]:
    """Create the IDesktopWallpaper COM object.

    Returns (punk, release_fn, must_co_uninitialize).  Raises OSError with a
    decoded HRESULT when creation fails.

    Threading note: CoInitializeEx is called with COINIT_APARTMENTTHREADED
    (STA, 2) - the model IDesktopWallpaper expects; the previous build used
    COINIT_MULTITHREADED and SetWallpaper silently no-opped on some systems.
    If the current thread already lives in an MTA apartment we still proceed
    (creation usually works) but log it loudly so the log explains behaviour.
    We *always* pair our own successful init with CoUninitialize on teardown.
    """
    ole32 = ctypes.windll.ole32
    hr_co = ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED (STA)
    if hr_co in (0, -0x7FFFE9FA):          # S_OK / S_FALSE (already init here)
        must_uninit = hr_co == 0
    else:                                  # e.g. RPC_E_CHANGED_MODE (-0x7FFFC685)
        log.warning("CoInitializeEx returned %s; continuing without owning "
                    "the apartment", _fmt_hr(hr_co))
        must_uninit = False
    try:
        punk = ctypes.c_void_p()
        clsid, iid = make_guid(_CLSID_DW), make_guid(_IID_DW)
        hr = ole32.CoCreateInstance(
            ctypes.byref(clsid), None,
            ctypes.c_uint(1 | 4),           # CLSCTX_INPROC_SERVER | LOCAL
            ctypes.byref(iid), ctypes.byref(punk))
        log.debug("CoCreateInstance(CLSID=%s IID=%s) -> hr=%s ptr=%s",
                  _CLSID_DW, _IID_DW, _fmt_hr(hr), hex(punk.value or 0))
        if hr < 0 or not punk.value:
            raise OSError(
                f"CoCreateInstance(CLSID_DesktopWallpaper) failed: "
                f"{_describe_hr(hr)} [{_fmt_hr(hr)}]")
        return punk, vtable_slot(punk, SLOT_RELEASE, ctypes.c_ulong), must_uninit
    except Exception:
        # Never leave an apartment we initialised dangling on failure.
        if must_uninit:
            try:
                ole32.CoUninitialize()
            except Exception:
                pass
        raise


_HRESULT_NAMES = {
    0x00000000: "S_OK",
    0x4001000C: "S_FALSE (DW already enabled)",
    0x80004001: "E_NOTIMPL (method not implemented)",
    0x80004002: "E_NOINTERFACE (interface not supported by this COM class)",
    0x80004005: "E_FAIL (unspecified failure)",
    0x8000FFFF: "E_UNEXPECTED",
    0x80010106: "RPC_E_CHANGED_MODE (apartment already initialised in another model)",
    0x80010117: "RPC_E_CALL_REJECTED",
    0x80040154: "REGDB_E_CLASSNOTREG (COM class not registered)",
    0x800401F0: "CO_E_NOTINITIALIZED (COM was not initialised)",
    0x80070005: "E_ACCESSDENIED (permission denied)",
    0x80070057: "E_INVALIDARG (bad argument)",
    0xC0021005: "Unknown DesktopWallpaper error",
}


def _describe_hr(hr: int) -> str:
    key = hr & 0xFFFFFFFF
    name = _HRESULT_NAMES.get(key)
    return f"{name}" if name else f"HRESULT {_fmt_hr(hr)}"


def dw_monitor_ids() -> List[str]:
    """Device-path IDs accepted by IDesktopWallpaper::SetWallpaper.

    Tries the comtypes-generated proxy first (robust, marshals out-params
    correctly); falls back to raw ctypes vtable calls when comtypes is absent.
    """
    ids = _dw_ids_comtypes()
    if ids:
        return ids
    try:
        punk, release, must_uninit = dw_instance()
    except Exception as exc:
        log.warning("IDesktopWallpaper creation failed: %s", exc)
        return []
    try:
        _set_ret_types(punk)
        get_count = dw_get_slot(punk, SLOT_GET_COUNT)
        get_path = dw_get_slot(punk, SLOT_GET_PATH_AT)
        count = ctypes.c_uint(0)
        hr = get_count(punk, ctypes.byref(count))
        if hr < 0:
            log.warning("GetMonitorDevicePathCount failed: %s [%s]",
                        _describe_hr(hr), _fmt_hr(hr))
            return []
        if not (1 <= count.value <= 64):
            log.error("GetMonitorDevicePathCount returned implausible value %d "
                      "(hr=%s) - treating DW pass as unavailable",
                      count.value, _fmt_hr(hr))
            return []
        log.info("IDesktopWallpaper reports %d monitor(s)", count.value)
        ids: List[str] = []
        for i in range(count.value):
            bstr = ctypes.c_wchar_p()
            hr = get_path(punk, ctypes.c_uint(i), ctypes.byref(bstr))
            if hr >= 0 and bstr.value:
                ids.append(bstr.value)
                log.debug("DW id[%d] = %s", i, bstr.value)
            else:
                log.warning("GetMonitorDevicePathAt(%d) failed: %s [%s] value=%r",
                            i, _describe_hr(hr), _fmt_hr(hr), bstr.value)
            if bstr.value is not None:
                try:
                    ctypes.windll.oleaut32.SysFreeString(bstr)
                except Exception:
                    pass
        return ids
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


def dw_monitor_rects() -> List[Tuple[int, int, int, int]]:
    """IDesktopWallpaper::GetMonitorRECT per index, as (left, top, w, h).

    Used only to cross-check the positional GDI<->DW pairing in monitors.py;
    entries are None when the call fails so the check can skip them.
    """
    try:
        punk, release, must_uninit = dw_instance()
    except Exception as exc:
        log.debug("dw_monitor_rects: DW unavailable (%s)", exc)
        return []
    try:
        _set_ret_types(punk)
        get_count = dw_get_slot(punk, SLOT_GET_COUNT)
        get_rect = dw_get_slot(punk, SLOT_GET_MONITOR_RECT)
        get_path = dw_get_slot(punk, SLOT_GET_PATH_AT)
        count = ctypes.c_uint(0)
        if get_count(punk, ctypes.byref(count)) < 0 or not (1 <= count.value <= 64):
            return []
        rects: List[Tuple[int, int, int, int]] = []
        for i in range(count.value):
            r = _RECT()
            bstr = ctypes.c_wchar_p()
            # Önce index'e ait string cihaz yolunu (monitorID) çek
            if get_path(punk, ctypes.c_uint(i), ctypes.byref(bstr)) >= 0 and bstr.value:
                # Index yerine doğrudan string referansını gönder
                hr = get_rect(punk, bstr.value, ctypes.byref(r))
                if hr >= 0:
                    rects.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
                else:
                    rects.append(None)
                try:
                    ctypes.windll.oleaut32.SysFreeString(bstr)
                except Exception:
                    pass
            else:
                rects.append(None)
        return rects
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


# ---------------------------------------------------------------- comtypes path
# comtypes ships a generated proxy for IDesktopWallpaper (its typelib is in
# the Windows registry).  When available we prefer it: out-parameters are
# marshalled correctly and every call raises COMError with the exact HRESULT.

_COMTYPES_PROXY = None          # cached module, or False when unavailable


def _comtypes_dw_proxy():
    """Return the generated comtypes.gen DesktopWallpaper module, or None."""
    global _COMTYPES_PROXY
    if _COMTYPES_PROXY is not None:
        return _COMTYPES_PROXY or None
    try:
        import comtypes.client
        mod = comtypes.client.GetModule("DesktopWallpaper.dll")
        _COMTYPES_PROXY = mod
        log.info("comtypes proxy generated for DesktopWallpaper.dll "
                 "(module=%s)", getattr(mod, "__name__", "?"))
        return mod
    except Exception as exc:
        log.debug("comtypes proxy unavailable (%s); using raw ctypes vtable", exc)
        _COMTYPES_PROXY = False
        return None


def _dw_ids_comtypes() -> List[str]:
    """Monitor device paths via the comtypes-generated interface (preferred)."""
    mod = _comtypes_dw_proxy()
    if mod is None:
        return []
    import comtypes
    try:
        comtypes.CoInitialize()
    except Exception as exc:
        log.debug("CoInitialize (comtypes): %s", _fmt_hr(
            getattr(exc, "hresult", 0) or 0))
    try:
        dw = comtypes.CoCreateInstance(mod.CLSID_DesktopWallpaper,
                                      interface=mod.IDesktopWallpaper)
        count = dw.GetMonitorDevicePathCount()
        if not (1 <= count <= 64):
            log.error("comtypes GetMonitorDevicePathCount returned %d - ignored",
                      count)
            return []
        ids = [dw.GetMonitorDevicePathAt(i) for i in range(count)]
        log.info("IDesktopWallpaper (comtypes) reports %d monitor(s)", len(ids))
        for i, s in enumerate(ids):
            log.debug("DW id[%d] = %s", i, s)
        return ids
    except comtypes.COMError as exc:
        log.warning("comtypes DW enumeration failed: %s [%s]",
                    _describe_hr(exc.hresult), _fmt_hr(exc.hresult))
        return []
    except Exception as exc:
        log.warning("comtypes DW enumeration error: %s", exc)
        return []
    finally:
        try:
            comtypes.CoUninitialize()
        except Exception:
            pass


def dw_set_wallpapers_comtypes(dev_paths: List[str], files: List[str]):
    """Assign wallpapers per monitor through comtypes; None if unavailable.

    Returns (applied, failures) where failures is [(idx, hr_hex, name)].
    """
    mod = _comtypes_dw_proxy()
    if mod is None:
        return None
    import comtypes
    try:
        comtypes.CoInitialize()
    except Exception:
        pass
    applied, failures = 0, []
    try:
        dw = comtypes.CoCreateInstance(mod.CLSID_DesktopWallpaper,
                                       interface=mod.IDesktopWallpaper)
        try:
            dw.Enable(True)
            log.debug("IDesktopWallpaper::Enable(True) ok")
        except comtypes.COMError as exc:
            log.debug("Enable(True) hr=%s (continuing)", _fmt_hr(exc.hresult))
        try:
            status = dw.GetStatus()
            log.debug("IDesktopWallpaper::GetStatus backend=%d", int(status))
        except comtypes.COMError as exc:
            log.debug("GetStatus hr=%s", _fmt_hr(exc.hresult))
        for i, (dev, img) in enumerate(zip(dev_paths, files)):
            try:
                dw.SetWallpaper(dev, os.path.abspath(img))
                applied += 1
                log.info("SetWallpaper (comtypes) monitor %d OK file=%s",
                         i + 1, os.path.basename(img))
            except comtypes.COMError as exc:
                hr = exc.hresult & 0xFFFFFFFF
                failures.append((i + 1, f"0x{hr:08X}", _describe_hr(exc.hresult)))
                log.error("SetWallpaper (comtypes) monitor %d FAILED: %s [0x%08X]",
                          i + 1, _describe_hr(exc.hresult), hr)
        # Verification read-back so the log proves what each screen shows now.
        for i, dev in enumerate(dev_paths):
            try:
                cur = dw.GetWallpaper(dev)
                log.info("verify monitor %d wallpaper = %s", i + 1, cur)
            except comtypes.COMError as exc:
                log.debug("GetWallpaper(%d) hr=%s", i + 1, _fmt_hr(exc.hresult))
        return applied, failures
    except comtypes.COMError as exc:
        log.warning("comtypes DW creation failed: %s [%s]",
                    _describe_hr(exc.hresult), _fmt_hr(exc.hresult))
        return None
    except Exception as exc:
        log.warning("comtypes DW assignment error: %s", exc)
        return None
    finally:
        try:
            comtypes.CoUninitialize()
        except Exception:
            pass

