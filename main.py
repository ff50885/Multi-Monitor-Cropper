#!/usr/bin/env python3
"""DualCropper GUI - modern CustomTkinter front-end for the span-wallpaper engine.

Flow:
  1. Import a source image (file dialog).
  2. Describe the two monitors: size (inches), native resolution and which
     side of the desk each one sits on; plus bezel gap and vertical alignment.
     Monitors are "Monitor 1" and "Monitor 2" - any dual-monitor pair works,
     regardless of which panel is physically bigger.
  3. Preview the crop plan live on the source image.
  4. Generate -> each crop is resampled to its own monitor's native
     resolution, saved as PNG and applied per-monitor as desktop background
     (left crop -> left display, right crop -> right display).

UI language defaults to English; other languages are selectable and stored in
JSON files under dualcropper/locales/.

PyInstaller build (Windows):  build_exe.bat   or   pyinstaller DualCropper.spec
"""

from __future__ import annotations

import json
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageOps

try:  # Modern UI layer; falls back to plain ttk look if unavailable.
    import customtkinter as ctk
    HAVE_CTK = True
except Exception:  # pragma: no cover
    HAVE_CTK = False

if HAVE_CTK:
    CTkImage = ctk.CTkImage
else:  # minimal stand-in so the code paths stay identical
    from PIL import ImageTk

    class CTkImage:  # type: ignore
        def __init__(self, light_image=None, dark_image=None, size=None):
            self._img = light_image
            self.size = size

        def photo(self):
            return ImageTk.PhotoImage(self._img)

import math

from dualcropper.core import (
    Anchor, CropPlan, CropRect, DualCropper, Side, VerticalAlign,
)
from dualcropper import i18n, wallpaper
from dualcropper.logger import get_logger, log_path

log = get_logger("gui")

OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "DualCropper")
SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".dualcropper.json")

RESOLUTIONS = ["1920x1080", "2560x1440", "3840x2160",
               "1680x1050", "1280x1024"]


def tr(key: str, **fmt) -> str:
    s = i18n.tr(key)
    return s.format(**fmt) if fmt else s


def parse_resolution(text: str) -> Tuple[int, int]:
    """Parse '1920x1080' (case/space tolerant) into (w, h)."""
    t = text.lower().replace(" ", "").replace("×", "x")
    w, _, h = t.partition("x")
    return int(w), int(h)


class App(ctk.CTk if HAVE_CTK else object):
    # ------------------------------------------------------------ construction

    def __init__(self):
        if not HAVE_CTK:
            raise RuntimeError("customtkinter required: pip install customtkinter")
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        settings = self._load_settings()
        i18n.set_language(settings.get("language", i18n.DEFAULT_LANG))
        self.title(tr("app_title"))

        self._src_path: Optional[str] = None
        self._src_img: Optional[Image.Image] = None
        self._preview_ref = None
        self._busy = False
        self._plan: Optional[CropPlan] = None

        self._saved = self._load_settings()
        # Monitor count is an explicit user choice ("Auto (from Detect)" until
        # the user picks a number or presses Detect).  Until then NO monitor
        # rows are shown - not even 1 and 2.
        raw_count = self._saved.get("monitors")
        try:
            raw_count = int(raw_count)
        except (TypeError, ValueError):
            raw_count = None
        self._count_choice = str(raw_count) if raw_count in (2, 3, 4) else \
            tr("count_auto")
        self._visible_monitors = raw_count if raw_count in (2, 3, 4) else None
        self._build_ui()
        try:
            self.pos_x.set(float(self._saved.get("pos_x", 0.5)))
            self.pos_y.set(float(self._saved.get("pos_y", 0.5)))
        except Exception:
            pass
        for i in range(4):
            v = self._saved.get(f"m{i}_inch")
            if v:
                e = self.mon_inch[i]
                e.delete(0, "end"); e.insert(0, v)
            rv = self._saved.get(f"m{i}_res")
            if rv:
                m = self.mon_res[i]
                if rv not in RESOLUTIONS:
                    m.configure(values=[rv] + RESOLUTIONS)
                m.set(rv)
        gv = self._saved.get("gap")
        if gv:
            self.gap.delete(0, "end"); self.gap.insert(0, gv)
        ov = self._saved.get("out")
        if ov:
            self.out_entry.delete(0, "end"); self.out_entry.insert(0, ov)
        av = self._saved.get("align")
        if av:
            self.align_menu.set(av)
        img = self._saved.get("image")
        if img and os.path.isfile(img):
            self._load_image(img)
        self._set_status(tr("status_pick_image"))
        self.geometry("1120x760")
        self.minsize(980, 640)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    @staticmethod
    def _load_settings() -> dict:
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}

    def _on_close(self):
        log.info("application closing; state persisted to %s", SETTINGS_PATH)
        self._persist_state()
        self.destroy()

    @staticmethod
    def _open_log():
        """Reveal dualcropper.log in the platform's default text/handler."""
        import subprocess
        p = log_path()
        log.info("opening log file: %s", p)
        try:
            if sys.platform.startswith("win"):
                os.startfile(p)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", p])
            else:
                subprocess.Popen(["xdg-open", p])
        except Exception as exc:
            log.exception("could not open log file")
            messagebox.showinfo(tr("app_title"), tr("log_path_info", path=p))

    def _persist_state(self):
        """Store slider positions + monitor count so the next start is identical."""
        try:
            d = {
                "language": i18n.current_language(),
                "monitors": self._active_monitor_count(),
                "pos_x": float(self.pos_x.get()),
                "pos_y": float(self.pos_y.get()),
                "gap": self.gap.get(),
                "out": self.out_entry.get(),
                "align": self.align_menu.get(),
                "image": self._src_path if self._src_img else "",
            }
            for i in range(4):
                d[f"m{i}_inch"] = self.mon_inch[i].get()
                d[f"m{i}_res"] = self.mon_res[i].get()
            self._save_settings(d)
        except Exception:
            pass

    @staticmethod
    def _save_settings(d: dict) -> None:
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as fh:
                json.dump(d, fh, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _on_language_change(self, name: str) -> None:
        i18n.set_language(i18n.code_for_name(name))
        self._save_settings({"language": i18n.current_language()})
        # Rebuild every widget text without losing current values.
        vals = {
            "gap": self.gap.get(), "out": self.out_entry.get(),
            "align": self.align_menu.get(),
            "pos_x": self.pos_x.get(), "pos_y": self.pos_y.get(),
            "auto": self.auto_apply.get(), "lang": name,
        }
        for i in range(4):
            vals[f"m{i}_inch"] = self.mon_inch[i].get()
            vals[f"m{i}_res"] = self.mon_res[i].get()
        if self._src_img is not None:
            vals["image"] = self._src_path
        for child in self.winfo_children():
            child.destroy()
        self._build_ui()
        self._apply_saved_values(vals)

    def _apply_saved_values(self, v: dict) -> None:
        def set_entry(e, key):
            if key in v:
                e.delete(0, "end")
                e.insert(0, v[key])

        for i in range(4):
            set_entry(self.mon_inch[i], f"m{i}_inch")
            self.mon_res[i].set(v.get(f"m{i}_res", "1920x1080"))
        set_entry(self.gap, "gap")
        set_entry(self.out_entry, "out")
        self.align_menu.set(v.get("align", tr("align_bottom")))
        try:
            self.pos_x.set(float(v.get("pos_x", 0.5)))
            self.pos_y.set(float(v.get("pos_y", 0.5)))
        except Exception:
            pass
        self.lang_menu.set(v.get("lang", "English"))
        if v.get("auto") == 1:
            self.auto_apply.select()
        else:
            self.auto_apply.deselect()
        if v.get("image"):
            self._load_image(v["image"])
        self._update_plan()

    # ------------------------------------------------------------------- ui

    def _lbl(self, parent, key, **kw):
        return ctk.CTkLabel(parent, text=tr(key), **kw)

    def _build_ui(self):
        pad = dict(padx=14, pady=10)

        # ---- header
        header = ctk.CTkFrame(self, corner_radius=0, fg_color=("gray86", "gray13"))
        header.pack(fill="x")
        ctk.CTkLabel(header, text="🖼️  DualCropper",
                     font=ctk.CTkFont(size=24, weight="bold")
                     ).pack(side="left", padx=20, pady=12)
        self._lbl(header, "header_subtitle", text_color=("gray30", "gray70"),
                  font=ctk.CTkFont(size=13)).pack(side="left", padx=6)

        lang_box = ctk.CTkFrame(header, fg_color="transparent")
        lang_box.pack(side="right", padx=16)
        self._lbl(lang_box, "language_label", text_color=("gray30", "gray70"),
                  font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(0, 6))
        self.lang_menu = ctk.CTkOptionMenu(lang_box, values=i18n.language_names(),
                                           width=120, height=28)
        self.lang_menu.set(next((n for c, n in i18n.LANGUAGES
                                 if c == i18n.current_language()), "English"))
        self.lang_menu.configure(command=self._on_language_change)
        self.lang_menu.pack(side="right")

        # Diagnostics: open the rotating log file with the OS default handler.
        log_btn = ctk.CTkButton(header, text=tr("open_log"), width=96, height=28,
                                fg_color=("gray75", "gray28"),
                                command=self._open_log)
        log_btn.pack(side="right", padx=8)

        # ---- body: left form | right preview
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        form = ctk.CTkScrollableFrame(body, width=380, label_text=tr("config_panel"))
        form.grid(row=0, column=0, sticky="nsew", **pad)

        prev_box = ctk.CTkFrame(body)
        prev_box.grid(row=0, column=1, sticky="nsew", **pad)
        self.preview = ctk.CTkLabel(prev_box, text="", fg_color="transparent")
        self.preview.pack(fill="both", expand=True, padx=8, pady=8)
        self.preview.bind("<Configure>", lambda e: self._redraw_preview())

        # ---- 1) image card
        img_card = ctk.CTkFrame(form, corner_radius=10)
        img_card.pack(fill="x", padx=8, pady=(12, 6))
        self._lbl(img_card, "card_image",
                  font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", padx=12, pady=(10, 2))
        row = ctk.CTkFrame(img_card, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(2, 6))
        self.img_entry = ctk.CTkEntry(row, placeholder_text=tr("no_image"), state="readonly")
        self.img_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(row, text=tr("import"), width=96,
                      command=self._choose_image).pack(side="right")
        self.img_info = ctk.CTkLabel(img_card, text="", text_color=("gray40", "gray65"),
                                     font=ctk.CTkFont(size=12))
        self.img_info.pack(anchor="w", padx=12, pady=(0, 10))

        # ---- 2) monitors card
        mon_card = ctk.CTkFrame(form, corner_radius=10)
        mon_card.pack(fill="x", padx=8, pady=6)
        self._lbl(mon_card, "card_monitors",
                  font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", padx=12, pady=(10, 2))

        g = ctk.CTkFrame(mon_card, fg_color="transparent")
        g.pack(fill="x", padx=12, pady=(2, 8))
        g.grid_columnconfigure((0, 1), weight=1)

        def add_label(r, c, key):
            self._lbl(g, key, font=ctk.CTkFont(size=12, weight="bold"),
                      text_color=("gray35", "gray70")).grid(row=r, column=c,
                                                             sticky="w", pady=(6, 0))

        def add_entry(r, c, value, width=150):
            e = ctk.CTkEntry(g, width=width, height=30)
            e.insert(0, value)
            e.grid(row=r, column=c, sticky="ew", padx=(0, 10), pady=(2, 2))
            e.bind("<KeyRelease>", lambda _e: self._update_plan())
            return e

        # Monitor rows 1..4 (Monitor 1 = leftmost display).  Rows beyond the
        # detected monitor count are hidden by _refresh_monitor_rows().
        defaults = [("32", "1920x1080"), ("24", "1920x1080"),
                    ("27", "1920x1080"), ("27", "1920x1080")]
        self.mon_inch: List[ctk.CTkEntry] = []
        self.mon_res: List[ctk.CTkOptionMenu] = []
        self.mon_rows: List[tuple] = []
        for i in range(4):
            r = i * 2
            add_label(r, 0, f"mon{i + 1}_inch")
            e = add_entry(r + 1, 0, defaults[i][0])
            add_label(r, 1, f"mon{i + 1}_res")
            m = self._res_menu(g, r + 1, 1, defaults[i][1])
            self.mon_inch.append(e)
            self.mon_res.append(m)
            self.mon_rows.append((r, e, m))

        # Detect button + result label
        det_row = ctk.CTkFrame(mon_card, fg_color="transparent")
        det_row.pack(fill="x", padx=12, pady=(0, 6))
        ctk.CTkButton(det_row, text=tr("detect"), width=110, height=28,
                      command=self._detect_monitors).pack(side="left")
        self.det_info = ctk.CTkLabel(det_row, text="",
                                     text_color=("gray40", "gray65"),
                                     font=ctk.CTkFont(size=12))
        self.det_info.pack(side="left", padx=10)

        grow = ctk.CTkFrame(mon_card, fg_color="transparent")
        grow.pack(fill="x", padx=12, pady=(0, 4))
        grow.grid_columnconfigure((0, 1), weight=1)
        self._lbl(grow, "gap_cm", font=ctk.CTkFont(size=12, weight="bold"),
                  text_color=("gray35", "gray70")).grid(row=0, column=0,
                                                         sticky="w", pady=(6, 0))
        self.gap = ctk.CTkEntry(grow, width=150, height=30)
        self.gap.insert(0, "1.5")
        self.gap.grid(row=1, column=0, sticky="ew", padx=(0, 10), pady=(2, 2))
        self.gap.bind("<KeyRelease>", lambda _e: self._update_plan())

        self._lbl(grow, "vertical_align", font=ctk.CTkFont(size=12, weight="bold"),
                  text_color=("gray35", "gray70")).grid(row=0, column=1,
                                                        sticky="w", pady=(6, 0))
        self.align_menu = ctk.CTkOptionMenu(
            grow, values=[tr("align_bottom"), tr("align_top"), tr("align_center")], height=30)
        self.align_menu.set(tr("align_bottom"))
        self.align_menu.grid(row=1, column=1, sticky="ew", padx=(0, 10), pady=(2, 2))
        self.align_menu.configure(command=lambda _v: self._update_plan())

        # ---- crop position sliders (replaces the old Center/Start/End menu)
        pos_card = ctk.CTkFrame(form, corner_radius=10)
        pos_card.pack(fill="x", padx=8, pady=6)
        self._lbl(pos_card, "anchor",
                  font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", padx=12, pady=(10, 2))
        pg = ctk.CTkFrame(pos_card, fg_color="transparent")
        pg.pack(fill="x", padx=12, pady=(2, 10))
        pg.grid_columnconfigure(1, weight=1)

        def add_slider(row, key, initial):
            self._lbl(pg, key, font=ctk.CTkFont(size=12, weight="bold"),
                      text_color=("gray35", "gray70")).grid(row=row, column=0,
                                                            sticky="w", padx=(0, 8))
            # NOTE: CTkSlider takes `from_` (trailing underscore, since `from`
            # is a Python keyword) and `to`. Older customtkinter versions used
            # these names; `from_value`/`to_value` are not supported kwargs.
            s = ctk.CTkSlider(pg, from_=0.0, to=1.0, number_of_steps=100,
                              width=220, command=lambda _v: self._update_plan())
            s.set(initial)
            s.grid(row=row, column=1, sticky="ew", padx=(0, 8), pady=3)
            lab = ctk.CTkLabel(pg, text=f"{int(initial * 100)}%", width=44,
                               text_color=("gray40", "gray65"),
                               font=ctk.CTkFont(size=12))
            lab.grid(row=row, column=2, sticky="e")
            s.configure(command=lambda v, l=lab: (l.configure(text=f"{int(v * 100)}%"),
                                                  self._update_plan()))
            return s

        self.pos_x = add_slider(0, "pos_x", 0.5)
        self.pos_y = add_slider(1, "pos_y", 0.5)

        # ---- 3) output card
        out_card = ctk.CTkFrame(form, corner_radius=10)
        out_card.pack(fill="x", padx=8, pady=6)
        self._lbl(out_card, "card_output",
                  font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", padx=12, pady=(10, 2))
        orow = ctk.CTkFrame(out_card, fg_color="transparent")
        orow.pack(fill="x", padx=12, pady=(2, 6))
        self.out_entry = ctk.CTkEntry(orow, placeholder_text=tr("output_dir_placeholder"))
        self.out_entry.insert(0, OUTPUT_DIR)
        self.out_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(orow, text=tr("choose_folder"), width=84,
                      command=self._choose_outdir).pack(side="left")

        self.auto_apply = ctk.CTkSwitch(out_card, text=tr("auto_apply"),
                                        font=ctk.CTkFont(size=13))
        self.auto_apply.select()
        self.auto_apply.pack(anchor="w", padx=14, pady=(0, 4))

        self.gen_btn = ctk.CTkButton(out_card, text=tr("generate"), height=42,
                                     font=ctk.CTkFont(size=15, weight="bold"),
                                     command=self._generate)
        self.gen_btn.pack(fill="x", padx=12, pady=(6, 12))

        # ---- status bar
        self.status = ctk.CTkLabel(self, text="", anchor="w",
                                   text_color=("gray30", "gray70"),
                                   font=ctk.CTkFont(size=12))
        self.status.pack(fill="x", padx=18, pady=(0, 8))

    def _res_menu(self, parent, row, col, default):
        m = ctk.CTkOptionMenu(parent, values=RESOLUTIONS + [tr("res_custom")], height=30)
        m.set(default)
        m.grid(row=row, column=col, sticky="ew", padx=(0, 10), pady=(2, 8))
        m.configure(command=lambda v, mm=m: self._on_res(mm, v))
        return m

    def _on_res(self, menu, value):
        if value == tr("res_custom"):
            dlg = ctk.CTkInputDialog(text=tr("custom_res_prompt"),
                                     title=tr("custom_res_title"))
            try:
                w, h = parse_resolution(dlg.get_input() or "")
                menu.set(f"{w}x{h}")
            except Exception:
                pass  # keep previous selection on bad input
        self._update_plan()

    # ---------------------------------------------------------------- helpers

    def _set_status(self, msg: str):
        if hasattr(self, "status"):
            self.status.configure(text=msg)

    def _choose_image(self):
        path = filedialog.askopenfilename(
            title=tr("image_dialog_title"),
            filetypes=[(tr("filetype_images"), "*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff"),
                       (tr("filetype_all"), "*.*")])
        if not path:
            return
        self._load_image(path)
        self._update_plan()

    def _load_image(self, path: str) -> bool:
        try:
            img = Image.open(path)
            img.load()
        except Exception as exc:
            messagebox.showerror(tr("app_title"), tr("open_error", err=exc))
            return False
        self._src_path = path
        self._src_img = img.convert("RGB")
        name = os.path.basename(path)
        self.img_entry.configure(state="normal")
        self.img_entry.delete(0, "end")
        self.img_entry.insert(0, name)
        self.img_entry.configure(state="readonly")
        self.img_info.configure(text=f"{img.width} × {img.height} px")
        return True

    def _choose_outdir(self):
        d = filedialog.askdirectory(title=tr("folder_dialog_title"))
        if d:
            self.out_entry.delete(0, "end")
            self.out_entry.insert(0, d)

    # ----------------------------------------------------------- option maps
    # Values shown in the menus are *translated*; map them back to enums.

    def _align(self) -> VerticalAlign:
        v = self.align_menu.get()
        if v == tr("align_top"):
            return VerticalAlign.TOP
        if v == tr("align_center"):
            return VerticalAlign.CENTER
        return VerticalAlign.BOTTOM

    def _anchor(self) -> Tuple[float, float]:
        """Continuous crop-window position (0..1) from the two sliders."""
        return (float(self.pos_x.get()), float(self.pos_y.get()))

    # ------------------------------------------------------- monitor detection

    def _detect_monitors(self):
        """Query the OS for connected displays and fill the form rows.

        Every step is mirrored to dualcropper.log (path shown in the status
        line) so detection problems can be diagnosed externally.
        """
        from dualcropper.logger import log_path as _lp, get_logger
        dlog = get_logger("gui.detect")
        dlog.info("Detect pressed; log file: %s", _lp())
        try:
            from dualcropper import monitors as mon_api
            mons = mon_api.list_monitors()
        except Exception as exc:
            dlog.exception("monitor enumeration raised")
            self.det_info.configure(text=tr("wp_windows_multi_fail", err=exc))
            return
        if not mons:
            dlog.error("detection returned 0 monitors")
            self.det_info.configure(text=tr("det_none"))
            return
        # Zero-size rects mean the OS pass failed to deliver geometry - keep
        # the count but warn loudly (and in the log).
        zeroed = [m for m in mons if m.width <= 0 or m.height <= 0]
        if zeroed:
            dlog.error("%d monitor(s) reported 0x0 size: %s",
                       len(zeroed), ", ".join(m.label for m in zeroed))
        parts = []
        for m in mons[:4]:
            # Physical diagonal, assuming a 16:9 panel: diag_px / sqrt(16^2+9^2)
            # inches -> cm -> inches conversion collapses to px / hypot(16, 9).
            if m.width > 0 and m.height > 0:
                diag = (m.width ** 2 + m.height ** 2) ** 0.5 / math.hypot(16, 9)
                res_txt = f"{m.width}x{m.height}"
                menu = self.mon_res[m.number - 1]
                if res_txt not in RESOLUTIONS:
                    menu.configure(values=[res_txt] + RESOLUTIONS)
                menu.set(res_txt)

                """
                try:
                    self.mon_inch[m.number - 1].delete(0, "end")
                    self.mon_inch[m.number - 1].insert(0, f"{diag:.1f}")
                except Exception:
                    pass
                """
                parts.append(f"M{m.number}: {res_txt}, {diag:.0f}\""
                             + (" *" if m.primary else ""))
            else:
                parts.append(f"M{m.number}: ?")
        info = tr("det_found", count=len(mons), info=" · ".join(parts))
        if zeroed:
            info += "  " + tr("det_zero_size")
        self.det_info.configure(text=info)
        dlog.info("detection result: %s", info)
        self._visible_monitors = max(2, min(4, len(mons)))
        self._persist_state()
        self._update_plan()


    def _active_monitor_count(self) -> int:
        """How many monitor rows participate in cropping (2..4)."""
        n = getattr(self, "_visible_monitors", None)
        if n is None:
            # No detection yet: use the rows that actually carry values;
            # rows 3/4 stay at their defaults but are ignored unless the
            # user changed them or pressed Detect.
            return 2
        return n

    # --------------------------------------------------------- plan/preview

    def _collect_params(self) -> Optional[dict]:
        """Read the form; return None (and flag error) on invalid input.

        Supports 2-4 monitors.  Monitor numbering is physical, left-to-right
        (Monitor 1 = leftmost display).  The DualCropper engine only knows a
        two-panel span, so for N>2 the first N-1 panels are merged into one
        virtual "large" panel (their widths summed) and cropped as a single
        contiguous strip; the last panel gets the "small" crop.  Each output
        file is then sliced from that strip at its own native width - all
        crops share the same px/cm scale, so the artwork stays continuous
        across every bezel.
        """
        try:
            n = self._active_monitor_count()
            inches = [float(self.mon_inch[i].get().replace(",", "."))
                      for i in range(n)]
            res = [parse_resolution(self.mon_res[i].get()) for i in range(n)]
            gap = float(self.gap.get().replace(",", "."))
        except Exception:
            self._set_status(tr("status_check_numbers"))
            return None
        if any(x <= 0 for x in inches) or gap < 0:
            self._set_status(tr("status_positive_values"))
            return None

        # Panel cm sizes via the engine's 16:9 geometry helper.
        dummy = DualCropper((100, 100), large_inch=inches[0], small_inch=inches[0])
        cms = [dummy._panel_cm(d) for d in inches]  # noqa: SLF001 - internal math reuse

        # Engine naming convention: 'large' = the wide side of the pair.
        # For N==2 keep the old behaviour (bigger diagonal == 'large').
        # For N>2 the merged group is always the 'large' side.
        if n == 2:
            if inches[0] >= inches[1]:
                large_idx, small_idx = [0], 1
            else:
                large_idx, small_idx = [1], 0
        else:
            large_idx, small_idx = list(range(n - 1)), n - 1

        # A virtual panel whose 16:9-derived width equals the summed widths of
        # the merged monitors: scale the first member's diagonal accordingly.
        large_diag = inches[large_idx[0]] * (sum(cms[i][0] for i in large_idx)
                                             / cms[large_idx[0]][0])
        small_diag = inches[small_idx]

        # Target resolutions: merged side uses the first member's resolution
        # for height (all members assumed same height ratio); per-monitor
        # slicing later uses each monitor's own resolution.
        t_large = res[large_idx[0]]
        t_small = res[small_idx]

        px, py = self._anchor()
        return dict(n=n, inches=inches, res=res, cms=cms, gap=gap,
                    large_idx=large_idx, small_idx=small_idx,
                    large_diag=large_diag, small_diag=small_diag,
                    target=t_large, target_small=t_small,
                    align=self._align(), pos=(px, py))

    def _make_cropper(self, p: dict) -> DualCropper:
        src = self._src_img.size if self._src_img else (p["target"][0] * 2 + 200,
                                                        p["target"][1])
        px, py = p["pos"]
        # The engine places the smaller panel on Side.LEFT/RIGHT; our merged
        # group is on the opposite side of the single small panel.
        small_side = Side.RIGHT if p["small_idx"] == p["n"] - 1 else Side.LEFT
        return DualCropper(src, large_inch=p["large_diag"], small_inch=p["small_diag"],
                           target=p["target"], target_small=p["target_small"],
                           small_side=small_side, align=p["align"],
                           bezel_gap_cm=p["gap"], anchor_x=px, anchor_y=py)

    def _rects_by_monitor(self, p: dict):
        """Return [crop rect for Monitor 1, ..., Monitor N] (physical order)."""
        if self._plan is None:
            return None
        plan = self._plan
        rects = [None] * p["n"]
        rects[p["small_idx"]] = plan.small
        # Split the 'large' strip horizontally among its member monitors in
        # proportion to their physical widths (each slice keeps full height).
        idx = p["large_idx"]
        total_cm = sum(p["cms"][i][0] for i in idx)
        x = plan.large.x
        y = plan.large.y
        h = plan.large.height
        for k, mi in enumerate(idx):
            w_slice = round(plan.large.width * p["cms"][mi][0] / total_cm)
            if k == len(idx) - 1:  # absorb rounding drift in the last slice
                w_slice = plan.large.x + plan.large.width - x
            rects[mi] = CropRect(x, y, w_slice, h)
            x += w_slice
        return rects

    def _targets_by_monitor(self, p: dict):
        """Output size for each monitor row (its own native resolution)."""
        return [tuple(p["res"][i]) for i in range(p["n"])]

    def _update_plan(self):
        p = self._collect_params()
        if p is None or self._src_img is None:
            self._redraw_preview()
            return
        try:
            self._plan = self._make_cropper(p).compute()
        except Exception as exc:
            self._plan = None
            self._set_status(tr("status_no_plan", err=exc))
            self._redraw_preview()
            return
        pl = self._plan
        rects = self._rects_by_monitor(p)
        targets = self._targets_by_monitor(p)
        parts = [f"M{i + 1} {r.width}x{r.height}->{t[0]}x{t[1]}"
                 for i, (r, t) in enumerate(zip(rects, targets))]
        self._set_status(tr("status_plan_ready", scale=pl.px_per_cm)
                         + "  ·  " + " | ".join(parts))
        self._redraw_preview()

    def _redraw_preview(self):
        """Render the source with crop rectangles overlaid, fitted to the pane."""
        box = self.preview
        box_w, box_h = max(box.winfo_width(), 64), max(box.winfo_height(), 64)

        def show(img: Image.Image):
            self._preview_ref = CTkImage(light_image=img, dark_image=img,
                                         size=img.size)
            if not HAVE_CTK:  # stand-in path
                self._preview_ref = self._preview_ref.photo()
            box.configure(image=self._preview_ref, text="")

        if self._src_img is None:
            ph = Image.new("RGB", (box_w, box_h), (24, 26, 32))
            d = ImageDraw.Draw(ph)
            d.text((16, box_h // 2 - 8), tr("status_preview_hint"),
                   fill=(120, 125, 140))
            show(ph)
            return

        thumb = ImageOps.contain(self._src_img, (box_w - 8, box_h - 8), Image.LANCZOS)
        scale = thumb.width / self._src_img.width
        view = thumb.copy()
        draw = ImageDraw.Draw(view)
        p = self._collect_params()
        if self._plan and p:
            colors = [(46, 204, 113), (52, 152, 219), (231, 76, 60), (241, 196, 15)]
            for i, rect in enumerate(self._rects_by_monitor(p)):
                color = colors[i % len(colors)]
                tag = tr(f"tag_m{i + 1}")
                x0, y0 = rect.x * scale, rect.y * scale
                x1, y1 = (rect.x + rect.width) * scale, (rect.y + rect.height) * scale
                draw.rectangle([x0, y0, x1, y1], outline=color, width=3)
                draw.rectangle([x0, y0, x0 + 62, y0 + 18], fill=color)
                draw.text((x0 + 4, y0 + 2), tag, fill=(10, 12, 16))
        show(view)

    # -------------------------------------------------------------- mapping

    def _display_order(self, p: dict) -> List[Tuple]:
        """Pair each physical display slot (left-to-right) with crop+target.

        Monitor numbering IS the physical left-to-right order, so index i of
        the returned list is applied to display slot i by the wallpaper
        backend - no position guessing anywhere else.
        """
        rects = self._rects_by_monitor(p)
        targets = self._targets_by_monitor(p)
        return list(zip(rects, targets))

    # ----------------------------------------------------------- generation

    def _generate(self):
        if self._busy:
            log.info("generate ignored: a job is already running")
            return
        p = self._collect_params()
        if p is None:
            log.warning("generate aborted: parameter validation failed")
            return
        if self._src_img is None:
            log.warning("generate aborted: no source image loaded")
            messagebox.showwarning(tr("app_title"), tr("need_image_title"))
            return
        try:
            self._plan = self._make_cropper(p).compute()
        except Exception as exc:
            log.exception("crop plan computation failed for params=%s", p)
            messagebox.showerror(tr("app_title"), tr("plan_error_title", err=exc))
            return
        log.info("generate: %d monitor(s), gap=%.2fcm, pos=(%.2f,%.2f), src=%s",
                 p["n"], p["gap"], p["pos"][0], p["pos"][1],
                 self._src_img.size)

        out_dir = self.out_entry.get().strip() or OUTPUT_DIR
        self._busy = True
        self.gen_btn.configure(state="disabled", text=tr("generating"))
        threading.Thread(target=self._worker, args=(p, self._plan, out_dir),
                         daemon=True).start()

    def _worker(self, p: dict, plan: CropPlan, out_dir: str):
        try:
            os.makedirs(out_dir, exist_ok=True)
            src = self._src_img
            # pairs[0] -> LEFTMOST physical display slot, pairs[1] -> RIGHT.
            pairs = self._display_order(p)
            base = os.path.splitext(os.path.basename(self._src_path))[0]

            paths: List[str] = []
            for slot, (rect, target) in enumerate(pairs):
                img = src.crop(rect.box()).resize(target, Image.LANCZOS)
                log.info("crop slot %d: box=%s -> resize=%dx%d",
                         slot + 1, rect.box(), target[0], target[1])
                # 'slot0' is always applied to the leftmost monitor and
                # 'slot1' to the right one - the name matches the assignment,
                # so identical wallpapers can never be a naming artefact.
                fp = os.path.join(out_dir,
                                  f"{base}_monitor{slot + 1}_{img.width}x{img.height}.png")
                img.save(fp, "PNG")
                paths.append(fp)
                log.info("saved %s", fp)

            msg = tr("saved", files=", ".join(os.path.basename(x) for x in paths))
            if self.auto_apply.get() == 1:
                log.info("auto-apply enabled: assigning %d wallpaper(s)", len(paths))
                ok, info = wallpaper.set_wallpapers_per_monitor(paths)
                log.info("assignment result: ok=%s info=%s", ok, info)
                if ok:
                    msg += tr("wallpaper_ok", info=info)
                else:
                    # Never silently push ONE file to every screen: report the
                    # failure and tell the user they can assign manually.
                    msg += tr("wallpaper_fail_manual", info=info,
                              files=", ".join(paths))
            self.after(0, lambda: self._done(msg))
        except Exception as exc:
            log.exception("generation worker crashed")
            self.after(0, lambda e=exc: self._fail(e))

    def _done(self, msg: str):
        self._busy = False
        self.gen_btn.configure(state="normal", text=tr("generate"))
        self._set_status(msg)

    def _fail(self, exc: Exception):
        self._busy = False
        self.gen_btn.configure(state="normal", text=tr("generate"))
        self._set_status(tr("save_error_title", err=exc))
        messagebox.showerror(tr("app_title"), tr("save_error_title", err=exc))


def main():
    if not HAVE_CTK:
        print("customtkinter not found. Please run: pip install customtkinter")
        raise SystemExit(1)
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
