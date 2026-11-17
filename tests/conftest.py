from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

MakeRepo = Callable[..., Path]


@pytest.fixture
def repo_root() -> Path:
    return REPO


@pytest.fixture
def make_repo(tmp_path: Path) -> MakeRepo:
    """Copy config/ and calendars/ into a temp repo so tests can alter any layer."""

    def _make(
        *,
        calendar_yaml: str | None = None,
        local_yaml: str | None = None,
        defaults_yaml: str | None = None,
        copy_fonts: bool = False,
    ) -> Path:
        root = tmp_path / "repo"
        shutil.copytree(REPO / "config", root / "config")
        shutil.copytree(REPO / "calendars", root / "calendars")
        if copy_fonts:
            shutil.copytree(REPO / "fonts", root / "fonts")
        if defaults_yaml is not None:
            (root / "config" / "defaults.yaml").write_text(defaults_yaml, encoding="utf-8")
        if calendar_yaml is not None:
            (root / "calendars" / "2027" / "calendar.yaml").write_text(
                calendar_yaml, encoding="utf-8"
            )
        if local_yaml is not None:
            (root / "local.yaml").write_text(local_yaml, encoding="utf-8")
        return root

    return _make
