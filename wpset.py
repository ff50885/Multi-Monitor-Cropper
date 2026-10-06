# wpset.py
import os
import ctypes
from ctypes import wintypes

try:
    import comtypes
    import comtypes.client
    from comtypes import IUnknown, GUID, COMMETHOD, HRESULT
except ImportError:
    pass # app.py sadece gerektiğinde çağıracağı için Windows dışı sistemlerde çökmeyi önler

class IDesktopWallpaper(IUnknown):
    _iid_ = GUID('{B92B56A9-8B55-4E14-9A89-0199BBB6F93B}')
    
    # DİKKAT: COM arayüzlerinde listelenen metodların SIRASI (vtable) değiştirilemez veya atlanamaz.
    _methods_ = [
        COMMETHOD([], HRESULT, 'SetWallpaper', 
                  (['in'], wintypes.LPCWSTR, 'monitorID'), 
                  (['in'], wintypes.LPCWSTR, 'wallpaper')),
        
        # Kullanılmasa bile sıralamanın bozulmaması için burada kalmak ZORUNDA.
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
        try:
            ctypes.windll.ole32.CoInitializeEx(None, 2) # STA thread init
        except Exception:
            pass
        CLSID_DesktopWallpaper = GUID('{C2CF3110-460E-4fc1-B9D0-8A1C0C9CC4BD}')
        self.shell = comtypes.client.CreateObject(CLSID_DesktopWallpaper, interface=IDesktopWallpaper)

    def get_sorted_monitors(self):
        count = self.shell.GetMonitorDevicePathCount()
        monitors = []
        for i in range(count):
            device_path = self.shell.GetMonitorDevicePathAt(i)
            rect = self.shell.GetMonitorRECT(device_path)
            monitors.append((rect.left, rect.top, device_path))
        # Önce üstten alta, sonra soldan sağa fiziksel dizilime göre sırala
        monitors.sort(key=lambda item: (item[1], item[0]))
        return [m[2] for m in monitors]

    def assign_sequential(self, image_paths):
        sorted_paths = self.get_sorted_monitors()
        for i, img_path in enumerate(image_paths):
            if i >= len(sorted_paths): 
                break # Monitör sayısından fazla resim varsa sessizce yoksay
            abs_path = os.path.abspath(img_path)
            self.shell.SetWallpaper(sorted_paths[i], abs_path)
