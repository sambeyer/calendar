"""Build orchestration: config + data -> plans -> PDF.

Modes:
    draft  guides on, placeholder images allowed
    proof  guides on, placeholder images allowed with a warning
    print  guides off, placeholder images REFUSED

Stage 2 has no real-photo pipeline yet, so every photo is a placeholder. Print builds are
therefore refused until stage 3 supplies real, verified photos.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from birdcal.config import Config, ConfigError, LoadedConfig, load_config
from birdcal.data import YearData, load_year_data, validate_year_data
from birdcal.dates import year_grids
from birdcal.geometry import SheetGeometry
from birdcal.layout import (
    BirdView,
    DayLabels,
    LayoutContext,
    LayoutError,
    PagePlan,
    back_page,
    binding_and_hanger_ops,
    cover_page,
    month_page,
    trim_mark_ops,
)
from birdcal.render import PdfRenderer
from birdcal.text import register_fonts

Mode = Literal["draft", "proof", "print"]
GUIDED_MODES: frozenset[str] = frozenset({"draft", "proof"})


class BuildError(Exception):
    """The build cannot proceed. The message says why and where."""


@dataclass
class BuildResult:
    path: Path
    mode: Mode
    pages: int
    warnings: list[str] = field(default_factory=list)


def _bird_view(config: Config, data: YearData, month: int) -> BirdView:
    bird = next(b for b in data.birds.birds if b.month == month)
    photo = bird.photo
    px_w = config.draft.placeholder_px_width
    px_h = config.draft.placeholder_px_height
    return BirdView(
        month=month,
        common_name=bird.common_name,
        scientific_name=bird.scientific_name,
        description=bird.description,
        location=bird.location,
        date_taken=bird.date_taken,
        photo_credit=bird.photo_credit,
        photo_file=photo.file if photo else None,
        fit=photo.fit if photo else config.photo.fit,
        focal=(photo.focal_point if photo and photo.focal_point else (0.5, 0.5)),
        border_mm=photo.border_mm if photo else None,
        corner_radius_mm=photo.corner_radius_mm if photo else None,
        px_w=px_w,
        px_h=px_h,
        # Stage 2: no real photo pipeline yet, so every photo is a placeholder.
        placeholder=True,
    )


def _day_labels(data: YearData) -> dict[dt.date, DayLabels]:
    holidays: dict[dt.date, list[str]] = {}
    for h in data.holidays.holidays:
        holidays.setdefault(h.date, []).append(h.name)
    events: dict[dt.date, list[str]] = {}
    for ev in data.events.events:
        events.setdefault(ev.resolve(data.year), []).append(ev.label)
    out: dict[dt.date, DayLabels] = {}
    for day in set(holidays) | set(events):
        out[day] = DayLabels(
            holidays=tuple(holidays.get(day, ())),
            events=tuple(events.get(day, ())),
        )
    return out


def _back_entries(data: YearData) -> list[tuple[dt.date, str, str]]:
    entries = [(h.date, h.name, "holiday") for h in data.holidays.holidays]
    entries += [(ev.resolve(data.year), ev.label, "event") for ev in data.events.events]
    return sorted(entries, key=lambda e: (e[0], e[1]))


def _check_inputs(loaded: LoadedConfig, data: YearData, root: Path, mode: Mode) -> list[str]:
    """Fail on errors; return warnings. Raises BuildError."""
    issues = validate_year_data(data)
    errors = [i for i in issues if i.level == "error"]
    if errors:
        raise BuildError("data has errors:\n  " + "\n  ".join(str(e) for e in errors))
    warnings = [f"{i.where}: {i.message}" for i in issues if i.level == "warning"]

    for role, font in loaded.config.fonts.model_dump().items():
        if not (root / font["file"]).is_file():
            raise BuildError(f"fonts.{role}: font file not found: {font['file']}")

    placeholders = [b.month for b in data.birds.birds if b.common_name is None or b.photo is None]
    if placeholders:
        months = ", ".join(f"{m:02d}" for m in placeholders)
        if mode == "print":
            raise BuildError(
                f"print refused: months {months} have no bird or photo yet, and placeholder "
                "images are never allowed in print output"
            )
        warnings.append(f"placeholder images used for months {months}")
    if mode == "proof":
        warnings.append("proof uses placeholder images until the real-photo pipeline (stage 3)")
    if mode == "print":
        raise BuildError(
            "print refused: real photo embedding is not implemented yet (stage 3); "
            "every photo is currently a placeholder"
        )
    return warnings


def build_calendar(root: Path, year: int, mode: Mode, out_dir: Path | None = None) -> BuildResult:
    """Build the calendar PDF for ``year`` in ``mode``. Returns the output path and warnings."""
    try:
        loaded = load_config(root, year)
    except (ConfigError, NotImplementedError) as exc:
        raise BuildError(str(exc)) from exc
    cfg = loaded.config
    calendar_dir = root / "calendars" / str(year)
    try:
        data = load_year_data(calendar_dir, year)
    except ConfigError as exc:
        raise BuildError(str(exc)) from exc

    warnings = _check_inputs(loaded, data, root, mode)
    ctx, plans = plan_pages(root, cfg, data)

    geom = ctx.geom
    guides = mode in GUIDED_MODES

    out_root = out_dir or (root / "build" / str(year))
    out_root.mkdir(parents=True, exist_ok=True)
    path = out_root / f"calendar-{year}-{mode}.pdf"
    renderer = PdfRenderer(
        path, geom, ctx.registry["credit"], title=f"{year} bird calendar ({mode})"
    )
    marks = trim_mark_ops(cfg, geom)
    for plan in plans:
        renderer.add_page(plan, marks, guides=guides)
    renderer.save()
    return BuildResult(path=path, mode=mode, pages=len(plans), warnings=warnings)


def geometry_for(cfg: Config) -> SheetGeometry:
    return SheetGeometry(
        trim_w=cfg.page.width_mm,
        trim_h=cfg.page.height_mm,
        bleed=cfg.bleed_mm,
        marks_space=(cfg.trim_marks.offset_mm + cfg.trim_marks.length_mm)
        if cfg.trim_marks.enabled
        else 0.0,
    )


def plan_pages(root: Path, cfg: Config, data: YearData) -> tuple[LayoutContext, list[PagePlan]]:
    """Lay out every page (cover, twelve months, optional back). Shared by build and tests."""
    year = data.year
    registry = register_fonts(cfg.fonts, root)
    ctx = LayoutContext(config=cfg, geom=geometry_for(cfg), registry=registry, year=year)
    plans: list[PagePlan] = []
    try:
        if cfg.pages.cover:
            cover = cover_page(ctx, _bird_view(cfg, data, cfg.pages.cover_month))
            plans.append(_with_features(ctx, cover, "cover"))
        labels = _day_labels(data)
        for grid in year_grids(year, cfg.grid.week_start, cfg.grid.rows):
            plan = month_page(ctx, grid, _bird_view(cfg, data, grid.month), labels)
            plans.append(_with_features(ctx, plan, "months"))
        if cfg.pages.back:
            plans.append(_with_features(ctx, back_page(ctx, _back_entries(data)), "back"))
    except LayoutError as exc:
        raise BuildError(f"layout failed: {exc}") from exc
    return ctx, plans


def _with_features(ctx: LayoutContext, plan: PagePlan, kind: str) -> PagePlan:
    """Attach hanger and binding guides (drawn only in guided modes) to a page."""
    extra = binding_and_hanger_ops(ctx, kind)
    return PagePlan(
        kind=plan.kind,
        month=plan.month,
        ops=plan.ops,
        guides=plan.guides + tuple(extra),
        content=plan.content,
    )
