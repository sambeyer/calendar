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
from dataclasses import dataclass, field, replace
from typing import Literal

from birdcal.config import Config, FontRole
from birdcal.dates import MonthGrid, column_weekdays, day_name, month_title
from birdcal.geometry import Rect, SheetGeometry
from birdcal.text import TextFitError, fit_text, string_width_mm

Align = Literal["left", "centre", "right"]


class LayoutError(Exception):
    """A page cannot be laid out. The message names the page and the field."""


# --- drawing operations -------------------------------------------------------


@dataclass(frozen=True)
class FillRect:
    rect: Rect
    colour: str
    alpha: float = 1.0


@dataclass(frozen=True)
class RoundedFillRect:
    rect: Rect
    colour: str
    radius_mm: float


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


Op = FillRect | RoundedFillRect | StrokeRect | Line | Circle | TextOp | ImageOp


@dataclass(frozen=True)
class PagePlan:
    kind: Literal["cover", "month", "back", "next_photo", "blank"]
    month: int | None
    ops: tuple[Op, ...]
    guides: tuple[Op, ...] = field(default=())
    content: Rect | None = None
    # The back of a flip-top sheet: the photo is upright, but the binding holes and hanger hole
    # sit along the bottom edge instead of the top.
    binding_at_bottom: bool = False
    # The page's content (photo, text, panel) is printed upside down, as the back of the calendar
    # is read unopened. Guides, binding and hanger holes are not turned.
    turned: bool = False


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
    """Hanger hole centre in trim mm: the top edge of the photo, never mirrored."""
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
    # Check each side the hanger appears on: front pages have the holes where they are drawn,
    # and photo backs have them mirrored to the bottom edge.
    sides: list[bool] = []
    if any(kind != "photo_back" for kind in hanger.pages):
        sides.append(False)
    if "photo_back" in hanger.pages:
        sides.append(True)
    for at_bottom in sides:
        for x, y in hole_centres(config, geom):
            hy = geom.trim_h - y if at_bottom else y
            if hole.overlaps(Rect(x - hr, hy - hr, 2 * hr, 2 * hr)):
                raise LayoutError(
                    "hanger hole overlaps a binding hole; move hanger.x_mm or hanger.offset_mm"
                )


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
    """The bird's details under the grid, or beside it.

    Below the grid the details are in a left column (bird name, scientific name, location,
    credit) and the description fills the right column. Beside the grid they stack in one column,
    with the description below them. Descriptions shrink to fit, down to the font's minimum.
    """
    cfg = ctx.config
    sp = cfg.layout.spacing
    name = bird.common_name or "Bird name to come"
    desc = bird.description or ("Description to come" if bird.is_empty else "")
    sci = bird.scientific_name or ""
    location = bird.location or ""
    credit_bits = []
    if bird.date_taken is not None:
        credit_bits.append(f"taken {bird.date_taken.strftime('%d/%m/%Y')}")
    if bird.photo_credit:
        credit_bits.append(f"Photo: {bird.photo_credit}")
    credit = " · ".join(credit_bits)

    below = cfg.layout.caption.placement == "below_grid"
    if below:
        split = rect.x + rect.w * sp.caption_split_fraction
        column = Rect(rect.x, rect.y, split - rect.x - sp.gap_mm / 2, rect.h)
        desc_rect = Rect(split + sp.gap_mm / 2, rect.y, rect.right - split - sp.gap_mm / 2, rect.h)
    else:
        column = rect
        desc_rect = Rect(rect.x, rect.y, rect.w, rect.h)  # narrowed below, after the others

    ops: list[Op] = []
    y = column.y

    def place(text: str, role_key: str, max_h: float, label: str) -> float:
        """Add one left-column block at ``y`` and return its height (0 when empty)."""
        op = _text_op(
            Rect(column.x, y, column.w, max_h),
            text,
            getattr(cfg.fonts, role_key),
            ctx.registry,
            role_key,
            where=f"{where} {label}",
            align="left",
            max_height=max_h,
        )
        if op is None:
            return 0.0
        ops.append(op)
        return _block_height(op)

    # The name keeps the top-left of the caption. The scientific name sits close under it.
    h = place(name, "bird_name", column.h * 0.6 if below else column.h * 0.35, "bird name")
    y += h + (sp.gap_mm / 4 if below else sp.gap_mm)
    if sci:
        h = place(sci, "scientific_name", column.bottom - y, "scientific name")
        y += h + sp.gap_mm / 2
    if location:
        h = place(location, "location", column.bottom - y, "location")
        y += h + sp.gap_mm / 2
    if credit:
        h = place(credit, "credit", max(0.0, column.bottom - y), "credit")
        y += h

    if below:
        desc_op = _text_op(
            desc_rect,
            desc,
            cfg.fonts.description,
            ctx.registry,
            "description",
            where=f"{where} description",
            align="left",
            max_height=desc_rect.h,
        )
        if desc_op is not None:
            # Centred vertically in the right column; the text stays left-aligned.
            top = desc_rect.y + (desc_rect.h - _block_height(desc_op)) / 2
            desc_op = replace(
                desc_op, rect=Rect(desc_rect.x, top, desc_rect.w, _block_height(desc_op))
            )
    else:
        # Stacked beside the grid: the description takes the rest of the column.
        desc_h = max(0.0, column.bottom - y)
        desc_op = _text_op(
            Rect(column.x, y, column.w, desc_h),
            desc,
            cfg.fonts.description,
            ctx.registry,
            "description",
            where=f"{where} description",
            align="left",
            max_height=desc_h,
        )
    if desc_op:
        ops.append(desc_op)
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
                align="left",
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
            badge = grid_cfg.holiday_badge
            bp = badge.padding_mm
            label_w = cell.w - 2 * pad
            label_top = number_rect.bottom + 0.5
            bottom_limit = cell.bottom - pad
            label_h = bottom_limit - label_top
            hol_text = "\n".join(
                f"{cfg.labels.holiday.prefix}{t}{cfg.labels.holiday.suffix}"
                for t in day_labels.holidays
            )
            hol_op = _text_op(
                Rect(cell.x + pad + bp, label_top + bp, label_w - 2 * bp, label_h - 2 * bp),
                hol_text,
                cfg.fonts.holiday_label,
                reg,
                "holiday_label",
                where=f"{where} {day.date.isoformat()} holiday",
                align="right",
            )
            used = 0.0 if hol_op is None else _block_height(hol_op) + 2 * bp + 0.5
            ev_text = "\n".join(
                f"{cfg.labels.event.prefix}{t}{cfg.labels.event.suffix}" for t in day_labels.events
            )
            ev_op = _text_op(
                Rect(cell.x + pad, label_top, label_w, max(0.0, label_h - used)),
                ev_text,
                cfg.fonts.event_label,
                reg,
                "event_label",
                where=f"{where} {day.date.isoformat()} event",
                align="right",
            )
            # Stack from the bottom right: events at the bottom, the holiday badge above them.
            stack_y = bottom_limit
            if ev_op is not None:
                ev_h = _block_height(ev_op)
                ev_op = replace(ev_op, rect=Rect(cell.x + pad, stack_y - ev_h, label_w, ev_h))
                stack_y -= ev_h + 0.5
            if hol_op is not None:
                hol_h = _block_height(hol_op)
                hol_w = max(
                    string_width_mm(line, hol_op.font, hol_op.size_pt) for line in hol_op.lines
                )
                badge_rect = Rect(
                    cell.right - pad - hol_w - 2 * bp,
                    stack_y - hol_h - 2 * bp,
                    hol_w + 2 * bp,
                    hol_h + 2 * bp,
                )
                ops.append(RoundedFillRect(badge_rect, badge.colour, badge.radius_mm))
                ops.append(
                    replace(
                        hol_op,
                        rect=Rect(
                            cell.right - pad - bp - (label_w - 2 * bp),
                            badge_rect.y + bp,
                            label_w - 2 * bp,
                            hol_h,
                        ),
                    )
                )
            if ev_op is not None:
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
    *,
    turned: bool = False,
) -> PagePlan:
    """A full-page photo with nothing else on it."""
    rect = photo_region(ctx.geom)
    ops: list[Op] = [_photo_op(ctx, rect, bird)]
    # Holes are punched through the photo, so no binding band is drawn over it.
    guides: list[Op] = [
        StrokeRect(ctx.geom.bleed_rect, "#1F6FD6", 0.25, (3.0, 2.0)),
        StrokeRect(ctx.geom.trim_rect, "#E0301E", 0.25),
        *_hole_guides(ctx, at_bottom=kind == "next_photo"),
    ]
    return PagePlan(
        kind=kind,
        month=month,
        ops=tuple(ops),
        guides=tuple(guides),
        content=rect,
        binding_at_bottom=kind == "next_photo",
        turned=turned,
    )


def next_month_page(
    ctx: LayoutContext, bird: BirdView, next_month: int, *, turned: bool = False
) -> PagePlan:
    """The back of a month's grid sheet: the next month's photo, full page.

    ``turned`` prints the whole sheet upside down: used for the back of the calendar, which is
    read while the calendar is unopened.
    """
    return photo_page(ctx, bird, "next_photo", next_month, turned=turned)


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


@dataclass(frozen=True)
class BackContent:
    """The back page's words: (name, bio) for each photographer, and the donation appeal."""

    photographers: tuple[tuple[str | None, str | None], ...]
    appeal: str | None


BACK_TEXT_HOLE_GAP_MM = 3.0  # clear space between the binding holes and the back text panel


def _reflow(text: str | None) -> str | None:
    """Collapse all whitespace, including line breaks, to single spaces. None when empty."""
    if text is None:
        return None
    return " ".join(text.split()) or None


def back_text_ops(ctx: LayoutContext, content: BackContent, binding_at_bottom: bool) -> list[Op]:
    """Photographer bios and the appeal on a solid panel in one corner of the back page.

    The panel stays inside the safe margin, clear of the binding band and the hanger hole.
    Text that cannot fit at its minimum size raises LayoutError naming the field.
    """
    cfg = ctx.config
    bt = cfg.back_text
    reg = ctx.registry
    trim = ctx.geom.trim_rect
    keep = cfg.binding.keep_clear_mm
    edge = max(bt.edge_mm, cfg.safe_margin_mm)
    pad = bt.padding_mm
    top, bottom = trim.y + edge, trim.bottom - edge
    # The text stays inside the padding, and above the binding holes when they are at the bottom.
    text_bottom = bottom - pad
    if binding_at_bottom:
        holes = cfg.binding.holes
        if holes is None:
            text_bottom = min(text_bottom, trim.bottom - keep)
        else:
            clear = holes.edge_offset_mm + holes.diameter_mm / 2 + BACK_TEXT_HOLE_GAP_MM
            text_bottom = min(text_bottom, trim.bottom - clear)
    else:
        top = max(top, trim.y + keep)
    left, right = trim.x + edge, trim.right - edge
    width = min(bt.width_mm, right - left)
    x0 = left if bt.position.endswith("left") else right - width
    inner_w, inner_h = width - 2 * pad, (text_bottom - (top + pad))
    if inner_w <= 0 or inner_h <= 0:
        raise LayoutError("back text has no room; check back_text and binding.keep_clear_mm")

    # Line breaks in the source text (from YAML block scalars) are reflowed: the text wraps to
    # the panel width, not to the line lengths it was typed at.
    items: list[tuple[str, str]] = [("back_heading", "About the photographers")]
    for i, (name, bio) in enumerate(content.photographers, start=1):
        items.append(("back_name", _reflow(name) or f"Photographer {i} name to come"))
        items.append(("back_body", _reflow(bio) or "Bio to come"))
    items.append(("back_appeal", _reflow(content.appeal) or "Donation message to come"))

    gap = bt.gap_mm
    texts: list[TextOp] = []
    total = 0.0
    for role_key, text in items:
        op = _text_op(
            Rect(x0 + pad, 0, inner_w, inner_h),
            text,
            getattr(cfg.fonts, role_key),
            reg,
            role_key,
            where=f"back text ({role_key})",
            align="left",
            max_height=inner_h,
        )
        if op is None:  # pragma: no cover - placeholders are never empty
            continue
        texts.append(op)
        total += _block_height(op) + gap
    total -= gap
    if total > inner_h + 1e-9:
        raise LayoutError(
            f"back text needs {total:.0f} mm but {inner_h:.0f} mm is available; "
            "shorten the bios or widen back_text.width_mm"
        )

    if bt.position.startswith("top"):
        text_start = top + pad
        panel = Rect(x0, top, width, text_start + total + pad - top)
    else:
        text_start = text_bottom - total
        panel = Rect(x0, text_start - pad, width, bottom - (text_start - pad))
    if cfg.hanger.enabled:
        cx, cy = hanger_centre(cfg)
        margin = cfg.hanger.diameter_mm / 2 + 3
        if (
            panel.x < cx + margin
            and panel.right > cx - margin
            and panel.y < cy + margin
            and panel.bottom > cy - margin
        ):
            raise LayoutError(
                "back text panel covers the hanger hole; change back_text.position or width_mm"
            )

    ops: list[Op] = [FillRect(panel, bt.panel_colour, bt.panel_opacity)]
    cursor = text_start
    for op in texts:
        height = _block_height(op)
        ops.append(replace(op, rect=Rect(x0 + pad, cursor, inner_w, height)))
        cursor += height + gap
    return ops


def _month_guides(ctx: LayoutContext, content: Rect) -> list[Op]:
    return _common_guides(ctx) + [
        StrokeRect(content, "#7A3DB8", 0.25, (2.0, 2.0)),
    ]


def _common_guides(ctx: LayoutContext) -> list[Op]:
    cfg = ctx.config
    geom = ctx.geom
    guides: list[Op] = []
    safe = geom.trim_rect.inset(cfg.safe_margin_mm)
    guides.append(StrokeRect(geom.bleed_rect, "#1F6FD6", 0.25, (3.0, 2.0)))
    guides.append(StrokeRect(safe, "#2E9E4F", 0.25, (3.0, 2.0)))
    guides.append(StrokeRect(geom.trim_rect, "#E0301E", 0.25))
    guides += _hole_guides(ctx)
    return guides


def _hole_guides(ctx: LayoutContext, at_bottom: bool = False) -> list[Op]:
    holes = ctx.config.binding.holes
    if holes is None:
        return []
    trim_h = ctx.geom.trim_h
    return [
        Circle(x, trim_h - y if at_bottom else y, holes.diameter_mm / 2, "#E0301E", 0.4)
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
