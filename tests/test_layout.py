from __future__ import annotations

import itertools
from pathlib import Path

import pytest
from conftest import MakeRepo

from birdcal.build import BuildError, build_calendar, plan_pages
from birdcal.config import load_config
from birdcal.data import load_year_data
from birdcal.geometry import Rect
from birdcal.layout import (
    FillRect,
    ImageOp,
    LayoutContext,
    Line,
    Op,
    PagePlan,
    TextOp,
    hole_centres,
    keep_clear_rect,
    trim_mark_ops,
)
from birdcal.text import LEADING, string_width_mm


def _plans(root: Path, year: int = 2027) -> tuple[LayoutContext, list[PagePlan]]:
    loaded = load_config(root, year)
    data = load_year_data(root / "calendars" / str(year), year)
    ctx, plans = plan_pages(root, loaded.config, data)
    return ctx, plans


def _op_rect(op: Op) -> Rect | None:
    if isinstance(op, (FillRect, ImageOp)):
        return op.rect
    if isinstance(op, TextOp):
        return op.rect
    return None


def _text_extent(op: TextOp) -> Rect:
    widths = [string_width_mm(line, op.font, op.size_pt) for line in op.lines]
    width = max(widths) if widths else 0.0
    height = len(op.lines) * op.size_pt * LEADING / 72 * 25.4
    if op.align == "right":
        x = op.rect.right - width
    elif op.align == "centre":
        x = op.rect.cx - width / 2
    else:
        x = op.rect.x
    return Rect(x, op.rect.y, width, height)


def _assert_page_invariants(ctx: LayoutContext, plan: PagePlan) -> None:
    cfg = ctx.config
    content = plan.content
    assert content is not None
    keep = keep_clear_rect(cfg, ctx.geom)
    label = f"{plan.kind} {plan.month}"

    for op in plan.ops:
        rect = _op_rect(op)
        if rect is not None:
            assert content.contains(rect), f"{label}: {op} is outside the content area"
            if keep is not None:
                assert not rect.overlaps(keep), f"{label}: {op} enters the binding keep-clear zone"
        if isinstance(op, Line):
            assert content.x - 1e-6 <= min(op.x1, op.x2)
            assert max(op.x1, op.x2) <= content.right + 1e-6
            assert min(op.y1, op.y2) >= content.y - 1e-6

    # Every text block fits its box; its rendered extent is inside that box.
    texts = [op for op in plan.ops if isinstance(op, TextOp)]
    for op in texts:
        extent = _text_extent(op)
        assert op.rect.contains(extent, tol=1e-3), f"{label}: text overflows {op.lines[:2]}"

    # No two text blocks on the same page overlap (catches footer collisions).
    extents = [(op, _text_extent(op)) for op in texts]
    for (a_op, a), (b_op, b) in itertools.combinations(extents, 2):
        assert not a.overlaps(b, tol=1e-3), (
            f"{label}: text overlaps: {a_op.lines[:1]} and {b_op.lines[:1]}"
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"grid": {"week_start": "monday", "rows": "auto"}},
        {"grid": {"week_start": "monday", "rows": 6}},
        {"grid": {"week_start": "sunday", "rows": "auto"}},
        {"grid": {"week_start": "sunday", "rows": 6}},
        {"layout": {"caption": {"placement": "beside_grid"}}},
        {"layout": {"caption": {"placement": "beside_grid"}}, "grid": {"week_numbers": True}},
        {"grid": {"week_numbers": True, "rows": 6}},
    ],
)
def test_every_page_respects_safe_area_keep_clear_and_text_fit(
    make_repo: MakeRepo, overrides: dict[str, object]
) -> None:
    import yaml

    root = make_repo(calendar_yaml="year: 2027\n" + yaml.safe_dump(overrides), copy_fonts=True)
    ctx, plans = _plans(root)
    assert len(plans) == 13
    for plan in plans:
        _assert_page_invariants(ctx, plan)


@pytest.mark.parametrize("year", [2024, 2028, 2032])
def test_other_years_lay_out_cleanly(make_repo: MakeRepo, year: int) -> None:
    """Year-agnostic: same layout code, different year data (leap and non-leap years)."""
    root = make_repo(calendar_yaml=f"year: {year}\n", copy_fonts=True)
    cal_dir = root / "calendars" / str(year)
    cal_dir.mkdir(parents=True)
    (root / "calendars" / "2027" / "calendar.yaml").rename(cal_dir / "calendar.yaml")
    (cal_dir / "calendar.yaml").write_text(f"year: {year}\n", encoding="utf-8")
    for name in ("birds.yaml", "events.yaml"):
        text = (root / "calendars" / "2027" / name).read_text(encoding="utf-8")
        (cal_dir / name).write_text(text.replace("year: 2027", f"year: {year}"), encoding="utf-8")
    (cal_dir / "holidays.yaml").write_text(
        f"year: {year}\nregion: AU-VIC\nstatus: proposed\nholidays: []\n", encoding="utf-8"
    )
    ctx, plans = _plans(root, year)
    assert len(plans) == 13
    for plan in plans:
        _assert_page_invariants(ctx, plan)


def test_hole_pattern_is_inside_edge_and_clear_of_content(repo_root: Path) -> None:
    cfg = load_config(repo_root, 2027).config
    from birdcal.build import geometry_for

    geom = geometry_for(cfg)
    centres = hole_centres(cfg, geom)
    assert len(centres) >= 2
    radius = cfg.binding.holes.diameter_mm / 2  # type: ignore[union-attr]
    for x, y in centres:
        assert 0 < x < geom.trim_w
        assert y + radius <= cfg.binding.keep_clear_mm
    # 3:1 spiral: pitch is 25.4 / 3 mm; check the spacing of the first two holes.
    spacing = centres[1][0] - centres[0][0]
    assert abs(spacing - 8.47) < 1e-6
    # Centred pattern: equal margins on both ends.
    assert abs(centres[0][0] - (geom.trim_w - centres[-1][0])) < 1e-6


def test_trim_marks_sit_outside_the_bleed(repo_root: Path) -> None:
    from birdcal.build import geometry_for

    cfg = load_config(repo_root, 2027).config
    geom = geometry_for(cfg)
    marks = trim_mark_ops(cfg, geom)
    assert len(marks) == 8
    for op in marks:
        assert isinstance(op, Line)
        for x, y in ((op.x1, op.y1), (op.x2, op.y2)):
            outside_x = max(0.0, -x, x - geom.trim_w)
            outside_y = max(0.0, -y, y - geom.trim_h)
            assert max(outside_x, outside_y) >= cfg.bleed_mm - 1e-9
            assert max(outside_x, outside_y) <= geom.outside + 1e-9


def test_oversized_description_names_month_and_field(make_repo: MakeRepo) -> None:
    long_text = " ".join(["overflowing"] * 1500)  # far more text than the box can hold
    root = make_repo(copy_fonts=True)
    birds = root / "calendars" / "2027" / "birds.yaml"
    birds.write_text(
        birds.read_text(encoding="utf-8").replace(
            "  - month: 1\n    common_name: null\n    scientific_name: null\n    description: null",
            f"  - month: 1\n    common_name: Test\n    scientific_name: Testus\n    description: {long_text}",
            1,
        ),
        encoding="utf-8",
    )
    birds_text = birds.read_text(encoding="utf-8")
    assert "description: overflowing" in birds_text
    with pytest.raises(BuildError, match="month 01 description"):
        _plans(root)


def test_description_shrinks_before_failing(make_repo: MakeRepo) -> None:
    text = "A moderately long description of a bird that needs to shrink to fit the box. " * 9
    root = make_repo(copy_fonts=True)
    birds = root / "calendars" / "2027" / "birds.yaml"
    birds.write_text(
        birds.read_text(encoding="utf-8").replace(
            "  - month: 1\n    common_name: null\n    scientific_name: null\n    description: null",
            f"  - month: 1\n    common_name: Test\n    scientific_name: Testus\n    description: {text.strip()}",
            1,
        ),
        encoding="utf-8",
    )
    ctx, plans = _plans(root)
    jan = plans[1]
    desc = [op for op in jan.ops if isinstance(op, TextOp) and op.font.endswith("Regular")]
    sizes = sorted({op.size_pt for op in desc})
    assert sizes
    assert min(sizes) < 10  # shrank below the 10 pt role size to fit
    for plan in plans:
        _assert_page_invariants(ctx, plan)


def test_photo_too_tall_names_the_month(make_repo: MakeRepo) -> None:
    root = make_repo(calendar_yaml="year: 2027\nlayout: {photo_height_mm: 300}\n", copy_fonts=True)
    with pytest.raises(BuildError, match="month 01: no room for the calendar"):
        _plans(root)


def test_hanger_outside_keep_clear_zone_is_refused(make_repo: MakeRepo) -> None:
    root = make_repo(calendar_yaml="year: 2027\nhanger: {offset_mm: 60}\n", copy_fonts=True)
    with pytest.raises(BuildError, match="hanger hole overlaps the content area"):
        _plans(root)


def test_print_mode_refuses_placeholder_images(repo_root: Path, tmp_path: Path) -> None:
    with pytest.raises(BuildError, match="print refused"):
        build_calendar(repo_root, 2027, "print", out_dir=tmp_path)


def test_draft_build_writes_cover_twelve_months(repo_root: Path, tmp_path: Path) -> None:
    result = build_calendar(repo_root, 2027, "draft", out_dir=tmp_path)
    assert result.pages == 13
    assert result.path.exists()
    assert any("placeholder images used" in w for w in result.warnings)


def test_back_page_adds_a_fourteenth_page(make_repo: MakeRepo, tmp_path: Path) -> None:
    root = make_repo(calendar_yaml="year: 2027\npages: {back: true}\n", copy_fonts=True)
    result = build_calendar(root, 2027, "draft", out_dir=tmp_path)
    assert result.pages == 14


def test_config_error_is_build_error(make_repo: MakeRepo, tmp_path: Path) -> None:
    root = make_repo(calendar_yaml="year: 2027\nsides: {mode: duplex}\n", copy_fonts=True)
    with pytest.raises(BuildError, match="duplex"):
        build_calendar(root, 2027, "draft", out_dir=tmp_path)
