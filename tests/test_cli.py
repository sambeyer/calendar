from __future__ import annotations

from pathlib import Path

import pytest
from conftest import MakeRepo

from birdcal.cli import main


def test_validate_real_2027_exits_zero_with_warnings(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--root", str(repo_root), "validate", "2027"])
    out = capsys.readouterr().out
    assert code == 0
    assert "printer_spec_confirmed: false (release is blocked)" in out
    assert "Unconfirmed printer values" in out
    assert "0 error(s)" in out


def test_missing_font_files_are_errors(
    make_repo: MakeRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_repo(copy_fonts=False)  # config and calendars only; fonts/ absent
    code = main(["--root", str(root), "validate", "2027"])
    out = capsys.readouterr().out
    assert code == 1
    assert "font file not found" in out


def test_config_error_exits_two(make_repo: MakeRepo, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_repo(calendar_yaml="year: 2027\npage: {width_mm: 0}\n")
    code = main(["--root", str(root), "validate", "2027"])
    assert code == 2
    assert "configuration error" in capsys.readouterr().err


def test_facing_pages_is_refused_with_clear_message(
    make_repo: MakeRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_repo(calendar_yaml="year: 2027\nlayout: {mode: facing_pages}\n")
    code = main(["--root", str(root), "validate", "2027"])
    assert code == 2
    assert "facing_pages is declared but not implemented" in capsys.readouterr().err
