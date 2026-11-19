from __future__ import annotations

import re
from pathlib import Path

import pytest
from conftest import MakeRepo
from helpers import birds_yaml

from birdcal.build import BuildError, build_calendar, plan_pages
from birdcal.config import load_config
from birdcal.data import load_year_data
from birdcal.geometry import Rect
from birdcal.layout import FillRect, LayoutContext, PagePlan, TextOp

BACK_YAML = """\
year: 2027
photographers:
  - name: First Photographer
    bio: Photographs birds across south-east Australia and writes about them.
  - name: Second Photographer
    bio: Spends early mornings in wetlands and grasslands looking for small birds.
appeal: "This calendar is free. Please donate to a bird conservation charity instead."
"""


def _plans(root: Path, year: int = 2027) -> tuple[LayoutContext, list[PagePlan]]:
    loaded = load_config(root, year)
    data = load_year_data(root / "calendars" / str(year), year)
    return plan_pages(root, loaded.config, data)


def _filled(make_repo: MakeRepo, back: str = BACK_YAML) -> Path:
    root = make_repo(copy_fonts=True)
    files = {m: f"IMG_{m:02d}.jpg" for m in range(1, 13)}
    (root / "calendars" / "2027" / "birds.yaml").write_text(birds_yaml(files), encoding="utf-8")
    (root / "calendars" / "2027" / "back.yaml").write_text(back, encoding="utf-8")
    return root


def _last_back(plans: list[PagePlan]) -> PagePlan:
    return [p for p in plans if p.kind == "next_photo"][-1]


def _panel(plan: PagePlan) -> Rect:
    # Weekend shading is also a FillRect; the text panel is the translucent one.
    fills = [op for op in plan.ops if isinstance(op, FillRect) and op.alpha < 1]
    assert len(fills) == 1, "exactly one text panel on the last back"
    return fills[0].rect


def test_only_the_last_sheet_back_has_the_text_panel(make_repo: MakeRepo) -> None:
    _, plans = _plans(_filled(make_repo))
    with_panel = [
        i
        for i, p in enumerate(plans)
        if any(isinstance(o, FillRect) and o.alpha < 1 for o in p.ops)
    ]
    assert with_panel == [len(plans) - 1]


def test_panel_is_inside_the_safe_margin_and_clear_of_the_binding(make_repo: MakeRepo) -> None:
    ctx, plans = _plans(_filled(make_repo))
    cfg = ctx.config
    trim = ctx.geom.trim_rect
    panel = _panel(_last_back(plans))
    margin = cfg.safe_margin_mm
    assert panel.x >= trim.x + margin and panel.right <= trim.right - margin
    assert panel.y >= trim.y + margin and panel.bottom <= trim.bottom - margin
    # The last back is a photo back: its binding holes are at the bottom edge.
    assert _last_back(plans).binding_at_bottom
    assert panel.bottom <= trim.bottom - cfg.binding.keep_clear_mm + 1e-9


@pytest.mark.parametrize(
    ("position", "left", "top"),
    [
        ("top_left", True, True),
        ("top_right", False, True),
        ("bottom_left", True, False),
        ("bottom_right", False, False),
    ],
)
def test_position_setting_moves_the_panel_to_that_corner(
    make_repo: MakeRepo, position: str, left: bool, top: bool
) -> None:
    root = _filled(make_repo)
    config_path = root / "config" / "defaults.yaml"
    text = re.sub(
        r"position: (top|bottom)_(left|right)",
        f"position: {position}",
        config_path.read_text(encoding="utf-8"),
        count=1,
    )
    config_path.write_text(text, encoding="utf-8")
    ctx, plans = _plans(root)
    trim = ctx.geom.trim_rect
    panel = _panel(_last_back(plans))
    assert (panel.x < trim.x + trim.w / 2) == left
    assert (panel.y < trim.y + trim.h / 2) == top


def test_panel_never_covers_the_hanger_hole(make_repo: MakeRepo) -> None:
    ctx, plans = _plans(_filled(make_repo))
    cfg = ctx.config
    panel = _panel(_last_back(plans))
    hx, hy = cfg.hanger.x_mm, cfg.hanger.offset_mm
    r = cfg.hanger.diameter_mm / 2
    overlaps = (
        panel.x < hx + r and panel.right > hx - r and panel.y < hy + r and panel.bottom > hy - r
    )
    assert not overlaps


def test_text_sits_inside_the_panel(make_repo: MakeRepo) -> None:
    _, plans = _plans(_filled(make_repo))
    last = _last_back(plans)
    panel = _panel(last)
    texts = [op for op in last.ops if isinstance(op, TextOp)]
    assert len(texts) == 6  # heading, two names, two bios, appeal
    for op in texts:
        assert op.rect.x >= panel.x and op.rect.right <= panel.right
        assert op.rect.y >= panel.y and op.rect.bottom <= panel.bottom + 1e-6


def test_text_is_white_on_a_translucent_black_panel(make_repo: MakeRepo) -> None:
    _, plans = _plans(_filled(make_repo))
    fill = next(op for op in _last_back(plans).ops if isinstance(op, FillRect) and op.alpha < 1)
    assert fill.colour.upper() == "#000000"
    assert 0 < fill.alpha < 1  # translucent, so the photo still shows through
    text_colours = {op.colour.upper() for op in _last_back(plans).ops if isinstance(op, TextOp)}
    assert text_colours == {"#FFFFFF"}


def test_too_much_text_names_the_back_text(make_repo: MakeRepo, tmp_path: Path) -> None:
    long_bio = " ".join(["word"] * 900)
    back = BACK_YAML.replace("Photographs birds", long_bio + " Photographs birds")
    root = _filled(make_repo, back=back)
    with pytest.raises(BuildError, match="back text"):
        build_calendar(root, 2027, "draft", out_dir=tmp_path)


def test_print_is_refused_until_the_back_text_is_finished(
    make_repo: MakeRepo, tmp_path: Path
) -> None:
    unfinished = """\
year: 2027
photographers:
  - name: null
    bio: null
  - name: Second Photographer
    bio: A bio.
appeal: null
"""
    root = _filled(make_repo, back=unfinished)
    with pytest.raises(BuildError, match="back page text not finished") as info:
        build_calendar(root, 2027, "print", out_dir=tmp_path)
    message = str(info.value)
    assert "photographer 1 name" in message
    assert "donation appeal" in message


def test_draft_build_shows_placeholders_when_text_is_missing(
    make_repo: MakeRepo, tmp_path: Path
) -> None:
    root = make_repo(copy_fonts=True)
    (root / "calendars" / "2027" / "birds.yaml").write_text(birds_yaml({}), encoding="utf-8")
    (root / "calendars" / "2027" / "back.yaml").unlink()
    _, plans = _plans(root)
    texts = [op.lines for op in _last_back(plans).ops if isinstance(op, TextOp)]
    assert any("Bio to come" in line for lines in texts for line in lines)
    assert build_calendar(root, 2027, "draft", out_dir=tmp_path).pages == 26
