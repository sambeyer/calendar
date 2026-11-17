from __future__ import annotations

import itertools

from birdcal.geometry import (
    Rect,
    SheetGeometry,
    effective_ppi,
    fill_placement,
    fit_placement,
)
from birdcal.text import candidate_sizes


def test_rect_containment_and_overlap_ignore_touching_edges() -> None:
    a = Rect(0, 0, 10, 10)
    assert a.contains(Rect(0, 0, 10, 10))
    assert not a.contains(Rect(5, 5, 6, 1))
    assert a.overlaps(Rect(9, 9, 5, 5))
    assert not a.overlaps(Rect(10, 0, 5, 5))  # touching only


def test_sheet_boxes_are_media_bleed_trim_in_order() -> None:
    geom = SheetGeometry(trim_w=297, trim_h=420, bleed=3, marks_space=8)
    assert geom.media_w == 297 + 2 * 11
    assert geom.media_h == 420 + 2 * 11
    assert geom.bleed_rect == Rect(-3, -3, 303, 426)
    x0, y0, x1, y1 = geom.box_pt(geom.trim_rect)
    assert round(x1 - x0, 3) == round(297 * 72 / 25.4, 3)
    assert round(y1 - y0, 3) == round(420 * 72 / 25.4, 3)


def test_fill_covers_box_and_keeps_focal_point_centred_when_possible() -> None:
    box = Rect(0, 0, 300, 170)
    placement = fill_placement(6000, 4000, box, (0.5, 0.5))
    assert placement.draw.w >= box.w - 1e-9
    assert placement.draw.h >= box.h - 1e-9
    assert placement.draw.cx == box.cx
    assert placement.draw.cy == box.cy
    assert placement.clip == box


def test_fill_clamps_at_edges_so_there_is_no_gap() -> None:
    box = Rect(0, 0, 300, 170)
    placement = fill_placement(6000, 4000, box, (0.0, 0.0))  # focal at far top-left
    assert placement.draw.x == 0
    assert placement.draw.y == 0
    right_focal = fill_placement(6000, 4000, box, (1.0, 1.0))
    assert right_focal.draw.right == box.right
    assert right_focal.draw.bottom == box.bottom


def test_fit_shows_whole_image_inside_box() -> None:
    box = Rect(0, 0, 300, 170)
    placement = fit_placement(3000, 2000, box)
    assert placement.draw.w <= box.w + 1e-9
    assert placement.draw.h <= box.h + 1e-9
    assert box.contains(placement.draw)


def test_effective_ppi_high_res_and_small_images() -> None:
    box = Rect(0, 0, 300, 170)
    big = effective_ppi(6000, 4000, box, "fill")
    small = effective_ppi(1000, 700, box, "fill")
    assert big > 300
    assert small < 200


def test_candidate_sizes_shrink_and_always_end_at_floor() -> None:
    sizes = candidate_sizes(10, 7.1)
    assert sizes[0] == 10
    assert sizes[-1] == 7.1
    assert all(a > b for a, b in itertools.pairwise(sizes))
