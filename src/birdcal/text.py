"""Font registration and text fitting.

Fitting rule: start at the role's size, shrink in 0.25 pt steps down to ``min_size_pt``,
and take the first size where the wrapped text fits the box. If nothing fits, raise
:class:`TextFitError`. Text is never truncated or allowed to overflow.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from birdcal.config import FontRole, Fonts

LEADING = 1.2  # line height as a multiple of the font size
SHRINK_STEP_PT = 0.25


class TextFitError(Exception):
    """Text does not fit its box even at the minimum size."""


@dataclass(frozen=True)
class FittedText:
    lines: tuple[str, ...]
    size_pt: float
    font: str

    @property
    def height_mm(self) -> float:
        return len(self.lines) * self.size_pt * LEADING / 72 * 25.4


def font_name(role_font: FontRole) -> str:
    """Stable registry name derived from the font file, so shared files register once."""
    return "birdcal-" + Path(role_font.file).stem


def register_fonts(fonts: Fonts, root: Path) -> dict[str, str]:
    """Register every configured font file. Returns role name -> registry name."""
    registered: dict[str, str] = {}
    for role, role_font in fonts.model_dump().items():
        spec = FontRole.model_validate(role_font)
        name = font_name(spec)
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(root / spec.file)))
        registered[role] = name
    return registered


def string_width_mm(text: str, font: str, size_pt: float) -> float:
    return float(pdfmetrics.stringWidth(text, font, size_pt)) / 72 * 25.4


def wrap(text: str, font: str, size_pt: float, max_width_mm: float) -> list[str]:
    """Greedy word wrap. Explicit newlines start a new line. A single word wider than the
    box is returned as-is so the caller's fit check fails rather than hiding it."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if string_width_mm(candidate, font, size_pt) <= max_width_mm:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def candidate_sizes(start: float, floor: float) -> list[float]:
    """Sizes tried in order: start, then shrinking by the step, always ending at the floor."""
    sizes: list[float] = []
    size = start
    while size > floor + 1e-9:
        sizes.append(size)
        size -= SHRINK_STEP_PT
    sizes.append(floor)
    return sizes


def fit_text(
    text: str,
    role_font: FontRole,
    registry_name: str,
    max_width_mm: float,
    max_height_mm: float,
    *,
    where: str,
) -> FittedText:
    """Fit ``text`` into the box, shrinking the font as needed. Raises TextFitError."""
    if not text.strip():
        return FittedText(lines=(), size_pt=role_font.size_pt, font=registry_name)
    floor = role_font.min_size_pt if role_font.min_size_pt is not None else role_font.size_pt
    last_height = 0.0
    for size in candidate_sizes(role_font.size_pt, floor):
        lines = wrap(text, registry_name, size, max_width_mm)
        fitted = FittedText(lines=tuple(lines), size_pt=size, font=registry_name)
        longest = max(string_width_mm(line, registry_name, size) for line in lines)
        last_height = fitted.height_mm
        if longest <= max_width_mm + 1e-9 and last_height <= max_height_mm + 1e-9:
            return fitted
    raise TextFitError(
        f"{where}: text does not fit at minimum size {floor} pt "
        f"(needs {last_height:.1f} mm high, {max_height_mm:.1f} mm available): {text[:60]!r}"
    )
