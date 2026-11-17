from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import MakeRepo
from helpers import MONTHS, PROFILE, birds_yaml, save_jpeg

from birdcal.build import build_calendar
from birdcal.cli import main
from birdcal.release import ReleaseError, parse_tag_info, release, reproduce

IDENTITY = {
    "GIT_AUTHOR_NAME": "birdcal test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "birdcal test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}
HOLIDAYS_VERIFIED = (
    "year: 2027\nregion: AU-VIC\nstatus: verified\n"
    "source: https://example.invalid/public-holidays\nchecked_on: 2026-11-17\n"
    "holidays: []\n"
)


def _git(root: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def _release_repo(
    make_repo: MakeRepo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    confirmed: bool = True,
    verified: bool = True,
) -> Path:
    for key, value in IDENTITY.items():
        monkeypatch.setenv(key, value)
    root = make_repo(
        calendar_yaml=(
            f"year: 2027\nprinter_spec_confirmed: {str(confirmed).lower()}\n"
            f"colour: {{profile: '{PROFILE}'}}\n"
        ),
        copy_fonts=True,
    )
    repo = Path(__file__).resolve().parents[1]
    for name in ("pyproject.toml", "uv.lock", "README.md", ".gitignore"):
        shutil.copy(repo / name, root / name)
    shutil.copytree(
        repo / "src" / "birdcal",
        root / "src" / "birdcal",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    if verified:
        (root / "calendars" / "2027" / "holidays.yaml").write_text(
            HOLIDAYS_VERIFIED, encoding="utf-8"
        )

    photos = tmp_path / "photos"
    photos.mkdir()
    files = {m: f"IMG_{m:02d}.jpg" for m in MONTHS}
    for m, name in files.items():
        save_jpeg(photos / name, 2400 + 40 * m, 1600, quality=80 + m)
    (root / "local.yaml").write_text(f"paths: {{photos_dir: '{photos}'}}\n", encoding="utf-8")
    (root / "calendars" / "2027" / "birds.yaml").write_text(birds_yaml(files), encoding="utf-8")
    assert main(["--root", str(root), "lock-photos", "2027"]) == 0

    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture")
    return root


def test_release_refuses_unconfirmed_printer_spec(
    make_repo: MakeRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _release_repo(make_repo, tmp_path, monkeypatch, confirmed=False)
    with pytest.raises(ReleaseError, match="printer_spec_confirmed is false"):
        release(root, 2027)
    assert _git(root, "tag", "--list") == ""


def test_release_refuses_unverified_holidays(
    make_repo: MakeRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _release_repo(make_repo, tmp_path, monkeypatch, verified=False)
    with pytest.raises(ReleaseError, match="holidays are not verified"):
        release(root, 2027)


def test_release_refuses_dirty_tree(
    make_repo: MakeRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _release_repo(make_repo, tmp_path, monkeypatch)
    (root / "README.md").write_text("changed\n", encoding="utf-8")
    with pytest.raises(ReleaseError, match="uncommitted changes"):
        release(root, 2027)


def test_release_tags_annotated_with_pdf_hash_and_build_info(
    make_repo: MakeRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _release_repo(make_repo, tmp_path, monkeypatch)
    result = release(root, 2027)
    assert result.tag == "calendar-2027"
    assert _git(root, "cat-file", "-t", "calendar-2027") == "tag"  # annotated, not lightweight

    message = _git(root, "tag", "-l", "--format=%(contents)", "calendar-2027")
    info = parse_tag_info(message)
    pdf_sha = hashlib.sha256(result.pdf.read_bytes()).hexdigest()
    assert info["pdf_sha256"] == pdf_sha
    assert f"print PDF sha256 {pdf_sha}" in message
    assert info["print"]["page_mm"] == [420.0, 297.0]
    assert info["print"]["bleed_mm"] == 3
    assert info["print"]["binding"] == "spiral"
    assert info["photos_lock_sha256"]
    assert info["commit"] == _git(root, "rev-parse", "HEAD")
    assert "pydantic" in info["libraries"] and "reportlab" in info["libraries"]
    assert set(info["font_sha256"]) == {
        "Inter-Regular.ttf",
        "Inter-Medium.ttf",
        "Inter-SemiBold.ttf",
    }
    on_disk = json.loads(result.info_path.read_text(encoding="utf-8"))
    assert on_disk == info
    assert _git(root, "remote") == ""  # nothing is pushed anywhere


def test_second_release_gets_a_revision_tag(
    make_repo: MakeRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _release_repo(make_repo, tmp_path, monkeypatch)
    release(root, 2027)
    second = release(root, 2027)
    assert second.tag == "calendar-2027-r2"


def test_reproduce_matches_the_tag_byte_for_byte(
    make_repo: MakeRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _release_repo(make_repo, tmp_path, monkeypatch)
    release(root, 2027)
    result = reproduce(root, 2027)
    assert result.matched, result.differences
    assert result.tag == "calendar-2027"


def test_reproduce_reports_a_tampered_tag(
    make_repo: MakeRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _release_repo(make_repo, tmp_path, monkeypatch)
    release(root, 2027)
    message = _git(root, "tag", "-l", "--format=%(contents)", "calendar-2027")
    info = parse_tag_info(message)
    info["pdf_sha256"] = "0" * 64
    body = "\n".join(
        [
            "calendar-2027-r9: tampered for a test",
            "",
            "--- build-info (json) ---",
            json.dumps(info, indent=2, sort_keys=True),
            "--- end build-info ---",
            "",
        ]
    )
    message_file = tmp_path / "tag.txt"
    message_file.write_text(body, encoding="utf-8")
    _git(root, "tag", "-a", "calendar-2027-r9", "-F", str(message_file))

    result = reproduce(root, 2027, tag="calendar-2027-r9")
    assert not result.matched
    assert any(line.startswith("pdf_sha256:") for line in result.differences)


def test_source_date_epoch_fixes_pdf_dates(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pypdf

    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1767225600")  # 1 Jan 2026 00:00 UTC
    result = build_calendar(repo_root, 2027, "draft", out_dir=tmp_path)
    meta = pypdf.PdfReader(str(result.path)).metadata
    assert meta is not None
    assert str(meta.get("/CreationDate")).startswith("D:20260101")
    assert str(meta.get("/ModDate")).startswith("D:20260101")


def test_real_repo_release_is_refused_until_printer_spec_is_confirmed(repo_root: Path) -> None:
    """The committed 2027 config must not be releasable yet: the gate is doing its job."""
    with pytest.raises(ReleaseError):
        release(repo_root, 2027)
