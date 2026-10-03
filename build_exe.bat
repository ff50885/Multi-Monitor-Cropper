@echo off
REM ============================================================
REM  DualCropper - Windows .exe build script
REM  Requires Python 3.9+ on the PATH. Output: dist\DualCropper.exe
REM ============================================================
setlocal

where python >nul 2>nul || (echo [HATA] Python bulunamadi. python.org adresinden kurun. & exit /b 1)

echo [1/3] Bagimliliklar yukleniyor...
python -m pip install --upgrade pip >nul
python -m pip install pillow customtkinter pyinstaller || (echo [HATA] pip install basarisiz & exit /b 1)

echo [2/3] PyInstaller calistiriliyor...
python -m PyInstaller --noconfirm --clean DualCropper.spec
if errorlevel 1 (
    echo [HATA] Build basarisiz oldu. Yukaridaki loga bakin.
    exit /b 1
)

echo [3/3] Tamamlandi: dist\DualCropper.exe
dir dist\DualCropper.exe
endlocal
