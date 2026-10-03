#!/usr/bin/env python3
"""dualcropper.core - geometry engine for seamless dual-monitor span wallpapers.

Splits a single source image into two per-panel crops that, once each is
resampled to its panel's native resolution, render the artwork at an identical
physical scale (source px per cm) on both displays. This compensates for the
differing pixel densities (PPI) of mixed-size monitors and keeps image features
continuous across the bezel seam.

Pure stdlib and side-effect free: the class only solves crop geometry.

Example:
    from PIL import Image
    plan = DualCropper((6269, 3055), 32.0, 24.0, small_side=Side.RIGHT).compute()
    large = Image.open(src).crop(plan.large.box()).resize(plan.target, Image.LANCZOS)
    small = Image.open(src).crop(plan.small.box()).resize(plan.target, Image.LANCZOS)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Tuple

__all__ = ["Side", "VerticalAlign", "Anchor", "CropRect", "CropPlan", "DualCropper"]


class Side(Enum):
    """Horizontal position of the smaller panel relative to the larger one."""
    LEFT = "left"
    RIGHT = "right"


class VerticalAlign(Enum):
    """Physical vertical alignment of the two panels as placed on the desk."""
    TOP = "top"        # top edges collinear
    CENTER = "center"  # vertical centres collinear
    BOTTOM = "bottom"  # bottom edges collinear (typical desk setup)


class Anchor(Enum):
    """Placement of the crop window inside the unused source-image margin."""
    START = "start"    # flush with the left/top margin edge
    CENTER = "center"  # centred within the margin
    END = "end"        # flush with the right/bottom margin edge


@dataclass(frozen=True)
class CropRect:
    """Axis-aligned crop window in source-image pixel coordinates."""
    x: int
    y: int
    width: int
    height: int

    def box(self) -> Tuple[int, int, int, int]:
        """Return a PIL-compatible (left, upper, right, lower) tuple."""
        return self.x, self.y, self.x + self.width, self.y + self.height


@dataclass(frozen=True)
class CropPlan:
    """Immutable result: one crop per panel plus shared metadata.

    ``large`` is the crop for Monitor 1 (the physically bigger panel),
    ``small`` for Monitor 2.  ``target`` / ``target_small`` are the exact
    output pixel sizes each crop must be resampled to - normally the two
    monitors' native resolutions.  ``left`` / ``right`` expose the same two
    rectangles keyed by their horizontal position on the desk, so callers can
    map crops to displays without re-deriving the layout.
    """
    large: CropRect                # crop destined for Monitor 1 (bigger panel)
    small: CropRect                # crop destined for Monitor 2 (smaller panel)
    target: Tuple[int, int]        # output size for the large crop (M1 native)
    px_per_cm: float               # common physical scale enforced on both panels
    large_cm: Tuple[float, float]  # physical (width, height) of the large panel
    small_cm: Tuple[float, float]  # physical (width, height) of the small panel
    target_small: Tuple[int, int] = None   # output size for the small crop (M2)

    @property
    def left(self) -> CropRect:
        """Crop rectangle that sits on the left side of the desk layout."""
        return self.small if self.small.x <= self.large.x else self.large

    @property
    def right(self) -> CropRect:
        """Crop rectangle that sits on the right side of the desk layout."""
        return self.large if self.small.x <= self.large.x else self.small


class DualCropper:
    """Compute per-panel crop rectangles for a two-monitor span wallpaper.

    Core invariant: both crops are sized in source pixels proportionally to
    their panel's physical width using one shared proportionality constant
    (`px_per_cm`). After each crop is resampled to the panel native resolution,
    one source pixel occupies exactly `1 / px_per_cm` cm on *both* panels, so
    the artwork continues across the seam with no scale discontinuity.
    """

    _ASPECT = (16.0, 9.0)  # assumed panel aspect ratio (width, height)
    _CM_PER_INCH = 2.54    # inch-to-centimetre conversion

    def __init__(
        self,
        source: Tuple[int, int],
        large_inch: float = 32.0,
        small_inch: float = 24.0,
        target: Tuple[int, int] = (1920, 1080),
        target_small: Tuple[int, int] = None,
        small_side: Side = Side.RIGHT,
        align: VerticalAlign = VerticalAlign.BOTTOM,
        bezel_gap_cm: float = 0.0,
        anchor_x: Anchor = Anchor.CENTER,
        anchor_y: Anchor = Anchor.CENTER,
    ) -> None:
        """
        source       : source image size in pixels, (width, height).
        large_inch   : diagonal of Monitor 1 (the physically larger panel), inches.
        small_inch   : diagonal of Monitor 2 (the physically smaller panel), inches.
        target       : output pixel size for the large crop - normally M1's
                       native resolution, e.g. (1920, 1080).
        target_small : output pixel size for the small crop - normally M2's
                       native resolution; defaults to *target* when omitted,
                       so mixed-resolution dual setups are supported directly.
        small_side   : side on which the smaller panel is positioned.
        align        : physical vertical alignment of the panels.
        bezel_gap_cm : combined bezel width to hide at the seam, in centimetres.
        anchor_x/y   : crop-window placement within the surplus source margin.
        """
        if source[0] <= 0 or source[1] <= 0:
            raise ValueError("source size must be positive")
        if large_inch <= 0 or small_inch <= 0:
            raise ValueError("panel diagonals must be positive")
        if target[0] <= 0 or target[1] <= 0:
            raise ValueError("target resolution must be positive")
        if target_small is not None and (target_small[0] <= 0 or target_small[1] <= 0):
            raise ValueError("small target resolution must be positive")
        if bezel_gap_cm < 0:
            raise ValueError("bezel gap must be non-negative")

        self._src = source
        self._large_inch = float(large_inch)
        self._small_inch = float(small_inch)
        self._target = target
        self._target_small = target_small if target_small is not None else target
        self._side = small_side
        self._align = align
        self._gap = float(bezel_gap_cm)
        self._ax = anchor_x
        self._ay = anchor_y

    # ---------------------------------------------------------------- public

    def compute(self) -> CropPlan:
        """Solve the crop geometry and return an immutable CropPlan."""
        wl_cm, hl_cm = self._panel_cm(self._large_inch)
        ws_cm, hs_cm = self._panel_cm(self._small_inch)

        # Shared physical scale (source px per cm): the largest value for which
        # the full horizontal span (large + gap + small) and the large panel
        # height still fit inside the source image.
        scale = min(self._src[0] / (wl_cm + ws_cm + self._gap),
                    self._src[1] / hl_cm)

        # Crop footprints in source pixels = physical panel size * shared scale.
        wl, hl = wl_cm * scale, hl_cm * scale
        ws, hs = ws_cm * scale, hs_cm * scale
        gap = self._gap * scale  # source-px strip concealed behind the bezels

        # Position the crop window inside the leftover (surplus) source margin.
        x0 = self._offset(self._src[0] - (wl + gap + ws), self._ax)
        y0 = self._offset(self._src[1] - hl, self._ay)

        # Horizontal layout: panels are adjacent; the gap sits exactly at the
        # seam so the hidden strip matches the physical bezel width.
        if self._side is Side.RIGHT:
            xl, xs = x0, x0 + wl + gap
        else:
            xs, xl = x0, x0 + ws + gap

        # Vertical offset of the small crop mirrors the physical mounting: the
        # large crop defines the window, the small one slides inside it exactly
        # as the real panels are aligned on the desk.
        dy = {VerticalAlign.TOP: 0.0,
              VerticalAlign.CENTER: (hl - hs) / 2.0,
              VerticalAlign.BOTTOM: hl - hs}[self._align]

        return CropPlan(
            large=self._rect(xl, y0, wl, hl),
            small=self._rect(xs, y0 + dy, ws, hs),
            target=self._target,
            px_per_cm=scale,
            large_cm=(wl_cm, hl_cm),
            small_cm=(ws_cm, hs_cm),
            target_small=self._target_small,
        )

    # ------------------------------------------------------------- internals

    def _panel_cm(self, diagonal_inch: float) -> Tuple[float, float]:
        """Physical panel (width, height) in cm, derived from a 16:9 diagonal."""
        unit = self._CM_PER_INCH / math.hypot(*self._ASPECT)
        return (diagonal_inch * self._ASPECT[0] * unit,
                diagonal_inch * self._ASPECT[1] * unit)

    @staticmethod
    def _offset(surplus: float, anchor: "Anchor | float") -> float:
        """Window offset within the unused margin.

        *anchor* is either an :class:`Anchor` enum (discrete start/center/end)
        or a float in [0, 1] - a continuous position supplied by the GUI
        slider, where 0.0 flush-left/top, 0.5 centred and 1.0 flush-right/bottom.
        The enum values map exactly onto 0 / 0.5 / 1 so both modes agree.
        """
        if isinstance(anchor, Anchor):
            return {Anchor.START: 0.0,
                    Anchor.CENTER: surplus / 2.0,
                    Anchor.END: surplus}[anchor]
        t = min(max(float(anchor), 0.0), 1.0)
        return surplus * t

    def _rect(self, x: float, y: float, w: float, h: float) -> CropRect:
        """Round to integer pixels and clamp the window to the source bounds."""
        xi, yi = int(round(x)), int(round(y))
        return CropRect(xi, yi,
                        min(int(round(w)), self._src[0] - xi),
                        min(int(round(h)), self._src[1] - yi))
