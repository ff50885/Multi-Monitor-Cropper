# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for DualCropper. Build with:  pyinstaller DualCropper.spec
# (or simply run build_exe.bat on Windows)

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [
    ('dualcropper/locales', 'locales'),          # JSON language files
    ('README.md', '.'),
] + collect_data_files('customtkinter')           # CTk themes (.json)

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['PIL._tkinter_finder', 'dualcropper._win_monitors']
                  + collect_submodules('customtkinter'),
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='DualCropper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # windowed app, no terminal
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,              # optional: icon='app.ico'
)
