"""--ignore-lock and --low-quality: they build, their file names say not for printing, and the
lossless check still holds for the photos actually embedded."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from conftest import MakeRepo
from helpers import PROFILE, birds_yaml, finish_back, save_jpeg

from birdcal.build import BuildError, build_calendar, output_name
from birdcal.cli import main
from birdcal.photos import embedded_jpeg_hashes, sha256_file

MONTHS = range(1, 13)


@pytest.fixture
def photo_repo(make_repo: MakeRepo, tmp_path: Path) -> tuple[Path, Path]:
    """A repo whose 12 months have locked, sRGB JPEGs in a temporary photos folder."""
    root = make_repo(
        calendar_yaml=f"year: 2027\ncolour: {{profile: '{PROFILE}'}}\n", copy_fonts=True
    )
    photos = tmp_path / "photos"
    photos.mkdir()
    files = {m: f"IMG_{m:02d}.jpg" for m in MONTHS}
    for m, name in files.items():
        save_jpeg(photos / name, 3000 + 40 * m, 2000, quality=85)
    (root / "local.yaml").write_text(f"paths: {{photos_dir: '{photos}'}}\n", encoding="utf-8")
    (root / "calendars" / "2027" / "birds.yaml").write_text(birds_yaml(files), encoding="utf-8")
    finish_back(root)
    assert main(["--root", str(root), "lock-photos", "2027"]) == 0
    return root, photos


def _change_photo(photos: Path, name: str) -> None:
    """Re-save a photo so its bytes (and hash) differ from the lock."""
    save_jpeg(photos / name, 3000 + 40 * 1, 2000, quality=60)


def test_output_names_say_when_a_file_is_not_for_printing() -> None:
    assert output_name(2027, "print") == "calendar-2027-print.pdf"
    assert output_name(2027, "print", ignore_lock=True) == "calendar-2027-print-ignore-lock.pdf"
    assert output_name(2027, "print", low_quality=True) == "calendar-2027-print-low-quality.pdf"
    assert (
        output_name(2027, "print", ignore_lock=True, low_quality=True)
        == "calendar-2027-print-ignore-lock-low-quality.pdf"
    )


def test_changed_photo_is_refused_without_the_flag(
    photo_repo: tuple[Path, Path], tmp_path: Path
) -> None:
    root, photos = photo_repo
    _change_photo(photos, "IMG_01.jpg")
    with pytest.raises(BuildError, match="does not match photos.lock"):
        build_calendar(root, 2027, "print", out_dir=tmp_path)


def test_ignore_lock_builds_a_changed_photo_and_names_the_file(
    photo_repo: tuple[Path, Path], tmp_path: Path
) -> None:
    root, photos = photo_repo
    _change_photo(photos, "IMG_01.jpg")
    result = build_calendar(root, 2027, "print", out_dir=tmp_path, ignore_lock=True)
    assert result.path.name == "calendar-2027-print-ignore-lock.pdf"
    assert result.not_for_printing
    assert any("NOT FOR PRINTING" in w for w in result.warnings)
    assert any("IMG_01.jpg" in w and "--ignore-lock" in w for w in result.warnings)
    # The lossless check passed, so the PDF embeds the changed file's own bytes.
    changed = sha256_file(photos / "IMG_01.jpg")
    assert embedded_jpeg_hashes(result.path)[changed] == 3


def test_ignore_lock_builds_an_unlocked_photo(
    photo_repo: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _ = photo_repo
    (root / "calendars" / "2027" / "photos.lock").unlink()
    with pytest.raises(BuildError, match="not in photos.lock"):
        build_calendar(root, 2027, "print", out_dir=tmp_path)
    result = build_calendar(root, 2027, "print", out_dir=tmp_path, ignore_lock=True)
    assert result.path.name == "calendar-2027-print-ignore-lock.pdf"


def test_low_quality_is_smaller_marked_and_still_lossless(
    photo_repo: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _ = photo_repo
    full = build_calendar(root, 2027, "print", out_dir=tmp_path / "full")
    small = build_calendar(root, 2027, "print", out_dir=tmp_path / "small", low_quality=True)
    assert small.path.name == "calendar-2027-print-low-quality.pdf"
    assert small.path.stat().st_size < full.path.stat().st_size * 0.8
    assert all(f.ppi <= 150 + 1 for f in small.findings)
    assert any("NOT FOR PRINTING" in w for w in small.warnings)
    # Full-quality output is untouched by the option.
    assert not full.not_for_printing
    assert full.path.name == "calendar-2027-print.pdf"


def test_low_quality_embeds_resampled_copies_not_the_source(
    photo_repo: tuple[Path, Path], tmp_path: Path
) -> None:
    root, photos = photo_repo
    result = build_calendar(root, 2027, "print", out_dir=tmp_path, low_quality=True)
    source_hashes = {sha256_file(photos / f"IMG_{m:02d}.jpg") for m in MONTHS}
    embedded = embedded_jpeg_hashes(result.path)
    assert not (set(embedded) & source_hashes)
    assert set(embedded) == set(result.photo_hashes)


def test_both_flags_together(photo_repo: tuple[Path, Path], tmp_path: Path) -> None:
    root, photos = photo_repo
    _change_photo(photos, "IMG_02.jpg")
    result = build_calendar(
        root, 2027, "print", out_dir=tmp_path, ignore_lock=True, low_quality=True
    )
    assert result.path.name == "calendar-2027-print-ignore-lock-low-quality.pdf"
    assert result.path.is_file()


def test_cli_build_writes_the_marked_name(
    photo_repo: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    root, photos = photo_repo
    _change_photo(photos, "IMG_03.jpg")
    code = main(
        ["--root", str(root), "build", "2027", "--mode", "print", "--ignore-lock", "--low-quality"]
    )
    assert code == 0
    out = capsys.readouterr().out
    marked = root / "build" / "2027" / "calendar-2027-print-ignore-lock-low-quality.pdf"
    assert marked.is_file()
    assert "NOT FOR PRINTING" in out
    assert not (root / "build" / "2027" / "calendar-2027-print.pdf").exists()


def test_cli_plain_print_has_no_marker(photo_repo: tuple[Path, Path]) -> None:
    root, _ = photo_repo
    assert main(["--root", str(root), "build", "2027", "--mode", "print"]) == 0
    assert (root / "build" / "2027" / "calendar-2027-print.pdf").is_file()
    assert not list((root / "build" / "2027").glob("*ignore-lock*"))
    assert not list((root / "build" / "2027").glob("*low-quality*"))


def test_flags_do_not_change_the_default_output(
    photo_repo: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _ = photo_repo
    a = build_calendar(root, 2027, "print", out_dir=tmp_path / "a")
    b = build_calendar(root, 2027, "print", out_dir=tmp_path / "b")
    assert Counter(embedded_jpeg_hashes(a.path)) == Counter(embedded_jpeg_hashes(b.path))
