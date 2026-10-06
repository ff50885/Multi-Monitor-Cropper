import ctypes
from ctypes import wintypes
import math
import platform


class MonitorDetector:
    @staticmethod
    def get_monitors_info():
        if platform.system() != "Windows":
            return [{"res": "1920,1080", "inch": 24.0}]

        try:
            shcore = ctypes.windll.shcore
            set_process_dpi_awareness = shcore.SetProcessDpiAwareness
            set_process_dpi_awareness.argtypes = [wintypes.DWORD]
            set_process_dpi_awareness.restype = ctypes.c_long

            dpi_result = set_process_dpi_awareness(2)
            if dpi_result != 0:
                user32 = ctypes.windll.user32
                set_process_dpi_aware = user32.SetProcessDPIAware
                set_process_dpi_aware.argtypes = []
                set_process_dpi_aware.restype = wintypes.BOOL
                set_process_dpi_aware()
        except (AttributeError, OSError):
            user32 = ctypes.windll.user32
            set_process_dpi_aware = user32.SetProcessDPIAware
            set_process_dpi_aware.argtypes = []
            set_process_dpi_aware.restype = wintypes.BOOL
            set_process_dpi_aware()

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        class MONITORINFOEX(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
                ("szDevice", wintypes.WCHAR * 32),
            ]

        user32.GetMonitorInfoW.argtypes = [
            wintypes.HMONITOR,
            ctypes.POINTER(MONITORINFOEX),
        ]
        user32.GetMonitorInfoW.restype = wintypes.BOOL

        gdi32.CreateDCW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPVOID,
        ]
        gdi32.CreateDCW.restype = wintypes.HDC

        gdi32.GetDeviceCaps.argtypes = [
            wintypes.HDC,
            ctypes.c_int,
        ]
        gdi32.GetDeviceCaps.restype = ctypes.c_int

        gdi32.DeleteDC.argtypes = [wintypes.HDC]
        gdi32.DeleteDC.restype = wintypes.BOOL

        monitors = []

        def monitor_enum_proc(hMonitor, hdcMonitor, lprcMonitor, dwData):
            mi = MONITORINFOEX()
            mi.cbSize = ctypes.sizeof(MONITORINFOEX)

            if not user32.GetMonitorInfoW(hMonitor, ctypes.byref(mi)):
                return True

            width_px = mi.rcMonitor.right - mi.rcMonitor.left
            height_px = mi.rcMonitor.bottom - mi.rcMonitor.top

            hdc = gdi32.CreateDCW(mi.szDevice, None, None, None)

            if hdc:
                width_mm = gdi32.GetDeviceCaps(hdc, 4)
                height_mm = gdi32.GetDeviceCaps(hdc, 6)
                gdi32.DeleteDC(hdc)

                if width_mm > 0 and height_mm > 0:
                    diagonal_inch = math.hypot(width_mm, height_mm) / 25.4
                else:
                    diagonal_inch = 24.0
            else:
                diagonal_inch = 24.0

            monitors.append({
                "res": f"{width_px},{height_px}",
                "inch": round(diagonal_inch)
            })

            return True

        MonitorEnumProc = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HMONITOR,
            wintypes.HDC,
            ctypes.POINTER(wintypes.RECT),
            wintypes.LPARAM,
        )

        callback = MonitorEnumProc(monitor_enum_proc)

        user32.EnumDisplayMonitors.argtypes = [
            wintypes.HDC,
            ctypes.POINTER(wintypes.RECT),
            MonitorEnumProc,
            wintypes.LPARAM,
        ]
        user32.EnumDisplayMonitors.restype = wintypes.BOOL

        if not user32.EnumDisplayMonitors(None, None, callback, 0):
            return []

        return monitors
