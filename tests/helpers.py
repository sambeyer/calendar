"""Shared fixtures-as-functions for building synthetic photos and bird data."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageCms

MONTHS = range(1, 13)
PROFILE = "sRGB built-in"  # Pillow's built-in sRGB description; see README on profile names


def save_jpeg(
    path: Path,
    width: int,
    height: int,
    *,
    quality: int = 90,
    icc: bool = True,
    orientation: int | None = None,
) -> None:
    img = Image.linear_gradient("L").resize((width, height)).convert("RGB")
    kwargs: dict[str, object] = {"quality": quality}
    if icc:
        kwargs["icc_profile"] = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    if orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = orientation
        kwargs["exif"] = exif.tobytes()
    img.save(path, "JPEG", **kwargs)


def birds_yaml(files: dict[int, str], year: int = 2027) -> str:
    lines = [f"year: {year}", "birds:"]
    for month in MONTHS:
        lines += [
            f"  - month: {month}",
            f"    common_name: Bird {month}",
            f"    scientific_name: Avis {month}",
            "    description: A test bird.",
            "    location: Melbourne",
            "    photo_credit: Test photographer",
        ]
        if month in files:
            lines += [f"    photo: {{file: {files[month]}, fit: fill, focal_point: [0.5, 0.4]}}"]
        else:
            lines += ["    photo: null"]
    return "\n".join(lines) + "\n"
