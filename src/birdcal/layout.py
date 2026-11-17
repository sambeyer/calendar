"""Page layout engine.

Builds a :class:`PagePlan` for each page: a flat list of drawing operations in trim-space
millimetres. The renderer draws the plan; tests inspect it. One code path lays out all
twelve month pages, so only the data changes between months.

Only ``photo_over_grid`` is implemented. ``facing_pages`` is rejected by config validation.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from birdcal.config import Config, FontRole
from birdcal.dates import MonthGrid, column_weekdays, day_name, month_title
from birdcal.geometry import Rect, SheetGeometry
from birdcal.text import TextFitError, fit_text

Align = Literal["left", "centre", "right"]


class LayoutError(Exception):
    """A page cannot be laid out. The message names the page and the field."""


# --- drawing operations -------------------------------------------------------


@dataclass(frozen=True)
class FillRect:
    rect: Rect
    colour: str


@dataclass(frozen=True)
class StrokeRect:
    rect: Rect
    colour: str
    width_pt: float
    dash: tuple[float, ...] | None = None


@dataclass(frozen=True)
class Line:
    x1: float
    y1: float
    x2: float
    y2: float
    colour: str
    width_pt: float


@dataclass(frozen=True)
class Circle:
    cx: float
    cy: float
    r: float
    colour: str
    width_pt: float
    filled: bool = False


@dataclass(frozen=True)
class TextOp:
    rect: Rect
    lines: tuple[str, ...]
    font: str
    size_pt: float
    colour: str
    align: Align


@dataclass(frozen=True)
class ImageOp:
    rect: Rect
    file: str | None
    px_w: int
    px_h: int
    fit: Literal["fill", "fit"]
    focal: tuple[float, float]
    border_mm: float
    border_colour: str
    radius_mm: float
    placeholder: bool
    label: str
    source_path: str | None = None


Op = FillRect | StrokeRect | Line | Circle | TextOp | ImageOp


@dataclass(frozen=True)
class PagePlan:
    kind: Literal["cover", "month", "back", "next_photo", "blank"]
    month: int | None
    ops: tuple[Op, ...]
    guides: tuple[Op, ...] = field(default=())
    content: Rect | None = None


# --- inputs --------------------------------------------------------------------


@dataclass(frozen=True)
class BirdView:
    """Everything the layout needs about one month's bird, with placeholders resolved."""

    month: int
    common_name: str | None
    scientific_name: str | None
    description: str | None
    location: str | None
    date_taken: dt.date | None
    photo_credit: str | None
    photo_file: str | None
    fit: Literal["fill", "fit"]
    focal: tuple[float, float]
    border_mm: float | None
    corner_radius_mm: float | None
    px_w: int
    px_h: int
    placeholder: bool
    source_path: str | None = None

    @property
    def is_empty(self) -> bool:
        return self.common_name is None


@dataclass(frozen=True)
class DayLabels:
    holidays: tuple[str, ...] = ()
    events: tuple[str, ...] = ()


# --- geometry helpers ------------------------------------------------------------


def content_rect(config: Config, geom: SheetGeometry) -> Rect:
    """Area available for content: inside the safe margin, clear of the binding zone."""
    safe = config.safe_margin_mm
    keep = config.binding.keep_clear_mm
    top = max(safe, keep) if config.binding.edge == "top" else safe
    bottom = max(safe, keep) if config.binding.edge == "bottom" else safe
    return Rect(safe, top, geom.trim_w - 2 * safe, geom.trim_h - top - bottom)


def keep_clear_rect(config: Config, geom: SheetGeometry) -> Rect | None:
    keep = config.binding.keep_clear_mm
    if keep <= 0:
        return None
    if config.binding.edge == "top":
        return Rect(0, 0, geom.trim_w, keep)
    if config.binding.edge == "bottom":
        return Rect(0, geom.trim_h - keep, geom.trim_w, keep)
    return None


def grid_region(config: Config, geom: SheetGeometry) -> Rect:
    """Where a grid page's content goes: inside the safe margin, below the binding strip."""
    return content_rect(config, geom)


def photo_region(geom: SheetGeometry) -> Rect:
    """Photo pages are full bleed: the whole page, including the bleed."""
    return geom.bleed_rect


def hole_centres(config: Config, geom: SheetGeometry) -> list[tuple[float, float]]:
    """Binding hole centres in trim mm, along the top or bottom long edge."""
    holes = config.binding.holes
    if holes is None or config.binding.type not in ("spiral", "wire_o"):
        return []
    length = geom.trim_w
    end = holes.end_margin_mm
    pattern = holes.pattern
    if pattern.count is not None:
        if pattern.count == 1:
            positions = [length / 2]
        else:
            step = (length - 2 * end) / (pattern.count - 1)
            positions = [end + i * step for i in range(pattern.count)]
    else:
        pitch = (
            pattern.pitch_mm
            if pattern.pitch_mm is not None
            else 25.4 / (pattern.holes_per_inch or 1)
        )
        n = math.floor((length - 2 * end) / pitch + 1e-9) + 1
        total = (n - 1) * pitch
        start = (length - total) / 2 if holes.centre else end
        positions = [start + i * pitch for i in range(n)]
    if config.binding.edge == "top":
        y = holes.edge_offset_mm
    else:
        y = geom.trim_h - holes.edge_offset_mm
    return [(x, y) for x in positions]


def trim_mark_ops(config: Config, geom: SheetGeometry) -> list[Op]:
    """Crop marks outside the bleed. Empty when trim marks are off."""
    tm = config.trim_marks
    if not tm.enabled:
        return []
    start = geom.bleed + tm.offset_mm
    end = start + tm.length_mm
    colour = "#000000"
    width = tm.line_width_pt
    ops: list[Op] = []
    for x_edge, sx in ((0.0, -1), (geom.trim_w, 1)):
        for y_edge, sy in ((0.0, -1), (geom.trim_h, 1)):
            ops.append(Line(x_edge + sx * start, y_edge, x_edge + sx * end, y_edge, colour, width))
            ops.append(Line(x_edge, y_edge + sy * start, x_edge, y_edge + sy * end, colour, width))
    return ops


def hanger_centre(config: Config) -> tuple[float, float]:
    return config.hanger.x_mm, config.hanger.offset_mm


def _hanger_ops(config: Config, kind: str) -> list[Op]:
    hanger = config.hanger
    if not hanger.enabled or kind not in hanger.pages:
        return []
    cx, cy = hanger_centre(config)
    return [Circle(cx, cy, hanger.diameter_mm / 2, "#D00000", 0.5)]


def check_hanger(config: Config, geom: SheetGeometry) -> None:
    """The hanger hole must sit on the trim and clear of every binding hole."""
    hanger = config.hanger
    if not hanger.enabled:
        return
    r = hanger.diameter_mm / 2
    hole = Rect(hanger.x_mm - r, hanger.offset_mm - r, 2 * r, 2 * r)
    if not geom.trim_rect.contains(hole):
        raise LayoutError("hanger hole is not fully on the trim; check hanger.x_mm and offset_mm")
    holes = config.binding.holes
    if holes is None:
        return
    hr = holes.diameter_mm / 2
    for x, y in hole_centres(config, geom):
        if hole.overlaps(Rect(x - hr, y - hr, 2 * hr, 2 * hr)):
            raise LayoutError(
                "hanger hole overlaps a binding hole; move hanger.x_mm or hanger.offset_mm"
            )


def _hanger_clear_check(config: Config, geom: SheetGeometry, content: Rect, kind: str) -> None:
    hanger = config.hanger
    if not hanger.enabled or kind not in hanger.pages:
        return
    cx, cy = hanger_centre(config)
    r = hanger.diameter_mm / 2
    hole = Rect(cx - r, cy - r, 2 * r, 2 * r)
    if not geom.trim_rect.contains(hole):
        raise LayoutError(f"{kind} page: hanger.x_mm/offset_mm put the hanger outside the trim")
    if hole.overlaps(content):
        raise LayoutError(f"{kind} page: hanger hole overlaps the content area")


# --- text helpers ----------------------------------------------------------------


def _text_op(
    rect: Rect,
    text: str,
    role: FontRole,
    registry: Mapping[str, str],
    role_key: str,
    *,
    where: str,
    align: Align | None = None,
    max_height: float | None = None,
) -> TextOp | None:
    """Fit ``text`` into ``rect`` and return a TextOp, or None when the text is empty."""
    if not text.strip():
        return None
    height = rect.h if max_height is None else min(rect.h, max_height)
    try:
        fitted = fit_text(text, role, registry[role_key], rect.w, height, where=where)
    except TextFitError as exc:
        raise LayoutError(str(exc)) from exc
    return TextOp(
        rect=rect,
        lines=fitted.lines,
        font=fitted.font,
        size_pt=fitted.size_pt,
        colour=role.colour,
        align=align or role.align,
    )


def _block_height(op: TextOp) -> float:
    """Height in mm of a fitted text block (lines x size x leading)."""
    return len(op.lines) * op.size_pt * 1.2 / 72 * 25.4


# --- page builders ---------------------------------------------------------------


@dataclass(frozen=True)
class LayoutContext:
    config: Config
    geom: SheetGeometry
    registry: Mapping[str, str]
    year: int


def _photo_op(ctx: LayoutContext, rect: Rect, bird: BirdView) -> ImageOp:
    cfg = ctx.config
    border = bird.border_mm if bird.border_mm is not None else cfg.photo.border_mm
    radius = (
        bird.corner_radius_mm if bird.corner_radius_mm is not None else cfg.photo.corner_radius_mm
    )
    label = f"PLACEHOLDER  {bird.photo_file or 'no photo yet'}  {bird.px_w} x {bird.px_h} px"
    return ImageOp(
        rect=rect,
        file=bird.photo_file,
        px_w=bird.px_w,
        px_h=bird.px_h,
        fit=bird.fit,
        focal=bird.focal,
        border_mm=border,
        border_colour=cfg.photo.border_colour,
        radius_mm=radius,
        placeholder=bird.placeholder,
        label=label,
        source_path=bird.source_path,
    )


def _caption_ops(ctx: LayoutContext, rect: Rect, bird: BirdView, where: str) -> list[Op]:
    cfg = ctx.config
    reg = ctx.registry
    ops: list[Op] = []
    name = bird.common_name or "Bird name to come"
    desc = bird.description or ("Description to come" if bird.is_empty else "")
    sci = bird.scientific_name or ""
    meta_bits = []
    if bird.location:
        meta_bits.append(bird.location)
    if bird.date_taken is not None:
        meta_bits.append(f"taken {bird.date_taken.strftime('%d/%m/%Y')}")
    meta = " · ".join(meta_bits)

    credit_h = 6.0
    credit_rect = Rect(rect.x, rect.bottom - credit_h, rect.w, credit_h)
    credit_text = f"Photo: {bird.photo_credit}" if bird.photo_credit else ""

    cursor = rect.y
    name_op = _text_op(
        Rect(rect.x, cursor, rect.w, rect.h * 0.35),
        name,
        cfg.fonts.bird_name,
        reg,
        "bird_name",
        where=f"{where} bird name",
        align="left",
        max_height=rect.h * 0.35,
    )
    if name_op:
        ops.append(name_op)
        cursor += _block_height(name_op) + cfg.layout.spacing.gap_mm
    sci_op = _text_op(
        Rect(rect.x, cursor, rect.w, rect.h * 0.12),
        sci,
        cfg.fonts.scientific_name,
        reg,
        "scientific_name",
        where=f"{where} scientific name",
        align="left",
        max_height=rect.h * 0.12,
    )
    if sci_op:
        ops.append(sci_op)
        cursor += _block_height(sci_op) + cfg.layout.spacing.gap_mm / 2
    meta_op = _text_op(
        Rect(rect.x, cursor, rect.w, 8.0),
        meta,
        cfg.fonts.credit,
        reg,
        "credit",
        where=f"{where} location and date",
        align="left",
        max_height=8.0,
    )
    if meta_op:
        ops.append(meta_op)
        cursor += _block_height(meta_op) + cfg.layout.spacing.gap_mm / 2
    desc_height = max(0.0, credit_rect.y - cursor - cfg.layout.spacing.gap_mm)
    desc_op = _text_op(
        Rect(rect.x, cursor, rect.w, desc_height),
        desc,
        cfg.fonts.description,
        reg,
        "description",
        where=f"{where} description",
        align="left",
        max_height=desc_height,
    )
    if desc_op:
        ops.append(desc_op)
    credit_op = _text_op(
        credit_rect,
        credit_text,
        cfg.fonts.credit,
        reg,
        "credit",
        where=f"{where} photo credit",
        align="right",
        max_height=credit_h,
    )
    if credit_op:
        ops.append(credit_op)
    return ops


def _grid_ops(
    ctx: LayoutContext,
    grid_rect: Rect,
    month: MonthGrid,
    labels: Mapping[dt.date, DayLabels],
    where: str,
) -> list[Op]:
    cfg = ctx.config
    reg = ctx.registry
    spacing = cfg.layout.spacing
    grid_cfg = cfg.grid
    ops: list[Op] = []

    week_w = spacing.week_number_width_mm if grid_cfg.week_numbers else 0.0
    header_h = spacing.header_height_mm
    cols = 7
    rows = len(month.weeks)
    body = Rect(
        grid_rect.x + week_w, grid_rect.y + header_h, grid_rect.w - week_w, grid_rect.h - header_h
    )
    cell_w = body.w / cols
    cell_h = body.h / rows
    pad = grid_cfg.cell_padding_mm
    line = grid_cfg.line_width_pt

    # Weekday header: one shared size so the names match.
    names = [day_name(n, grid_cfg.day_name_format) for n in column_weekdays(month.week_start)]
    header_rects = [Rect(body.x + i * cell_w, grid_rect.y, cell_w, header_h) for i in range(cols)]
    size = None
    for name, rect in zip(names, header_rects, strict=True):
        probe = _text_op(
            rect,
            name,
            cfg.fonts.day_names,
            reg,
            "day_names",
            where=f"{where} day names",
            align="centre",
        )
        if probe is None:
            continue
        size = probe.size_pt if size is None else min(size, probe.size_pt)
    if size is not None:
        for name, rect in zip(names, header_rects, strict=True):
            ops.append(
                TextOp(rect, (name,), reg["day_names"], size, cfg.fonts.day_names.colour, "centre")
            )

    if week_w > 0:
        wk_header = _text_op(
            Rect(grid_rect.x, grid_rect.y, week_w, header_h),
            "Wk",
            cfg.fonts.day_names,
            reg,
            "day_names",
            where=f"{where} week header",
            align="centre",
        )
        if wk_header is not None:
            ops.append(wk_header)

    for r, week in enumerate(month.weeks):
        y = body.y + r * cell_h
        if week_w > 0:
            wk_rect = Rect(grid_rect.x, y, week_w, cell_h)
            op = _text_op(
                wk_rect.inset(0.5),
                str(week.iso_week),
                cfg.fonts.day_names,
                reg,
                "day_names",
                where=f"{where} week {week.iso_week}",
                align="centre",
            )
            if op:
                ops.append(op)
        for c, day in enumerate(week.days):
            cell = Rect(body.x + c * cell_w, y, cell_w, cell_h)
            if grid_cfg.weekend_shading and day.date.weekday() >= 5:
                ops.append(FillRect(cell, grid_cfg.weekend_colour))
            if not day.in_month and not grid_cfg.show_adjacent_days:
                continue
            number_rect = Rect(
                cell.x + pad, cell.y + pad, cell.w - 2 * pad, spacing.number_height_mm
            )
            number_colour = grid_cfg.adjacent_day_colour if not day.in_month else None
            number_role = cfg.fonts.day_numbers
            num_text = str(day.date.day)
            num_op = _text_op(
                number_rect,
                num_text,
                number_role,
                reg,
                "day_numbers",
                where=f"{where} day {day.date.day}",
                align="right",
            )
            if num_op is not None:
                if number_colour is not None:
                    num_op = TextOp(
                        num_op.rect,
                        num_op.lines,
                        num_op.font,
                        num_op.size_pt,
                        number_colour,
                        num_op.align,
                    )
                ops.append(num_op)
            if not day.in_month:
                continue
            day_labels = labels.get(day.date, DayLabels())
            label_top = number_rect.bottom + 0.5
            label_rect = Rect(
                cell.x + pad, label_top, cell.w - 2 * pad, cell.bottom - pad - label_top
            )
            hol_text = "\n".join(
                f"{cfg.labels.holiday.prefix}{t}{cfg.labels.holiday.suffix}"
                for t in day_labels.holidays
            )
            hol_op = _text_op(
                label_rect,
                hol_text,
                cfg.fonts.holiday_label,
                reg,
                "holiday_label",
                where=f"{where} {day.date.isoformat()} holiday",
            )
            if hol_op:
                ops.append(hol_op)
                label_rect = Rect(
                    label_rect.x,
                    label_rect.y + _block_height(hol_op) + 0.5,
                    label_rect.w,
                    max(0.0, label_rect.h - _block_height(hol_op) - 0.5),
                )
            ev_text = "\n".join(
                f"{cfg.labels.event.prefix}{t}{cfg.labels.event.suffix}" for t in day_labels.events
            )
            ev_op = _text_op(
                label_rect,
                ev_text,
                cfg.fonts.event_label,
                reg,
                "event_label",
                where=f"{where} {day.date.isoformat()} event",
            )
            if ev_op:
                ops.append(ev_op)

    # Grid lines last so they sit on top of shading.
    for r in range(rows + 1):
        y = body.y + r * cell_h
        ops.append(Line(body.x, y, body.right, y, grid_cfg.line_colour, line))
    for c in range(cols + 1):
        x = body.x + c * cell_w
        ops.append(Line(x, body.y, x, body.bottom, grid_cfg.line_colour, line))
    ops.append(
        Line(
            grid_rect.x,
            grid_rect.y + header_h,
            body.right,
            grid_rect.y + header_h,
            grid_cfg.line_colour,
            line,
        )
    )
    return ops


def month_page(
    ctx: LayoutContext,
    month: MonthGrid,
    bird: BirdView,
    labels: Mapping[dt.date, DayLabels],
) -> PagePlan:
    cfg = ctx.config
    sp = cfg.layout.spacing
    where = f"month {month.month:02d}"
    content = content_rect(cfg, ctx.geom)
    cal = grid_region(cfg, ctx.geom)
    min_grid = sp.header_height_mm + 6 * 9.0
    caption_below = cfg.layout.caption.placement == "below_grid"
    needed = (
        sp.title_height_mm
        + sp.gap_mm
        + min_grid
        + (sp.gap_mm + sp.caption_height_mm if caption_below else 0.0)
    )
    if cal.h < needed:
        raise LayoutError(
            f"{where}: no room for the calendar: {cal.h:.1f} mm available, {needed:.1f} mm needed. "
            "Reduce the caption height or the binding keep-clear depth."
        )
    _hanger_clear_check(cfg, ctx.geom, content, "months")

    ops: list[Op] = []

    title_rect = Rect(cal.x, cal.y, cal.w, sp.title_height_mm)
    title_op = _text_op(
        title_rect,
        month_title(ctx.year, month.month),
        cfg.fonts.month_title,
        ctx.registry,
        "month_title",
        where=f"{where} title",
    )
    if title_op:
        ops.append(title_op)

    body_y = cal.y + sp.title_height_mm + sp.gap_mm
    body_h = cal.bottom - body_y
    if caption_below:
        grid_rect = Rect(cal.x, body_y, cal.w, body_h - sp.caption_height_mm - sp.gap_mm)
        caption_rect = Rect(cal.x, grid_rect.bottom + sp.gap_mm, cal.w, sp.caption_height_mm)
    else:
        cap_w = cal.w * sp.caption_width_fraction
        grid_rect = Rect(cal.x, body_y, cal.w - cap_w - sp.gap_mm, body_h)
        caption_rect = Rect(grid_rect.right + sp.gap_mm, body_y, cap_w, body_h)

    ops.extend(_grid_ops(ctx, grid_rect, month, labels, where))
    ops.extend(_caption_ops(ctx, caption_rect, bird, where))

    guides = _month_guides(ctx, content)
    return PagePlan(
        kind="month", month=month.month, ops=tuple(ops), guides=tuple(guides), content=content
    )


def photo_page(
    ctx: LayoutContext,
    bird: BirdView,
    kind: Literal["cover", "next_photo"],
    month: int | None,
) -> PagePlan:
    """A full-page photo with nothing else on it."""
    rect = photo_region(ctx.geom)
    ops: list[Op] = [_photo_op(ctx, rect, bird)]
    # Holes are punched through the photo, so no binding band is drawn over it.
    guides: list[Op] = [
        StrokeRect(ctx.geom.bleed_rect, "#1F6FD6", 0.25, (3.0, 2.0)),
        StrokeRect(ctx.geom.trim_rect, "#E0301E", 0.25),
        *_hole_guides(ctx),
    ]
    return PagePlan(kind=kind, month=month, ops=tuple(ops), guides=tuple(guides), content=rect)


def next_month_page(ctx: LayoutContext, bird: BirdView, next_month: int) -> PagePlan:
    """The back of a month's grid sheet: the next month's photo, full page."""
    return photo_page(ctx, bird, "next_photo", next_month)


def blank_page(ctx: LayoutContext) -> PagePlan:
    """A sheet with nothing printed on this side. Guides still show the binding."""
    content = content_rect(ctx.config, ctx.geom)
    return PagePlan(
        kind="blank", month=None, ops=(), guides=tuple(_common_guides(ctx)), content=content
    )


def cover_page(ctx: LayoutContext, bird: BirdView) -> PagePlan:
    return photo_page(ctx, bird, "cover", None)


def back_page(ctx: LayoutContext, entries: Sequence[tuple[dt.date, str, str]]) -> PagePlan:
    """Back page: the year's holidays and events, as dd/mm/yyyy lines, in the photo region."""
    cfg = ctx.config
    sp = cfg.layout.spacing
    content = content_rect(cfg, ctx.geom)
    _hanger_clear_check(cfg, ctx.geom, content, "back")
    region = grid_region(cfg, ctx.geom)
    heading_rect = Rect(region.x, region.y, region.w, sp.title_height_mm)
    ops: list[Op] = []
    heading = _text_op(
        heading_rect,
        f"{ctx.year} public holidays and events",
        cfg.fonts.month_title,
        ctx.registry,
        "month_title",
        where="back heading",
    )
    if heading:
        ops.append(heading)
    lines = "\n".join(f"{d.strftime('%d/%m/%Y')}  {label}" for d, label, _kind in entries)
    body_rect = Rect(
        region.x,
        heading_rect.bottom + sp.gap_mm,
        region.w,
        region.bottom - heading_rect.bottom - sp.gap_mm,
    )
    body = _text_op(
        body_rect, lines, cfg.fonts.description, ctx.registry, "description", where="back list"
    )
    if body:
        ops.append(body)
    guides = _common_guides(ctx)
    return PagePlan(kind="back", month=None, ops=tuple(ops), guides=tuple(guides), content=content)


def _month_guides(ctx: LayoutContext, content: Rect) -> list[Op]:
    return _common_guides(ctx) + [
        StrokeRect(content, "#7A3DB8", 0.25, (2.0, 2.0)),
    ]


def _common_guides(ctx: LayoutContext) -> list[Op]:
    cfg = ctx.config
    geom = ctx.geom
    guides: list[Op] = []
    safe = geom.trim_rect.inset(cfg.safe_margin_mm)
    keep = keep_clear_rect(cfg, geom)
    if keep is not None:
        guides.append(FillRect(keep, "#FFD9A8"))
    guides.append(StrokeRect(geom.bleed_rect, "#1F6FD6", 0.25, (3.0, 2.0)))
    guides.append(StrokeRect(safe, "#2E9E4F", 0.25, (3.0, 2.0)))
    guides.append(StrokeRect(geom.trim_rect, "#E0301E", 0.25))
    guides += _hole_guides(ctx)
    return guides


def _hole_guides(ctx: LayoutContext) -> list[Op]:
    holes = ctx.config.binding.holes
    if holes is None:
        return []
    return [
        Circle(x, y, holes.diameter_mm / 2, "#E0301E", 0.4)
        for x, y in hole_centres(ctx.config, ctx.geom)
    ]


def binding_and_hanger_ops(ctx: LayoutContext, kind: str) -> list[Op]:
    """Physical features the printer punches. Drawn only in proof and draft guides."""
    return _hanger_ops(ctx.config, kind)


def placeholder_image_op(ctx: LayoutContext, rect: Rect, bird: BirdView) -> ImageOp:
    return _photo_op(ctx, rect, bird)


__all__ = [
    "BirdView",
    "Circle",
    "DayLabels",
    "FillRect",
    "ImageOp",
    "LayoutContext",
    "LayoutError",
    "Line",
    "Op",
    "PagePlan",
    "StrokeRect",
    "TextOp",
    "back_page",
    "binding_and_hanger_ops",
    "content_rect",
    "cover_page",
    "hole_centres",
    "keep_clear_rect",
    "month_page",
    "trim_mark_ops",
]
