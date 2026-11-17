from __future__ import annotations

import calendar
import datetime as dt
import itertools

import pytest

from birdcal.dates import (
    RowMode,
    WeekStart,
    column_weekdays,
    day_name,
    format_date_au,
    is_leap_year,
    month_grid,
    month_title,
    year_grids,
)

YEARS = range(2024, 2037)  # covers leap and non-leap years on both sides of 2027
WEEK_STARTS: tuple[WeekStart, ...] = ("monday", "sunday")
ROW_MODES: tuple[RowMode, ...] = ("auto", 6)


def test_anchor_1_jan_2027_is_friday() -> None:
    assert dt.date(2027, 1, 1).strftime("%A") == "Friday"


def test_anchor_feb_lengths() -> None:
    assert calendar.monthrange(2027, 2)[1] == 28
    assert calendar.monthrange(2028, 2)[1] == 29
    assert is_leap_year(2028) and not is_leap_year(2027)


def test_anchor_feb_2027_monday_start_is_four_rows() -> None:
    grid = month_grid(2027, 2, "monday", "auto")
    assert len(grid.weeks) == 4
    assert grid.weeks[0].days[0].date == dt.date(2027, 2, 1)


def test_anchor_jan_2027_monday_start_is_five_rows_and_iso_week_53() -> None:
    grid = month_grid(2027, 1, "monday", "auto")
    assert len(grid.weeks) == 5
    # Monday 28 Dec 2026 is in ISO week 53 of 2026 (2026 has 53 ISO weeks).
    assert grid.weeks[0].iso_week == 53


def test_aug_2027_monday_start_needs_six_rows_and_auto_reports_it() -> None:
    assert len(month_grid(2027, 8, "monday", "auto").weeks) == 6


@pytest.mark.parametrize("year", YEARS)
@pytest.mark.parametrize("week_start", WEEK_STARTS)
@pytest.mark.parametrize("rows", ROW_MODES)
@pytest.mark.parametrize("month", range(1, 13))
def test_every_date_appears_once_in_month_and_in_right_column(
    year: int, week_start: WeekStart, rows: RowMode, month: int
) -> None:
    grid = month_grid(year, month, week_start, rows)
    first_col = 0 if week_start == "monday" else 6
    expected = {dt.date(year, month, d) for d in range(1, calendar.monthrange(year, month)[1] + 1)}

    in_month = [d.date for w in grid.weeks for d in w.days if d.in_month]
    assert len(in_month) == len(expected)
    assert set(in_month) == expected

    for week in grid.weeks:
        assert len(week.days) == 7
        for col, day in enumerate(week.days):
            assert day.date.weekday() == (first_col + col) % 7, (
                f"{day.date} is in column {col} for {week_start}-start grid"
            )
    # Grid is contiguous: each day is the day after the previous one.
    flat = [d.date for w in grid.weeks for d in w.days]
    assert all(b - a == dt.timedelta(days=1) for a, b in itertools.pairwise(flat))


@pytest.mark.parametrize("year", YEARS)
@pytest.mark.parametrize("week_start", WEEK_STARTS)
def test_six_row_mode_always_has_six_weeks(year: int, week_start: WeekStart) -> None:
    for grid in year_grids(year, week_start, 6):
        assert len(grid.weeks) == 6


@pytest.mark.parametrize("year", YEARS)
def test_auto_rows_are_four_to_six_and_six_rows_only_when_needed(year: int) -> None:
    for grid in year_grids(year, "monday", "auto"):
        assert 4 <= len(grid.weeks) <= 6
        # Monday-start: cells needed = offset of the 1st from Monday + days in month.
        offset = dt.date(year, grid.month, 1).weekday()
        needed = offset + calendar.monthrange(year, grid.month)[1]
        assert len(grid.weeks) == -(-needed // 7)  # ceiling division


def test_sunday_start_monday_column_holds_iso_week() -> None:
    grid = month_grid(2027, 1, "sunday", "auto")
    assert column_weekdays("sunday")[0] == "Sunday"
    # Row 1 of a Sunday-start grid is 27 Dec 2026 to 2 Jan 2027; its Monday is 28 Dec.
    assert grid.weeks[0].days[1].date == dt.date(2026, 12, 28)
    assert grid.weeks[0].iso_week == 53


@pytest.mark.parametrize("week_start", WEEK_STARTS)
def test_week_number_is_iso_week_of_the_monday_in_each_row(week_start: WeekStart) -> None:
    monday_col = 0 if week_start == "monday" else 1
    for year in YEARS:
        for grid in year_grids(year, week_start, "auto"):
            for week in grid.weeks:
                monday = week.days[monday_col].date
                assert monday.weekday() == 0
                assert week.iso_week == monday.isocalendar().week


def test_day_name_formats() -> None:
    assert day_name("Monday", "short") == "Mon"
    assert day_name("Monday", "initial") == "M"
    assert day_name("Monday", "full") == "Monday"


def test_month_title_and_au_date_format() -> None:
    assert month_title(2027, 2) == "February 2027"
    assert format_date_au(dt.date(2027, 2, 28)) == "28/02/2027"


def test_month_outside_range_rejected() -> None:
    with pytest.raises(ValueError):
        month_grid(2027, 13, "monday", "auto")
