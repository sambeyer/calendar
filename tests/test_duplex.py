"""Double-sided sheets: photo pages are full bleed, grid pages sit below the binding strip.

Sheet order (the flip order a reader sees): cover front, then the back of the cover (January's
photo), then January's grid, the back of that sheet (February's photo), and so on. December's
grid is followed by January's photo again (the next year).
"""

from __future__ import annotations

from pathlib import Path

from conftest import MakeRepo
from helpers import birds_yaml

from birdcal.build import plan_pages
from birdcal.config import load_config
from birdcal.data import load_year_data
from birdcal.layout import Circle, ImageOp, PagePlan, TextOp, keep_clear_rect


def _plans(root: Path, year: int = 2027) -> tuple[object, list[PagePlan]]:
    loaded = load_config(root, year)
    data = load_year_data(root / "calendars" / str(year), year)
    return plan_pages(root, loaded.config, data)


def _photo_file(plan: PagePlan) -> str | None:
    images = [op for op in plan.ops if isinstance(op, ImageOp)]
    return images[0].file if images else None


def _named_birds(make_repo: MakeRepo, calendar_yaml: str = "year: 2027\n") -> Path:
    # The year's own calendar.yaml may set a cover or final-back photo; these tests use the
    # defaults unless they pass their own calendar_yaml.
    root = make_repo(calendar_yaml=calendar_yaml, copy_fonts=True)
    files = {m: f"IMG_{m:02d}.jpg" for m in range(1, 13)}
    (root / "calendars" / "2027" / "birds.yaml").write_text(birds_yaml(files), encoding="utf-8")
    return root


def test_final_back_photo_setting_replaces_januarys_photo_on_last_sheet(
    make_repo: MakeRepo,
) -> None:
    root = _named_birds(
        make_repo, calendar_yaml="year: 2027\nsides: {final_back_photo: BACK.jpg}\n"
    )
    _, plans = _plans(root)
    december_back = [p for p in plans if p.kind == "next_photo"][-1]
    assert _photo_file(december_back) == "BACK.jpg"
    # Every other back still shows the next month's photo.
    february_back = [p for p in plans if p.kind == "next_photo"][1]
    assert _photo_file(february_back) == "IMG_02.jpg"


def test_sheet_order_is_cover_then_month_and_back_pairs(make_repo: MakeRepo) -> None:
    root = _named_birds(make_repo)
    _, plans = _plans(root)
    kinds = [p.kind for p in plans]
    assert kinds[:2] == ["cover", "next_photo"]  # the cover's back shows January
    assert kinds[2:] == ["month", "next_photo"] * 12
    assert len(plans) == 26


def test_back_of_each_grid_sheet_shows_the_next_months_photo(make_repo: MakeRepo) -> None:
    root = _named_birds(make_repo)
    _, plans = _plans(root)
    months = [p for p in plans if p.kind == "month"]
    backs = [p for p in plans if p.kind == "next_photo"]
    cover_back = backs[0]
    assert cover_back.month == 1
    assert _photo_file(cover_back) == "IMG_01.jpg"
    for month_plan, back in zip(months, backs[1:], strict=True):
        this = month_plan.month
        assert this is not None
        assert back.month == this % 12 + 1
        assert _photo_file(back) == f"IMG_{this % 12 + 1:02d}.jpg"


def test_december_back_shows_january_of_the_next_year(make_repo: MakeRepo) -> None:
    root = _named_birds(make_repo)
    _, plans = _plans(root)
    december_back = [p for p in plans if p.kind == "next_photo"][-1]
    assert december_back.month == 1
    assert _photo_file(december_back) == "IMG_01.jpg"


def test_photo_pages_are_full_bleed(repo_root: Path) -> None:
    ctx, plans = _plans(repo_root)
    for plan in plans:
        if plan.kind in ("cover", "next_photo"):
            photo = next(op for op in plan.ops if isinstance(op, ImageOp))
            assert photo.rect == ctx.geom.bleed_rect  # type: ignore[attr-defined]
    # Only the back of the calendar (the last sheet) carries text, on its panel.
    texts = [
        plan
        for plan in plans
        if plan.kind == "next_photo" and any(isinstance(op, TextOp) for op in plan.ops)
    ]
    assert texts == [plans[-1]]


def test_grid_pages_sit_below_the_binding_strip(repo_root: Path) -> None:
    ctx, plans = _plans(repo_root)
    band = keep_clear_rect(ctx.config, ctx.geom)  # type: ignore[attr-defined]
    assert band is not None
    month = next(p for p in plans if p.kind == "month")
    assert not [op for op in month.ops if isinstance(op, ImageOp)], "grid pages carry no photo"
    texts = [op for op in month.ops if isinstance(op, TextOp)]
    assert texts
    assert all(op.rect.y >= band.bottom - 1e-6 for op in texts)


def test_hanger_hole_is_on_every_photo_back_only(repo_root: Path) -> None:
    """The hanger sits at the top of every photo back (every second page), never on the cover
    or a grid page."""
    ctx, plans = _plans(repo_root)
    hanger = ctx.config.hanger  # type: ignore[attr-defined]
    radius = hanger.diameter_mm / 2
    for plan in plans:
        found = [
            op
            for op in plan.guides
            if isinstance(op, Circle)
            and abs(op.r - radius) < 1e-9
            and abs(op.cx - hanger.x_mm) < 1e-9
            and abs(op.cy - hanger.offset_mm) < 1e-9
        ]
        if plan.kind == "next_photo":
            assert len(found) == 1, f"{plan.kind} {plan.month} has no hanger hole"
        else:
            assert not found, f"{plan.kind} {plan.month} must not have a hanger hole"


def test_single_sided_keeps_one_sheet_per_month(make_repo: MakeRepo) -> None:
    root = make_repo(calendar_yaml="year: 2027\nsides: {mode: single}\n", copy_fonts=True)
    _, plans = _plans(root)
    assert len(plans) == 13
    assert all(p.kind not in ("blank", "next_photo") for p in plans)


def test_only_the_backs_of_sheets_have_the_binding_at_the_bottom(repo_root: Path) -> None:
    """Photo backs are upright, with the binding at the bottom. The cover front and the grid
    pages keep the binding at the top."""
    _, plans = _plans(repo_root)
    for plan in plans:
        expected = plan.kind == "next_photo"
        assert plan.binding_at_bottom is expected, f"{plan.kind} {plan.month}"


def test_photos_are_only_turned_on_the_back_of_the_calendar(
    repo_root: Path, tmp_path: Path
) -> None:
    """Images stay the right way up except the back of the calendar, printed upside down."""
    import pypdf

    from birdcal.build import build_calendar

    result = build_calendar(repo_root, 2027, "draft", out_dir=tmp_path)
    reader = pypdf.PdfReader(str(result.path))
    for page in reader.pages[:-1]:
        contents = page.get_contents()
        assert contents is not None
        assert b"-1 0 0 -1" not in contents.get_data()
    last = reader.pages[-1].get_contents()
    assert last is not None
    assert b"-1 0 0 -1" in last.get_data()
