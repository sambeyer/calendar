"""Double-sided sheets: each month sheet's back shows the next month's photo."""

from __future__ import annotations

from pathlib import Path

from conftest import MakeRepo
from helpers import birds_yaml

from birdcal.build import plan_pages
from birdcal.config import load_config
from birdcal.data import load_year_data
from birdcal.geometry import Rect
from birdcal.layout import Circle, ImageOp, PagePlan, TextOp, keep_clear_rect


def _plans(root: Path, year: int = 2027) -> tuple[object, list[PagePlan]]:
    loaded = load_config(root, year)
    data = load_year_data(root / "calendars" / str(year), year)
    return plan_pages(root, loaded.config, data)


def _photo_file(plan: PagePlan) -> str | None:
    images = [op for op in plan.ops if isinstance(op, ImageOp)]
    return images[0].file if images else None


def _named_birds(make_repo: MakeRepo) -> Path:
    root = make_repo(copy_fonts=True)
    files = {m: f"IMG_{m:02d}.jpg" for m in range(1, 13)}
    (root / "calendars" / "2027" / "birds.yaml").write_text(birds_yaml(files), encoding="utf-8")
    return root


def test_sheet_order_is_cover_then_month_and_back_pairs(make_repo: MakeRepo) -> None:
    root = _named_birds(make_repo)
    _, plans = _plans(root)
    kinds = [p.kind for p in plans]
    assert kinds[:2] == ["cover", "next_photo"]  # the cover's back shows January
    assert kinds[2:] == ["month", "next_photo"] * 12
    assert len(plans) == 26


def test_back_of_each_month_shows_the_next_months_photo(make_repo: MakeRepo) -> None:
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
    heading = next(op for op in december_back.ops if isinstance(op, TextOp))
    assert heading.lines == ("Next month: January 2028",)


def test_middle_spine_keeps_photo_above_and_calendar_below_the_band(repo_root: Path) -> None:
    ctx, plans = _plans(repo_root)
    band = keep_clear_rect(ctx.config, ctx.geom)  # type: ignore[attr-defined]
    assert band is not None
    month = next(p for p in plans if p.kind == "month")
    photo = next(op for op in month.ops if isinstance(op, ImageOp))
    assert photo.rect.bottom <= band.y + 1e-6
    texts_below = [
        op for op in month.ops if isinstance(op, TextOp) and op.rect.y >= band.bottom - 1e-6
    ]
    assert texts_below, "calendar text should sit below the spine"


def test_hanger_hole_sits_above_the_photo_on_the_cover(repo_root: Path) -> None:
    ctx, plans = _plans(repo_root)
    cover = plans[0]
    photo = next(op for op in cover.ops if isinstance(op, ImageOp))
    hanger = next(
        op
        for op in cover.guides
        if isinstance(op, Circle) and abs(op.cy - ctx.config.hanger.offset_mm) < 1e-6  # type: ignore[attr-defined]
    )
    hole = Rect(hanger.cx - hanger.r, hanger.cy - hanger.r, 2 * hanger.r, 2 * hanger.r)
    assert hole.bottom < photo.rect.y, "hanger hole must sit above the photo"
    assert hanger.cx == ctx.config.hanger.x_mm  # type: ignore[attr-defined]


def test_single_sided_keeps_one_sheet_per_month(make_repo: MakeRepo) -> None:
    root = make_repo(calendar_yaml="year: 2027\nsides: {mode: single}\n", copy_fonts=True)
    _, plans = _plans(root)
    assert len(plans) == 13
    assert all(p.kind != "blank" and p.kind != "next_photo" for p in plans)


def test_hanger_hole_is_on_every_sheet(repo_root: Path) -> None:
    """hanger.pages covers every sheet, front and back, so each sheet hangs from the photo top."""
    ctx, plans = _plans(repo_root)
    offset = ctx.config.hanger.offset_mm  # type: ignore[attr-defined]
    for plan in plans:
        hangers = [
            op for op in plan.guides
            if isinstance(op, Circle) and abs(op.cy - offset) < 1e-6
        ]
        assert len(hangers) == 1, f"{plan.kind} {plan.month} has no hanger hole"
