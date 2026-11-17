from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from pydantic import ValidationError

from birdcal.config import ConfigError
from birdcal.data import (
    BirdFile,
    Event,
    HolidayFile,
    YearData,
    load_year_data,
    validate_year_data,
)


def _data_2027(repo_root: Path) -> YearData:
    return load_year_data(repo_root / "calendars" / "2027", 2027)


def test_2027_data_loads_with_no_errors(repo_root: Path) -> None:
    issues = validate_year_data(_data_2027(repo_root))
    assert [i for i in issues if i.level == "error"] == []


def test_2027_holiday_weekday_matches_date_for_every_entry(repo_root: Path) -> None:
    for holiday in _data_2027(repo_root).holidays.holidays:
        assert holiday.weekday == holiday.date.strftime("%A"), holiday


def test_2027_holidays_are_flagged_as_unverified(repo_root: Path) -> None:
    data = _data_2027(repo_root)
    assert data.holidays.status == "proposed"
    issues = validate_year_data(data)
    assert any("not yet verified" in i.message for i in issues)


def _holiday_file(entries: list[dict[str, object]], status: str = "proposed") -> HolidayFile:
    return HolidayFile.model_validate(
        {"year": 2027, "region": "AU-VIC", "status": status, "holidays": entries}
    )


def _with_holidays(repo_root: Path, holidays: HolidayFile) -> YearData:
    base = _data_2027(repo_root)
    return YearData(year=2027, birds=base.birds, holidays=holidays, events=base.events)


def test_wrong_weekday_is_an_error(repo_root: Path) -> None:
    bad = _holiday_file([{"date": "2027-01-01", "name": "New Year", "weekday": "Thursday"}])
    issues = validate_year_data(_with_holidays(repo_root, bad))
    errors = [i for i in issues if i.level == "error"]
    assert len(errors) == 1
    assert "is a Friday" in errors[0].message


def test_duplicate_holiday_date_is_an_error(repo_root: Path) -> None:
    dup = _holiday_file(
        [
            {"date": "2027-01-01", "name": "A", "weekday": "Friday"},
            {"date": "2027-01-01", "name": "B", "weekday": "Friday"},
        ]
    )
    issues = validate_year_data(_with_holidays(repo_root, dup))
    assert any(i.level == "error" and "duplicate" in i.message for i in issues)


def test_verified_status_requires_source_and_date() -> None:
    with pytest.raises(ValidationError, match="requires both"):
        _holiday_file([], status="verified")


def test_event_needs_exactly_one_of_date_or_month_day() -> None:
    with pytest.raises(ValidationError):
        Event.model_validate({"label": "x"})
    with pytest.raises(ValidationError):
        Event.model_validate({"label": "x", "date": "2027-05-14", "month": 5, "day": 14})
    with pytest.raises(ValidationError):
        Event.model_validate({"label": "x", "month": 5})


def test_recurring_event_resolves_to_year_and_rejects_feb_29_in_non_leap_year() -> None:
    birthday = Event.model_validate({"label": "Mum", "month": 5, "day": 14})
    assert birthday.resolve(2027) == dt.date(2027, 5, 14)
    leap = Event.model_validate({"label": "Leap", "month": 2, "day": 29})
    assert leap.resolve(2028) == dt.date(2028, 2, 29)
    with pytest.raises(ValueError, match="does not exist in 2027"):
        leap.resolve(2027)


def test_birds_need_exactly_one_entry_per_month() -> None:
    entries = [{"month": m} for m in range(1, 12)]  # eleven, December missing
    with pytest.raises(ValidationError, match="exactly one entry per month"):
        BirdFile.model_validate({"year": 2027, "birds": entries})


def test_focal_point_must_be_within_image() -> None:
    entry = {
        "month": 1,
        "common_name": "Rainbow Lorikeet",
        "photo": {"file": "a.jpg", "focal_point": [1.5, 0.5]},
    }
    with pytest.raises(ValidationError, match="between 0 and 1"):
        BirdFile.model_validate(
            {"year": 2027, "birds": [entry] + [{"month": m} for m in range(2, 13)]}
        )


def test_year_mismatch_in_data_file_is_a_config_error(tmp_path: Path, repo_root: Path) -> None:
    src = repo_root / "calendars" / "2027"
    dst = tmp_path / "2027"
    dst.mkdir()
    for name in ("birds.yaml", "events.yaml"):
        (dst / name).write_text((src / name).read_text(encoding="utf-8"), encoding="utf-8")
    (dst / "holidays.yaml").write_text(
        (src / "holidays.yaml").read_text(encoding="utf-8").replace("year: 2027", "year: 2028", 1),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="year is 2028, expected 2027"):
        load_year_data(dst, 2027)
