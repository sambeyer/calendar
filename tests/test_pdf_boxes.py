from __future__ import annotations

from pathlib import Path

import pypdf
import pytest

from birdcal.build import build_calendar
from birdcal.geometry import PT_PER_MM

TOL_PT = 0.01


def _pt(mm: float) -> float:
    return mm * PT_PER_MM


@pytest.fixture
def draft_pdf(repo_root: Path, tmp_path: Path) -> Path:
    return build_calendar(repo_root, 2027, "draft", out_dir=tmp_path).path


def _box(box: pypdf.generic.RectangleObject) -> tuple[float, float, float, float]:
    return (float(box.left), float(box.bottom), float(box.right), float(box.top))


def test_page_boxes_match_trim_bleed_and_marks(draft_pdf: Path) -> None:
    # Expected from config/defaults.yaml: A3 trim, 3 mm bleed, trim marks offset 3 + length 5.
    trim_w, trim_h, bleed = 420.0, 297.0, 3.0  # A3 landscape
    outside = bleed + 3 + 5
    reader = pypdf.PdfReader(str(draft_pdf))
    for page in reader.pages:
        media = _box(page.mediabox)
        assert media[2] - media[0] == pytest.approx(_pt(trim_w + 2 * outside), abs=TOL_PT)
        assert media[3] - media[1] == pytest.approx(_pt(trim_h + 2 * outside), abs=TOL_PT)
        bleed_box = _box(page.bleedbox)
        assert bleed_box[2] - bleed_box[0] == pytest.approx(_pt(trim_w + 2 * bleed), abs=TOL_PT)
        assert bleed_box[3] - bleed_box[1] == pytest.approx(_pt(trim_h + 2 * bleed), abs=TOL_PT)
        trim_box = _box(page.trimbox)
        assert trim_box[2] - trim_box[0] == pytest.approx(_pt(trim_w), abs=TOL_PT)
        assert trim_box[3] - trim_box[1] == pytest.approx(_pt(trim_h), abs=TOL_PT)
        # Nested: media contains bleed contains trim.
        assert media[0] <= bleed_box[0] <= trim_box[0] <= trim_box[2] <= bleed_box[2] <= media[2]
        assert media[1] <= bleed_box[1] <= trim_box[1] <= trim_box[3] <= bleed_box[3] <= media[3]
        art_box = _box(page.artbox)
        assert art_box == pytest.approx(trim_box, abs=TOL_PT)


def test_pdf_has_one_page_per_plan(draft_pdf: Path) -> None:
    assert len(pypdf.PdfReader(str(draft_pdf)).pages) == 26
