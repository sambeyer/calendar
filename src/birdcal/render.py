"""Render :class:`PagePlan` objects to PDF with reportlab.

Page boxes are set from the sheet geometry: MediaBox (trim + bleed + mark space),
BleedBox (trim + bleed), TrimBox and ArtBox (trim). ``invariant=1`` fixes the creation date
and document ID so identical inputs give identical bytes (verified in stage 4).

Guides (trim, bleed, safe area, keep-clear zone, holes, hanger) are drawn only when
``guides=True``, which is never the case for print builds. Trim marks are production marks
and are drawn whenever they are enabled.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from reportlab import rl_config
from reportlab.lib.colors import HexColor
from reportlab.pdfgen.canvas import Canvas

# reportlab wraps embedded JPEG data in ASCII85 by default. That is a transport encoding, but
# it makes the stream differ from the source file. Turning it off embeds the JPEG bytes
# exactly (DCTDecode only), which is what the lossless check relies on. Set once, globally.
rl_config.useA85 = 0

from birdcal.geometry import PT_PER_MM, Rect, SheetGeometry, placement_for
from birdcal.layout import (
    Circle,
    FillRect,
    ImageOp,
    Line,
    Op,
    PagePlan,
    StrokeRect,
    TextOp,
)
from birdcal.text import LEADING

PLACEHOLDER_BANDS = 24
PLACEHOLDER_TOP = "#9FC5E8"
PLACEHOLDER_BOTTOM = "#F6E7B0"


class _Converter:
    """Trim-space mm to reportlab points for one sheet."""

    def __init__(self, geom: SheetGeometry) -> None:
        self.geom = geom

    def x(self, x_mm: float) -> float:
        return (x_mm + self.geom.outside) * PT_PER_MM

    def y(self, y_mm: float) -> float:
        return (self.geom.trim_h - y_mm + self.geom.outside) * PT_PER_MM

    def len(self, mm: float) -> float:
        return mm * PT_PER_MM


def _lerp_hex(a: str, b: str, t: float) -> str:
    """Linear blend of two #RRGGBB colours, t in 0..1."""
    ca = HexColor(a)
    cb = HexColor(b)
    mixed = (
        ca.red + (cb.red - ca.red) * t,
        ca.green + (cb.green - ca.green) * t,
        ca.blue + (cb.blue - ca.blue) * t,
    )
    return "#" + "".join(f"{round(c * 255):02X}" for c in mixed)


class PdfRenderer:
    def __init__(
        self,
        path: Path,
        geom: SheetGeometry,
        font_for_labels: str,
        *,
        title: str,
    ) -> None:
        self.geom = geom
        self.conv = _Converter(geom)
        self.font_for_labels = font_for_labels
        self.canvas = Canvas(
            str(path),
            pagesize=(geom.media_w * PT_PER_MM, geom.media_h * PT_PER_MM),
            invariant=1,
            pageCompression=1,
        )
        self.canvas.setTitle(title)
        self.canvas.setAuthor("birdcal")
        self.canvas.setCreator("birdcal")

    # boxes ---------------------------------------------------------------
    def _set_boxes(self) -> None:
        media = (0, 0, self.geom.media_w * PT_PER_MM, self.geom.media_h * PT_PER_MM)
        bleed = self.geom.box_pt(self.geom.bleed_rect)
        trim = self.geom.box_pt(self.geom.trim_rect)
        self.canvas.setCropBox(media)
        self.canvas.setBleedBox(bleed)
        self.canvas.setTrimBox(trim)
        self.canvas.setArtBox(trim)

    # primitives --------------------------------------------------------------
    def _rect_path(self, rect: Rect) -> tuple[float, float, float, float]:
        x0 = self.conv.x(rect.x)
        y0 = self.conv.y(rect.bottom)
        return x0, y0, self.conv.len(rect.w), self.conv.len(rect.h)

    def draw_op(self, op: Op) -> None:
        c = self.canvas
        if isinstance(op, FillRect):
            if op.alpha < 1:
                # Colour before alpha: setting the alpha first lets a later state change
                # override it, which left the panel solid.
                c.saveState()
                c.setFillColor(HexColor(op.colour))
                c.setFillAlpha(op.alpha)
                c.rect(*self._rect_path(op.rect), stroke=0, fill=1)
                c.restoreState()
            else:
                c.setFillColor(HexColor(op.colour))
                c.rect(*self._rect_path(op.rect), stroke=0, fill=1)
        elif isinstance(op, StrokeRect):
            c.saveState()
            c.setStrokeColor(HexColor(op.colour))
            c.setLineWidth(op.width_pt)
            if op.dash:
                c.setDash(list(op.dash))
            c.rect(*self._rect_path(op.rect), stroke=1, fill=0)
            c.restoreState()
        elif isinstance(op, Line):
            c.saveState()
            c.setStrokeColor(HexColor(op.colour))
            c.setLineWidth(op.width_pt)
            c.line(self.conv.x(op.x1), self.conv.y(op.y1), self.conv.x(op.x2), self.conv.y(op.y2))
            c.restoreState()
        elif isinstance(op, Circle):
            c.saveState()
            c.setStrokeColor(HexColor(op.colour))
            c.setFillColor(HexColor(op.colour))
            c.setLineWidth(op.width_pt)
            c.circle(
                self.conv.x(op.cx),
                self.conv.y(op.cy),
                self.conv.len(op.r),
                stroke=0 if op.filled else 1,
                fill=1 if op.filled else 0,
            )
            c.restoreState()
        elif isinstance(op, TextOp):
            self._text(op)
        elif isinstance(op, ImageOp):
            self._image(op)
        else:  # pragma: no cover - exhaustive over Op
            raise TypeError(f"unknown op {op!r}")

    def _text(self, op: TextOp) -> None:
        c = self.canvas
        c.saveState()
        c.setFillColor(HexColor(op.colour))
        c.setFont(op.font, op.size_pt)
        leading_mm = op.size_pt * LEADING / 72 * 25.4
        ascent_mm = op.size_pt * 0.8 / 72 * 25.4
        for i, line in enumerate(op.lines):
            width_mm = c.stringWidth(line, op.font, op.size_pt) / 72 * 25.4
            if op.align == "right":
                x_mm = op.rect.right - width_mm
            elif op.align == "centre":
                x_mm = op.rect.cx - width_mm / 2
            else:
                x_mm = op.rect.x
            baseline_mm = op.rect.y + ascent_mm + i * leading_mm
            c.drawString(self.conv.x(x_mm), self.conv.y(baseline_mm), line)
        c.restoreState()

    def _image(self, op: ImageOp) -> None:
        c = self.canvas
        c.saveState()
        clip_rect = op.rect
        if op.radius_mm > 0:
            path = c.beginPath()
            x, y, w, h = self._rect_path(op.rect)
            path.roundRect(x, y, w, h, self.conv.len(op.radius_mm))
            c.clipPath(path, stroke=0, fill=0)
        else:
            x, y, w, h = self._rect_path(clip_rect)
            path = c.beginPath()
            path.rect(x, y, w, h)
            c.clipPath(path, stroke=0, fill=0)
        if op.turned:
            cx = self.conv.x(op.rect.x + op.rect.w / 2)
            cy = self.conv.y(op.rect.y + op.rect.h / 2)
            c.translate(cx, cy)
            c.rotate(180)
            c.translate(-cx, -cy)
        if op.placeholder or op.source_path is None:
            self._placeholder(op)
        else:
            self._photo(op)
        c.restoreState()
        if op.border_mm > 0:
            c.saveState()
            c.setStrokeColor(HexColor(op.border_colour))
            c.setLineWidth(self.conv.len(op.border_mm))
            inset = op.border_mm / 2
            c.rect(*self._rect_path(op.rect.inset(inset)), stroke=1, fill=0)
            c.restoreState()

    def _photo(self, op: ImageOp) -> None:
        """Draw the real JPEG at the placed size. Scaling is a PDF matrix, not resampling."""
        assert op.source_path is not None
        placement = placement_for(op.fit, op.px_w, op.px_h, op.rect, op.focal)
        draw = placement.draw
        x, y = self.conv.x(draw.x), self.conv.y(draw.bottom)
        self.canvas.drawImage(
            op.source_path,
            x,
            y,
            width=self.conv.len(draw.w),
            height=self.conv.len(draw.h),
            mask=None,
        )

    def _placeholder(self, op: ImageOp) -> None:
        """Vector gradient with the filename and pixel size: stands in for a real photo."""
        c = self.canvas
        placement = placement_for(op.fit, op.px_w, op.px_h, op.rect, op.focal)
        draw = placement.draw
        band_h = draw.h / PLACEHOLDER_BANDS
        for i in range(PLACEHOLDER_BANDS):
            t = i / max(1, PLACEHOLDER_BANDS - 1)
            c.setFillColor(HexColor(_lerp_hex(PLACEHOLDER_TOP, PLACEHOLDER_BOTTOM, t)))
            band = Rect(draw.x, draw.y + i * band_h, draw.w, band_h + 0.05)
            c.rect(*self._rect_path(band), stroke=0, fill=1)
        c.setFillColor(HexColor("#FFFFFF"))
        label_h = 6.0
        label_rect = Rect(op.rect.x, op.rect.bottom - label_h, op.rect.w, label_h)
        c.rect(*self._rect_path(label_rect), stroke=0, fill=1)
        c.setFillColor(HexColor("#222222"))
        c.setFont(self.font_for_labels, 8)
        c.drawString(self.conv.x(label_rect.x + 2), self.conv.y(label_rect.y + 4.3), op.label)

    # pages --------------------------------------------------------------------
    def add_page(self, plan: PagePlan, marks: Sequence[Op], *, guides: bool) -> None:
        self._set_boxes()
        for op in plan.ops:
            self.draw_op(op)
        if guides:
            for op in plan.guides:
                self.draw_op(op)
        for op in marks:
            self.draw_op(op)
        self.canvas.showPage()

    def save(self) -> None:
        self.canvas.save()
