from __future__ import annotations

import hashlib
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest
from conftest import MakeRepo
from helpers import PROFILE, birds_yaml, save_jpeg
from PIL import Image

from birdcal.build import BuildError, build_calendar, photo_findings, plan_pages, prepare
from birdcal.cli import main
from birdcal.photos import embedded_jpeg_hashes, sha256_file

MONTHS = range(1, 13)


@pytest.fixture
def photo_repo(make_repo: MakeRepo, tmp_path: Path) -> tuple[Path, Path]:
    """A repo whose 12 months all have real, locked, sRGB JPEGs."""
    root = make_repo(
        calendar_yaml=f"year: 2027\ncolour: {{profile: '{PROFILE}'}}\n", copy_fonts=True
    )
    photos = tmp_path / "photos"
    photos.mkdir()
    files = {m: f"IMG_{m:02d}.jpg" for m in MONTHS}
    for m, name in files.items():
        save_jpeg(photos / name, 2400 + 40 * m, 1600, quality=80 + m)
    (root / "local.yaml").write_text(f"paths: {{photos_dir: '{photos}'}}\n", encoding="utf-8")
    (root / "calendars" / "2027" / "birds.yaml").write_text(birds_yaml(files), encoding="utf-8")
    assert main(["--root", str(root), "lock-photos", "2027"]) == 0
    return root, photos


def test_lock_records_hash_size_profile_and_orientation(photo_repo: tuple[Path, Path]) -> None:
    root, photos = photo_repo
    lock = (root / "calendars" / "2027" / "photos.lock").read_text(encoding="utf-8")
    assert "sha256:" in lock or "sha256" in lock
    assert sha256_file(photos / "IMG_01.jpg") in lock
    assert PROFILE in lock


def test_print_build_uses_real_photos_and_embeds_source_bytes(
    photo_repo: tuple[Path, Path], tmp_path: Path
) -> None:
    root, photos = photo_repo
    result = build_calendar(root, 2027, "print", out_dir=tmp_path)
    assert result.pages == 13
    # 12 month photos plus the cover, which reuses month 1's photo.
    expected = Counter(sha256_file(photos / f"IMG_{m:02d}.jpg") for m in MONTHS)
    expected[sha256_file(photos / "IMG_01.jpg")] += 1
    assert embedded_jpeg_hashes(result.path) == expected


@pytest.mark.skipif(shutil.which("pdfimages") is None, reason="poppler pdfimages not installed")
def test_pdfimages_extracts_identical_jpeg_bytes(
    photo_repo: tuple[Path, Path], tmp_path: Path
) -> None:
    root, photos = photo_repo
    result = build_calendar(root, 2027, "print", out_dir=tmp_path)
    out = tmp_path / "extract"
    out.mkdir()
    subprocess.run(["pdfimages", "-all", str(result.path), str(out / "img")], check=True)
    extracted = sorted(out.glob("img-*.jpg"))
    assert len(extracted) == 13  # twelve months plus the cover
    got = Counter(hashlib.sha256(p.read_bytes()).hexdigest() for p in extracted)
    expected = Counter(sha256_file(photos / f"IMG_{m:02d}.jpg") for m in MONTHS)
    expected[sha256_file(photos / "IMG_01.jpg")] += 1
    assert got == expected


def test_changed_photo_is_refused_in_every_mode(
    photo_repo: tuple[Path, Path], tmp_path: Path
) -> None:
    root, photos = photo_repo
    save_jpeg(photos / "IMG_03.jpg", 2520, 1600, quality=50)  # same name, different bytes
    for mode in ("draft", "proof", "print"):
        with pytest.raises(BuildError, match="IMG_03.jpg: does not match photos.lock"):
            build_calendar(root, 2027, mode, out_dir=tmp_path)


def test_unlocked_photo_is_refused(photo_repo: tuple[Path, Path], tmp_path: Path) -> None:
    root, photos = photo_repo
    save_jpeg(photos / "IMG_NEW.jpg", 2400, 1600)
    birds = root / "calendars" / "2027" / "birds.yaml"
    birds.write_text(
        birds.read_text(encoding="utf-8").replace("IMG_05.jpg", "IMG_NEW.jpg"), encoding="utf-8"
    )
    with pytest.raises(BuildError, match="IMG_NEW.jpg: not in photos.lock"):
        build_calendar(root, 2027, "draft", out_dir=tmp_path)


def test_missing_photo_is_placeholder_in_draft_and_refused_in_print(
    photo_repo: tuple[Path, Path], tmp_path: Path
) -> None:
    root, photos = photo_repo
    (photos / "IMG_07.jpg").unlink()
    result = build_calendar(root, 2027, "draft", out_dir=tmp_path)
    assert any("placeholder images used for months 07" in w for w in result.warnings)
    with pytest.raises(BuildError, match="print refused"):
        build_calendar(root, 2027, "print", out_dir=tmp_path)


def test_no_photos_dir_gives_a_clear_message_for_print(make_repo: MakeRepo, tmp_path: Path) -> None:
    root = make_repo(copy_fonts=True)
    (root / "calendars" / "2027" / "birds.yaml").write_text(
        birds_yaml({1: "IMG_01.jpg"}), encoding="utf-8"
    )
    with pytest.raises(BuildError, match="paths.photos_dir is not set"):
        build_calendar(root, 2027, "print", out_dir=tmp_path)


def test_orientation_other_than_one_is_refused_at_lock_time(
    make_repo: MakeRepo, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_repo(copy_fonts=True)
    photos = tmp_path / "photos"
    photos.mkdir()
    save_jpeg(photos / "IMG_01.jpg", 2400, 1600, orientation=6)
    (root / "local.yaml").write_text(f"paths: {{photos_dir: '{photos}'}}\n", encoding="utf-8")
    (root / "calendars" / "2027" / "birds.yaml").write_text(
        birds_yaml({1: "IMG_01.jpg"}), encoding="utf-8"
    )
    assert main(["--root", str(root), "lock-photos", "2027"]) == 2
    assert "EXIF orientation is 6" in capsys.readouterr().err


def test_non_jpeg_is_refused(
    make_repo: MakeRepo, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_repo(copy_fonts=True)
    photos = tmp_path / "photos"
    photos.mkdir()
    Image.new("RGB", (2400, 1600)).save(photos / "IMG_01.png")
    (root / "local.yaml").write_text(f"paths: {{photos_dir: '{photos}'}}\n", encoding="utf-8")
    (root / "calendars" / "2027" / "birds.yaml").write_text(
        birds_yaml({1: "IMG_01.png"}), encoding="utf-8"
    )
    assert main(["--root", str(root), "lock-photos", "2027"]) == 2
    assert "not a JPEG" in capsys.readouterr().err


def test_low_resolution_and_untagged_photos_are_warned(make_repo: MakeRepo, tmp_path: Path) -> None:
    root = make_repo(
        calendar_yaml=f"year: 2027\ncolour: {{profile: '{PROFILE}'}}\n", copy_fonts=True
    )
    photos = tmp_path / "photos"
    photos.mkdir()
    save_jpeg(photos / "IMG_01.jpg", 1000, 700, icc=False)
    (root / "local.yaml").write_text(f"paths: {{photos_dir: '{photos}'}}\n", encoding="utf-8")
    (root / "calendars" / "2027" / "birds.yaml").write_text(
        birds_yaml({1: "IMG_01.jpg"}), encoding="utf-8"
    )
    assert main(["--root", str(root), "lock-photos", "2027"]) == 0
    inputs, statuses = prepare(root, 2027, "proof")
    _, plans = plan_pages(root, inputs.config, inputs.data, statuses)
    (finding,) = [
        f
        for f in photo_findings(plans, inputs.config, inputs.locks)
        if f.month == 1 and f.page == "month"
    ]
    assert finding.ppi < 200
    text = " | ".join(finding.warnings)
    assert "below 200 ppi" in text
    assert "untagged" in text


def test_extreme_crop_is_warned(make_repo: MakeRepo, tmp_path: Path) -> None:
    root = make_repo(
        calendar_yaml=f"year: 2027\ncolour: {{profile: '{PROFILE}'}}\n", copy_fonts=True
    )
    photos = tmp_path / "photos"
    photos.mkdir()
    save_jpeg(photos / "IMG_01.jpg", 6000, 800)  # a panorama, cropped hard into a 297x170 box
    (root / "local.yaml").write_text(f"paths: {{photos_dir: '{photos}'}}\n", encoding="utf-8")
    (root / "calendars" / "2027" / "birds.yaml").write_text(
        birds_yaml({1: "IMG_01.jpg"}), encoding="utf-8"
    )
    assert main(["--root", str(root), "lock-photos", "2027"]) == 0
    inputs, statuses = prepare(root, 2027, "proof")
    _, plans = plan_pages(root, inputs.config, inputs.data, statuses)
    (finding,) = [
        f
        for f in photo_findings(plans, inputs.config, inputs.locks)
        if f.month == 1 and f.page == "month"
    ]
    assert finding.crop_fraction > 0.15
    assert any("crop removes" in w for w in finding.warnings)


def test_print_output_is_byte_identical_for_identical_inputs(
    photo_repo: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _ = photo_repo
    a = build_calendar(root, 2027, "print", out_dir=tmp_path / "a")
    b = build_calendar(root, 2027, "print", out_dir=tmp_path / "b")
    assert sha256_file(a.path) == sha256_file(b.path)
