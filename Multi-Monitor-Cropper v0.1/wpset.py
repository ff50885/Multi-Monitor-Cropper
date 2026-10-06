# wpset.py
import os
import ctypes
from ctypes import wintypes

try:
    import comtypes
    import comtypes.client
    from comtypes import IUnknown, GUID, COMMETHOD, HRESULT
except ImportError:
    raise ImportError("Install 'comtypes': pip install comtypes")

class IDesktopWallpaper(IUnknown):
    _iid_ = GUID('{B92B56A9-8B55-4E14-9A89-0199BBB6F93B}')
    _methods_ = [
        COMMETHOD([], HRESULT, 'SetWallpaper',
                  (['in'], wintypes.LPCWSTR, 'monitorID'),
                  (['in'], wintypes.LPCWSTR, 'wallpaper')),
        
        COMMETHOD([], HRESULT, 'GetWallpaper',
                  (['in'], wintypes.LPCWSTR, 'monitorID'),
                  (['out'], ctypes.POINTER(wintypes.LPWSTR), 'wallpaper')),
        
        COMMETHOD([], HRESULT, 'GetMonitorDevicePathAt',
                  (['in'], wintypes.UINT, 'monitorIndex'),
                  (['out'], ctypes.POINTER(wintypes.LPWSTR), 'monitorID')),
        
        COMMETHOD([], HRESULT, 'GetMonitorDevicePathCount',
                  (['out'], ctypes.POINTER(wintypes.UINT), 'count')),
                  
        COMMETHOD([], HRESULT, 'GetMonitorRECT',
                  (['in'], wintypes.LPCWSTR, 'monitorID'),
                  (['out'], ctypes.POINTER(wintypes.RECT), 'displayRect')),
    ]

class WPSet:
    def __init__(self):
        CLSID_DesktopWallpaper = GUID('{C2CF3110-460E-4fc1-B9D0-8A1C0C9CC4BD}')
        self.shell = comtypes.client.CreateObject(CLSID_DesktopWallpaper, interface=IDesktopWallpaper)

    def monitor_count(self):
        return self.shell.GetMonitorDevicePathCount()

    def device_path_at(self, index):
        count = self.monitor_count()
        if index >= count:
            raise IndexError(f"Monitor index out of range ({index} >= {count})")
        return self.shell.GetMonitorDevicePathAt(index)

    def set_wallpaper(self, device_path, image_path):
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found: {image_path}")
        
        abs_path = os.path.abspath(image_path)
        self.shell.SetWallpaper(device_path, abs_path)

    def assign_sequential(self, image_paths):
        count = self.monitor_count()
        if len(image_paths) > count:
            raise ValueError("wpset: Theres is more img than monitors.")
        
        for i, img_path in enumerate(image_paths):
            dev_path = self.device_path_at(i)
            self.set_wallpaper(dev_path, img_path)