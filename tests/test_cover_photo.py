from __future__ import annotations

from birdcal.data import BirdFile, photo_files


def _birds(files: dict[int, str]) -> BirdFile:
    entries = [
        {"month": m, "photo": {"file": files[m]} if m in files else None} for m in range(1, 13)
    ]
    return BirdFile.model_validate({"year": 2027, "birds": entries})


def test_photo_files_lists_each_bird_photo_once() -> None:
    birds = _birds({1: "jan.jpg", 2: "jan.jpg", 3: "mar.jpg"})
    assert photo_files(birds, None) == ["jan.jpg", "mar.jpg"]


def test_photo_files_adds_cover_photo_when_set() -> None:
    birds = _birds({1: "jan.jpg"})
    assert photo_files(birds, "cover.jpg") == ["cover.jpg", "jan.jpg"]


def test_cover_photo_already_used_by_a_bird_is_listed_once() -> None:
    birds = _birds({1: "jan.jpg"})
    assert photo_files(birds, "jan.jpg") == ["jan.jpg"]


def test_final_back_photo_is_listed_with_the_bird_photos() -> None:
    birds = _birds({1: "jan.jpg"})
    assert photo_files(birds, None, "back.jpg") == ["back.jpg", "jan.jpg"]
