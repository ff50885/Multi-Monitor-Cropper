# 🖼️ DualCropper — Çift Monitör Duvar Kağıdı Uygulaması

Tek bir geniş resmi, iki monitörlü kurulumunuzda **fiziksel olarak kesintisiz**
görünecek şekilde bölen, modern arayüzlü Python uygulaması. Verdiğiniz
`DualCropper` geometri algoritması (`dualcropper/core.py`) olduğu gibi korunarak
uygulamanın hesaplama motoru olarak kullanılır.

## Özellikler
| # | Özellik | Nasıl |
|---|---------|-------|
| 1 | Modern UI | `customtkinter` ile koyu temalı, kart tabanlı arayüz + canlı önizleme (kırpma kutuları resim üzerinde gösterilir) |
| 2 | Python + PyInstaller | Saf Python; `DualCropper.spec` ve `build_exe.bat` ile tek dosya `.exe` üretilebilir |
| 3 | Resim içe aktarma | "İçe Aktar" butonu → dosya seçme diyaloğu (PNG/JPG/BMP/WEBP/TIFF) |
| 4 | Ekran yapılandırması | Büyük/küçük ekran inç değeri, çözünürlükler (hazır liste + özel), küçük ekranın konumu (Solda/Sağda), dikey hizalama (Alt/Üst/Orta), ekranlar arası boşluk (cm), kırpma sabitleme (Anchor) |
| 5 | Böl + kaydet + otomatik uygula | Motor, kaynak pikselleri ortak `px/cm` ölçeğiyle iki kırpma kutusuna böler; her kutu panelin native çözünürlüğüne LANCZOS ile yeniden boyutlanır, PNG olarak kaydedilir ve arka plan otomatik değiştirilir |

## Proje Yapısı
```
main.py                 # CustomTkinter GUI (giriş noktası)
dualcropper/
    core.py             # Verdiğiniz geometri motoru (saf stdlib, yan etkisiz)
    wallpaper.py        # Windows/Linux/macOS arka plan değiştirme katmanı
requirements.txt
DualCropper.spec        # PyInstaller yapılandırması
build_exe.bat           # Windows'ta çift tıkla -> dist\DualCropper.exe
```

## Çalıştırma
```bash
pip install -r requirements.txt
python main.py
```

## .exe Üretimi (Windows)
```bat
build_exe.bat
```
veya elle:
```bat
pip install pyinstaller
pyinstaller --noconfirm --clean DualCropper.spec
:: çıktı: dist\DualCropper.exe
```

## Kullanım Akışı
1. **İçe Aktar** ile panoramik/geniş resmi seçin (önizleme anında görünür).
2. **Monitörler** kartında inç, çözünürlük, konum, hizalama ve cm cinsinden
   çerçeve boşluğunu girin — kırpma kutuları önizlemede canlı güncellenir.
3. **⚡ Üret ve Uygula**: `~/DualCropper/` klasörüne iki PNG yazılır ve
   (anahtar açıksa) masaüstü arka planı monitör başına otomatik ayarlanır.

## Arka Plan Değiştirme Notları
- **Windows**: `SystemParametersInfoW` + çoklu monitör için `HKCU\Control Panel\Desktop`
  registry mekanizması; desteklenmezse tek duvar kağıdına zarif düşüş (fallback).
- **Linux**: `gsettings` (GNOME) veya `feh`.
- **macOS**: AppleScript (`osascript`).
- Hata durumunda uygulama çökmez; durum çubuğunda açıklayıcı mesaj gösterilir.

## Algoritmanın İşleyişi (kısaca)
Motor, her panelin 16:9 fiziksel genişliğini cm olarak hesaplar, kaynak resme
sığan en büyük ortak ölçeği (`px_per_cm = min(genişlik sığdırması, yükseklik
sığdırması)`) bulur ve bezel boşluğunu dikiş hattına yerleştirerek iki kırpma
dikdörtgeni üretir. Böylece her panele düşen görsel aynı fiziksel büyütmeyle
çizilir; çizgiler ekranlar arasında kaymadan devam eder.
