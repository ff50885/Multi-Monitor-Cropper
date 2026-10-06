# app.py
import sys
import ctypes

# DPI Awareness ilk işlem olmalıdır (Tk() çağrılmadan önce)
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageTk, ImageDraw, ImageFilter, ImageOps
import os
import re

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    TK_BASE = TkinterDnD.Tk
    HAS_DND = True
except ImportError:
    TK_BASE = tk.Tk
    HAS_DND = False

from cropper import Cropper, VerticalAlign
from monitor import MonitorDetector

# Yeni Pillow sürümleri için resampling uyumluluğu
RESAMPLE = getattr(Image.Resampling, 'LANCZOS', getattr(Image, 'LANCZOS', 1))

class MultiMonitorCropperApp(TK_BASE):
    def __init__(self):
        super().__init__()
        self.title("Multi Monitor Cropper")
        self.geometry("1150x850")

        self.BG_MAIN = "#12261A"      
        self.BG_PANEL = "#1C3626"     
        self.BG_ENTRY = "#162C1F"     
        self.FG_TEXT = "#FFFFFF"      
        self.FG_MUTED = "#93A899"     
        self.BTN_PRIMARY = "#CDE3B6"  
        self.BTN_TEXT = "#12261A"     
        self.ACCENT = "#5E8C6A"       
        
        self.FONT_SERIF = ("Georgia", 24, "bold")      
        self.FONT_SUBTITLE = ("Segoe UI", 10, "italic")
        self.FONT_MAIN = ("Segoe UI", 10)
        self.FONT_BTN = ("Segoe UI", 11, "bold")

        self.configure(bg=self.BG_MAIN)
        self.setup_ttk_styles()

        self.source_image = None
        self.source_path = ""
        self.photo_img = None
        self._button_images = {} 
        self.auto_detect_enabled = False
        self.panel_widgets = [] 

        self.num_panels_var = tk.StringVar(value="2")
        self.bezel_var = tk.StringVar(value="0.0")
        self.align_var = tk.StringVar(value="Bottom")
        self.fit_mode_var = tk.StringVar(value="Crop")
        
        self.pan_x = 0.5
        self.pan_y = 0.5
        self.drag_start_x = 0
        self.drag_start_y = 0
        self._preview_job = None # Debounce optimizasyonu için
        
        self.panel_vars = [
            {"inch": tk.StringVar(value="24.0"), "res": tk.StringVar(value="1920,1080"), "x_off": tk.StringVar(value="0.0"), "y_off": tk.StringVar(value="0.0")},
            {"inch": tk.StringVar(value="32.0"), "res": tk.StringVar(value="3840,2160"), "x_off": tk.StringVar(value="0.0"), "y_off": tk.StringVar(value="0.0")},
            {"inch": tk.StringVar(value="24.0"), "res": tk.StringVar(value="1920,1080"), "x_off": tk.StringVar(value="0.0"), "y_off": tk.StringVar(value="0.0")},
        ]
        
        self.bezel_var.trace_add("write", self.schedule_preview)
        self.align_var.trace_add("write", self.schedule_preview)
        self.fit_mode_var.trace_add("write", self.schedule_preview)
        self.num_panels_var.trace_add("write", lambda *args: self.on_panel_count_changed())
        
        for p_vars in self.panel_vars:
            p_vars["inch"].trace_add("write", self.schedule_preview)
            p_vars["res"].trace_add("write", self.schedule_preview)
            p_vars["x_off"].trace_add("write", self.schedule_preview)
            p_vars["y_off"].trace_add("write", self.schedule_preview)

        self.create_widgets()
        self.build_dynamic_panels()

    def schedule_preview(self, *args):
        # Yüksek çözünürlüklü görsellerde canvas resize sırasında çökmeyi önlemek için Debounce
        if self._preview_job is not None:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(150, self.update_preview)

    def setup_ttk_styles(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TCombobox', fieldbackground=self.BG_ENTRY, background=self.BG_PANEL, 
                        foreground=self.FG_TEXT, bordercolor=self.BG_ENTRY, arrowcolor=self.FG_TEXT)
        style.map('TCombobox', fieldbackground=[('readonly', self.BG_ENTRY)], selectbackground=[('readonly', self.ACCENT)])

    def create_widgets(self):
        control_frame = tk.Frame(self, width=390, bg=self.BG_PANEL)
        control_frame.pack(side=tk.LEFT, fill=tk.Y, padx=15, pady=15)
        control_frame.pack_propagate(False)

        inner_frame = tk.Frame(control_frame, bg=self.BG_PANEL)
        inner_frame.pack(fill=tk.BOTH, expand=True, padx=25, pady=25)

        lbl_title = tk.Label(inner_frame, text="🌿 Cropper", bg=self.BG_PANEL, fg=self.FG_TEXT, font=self.FONT_SERIF)
        lbl_title.pack(anchor=tk.W)
        lbl_subtitle = tk.Label(inner_frame, text="Bring Nature to Displays", bg=self.BG_PANEL, fg=self.FG_MUTED, font=self.FONT_SUBTITLE)
        lbl_subtitle.pack(anchor=tk.W, pady=(0, 20))

        self.btn_auto = self._create_pill_button(inner_frame, "Auto-Detect: OFF", self.toggle_auto_detect, 
                                                 width=320, height=35, bg=self.BG_PANEL, btn_bg=self.BG_ENTRY, fg=self.FG_TEXT)
        self.btn_auto.pack(pady=(5, 0))

        lbl_disclaimer = tk.Label(inner_frame, text="* Auto-detect results may differ due to OS scaling.", bg=self.BG_PANEL, fg=self.FG_MUTED, font=("Segoe UI", 8))
        lbl_disclaimer.pack(pady=(0, 10))

        self.btn_load = self._create_pill_button(inner_frame, "📥  Select Image", self.load_image, 
                                                 width=320, height=45, bg=self.BG_PANEL, btn_bg=self.BTN_PRIMARY, fg=self.BTN_TEXT)
        self.btn_load.pack(pady=5)
        
        self.lbl_file = tk.Label(inner_frame, text="No file selected", bg=self.BG_PANEL, fg=self.FG_MUTED, font=self.FONT_MAIN)
        self.lbl_file.pack(pady=(0, 15))

        tk.Frame(inner_frame, bg=self.ACCENT, height=1).pack(fill=tk.X, pady=10)

        static_config = tk.Frame(inner_frame, bg=self.BG_PANEL)
        static_config.pack(fill=tk.X, pady=5)

        self._create_label(static_config, "Total Displays:", 0, 0)
        self.num_opt = tk.OptionMenu(static_config, self.num_panels_var, "1", "2", "3")
        self.num_opt.config(bg=self.BG_ENTRY, fg=self.FG_TEXT, font=self.FONT_MAIN, relief="flat", highlightthickness=0, width=5)
        self.num_opt["menu"].config(bg=self.BG_PANEL, fg=self.FG_TEXT, font=self.FONT_MAIN, relief="flat")
        self.num_opt.grid(row=0, column=1, sticky="e", pady=5)

        self._create_label(static_config, "Bezel Gap (cm):", 1, 0)
        self.entry_bezel = tk.Entry(static_config, textvariable=self.bezel_var, bg=self.BG_ENTRY, fg=self.FG_TEXT, 
                                    insertbackground=self.FG_TEXT, font=self.FONT_MAIN, relief="flat", width=8)
        self.entry_bezel.grid(row=1, column=1, sticky="e", pady=5, ipady=3)
        
        self._create_label(static_config, "Align & Mode:", 2, 0)
        
        align_opt = tk.OptionMenu(static_config, self.align_var, "Top", "Center", "Bottom")
        align_opt.config(bg=self.BG_ENTRY, fg=self.FG_TEXT, font=self.FONT_MAIN, relief="flat", highlightthickness=0, width=6)
        align_opt["menu"].config(bg=self.BG_PANEL, fg=self.FG_TEXT, font=self.FONT_MAIN, relief="flat")
        align_opt.grid(row=2, column=1, sticky="e", pady=5)
        
        mode_opt = tk.OptionMenu(static_config, self.fit_mode_var, "Crop", "Blur Fit")
        mode_opt.config(bg=self.BG_ENTRY, fg=self.FG_TEXT, font=self.FONT_MAIN, relief="flat", highlightthickness=0, width=6)
        mode_opt["menu"].config(bg=self.BG_PANEL, fg=self.FG_TEXT, font=self.FONT_MAIN, relief="flat")
        mode_opt.grid(row=3, column=1, sticky="e", pady=5)

        tk.Frame(inner_frame, bg=self.ACCENT, height=1).pack(fill=tk.X, pady=15)

        self.dynamic_frame = tk.Frame(inner_frame, bg=self.BG_PANEL)
        self.dynamic_frame.pack(fill=tk.X)

        tk.Frame(inner_frame, bg=self.ACCENT, height=1).pack(fill=tk.X, pady=15)

        self.btn_export = self._create_pill_button(inner_frame, "💾  Export Only", lambda: self.process_images(set_bg=False),
                                                   width=320, height=40, bg=self.BG_PANEL, btn_bg=self.BG_ENTRY, fg=self.FG_TEXT)
        self.btn_export.pack(pady=5)
        
        self.btn_export_set = self._create_pill_button(inner_frame, "✨ Export & Apply", lambda: self.process_images(set_bg=True),
                                                       width=320, height=45, bg=self.BG_PANEL, btn_bg=self.BTN_PRIMARY, fg=self.BTN_TEXT)
        self.btn_export_set.pack(pady=5)

        preview_frame = tk.Frame(self, bg=self.BG_MAIN)
        preview_frame.pack(side=tk.RIGHT, expand=True, fill=tk.BOTH, padx=(0, 15), pady=15)

        self.canvas = tk.Canvas(preview_frame, bg=self.BG_PANEL, highlightthickness=0, relief="flat", cursor="fleur")
        self.canvas.pack(expand=True, fill=tk.BOTH)
        
        placeholder = "Preview will appear here...\n(Drag & Drop an image)" if HAS_DND else "Preview will appear here..."
        self.canvas.create_text(380, 380, text=placeholder, fill=self.FG_MUTED, font=self.FONT_MAIN, tags="placeholder", justify="center")
        
        if HAS_DND:
            self.canvas.drop_target_register(DND_FILES)
            self.canvas.dnd_bind('<<Drop>>', self.on_drop)
            
        self.canvas.bind("<Configure>", self.schedule_preview)
        self.canvas.bind("<ButtonPress-1>", self.on_pan_start)
        self.canvas.bind("<B1-Motion>", self.on_pan_drag)
        self.canvas.bind("<Double-Button-1>", self.on_pan_reset)

    def parse_float(self, val, default=0.0):
        try:
            return float(str(val).replace(',', '.'))
        except ValueError:
            return default

    def parse_resolution(self, value, default=(1920, 1080)):
        try:
            parts = str(value).replace(" ", "").split(",")
            if len(parts) != 2: return default
            w, h = int(parts[0]), int(parts[1])
            if w <= 0 or h <= 0: return default
            return w, h
        except Exception:
            return default

    def on_drop(self, event):
        # DND yol ayrıştırıcısı boşluk içeren dosyalar için güvenli hale getirildi
        paths = re.findall(r'\{(.*?)\}|(\S+)', event.data)
        if not paths: return
        path = paths[0][0] or paths[0][1]
        self.load_image_from_path(path)

    def on_pan_start(self, event):
        self.drag_start_x = event.x
        self.drag_start_y = event.y

    def on_pan_drag(self, event):
        if not self.source_image: return
        dx = event.x - self.drag_start_x
        dy = event.y - self.drag_start_y
        self.drag_start_x = event.x
        self.drag_start_y = event.y
        
        self.pan_x -= (dx * 1.5) / self.canvas.winfo_width()
        self.pan_y -= (dy * 1.5) / self.canvas.winfo_height()
        self.pan_x = max(0.0, min(1.0, self.pan_x))
        self.pan_y = max(0.0, min(1.0, self.pan_y))
        self.schedule_preview()
        
    def on_pan_reset(self, event):
        self.pan_x, self.pan_y = 0.5, 0.5
        self.schedule_preview()

    def on_panel_count_changed(self):
        self.build_dynamic_panels()
        self.schedule_preview()

    def build_dynamic_panels(self):
        for widget in self.panel_widgets:
            widget.destroy()
        self.panel_widgets.clear()
        
        count = int(self.num_panels_var.get())
        standard_resolutions = ["1920,1080", "2560,1440", "3840,2160", "1280,720", "2560,1080", "3440,1440"]

        for i in range(count):
            lbl = tk.Label(self.dynamic_frame, text=f"🖥️ Display {i+1} (Inch | Res | X, Y cm):", bg=self.BG_PANEL, fg=self.FG_MUTED, font=self.FONT_MAIN)
            lbl.grid(row=i*2, column=0, columnspan=3, sticky="w", pady=(10, 2))
            
            entry_inch = tk.Entry(self.dynamic_frame, textvariable=self.panel_vars[i]["inch"], bg=self.BG_ENTRY, fg=self.FG_TEXT, 
                                  insertbackground=self.FG_TEXT, font=self.FONT_MAIN, relief="flat", width=6)
            entry_inch.grid(row=i*2+1, column=0, sticky="w", padx=(0, 5), ipady=3)

            combo_res = ttk.Combobox(self.dynamic_frame, textvariable=self.panel_vars[i]["res"], values=standard_resolutions, font=self.FONT_MAIN, width=12)
            combo_res.grid(row=i*2+1, column=1, sticky="w", ipady=3)
            
            offset_frame = tk.Frame(self.dynamic_frame, bg=self.BG_PANEL)
            offset_frame.grid(row=i*2+1, column=2, sticky="w", padx=(5, 0))
            
            entry_x = tk.Entry(offset_frame, textvariable=self.panel_vars[i]["x_off"], bg=self.BG_ENTRY, fg=self.FG_TEXT, 
                               insertbackground=self.FG_TEXT, font=self.FONT_MAIN, relief="flat", width=4)
            entry_x.pack(side=tk.LEFT, padx=(0, 2), ipady=3)
            
            entry_y = tk.Entry(offset_frame, textvariable=self.panel_vars[i]["y_off"], bg=self.BG_ENTRY, fg=self.FG_TEXT, 
                               insertbackground=self.FG_TEXT, font=self.FONT_MAIN, relief="flat", width=4)
            entry_y.pack(side=tk.LEFT, ipady=3)
            
            self.panel_widgets.extend([lbl, entry_inch, combo_res, offset_frame])
            
            if self.auto_detect_enabled:
                entry_inch.config(state="disabled")
                combo_res.config(state="disabled")

    def toggle_auto_detect(self):
        self.auto_detect_enabled = not self.auto_detect_enabled
        if self.auto_detect_enabled:
            img = self._generate_pill_image(320, 35, self.BG_PANEL, self.BTN_PRIMARY)
            self._button_images["Auto-Detect: ON"] = img
            self.btn_auto.config(text="Auto-Detect: ON", image=img, fg=self.BTN_TEXT)
            self.num_opt.config(state="disabled")
            
            monitors = MonitorDetector.get_monitors_info()
            count = min(len(monitors), 3)
            if count > 0:
                self.num_panels_var.set(str(count))
                for i in range(count):
                    # Tam sayı yuvarlaması yerine 1 ondalıklı daha hassas inç
                    self.panel_vars[i]["inch"].set(str(monitors[i]["inch"]))
                    self.panel_vars[i]["res"].set(monitors[i]["res"])
        else:
            img = self._generate_pill_image(320, 35, self.BG_PANEL, self.BG_ENTRY)
            self._button_images["Auto-Detect: OFF"] = img
            self.btn_auto.config(text="Auto-Detect: OFF", image=img, fg=self.FG_TEXT)
            self.num_opt.config(state="normal")
            
        self.build_dynamic_panels()

    def _create_label(self, parent, text, row, col, pady=(0, 2)):
        lbl = tk.Label(parent, text=text, bg=self.BG_PANEL, fg=self.FG_MUTED, font=self.FONT_MAIN)
        lbl.grid(row=row, column=col, sticky="w", pady=pady)
        return lbl

    def _generate_pill_image(self, width, height, bg, btn_bg):
        img = Image.new('RGB', (width, height), bg)
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((0, 0, width, height), radius=height//2, fill=btn_bg)
        return ImageTk.PhotoImage(img)

    def _create_pill_button(self, parent, text, command, width, height, bg, btn_bg, fg):
        photo = self._generate_pill_image(width, height, bg, btn_bg)
        self._button_images[text] = photo 
        btn = tk.Button(parent, text=text, image=photo, command=command, compound="center",
                        bg=bg, fg=fg, font=self.FONT_BTN, relief="flat", borderwidth=0, 
                        activebackground=bg, activeforeground=fg, cursor="hand2")
        return btn

    def load_image(self):
        path = filedialog.askopenfilename(filetypes=[("Image files", "*.jpg *.jpeg *.png *.bmp")])
        if path:
            self.load_image_from_path(path)
            
    def load_image_from_path(self, path):
        try:
            image = Image.open(path)
            image = ImageOps.exif_transpose(image) # Mobil cihazlarda çekilmiş fotoğrafları rotasyona göre düzelt
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGB")
        except Exception as e:
            messagebox.showerror("Image Error", f"Image could not be opened:\n{str(e)}")
            return
            
        self.source_path = path
        self.source_image = image
        filename = os.path.basename(path)
        if len(filename) > 30: filename = filename[:27] + "..."
        self.lbl_file.config(text=filename)
        self.pan_x, self.pan_y = 0.5, 0.5 
        self.schedule_preview()

    def get_settings(self):
        count = int(self.num_panels_var.get())
        panels, targets, x_offs, y_offs = [], [], [], []
        
        for i in range(count):
            inch_val = self.parse_float(self.panel_vars[i]["inch"].get(), 24.0)
            res_val = self.parse_resolution(self.panel_vars[i]["res"].get())
            x_off_val = self.parse_float(self.panel_vars[i]["x_off"].get(), 0.0)
            y_off_val = self.parse_float(self.panel_vars[i]["y_off"].get(), 0.0)
            
            panels.append(max(1.0, inch_val))
            targets.append(res_val)
            x_offs.append(x_off_val)
            y_offs.append(y_off_val)
            
        b_gap = max(0.0, self.parse_float(self.bezel_var.get(), 0.0))
        
        align_val = self.align_var.get()
        if align_val == "Top": align = VerticalAlign.TOP
        elif align_val == "Center": align = VerticalAlign.CENTER
        else: align = VerticalAlign.BOTTOM
            
        return panels, targets, x_offs, y_offs, b_gap, align, self.fit_mode_var.get()

    def get_working_image_and_cropper(self):
        panels, targets, x_offs, y_offs, gap, align, fit_mode = self.get_settings()
        c = Cropper(self.source_image.size, panels, targets, align, gap, self.pan_x, self.pan_y, y_offs, x_offs)
        
        if fit_mode == "Blur Fit":
            tw, th = c.get_bounding_box_cm()
            target_ratio = tw / th if th > 0 else 1.0
            img_ratio = self.source_image.width / self.source_image.height
            
            if abs(target_ratio - img_ratio) > 0.02:
                if target_ratio > img_ratio:
                    new_w = int(self.source_image.height * target_ratio)
                    new_h = self.source_image.height
                else:
                    new_w = self.source_image.width
                    new_h = int(self.source_image.width / target_ratio)
                    
                bg = self.source_image.resize((new_w, new_h), RESAMPLE).filter(ImageFilter.GaussianBlur(25))
                offset = ((new_w - self.source_image.width)//2, (new_h - self.source_image.height)//2)
                bg.paste(self.source_image, offset)
                
                c = Cropper(bg.size, panels, targets, align, gap, self.pan_x, self.pan_y, y_offs, x_offs)
                return bg, c
                
        return self.source_image, c

    def update_preview(self):
        if not self.source_image:
            return

        try:
            img, cropper = self.get_working_image_and_cropper()
            plan = cropper.compute()

            cw = self.canvas.winfo_width()
            ch = self.canvas.winfo_height()
            if cw < 10 or ch < 10:
                cw, ch = 750, 750

            scale = min(cw / img.width, ch / img.height)
            new_w = int(img.width * scale)
            new_h = int(img.height * scale)

            resized = img.resize((new_w, new_h), RESAMPLE)
            self.photo_img = ImageTk.PhotoImage(resized)

            self.canvas.delete("all")
            x_offset = (cw - new_w) // 2
            y_offset = (ch - new_h) // 2
            
            self.canvas.create_rectangle(x_offset-2, y_offset-2, x_offset+new_w+2, y_offset+new_h+2, fill=self.BG_ENTRY, outline="")
            self.canvas.create_image(x_offset, y_offset, anchor=tk.NW, image=self.photo_img)

            for idx, panel in enumerate(plan.panels):
                box = panel.crop.box()
                x1 = x_offset + box[0] * scale
                y1 = y_offset + box[1] * scale
                x2 = x_offset + box[2] * scale
                y2 = y_offset + box[3] * scale

                # Etiket taşmalarını önlemek için maksimum kutu limiti (örn: ekranın sağı)
                lbl_x2 = min(x1 + 100, x2 - 5)

                self.canvas.create_rectangle(x1, y1, x2, y2, outline=self.BTN_PRIMARY, width=3)
                self.canvas.create_rectangle(x1, y1, lbl_x2, y1+30, fill=self.BTN_PRIMARY, outline="")
                self.canvas.create_text(x1 + (lbl_x2-x1)/2, y1 + 15, text=f"Display {idx+1}", fill=self.BTN_TEXT, font=self.FONT_BTN)
        except Exception as e:
            # Hata yutulması yerine en azından konsola dökülmesi hata takibini sağlar
            import traceback
            traceback.print_exc()

    def process_images(self, set_bg):
        if not self.source_image:
            messagebox.showwarning("Warning", "Please load an image first.")
            return

        try:
            img, cropper = self.get_working_image_and_cropper()
            plan = cropper.compute()

            out_dir = filedialog.askdirectory(title="Select Output Directory")
            if not out_dir:
                return

            saved_paths = []
            for idx, panel in enumerate(plan.panels):
                box = panel.crop.box()
                cropped = img.crop(box)
                resized = cropped.resize(panel.target, RESAMPLE)
                if resized.mode != "RGB":
                    resized = resized.convert("RGB") # Windows API alpha kanallı PNG'lerde hata verebilir
                
                # Mevcut dosyayı üzerine yazmamak için (overwrite protection)
                base_name = f"panel_{idx}"
                save_path = os.path.join(out_dir, f"{base_name}.png")
                counter = 1
                while os.path.exists(save_path):
                    save_path = os.path.join(out_dir, f"{base_name}_{counter}.png")
                    counter += 1
                    
                resized.save(save_path)
                saved_paths.append(save_path)

            if set_bg:
                try:
                    # Windows kütüphanesini tembel import ediyoruz (Sadece gerektiğinde)
                    from wpset import WPSet
                    ws = WPSet()
                    ws.assign_sequential(saved_paths)
                    messagebox.showinfo("Success", "Wallpapers exported and automatically applied to your monitors!")
                except Exception as e:
                    messagebox.showerror("Wallpaper Error", f"Failed to set wallpaper: {str(e)}")
            else:
                messagebox.showinfo("Success", "Wallpapers exported successfully without setting as background.")

        except Exception as e:
            import traceback
            traceback.print_exc()
            messagebox.showerror("Processing Error", str(e))

if __name__ == "__main__":
    app = MultiMonitorCropperApp()
    app.mainloop()