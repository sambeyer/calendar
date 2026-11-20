"""Build orchestration: config + data + photos -> plans -> PDF.

Modes:
    draft  guides on; a missing photo becomes a labelled placeholder
    proof  guides on; a missing photo becomes a placeholder, with a warning
    print  guides off; every month needs a verified photo, or the build is refused

Every build checks the photos on disk against ``photos.lock``. A changed or unlocked file
is always an error, unless ``ignore_lock`` is set. After rendering, the PDF's embedded JPEG
streams are compared with the hashes of the photos actually embedded; any difference fails the
build and removes the output.

Two options produce files that must not be printed. Their names say so, so they cannot be
mistaken for a print file:
    --ignore-lock   photos are used even though they do not match photos.lock
    --low-quality   photos are resampled and re-encoded at a lower resolution
"""

from __future__ import annotations

import datetime as dt
import tempfile
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from birdcal.config import Config, ConfigError, LoadedConfig, load_config
from birdcal.data import PhotoSpec, YearData, load_year_data, photo_files, validate_year_data
from birdcal.dates import year_grids
from birdcal.geometry import SheetGeometry, effective_ppi, placement_for
from birdcal.layout import (
    BackContent,
    BirdView,
    DayLabels,
    ImageOp,
    LayoutContext,
    LayoutError,
    PagePlan,
    back_page,
    back_text_ops,
    binding_and_hanger_ops,
    blank_page,
    check_hanger,
    cover_page,
    month_page,
    next_month_page,
    trim_mark_ops,
)
from birdcal.photos import (
    LOCK_FILENAME,
    LockRecord,
    PhotoError,
    PhotoStatus,
    check_against_lock,
    embedded_jpeg_hashes,
    read_lock,
    sha256_file,
    write_resampled_jpeg,
)
from birdcal.render import PdfRenderer
from birdcal.text import register_fonts

Mode = Literal["draft", "proof", "print"]
GUIDED_MODES: frozenset[str] = frozenset({"draft", "proof"})


class BuildError(Exception):
    """The build cannot proceed. The message says why and where."""


@dataclass(frozen=True)
class PhotoFinding:
    """Per-photo facts for check-photos and build warnings."""

    page: str  # "cover" or "month"
    month: int
    file: str
    px_w: int
    px_h: int
    box_w_mm: float
    box_h_mm: float
    ppi: float
    crop_fraction: float
    colour_profile: str | None
    warnings: tuple[str, ...]


@dataclass
class BuildResult:
    path: Path
    mode: Mode
    pages: int
    warnings: list[str] = field(default_factory=list)
    findings: list[PhotoFinding] = field(default_factory=list)
    photo_hashes: list[str] = field(default_factory=list)
    ignore_lock: bool = False
    low_quality: bool = False

    @property
    def not_for_printing(self) -> bool:
        return self.ignore_lock or self.low_quality


def output_name(
    year: int, mode: Mode, *, ignore_lock: bool = False, low_quality: bool = False
) -> str:
    """File name for a build. Each option that makes the file unsuitable for printing adds a
    suffix, so the flags are visible wherever the file goes."""
    suffix = ("-ignore-lock" if ignore_lock else "") + ("-low-quality" if low_quality else "")
    return f"calendar-{year}-{mode}{suffix}.pdf"


def _title(year: int, mode: Mode, ignore_lock: bool, low_quality: bool) -> str:
    flags = [
        name for name, on in (("ignore-lock", ignore_lock), ("low-quality", low_quality)) if on
    ]
    extra = f", NOT FOR PRINTING: {', '.join(flags)}" if flags else ""
    return f"{year} bird calendar ({mode}{extra})"


@dataclass(frozen=True)
class Inputs:
    loaded: LoadedConfig
    data: YearData
    statuses: Mapping[str, PhotoStatus]
    locks: Mapping[str, LockRecord]
    warnings: tuple[str, ...]

    @property
    def config(self) -> Config:
        return self.loaded.config


def _bird_view(
    cfg: Config,
    data: YearData,
    month: int,
    statuses: Mapping[str, PhotoStatus],
    photo_override: str | None = None,
) -> BirdView:
    """The month's view; ``photo_override`` replaces the bird's own photo (cover, final back)."""
    bird = next(b for b in data.birds.birds if b.month == month)
    photo = PhotoSpec(file=photo_override) if photo_override else bird.photo
    status = statuses.get(photo.file) if photo else None
    use_real = status is not None and status.usable and status.record is not None
    if status is not None and use_real and status.record is not None and status.path is not None:
        px_w, px_h = status.record.width, status.record.height
        source: str | None = str(status.path)
    else:
        px_w, px_h = cfg.draft.placeholder_px_width, cfg.draft.placeholder_px_height
        source = None
    return BirdView(
        month=month,
        common_name=bird.common_name,
        scientific_name=bird.scientific_name,
        description=bird.description,
        location=bird.location,
        date_taken=bird.date_taken,
        photo_credit=bird.photo_credit,
        photo_file=photo.file if photo else None,
        fit=photo.fit if photo else cfg.photo.fit,
        focal=(photo.focal_point if photo and photo.focal_point else (0.5, 0.5)),
        border_mm=photo.border_mm if photo else None,
        corner_radius_mm=photo.corner_radius_mm if photo else None,
        px_w=px_w,
        px_h=px_h,
        placeholder=not use_real,
        source_path=source,
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


def geometry_for(cfg: Config) -> SheetGeometry:
    return SheetGeometry(
        trim_w=cfg.page.width_mm,
        trim_h=cfg.page.height_mm,
        bleed=cfg.bleed_mm,
        marks_space=(cfg.trim_marks.offset_mm + cfg.trim_marks.length_mm)
        if cfg.trim_marks.enabled
        else 0.0,
    )


def plan_pages(
    root: Path,
    cfg: Config,
    data: YearData,
    statuses: Mapping[str, PhotoStatus] | None = None,
) -> tuple[LayoutContext, list[PagePlan]]:
    """Lay out every page (cover, twelve months, optional back). Shared by build and tests.

    Without ``statuses`` every photo is a placeholder.
    """
    year = data.year
    statuses = statuses or {}
    registry = register_fonts(cfg.fonts, root)
    ctx = LayoutContext(config=cfg, geom=geometry_for(cfg), registry=registry, year=year)
    duplex = cfg.sides.mode == "duplex"
    back_photos = duplex and cfg.sides.back_content == "next_month_photo"
    plans: list[PagePlan] = []
    try:
        check_hanger(cfg, ctx.geom)
        if cfg.pages.cover:
            cover = cover_page(
                ctx,
                _bird_view(
                    cfg, data, cfg.pages.cover_month, statuses, photo_override=cfg.pages.cover_photo
                ),
            )
            plans.append(cover)
            if duplex:
                if back_photos:
                    # The cover's back is January's photo: the first page after the cover.
                    plans.append(next_month_page(ctx, _bird_view(cfg, data, 1, statuses), 1))
                else:
                    plans.append(blank_page(ctx))
        labels = _day_labels(data)
        for grid in year_grids(year, cfg.grid.week_start, cfg.grid.rows):
            plans.append(month_page(ctx, grid, _bird_view(cfg, data, grid.month, statuses), labels))
            if duplex:
                if back_photos:
                    next_month = grid.month % 12 + 1
                    # The last sheet's back is not January's photo again unless the year sets it.
                    override = cfg.sides.final_back_photo if grid.month == 12 else None
                    back = next_month_page(
                        ctx,
                        _bird_view(cfg, data, next_month, statuses, photo_override=override),
                        next_month,
                        turned=grid.month == 12,
                    )
                else:
                    back = blank_page(ctx)
                if grid.month == 12 and cfg.back_text.enabled:
                    # The back of the calendar: bios and the appeal go on the last sheet's back.
                    text_ops = back_text_ops(ctx, _back_content(data), back.binding_at_bottom)
                    back = replace(back, ops=back.ops + tuple(text_ops))
                plans.append(back)
        if cfg.pages.back:
            plans.append(back_page(ctx, _back_entries(data)))
            if duplex:
                plans.append(blank_page(ctx))
    except LayoutError as exc:
        raise BuildError(f"layout failed: {exc}") from exc
    return ctx, [_with_features(ctx, p) for p in plans]


# Which hanger.pages entry each plan kind counts as. A back sheet belongs to its month.
_HANGER_KIND: dict[str, str] = {
    "cover": "cover",
    "month": "month",
    "next_photo": "photo_back",
    "back": "list",
    "blank": "blank",
}


def _with_features(ctx: LayoutContext, plan: PagePlan) -> PagePlan:
    """Attach hanger and binding guides (drawn only in guided modes) to a page."""
    extra = binding_and_hanger_ops(ctx, _HANGER_KIND[plan.kind])
    return replace(plan, guides=plan.guides + tuple(extra))


def photo_findings(
    plans: list[PagePlan],
    cfg: Config,
    locks: Mapping[str, LockRecord],
) -> list[PhotoFinding]:
    """PPI, crop and colour facts for every real photo placed on a page."""
    findings: list[PhotoFinding] = []
    for plan in plans:
        for op in plan.ops:
            if not isinstance(op, ImageOp) or op.placeholder or op.file is None:
                continue
            month = plan.month if plan.month is not None else cfg.pages.cover_month
            ppi = effective_ppi(op.px_w, op.px_h, op.rect, op.fit)
            placement = placement_for(op.fit, op.px_w, op.px_h, op.rect, op.focal)
            crop_x = 1 - op.rect.w / placement.draw.w if placement.draw.w else 0.0
            crop_y = 1 - op.rect.h / placement.draw.h if placement.draw.h else 0.0
            crop = max(crop_x, crop_y, 0.0)
            record = locks.get(op.file)
            profile = record.colour_profile if record else None
            warns: list[str] = []
            if ppi < cfg.photo.ppi.warn_below:
                warns.append(f"effective {ppi:.0f} ppi is below {cfg.photo.ppi.warn_below:.0f} ppi")
            if crop > cfg.photo.max_crop_fraction:
                warns.append(
                    f"crop removes {crop:.0%} of an axis (limit {cfg.photo.max_crop_fraction:.0%}); "
                    "check the focal point keeps the bird in frame"
                )
            if profile != cfg.colour.profile:
                shown = profile or "untagged"
                warns.append(f"colour profile is {shown!r}, expected {cfg.colour.profile!r}")
            findings.append(
                PhotoFinding(
                    page=plan.kind,
                    month=month,
                    file=op.file,
                    px_w=op.px_w,
                    px_h=op.px_h,
                    box_w_mm=op.rect.w,
                    box_h_mm=op.rect.h,
                    ppi=ppi,
                    crop_fraction=crop,
                    colour_profile=profile,
                    warnings=tuple(warns),
                )
            )
    return sorted(findings, key=lambda f: f.month)


def _back_content(data: YearData) -> BackContent:
    """Always two photographer slots, so missing ones show a placeholder in draft and proof."""
    people = [(p.name, p.bio) for p in data.back.photographers]
    people += [(None, None)] * max(0, 2 - len(people))
    return BackContent(photographers=tuple(people), appeal=data.back.appeal)


def _unfinished_back_text(data: YearData) -> list[str]:
    """What the back page still needs before a print file can be made."""
    missing: list[str] = []
    if len(data.back.photographers) != 2:
        missing.append(f"{len(data.back.photographers)} of 2 photographers listed")
    for i, person in enumerate(data.back.photographers, start=1):
        if not person.name:
            missing.append(f"photographer {i} name")
        if not person.bio:
            missing.append(f"photographer {i} bio")
    if not data.back.appeal:
        missing.append("donation appeal")
    return missing


def _bird_usable(bird: object, statuses: Mapping[str, PhotoStatus]) -> bool:
    """A month is usable when it has a bird name and a verified photo."""
    from birdcal.data import BirdEntry

    assert isinstance(bird, BirdEntry)
    if bird.photo is None or bird.common_name is None:
        return False
    status = statuses.get(bird.photo.file)
    return status is not None and status.usable


def prepare(
    root: Path, year: int, mode: Mode, *, ignore_lock: bool = False
) -> tuple[Inputs, dict[str, PhotoStatus]]:
    """Load everything, verify photos against the lock, and apply the per-mode rules."""
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

    issues = validate_year_data(data)
    errors = [i for i in issues if i.level == "error"]
    if errors:
        raise BuildError("data has errors:\n  " + "\n  ".join(str(e) for e in errors))
    warnings = [f"{i.where}: {i.message}" for i in issues if i.level == "warning"]

    for role, font in cfg.fonts.model_dump().items():
        if not (root / font["file"]).is_file():
            raise BuildError(f"fonts.{role}: font file not found: {font['file']}")

    photos_dir = Path(cfg.paths.photos_dir).expanduser() if cfg.paths.photos_dir else None
    locks = read_lock(calendar_dir / LOCK_FILENAME)
    referenced = photo_files(data.birds, cfg.pages.cover_photo, cfg.sides.final_back_photo)
    try:
        statuses = check_against_lock(photos_dir, referenced, locks, ignore_lock=ignore_lock)
    except PhotoError as exc:
        raise BuildError(f"photo check failed: {exc}") from exc
    for file_name, photo in sorted(statuses.items()):
        if ignore_lock and photo.lock_note:
            warnings.append(
                f"photo {file_name}: {photo.lock_note}; used because --ignore-lock is set"
            )

    if mode == "print" and cfg.back_text.enabled and cfg.sides.mode == "duplex":
        unfinished = _unfinished_back_text(data)
        if unfinished:
            raise BuildError("print refused: back page text not finished: " + ", ".join(unfinished))

    extra_photos = {
        "cover photo": cfg.pages.cover_photo if cfg.pages.cover else None,
        "final back photo": cfg.sides.final_back_photo,
    }
    for label, name in extra_photos.items():
        if mode != "print" or not name:
            continue
        status = statuses.get(name)
        if status is None or not status.usable:
            reason = status.problem if status and status.problem else "missing"
            raise BuildError(f"print refused: {label} {name!r} is not usable ({reason})")

    unusable = sorted(b.month for b in data.birds.birds if not _bird_usable(b, statuses))
    if unusable:
        months = ", ".join(f"{m:02d}" for m in unusable)
        reasons = sorted({s.problem for s in statuses.values() if s.problem})
        detail = f" ({'; '.join(reasons)})" if reasons else ""
        if mode == "print":
            raise BuildError(
                f"print refused: months {months} have no verified photo or bird name{detail}"
            )
        warnings.append(f"placeholder images used for months {months}{detail}")

    inputs = Inputs(
        loaded=loaded,
        data=data,
        statuses=statuses,
        locks=locks,
        warnings=tuple(warnings),
    )
    return inputs, statuses


def _low_quality_statuses(
    root: Path,
    cfg: Config,
    data: YearData,
    statuses: Mapping[str, PhotoStatus],
    scratch: Path,
) -> dict[str, PhotoStatus]:
    """Point each placed photo at a resampled copy in ``scratch``.

    Each photo is scaled so its effective PPI is at most ``photo.low_quality.ppi`` in every
    place it is shown. The copy's own size and hash replace the lock's, so the lossless check
    compares the PDF with what was actually embedded.
    """
    _, plans = plan_pages(root, cfg, data, statuses)
    scale: dict[str, float] = {}
    for plan in plans:
        for op in plan.ops:
            if not isinstance(op, ImageOp) or op.placeholder or op.file is None:
                continue
            ppi = effective_ppi(op.px_w, op.px_h, op.rect, op.fit)
            needed = min(1.0, cfg.photo.low_quality.ppi / ppi)
            scale[op.file] = max(scale.get(op.file, 0.0), needed)
    result = dict(statuses)
    for name, factor in sorted(scale.items()):
        status = statuses[name]
        if status.path is None or status.record is None:  # pragma: no cover - usable only
            continue
        dst = scratch / name
        width, height = write_resampled_jpeg(
            status.path, dst, scale=factor, quality=cfg.photo.low_quality.jpeg_quality
        )
        copy = replace(status.record, width=width, height=height, sha256=sha256_file(dst))
        result[name] = replace(status, record=copy, path=dst)
    return result


def build_calendar(
    root: Path,
    year: int,
    mode: Mode,
    out_dir: Path | None = None,
    *,
    ignore_lock: bool = False,
    low_quality: bool = False,
) -> BuildResult:
    """Build the calendar PDF for ``year`` in ``mode``.

    ``ignore_lock`` uses photos that do not match photos.lock. ``low_quality`` embeds resampled
    copies to make a smaller PDF. Either makes the file unsuitable for printing; the file name
    and title say so.
    """
    inputs, statuses = prepare(root, year, mode, ignore_lock=ignore_lock)
    cfg = inputs.config
    out_root = out_dir or (root / "build" / str(year))
    out_root.mkdir(parents=True, exist_ok=True)
    path = out_root / output_name(year, mode, ignore_lock=ignore_lock, low_quality=low_quality)
    with tempfile.TemporaryDirectory(prefix="birdcal-") as scratch:
        if low_quality:
            statuses = _low_quality_statuses(root, cfg, inputs.data, statuses, Path(scratch))
        ctx, plans = plan_pages(root, cfg, inputs.data, statuses)
        records = {name: s.record for name, s in statuses.items() if s.record is not None}
        findings = photo_findings(plans, cfg, records)
        warnings = list(inputs.warnings)
        if low_quality:
            warnings.append(
                f"NOT FOR PRINTING: --low-quality photos resampled to "
                f"{cfg.photo.low_quality.ppi:.0f} ppi, JPEG quality {cfg.photo.low_quality.jpeg_quality}"
            )
        if ignore_lock:
            warnings.append(
                "NOT FOR PRINTING: --ignore-lock used photos that differ from photos.lock"
            )
        for f in findings:
            warnings += [f"month {f.month:02d} {f.file}: {w}" for w in f.warnings]

        used_hashes = [
            records[op.file].sha256
            for plan in plans
            for op in plan.ops
            if isinstance(op, ImageOp)
            and not op.placeholder
            and op.file is not None
            and op.file in records
        ]
        guides = mode in GUIDED_MODES
        renderer = PdfRenderer(
            path,
            ctx.geom,
            ctx.registry["credit"],
            title=_title(year, mode, ignore_lock, low_quality),
        )
        marks = trim_mark_ops(cfg, ctx.geom)
        for plan in plans:
            renderer.add_page(plan, marks, guides=guides)
        renderer.save()

        try:
            embedded = embedded_jpeg_hashes(path)
        except PhotoError as exc:
            path.unlink(missing_ok=True)
            raise BuildError(f"lossless check failed: {exc}") from exc
        if embedded != Counter(used_hashes):
            path.unlink(missing_ok=True)
            raise BuildError(
                "lossless check failed: the PDF's embedded JPEG streams do not match the source "
                "photos byte for byte; the output was removed"
            )
    return BuildResult(
        path=path,
        mode=mode,
        pages=len(plans),
        warnings=warnings,
        findings=findings,
        photo_hashes=sorted(used_hashes),
        ignore_lock=ignore_lock,
        low_quality=low_quality,
    )
