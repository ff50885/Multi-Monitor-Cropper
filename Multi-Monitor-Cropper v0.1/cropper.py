#!/usr/bin/env python3
import math
from dataclasses import dataclass
from enum import Enum
from typing import Sequence, Tuple

class VerticalAlign(Enum):
    TOP = "top"
    CENTER = "center"
    BOTTOM = "bottom"

@dataclass(frozen=True)
class CropRect:
    x: int
    y: int
    width: int
    height: int

    def box(self) -> Tuple[int, int, int, int]:
        return self.x, self.y, self.x + self.width, self.y + self.height

@dataclass(frozen=True)
class Panel:
    inch: float
    cm: Tuple[float, float]
    target: Tuple[int, int]
    crop: CropRect

@dataclass(frozen=True)
class CropPlan:
    panels: Tuple[Panel, ...]
    px_per_cm: float

class Cropper:
    _ASPECT = (16.0, 9.0)
    _CM_PER_INCH = 2.54

    def __init__(
        self,
        source: Tuple[int, int],
        panels: Sequence[float],
        target: Sequence[Tuple[int, int]],
        align: VerticalAlign = VerticalAlign.BOTTOM,
        bezel_gap_cm: float = 0.0,
        anchor_x: float = 0.5,
        anchor_y: float = 0.5,
        y_offsets_cm: Sequence[float] = None,
        x_offsets_cm: Sequence[float] = None,
    ):
        self._src = source
        self._panels = tuple(float(d) for d in panels)
        self._targets = list(target)
        self._align = align
        self._gap = float(bezel_gap_cm)
        self._ax = anchor_x
        self._ay = anchor_y
        self._y_offsets = y_offsets_cm if y_offsets_cm else [0.0] * len(self._panels)
        self._x_offsets = x_offsets_cm if x_offsets_cm else [0.0] * len(self._panels)
        
        self._cms = [self._panel_cm(d) for d in self._panels]

    def _get_cm_layouts(self) -> Tuple[list, list]:
        max_h_raw = max(h for _, h in self._cms)
        def base_y(h: float) -> float:
            if self._align == VerticalAlign.TOP: return 0.0
            if self._align == VerticalAlign.CENTER: return (max_h_raw - h) / 2.0
            return max_h_raw - h
        
        y_cm = [base_y(h) + self._y_offsets[i] for i, (_, h) in enumerate(self._cms)]
        
        x_cm = []
        current_x = 0.0
        for i, (w, _) in enumerate(self._cms):
            current_x += self._x_offsets[i]
            x_cm.append(current_x)
            current_x += w + self._gap
            
        return x_cm, y_cm

    def get_bounding_box_cm(self) -> Tuple[float, float]:
        x_cm, y_cm = self._get_cm_layouts()
        min_x = min(x_cm)
        max_x = max(x + w for x, (w, _) in zip(x_cm, self._cms))
        min_y = min(y_cm)
        max_y = max(y + h for y, (_, h) in zip(y_cm, self._cms))
        return max_x - min_x, max_y - min_y

    def compute(self) -> CropPlan:
        total_w_cm, total_h_cm = self.get_bounding_box_cm()
        x_cm, y_cm = self._get_cm_layouts()
        min_x_cm = min(x_cm)
        min_y_cm = min(y_cm)

        scale = min(self._src[0] / total_w_cm, self._src[1] / total_h_cm)

        widths = [w * scale for w, _ in self._cms]
        heights = [h * scale for _, h in self._cms]
        
        total_w_px = total_w_cm * scale
        total_h_px = total_h_cm * scale

        surplus_x = max(0.0, self._src[0] - total_w_px)
        surplus_y = max(0.0, self._src[1] - total_h_px)
        
        x0 = surplus_x * self._ax
        y0 = surplus_y * self._ay

        panels = tuple(
            Panel(inch=self._panels[i], cm=self._cms[i], target=self._targets[i],
                  crop=self._rect(x0 + (x_cm[i] - min_x_cm) * scale, 
                                  y0 + (y_cm[i] - min_y_cm) * scale, 
                                  widths[i], heights[i]))
            for i in range(len(self._cms))
        )
        return CropPlan(panels=panels, px_per_cm=scale)

    def _panel_cm(self, diagonal_inch: float) -> Tuple[float, float]:
        unit = self._CM_PER_INCH / math.hypot(*self._ASPECT)
        return (diagonal_inch * self._ASPECT[0] * unit,
                diagonal_inch * self._ASPECT[1] * unit)

    def _rect(self, x: float, y: float, w: float, h: float) -> CropRect:
        xi, yi = int(round(x)), int(round(y))
        return CropRect(xi, yi,
                        min(int(round(w)), self._src[0] - xi),
                        min(int(round(h)), self._src[1] - yi))