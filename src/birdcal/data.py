"""Per-year data: birds, holidays and personal events, plus their validation.

Each file is validated against a schema. Cross-checks (weekday vs date, year vs file,
one bird per month, duplicate dates) are returned as :class:`Issue` records so the
``validate`` command can report everything at once.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from birdcal.config import ConfigError
from birdcal.dates import is_leap_year, weekday_name


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


@dataclass(frozen=True)
class Issue:
    level: Literal["error", "warning"]
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.level.upper():7} {self.where}: {self.message}"


# --- birds -------------------------------------------------------------------


class PhotoSpec(_Model):
    file: str = Field(min_length=1)
    fit: Literal["fill", "fit"] = "fill"
    focal_point: tuple[float, float] | None = Field(
        default=None, description="x,y in 0..1 of the image; the subject to keep in frame"
    )
    border_mm: float | None = Field(default=None, ge=0)
    corner_radius_mm: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _focal_point_in_range(self) -> PhotoSpec:
        if self.focal_point is not None and not all(0 <= v <= 1 for v in self.focal_point):
            raise ValueError("focal_point values must be between 0 and 1")
        return self


class BirdEntry(_Model):
    month: int = Field(ge=1, le=12)
    common_name: str | None = None
    scientific_name: str | None = None
    description: str | None = None
    location: str | None = None
    date_taken: dt.date | None = None
    photo_credit: str | None = None
    photo: PhotoSpec | None = None

    def is_filled(self) -> bool:
        return bool(self.common_name and self.photo)


class BirdFile(_Model):
    year: int
    birds: list[BirdEntry]

    @model_validator(mode="after")
    def _one_entry_per_month(self) -> BirdFile:
        months = sorted(b.month for b in self.birds)
        if months != list(range(1, 13)):
            raise ValueError(f"birds must contain exactly one entry per month 1-12, got {months}")
        return self


class Photographer(_Model):
    name: str | None = None
    bio: str | None = Field(default=None, description="a few sentences about the photographer")


class BackFile(_Model):
    """Text for the back of the last sheet. Missing file = empty (placeholders shown)."""

    year: int
    photographers: list[Photographer] = Field(default_factory=list)
    appeal: str | None = Field(default=None, description="donation encouragement")


def photo_files(birds: BirdFile, *extra: str | None) -> list[str]:
    """Every photo file the calendar uses: each bird's photo plus any extra photos set."""
    names = {b.photo.file for b in birds.birds if b.photo is not None}
    names.update(name for name in extra if name)
    return sorted(names)


# --- holidays and events -----------------------------------------------------


class Holiday(_Model):
    date: dt.date
    name: str = Field(min_length=1)
    weekday: str = Field(min_length=1, description="full English weekday name, checked vs date")


class HolidayFile(_Model):
    year: int
    region: Literal["AU-VIC"]
    status: Literal["proposed", "verified"]
    source: str | None = None
    checked_on: dt.date | None = None
    holidays: list[Holiday]

    @model_validator(mode="after")
    def _verified_needs_source(self) -> HolidayFile:
        if self.status == "verified" and (not self.source or self.checked_on is None):
            raise ValueError("status 'verified' requires both 'source' and 'checked_on'")
        return self


class Event(_Model):
    label: str = Field(min_length=1)
    date: dt.date | None = None
    month: int | None = Field(default=None, ge=1, le=12)
    day: int | None = Field(default=None, ge=1, le=31)

    @model_validator(mode="after")
    def _date_xor_month_day(self) -> Event:
        has_date = self.date is not None
        has_md = self.month is not None or self.day is not None
        if has_date == has_md:
            raise ValueError("give either 'date' or 'month' and 'day', not both or neither")
        if has_md and (self.month is None or self.day is None):
            raise ValueError("'month' and 'day' must be given together")
        return self

    def resolve(self, year: int) -> dt.date:
        """Concrete date for ``year``. Recurring events use month/day."""
        if self.date is not None:
            return self.date
        assert self.month is not None and self.day is not None
        if self.month == 2 and self.day == 29 and not is_leap_year(year):
            raise ValueError(f"'{self.label}' is 29 Feb, which does not exist in {year}")
        return dt.date(year, self.month, self.day)


class EventFile(_Model):
    year: int
    events: list[Event]


# --- loading and validation ---------------------------------------------------


@dataclass(frozen=True)
class YearData:
    year: int
    birds: BirdFile
    holidays: HolidayFile
    events: EventFile
    back: BackFile


def _load(path: Path, model: type[_Model]) -> _Model:
    if not path.exists():
        raise ConfigError(f"{path}: file not found")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: YAML syntax error: {exc}") from exc
    try:
        return model.model_validate(raw or {})
    except ValidationError as exc:
        lines = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        raise ConfigError(f"{path}: invalid:\n  " + "\n  ".join(lines)) from exc


def load_year_data(calendar_dir: Path, year: int) -> YearData:
    """Load birds.yaml, holidays.yaml and events.yaml for ``year``."""
    back = BackFile(year=year)
    back_path = calendar_dir / "back.yaml"
    if back_path.exists():
        loaded = _load(back_path, BackFile)
        assert isinstance(loaded, BackFile)
        back = loaded
    data = YearData(
        year=year,
        birds=_load(calendar_dir / "birds.yaml", BirdFile),  # type: ignore[arg-type]
        holidays=_load(calendar_dir / "holidays.yaml", HolidayFile),  # type: ignore[arg-type]
        events=_load(calendar_dir / "events.yaml", EventFile),  # type: ignore[arg-type]
        back=back,
    )
    for label, file_year in (
        ("birds.yaml", data.birds.year),
        ("holidays.yaml", data.holidays.year),
        ("events.yaml", data.events.year),
        ("back.yaml", data.back.year),
    ):
        if file_year != year:
            raise ConfigError(f"{calendar_dir / label}: year is {file_year}, expected {year}")
    return data


def validate_year_data(data: YearData) -> list[Issue]:
    """Cross-file and semantic checks. Returns issues; does not raise."""
    issues: list[Issue] = []
    year = data.year

    # Holidays
    hol_where = f"holidays.yaml ({data.holidays.status})"
    if data.holidays.status == "proposed":
        issues.append(
            Issue(
                "warning",
                hol_where,
                "not yet verified against the official Victorian Government list; "
                "`release` will refuse until status is 'verified'",
            )
        )
    seen_dates: dict[dt.date, str] = {}
    for h in data.holidays.holidays:
        where = f"holidays.yaml {h.date.isoformat()} {h.name!r}"
        if h.date.year != year:
            issues.append(Issue("error", where, f"date is not in {year}"))
        actual = weekday_name(h.date)
        if h.weekday != actual:
            issues.append(
                Issue("error", where, f"weekday is {h.weekday!r} but {h.date} is a {actual}")
            )
        if h.date in seen_dates:
            issues.append(Issue("error", where, f"duplicate date, also {seen_dates[h.date]!r}"))
        seen_dates[h.date] = h.name

    # Events
    for ev in data.events.events:
        where = f"events.yaml {ev.label!r}"
        try:
            resolved = ev.resolve(year)
        except ValueError as exc:
            issues.append(Issue("error", where, str(exc)))
            continue
        if resolved.year != year:
            issues.append(Issue("error", where, f"date is not in {year}"))

    # Birds
    for bird in data.birds.birds:
        where = f"birds.yaml month {bird.month:02d}"
        if not bird.is_filled():
            issues.append(Issue("warning", where, "empty slot: no common_name or photo yet"))
            continue
        if bird.common_name is None or bird.photo is None:  # pragma: no cover - for type checker
            continue
        if bird.scientific_name is None:
            issues.append(Issue("warning", where, "scientific_name is not set"))
        if bird.date_taken is not None and bird.date_taken.year != year:
            issues.append(
                Issue("warning", where, f"date_taken {bird.date_taken} is outside {year}")
            )
    back = data.back
    if len(back.photographers) != 2:
        issues.append(
            Issue(
                "warning",
                "back.yaml",
                f"expected 2 photographers, found {len(back.photographers)}",
            )
        )
    for i, person in enumerate(back.photographers, start=1):
        if not person.name:
            issues.append(Issue("warning", f"back.yaml photographer {i}", "name is not set"))
        if not person.bio:
            issues.append(Issue("warning", f"back.yaml photographer {i}", "bio is not set"))
    if not back.appeal:
        issues.append(Issue("warning", "back.yaml", "appeal is not set"))
    return issues
