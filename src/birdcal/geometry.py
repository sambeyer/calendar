"""Page geometry in millimetres, with pure functions for placement.

Coordinate system used by the layout: origin at the TOP-LEFT of the trim, x to the right,
y down, all in mm. The renderer converts to PDF points (origin bottom-left of the MediaBox).

Page boxes (outward from the trim):

    MediaBox  = trim + bleed + trim-mark space (if trim marks are on)
    BleedBox  = trim + bleed
    TrimBox   = trim
    ArtBox    = trim
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MM_PER_INCH = 25.4
PT_PER_MM = 72 / MM_PER_INCH


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def inset(self, dx: float, dy: float | None = None) -> Rect:
        dy = dx if dy is None else dy
        return Rect(self.x + dx, self.y + dy, self.w - 2 * dx, self.h - 2 * dy)

    def contains(self, other: Rect, tol: float = 1e-6) -> bool:
        return (
            other.x >= self.x - tol
            and other.y >= self.y - tol
            and other.right <= self.right + tol
            and other.bottom <= self.bottom + tol
        )

    def overlaps(self, other: Rect, tol: float = 1e-6) -> bool:
        """True if the interiors intersect (touching edges do not count)."""
        return (
            self.x < other.right - tol
            and other.x < self.right - tol
            and self.y < other.bottom - tol
            and other.y < self.bottom - tol
        )


@dataclass(frozen=True)
class SheetGeometry:
    trim_w: float
    trim_h: float
    bleed: float
    marks_space: float  # 0 when trim marks are off; otherwise offset + length

    @property
    def outside(self) -> float:
        """Distance from the trim to the MediaBox edge."""
        return self.bleed + self.marks_space

    @property
    def media_w(self) -> float:
        return self.trim_w + 2 * self.outside

    @property
    def media_h(self) -> float:
        return self.trim_h + 2 * self.outside

    @property
    def trim_rect(self) -> Rect:
        return Rect(0, 0, self.trim_w, self.trim_h)

    @property
    def bleed_rect(self) -> Rect:
        return self.trim_rect.inset(-self.bleed)

    def to_pdf_pt(self, x: float, y: float) -> tuple[float, float]:
        """Trim-space mm (top-left origin) to MediaBox points (bottom-left origin)."""
        px = (x + self.outside) * PT_PER_MM
        py = (self.trim_h - y + self.outside) * PT_PER_MM
        return px, py

    def box_pt(self, rect: Rect) -> tuple[float, float, float, float]:
        """A trim-space rectangle as (x0, y0, x1, y1) in MediaBox points."""
        x0, y1 = self.to_pdf_pt(rect.x, rect.y)
        x1, y0 = self.to_pdf_pt(rect.right, rect.bottom)
        return x0, y0, x1, y1


@dataclass(frozen=True)
class Placement:
    """Where an image is drawn, in trim-space mm. Anything outside ``clip`` is hidden."""

    draw: Rect
    clip: Rect


def fill_placement(img_w: int, img_h: int, box: Rect, focal: tuple[float, float]) -> Placement:
    """Cover ``box`` with the image, keeping the focal point as near the centre as possible.

    The image is scaled uniformly so it covers the box, then shifted so the focal point
    sits at the box centre, clamped so the image never leaves a gap inside the box.
    """
    if img_w <= 0 or img_h <= 0:
        raise ValueError("image dimensions must be positive")
    fx, fy = focal
    scale = max(box.w / img_w, box.h / img_h)
    draw_w = img_w * scale
    draw_h = img_h * scale
    x = box.cx - fx * draw_w
    y = box.cy - fy * draw_h
    x = min(max(x, box.right - draw_w), box.x)
    y = min(max(y, box.bottom - draw_h), box.y)
    return Placement(draw=Rect(x, y, draw_w, draw_h), clip=box)


def fit_placement(img_w: int, img_h: int, box: Rect) -> Placement:
    """Show the whole image inside ``box``, centred, without cropping."""
    if img_w <= 0 or img_h <= 0:
        raise ValueError("image dimensions must be positive")
    scale = min(box.w / img_w, box.h / img_h)
    draw_w = img_w * scale
    draw_h = img_h * scale
    draw = Rect(box.cx - draw_w / 2, box.cy - draw_h / 2, draw_w, draw_h)
    return Placement(draw=draw, clip=draw)


def placement_for(
    mode: Literal["fill", "fit"],
    img_w: int,
    img_h: int,
    box: Rect,
    focal: tuple[float, float],
) -> Placement:
    if mode == "fill":
        return fill_placement(img_w, img_h, box, focal)
    return fit_placement(img_w, img_h, box)


def effective_ppi(img_w: int, img_h: int, box: Rect, mode: Literal["fill", "fit"]) -> float:
    """Pixels per inch of the image as printed at ``box``.

    Uses the scale actually applied. For ``fill`` the image is cropped, not resampled, so the
    scale is the larger of the two per-axis scales; the PPI is the lower of the two axes.
    """
    placement = placement_for(mode, img_w, img_h, box, (0.5, 0.5))
    return MM_PER_INCH * img_w / placement.draw.w
