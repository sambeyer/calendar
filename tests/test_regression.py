"""Regression: a fixed fixture calendar must keep producing the same bytes.

If this fails after a code change, the change altered output for an existing year. That is
only acceptable when intended. To accept it, rerun with BIRDCAL_UPDATE_REGRESSION=1 and
review the change to tests/regression/expected.sha256 in the same commit.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from conftest import MakeRepo

from birdcal.build import build_calendar
from birdcal.photos import sha256_file

FIXTURE = Path(__file__).resolve().parent / "regression"
EXPECTED = FIXTURE / "expected.sha256"


def test_regression_fixture_matches_stored_hash(make_repo: MakeRepo, tmp_path: Path) -> None:
    root = make_repo(copy_fonts=True)
    calendar_dir = root / "calendars" / "2027"
    for name in ("birds.yaml", "holidays.yaml", "events.yaml"):
        shutil.copy(FIXTURE / name, calendar_dir / name)
    shutil.copy(FIXTURE / "calendar.yaml", calendar_dir / "calendar.yaml")

    result = build_calendar(root, 2027, "draft", out_dir=tmp_path)
    digest = sha256_file(result.path)

    if os.environ.get("BIRDCAL_UPDATE_REGRESSION") == "1":
        EXPECTED.write_text(digest + "\n", encoding="utf-8")
    assert EXPECTED.exists(), "no stored hash yet; run with BIRDCAL_UPDATE_REGRESSION=1"
    assert digest == EXPECTED.read_text(encoding="utf-8").strip(), (
        "regression fixture output changed; see the module docstring"
    )
