"""Date engine: month grids, week numbers and en-AU formatting.

Only the standard library is used. Names are hard-coded for en-AU so output never
depends on the machine's locale. Dates are never typed by hand: every grid is derived
from ``calendar`` and ``datetime``.

Week numbers: the ISO-8601 week number of the Monday that falls in each row. That Monday
is always present in every row, whichever day the week starts on, so the convention is
the same for Monday- and Sunday-start grids.
"""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass
from typing import Literal

WeekStart = Literal["monday", "sunday"]
RowMode = Literal["auto", 6]
DayNameFormat = Literal["short", "initial", "full"]

# Monday-first, matching datetime.weekday(). Index 0 is Monday.
WEEKDAY_NAMES_EN_AU: tuple[str, ...] = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
MONTH_NAMES_EN_AU: tuple[str, ...] = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
SATURDAY = 5
SUNDAY = 6


@dataclass(frozen=True)
class Day:
    date: dt.date
    in_month: bool


@dataclass(frozen=True)
class Week:
    days: tuple[Day, ...]
    iso_week: int


@dataclass(frozen=True)
class MonthGrid:
    year: int
    month: int
    week_start: WeekStart
    weeks: tuple[Week, ...]

    @property
    def column_names(self) -> tuple[str, ...]:
        """Full English weekday names in column order (Monday- or Sunday-first)."""
        return column_weekdays(self.week_start)


def column_weekdays(week_start: WeekStart) -> tuple[str, ...]:
    """Full weekday names in grid column order."""
    names = WEEKDAY_NAMES_EN_AU
    if week_start == "sunday":
        return (names[SUNDAY], *names[:SUNDAY])
    return names


def day_name(name: str, fmt: DayNameFormat) -> str:
    """Format a full weekday name as 'Mon' / 'M' / 'Monday'."""
    if fmt == "full":
        return name
    if fmt == "short":
        return name[:3]
    return name[0]


def month_name(month: int) -> str:
    """en-AU month name for ``month`` (1-12)."""
    if not 1 <= month <= 12:
        raise ValueError(f"month must be 1-12, got {month}")
    return MONTH_NAMES_EN_AU[month - 1]


def month_title(year: int, month: int) -> str:
    """Title such as 'February 2027'."""
    return f"{month_name(month)} {year}"


def format_date_au(date: dt.date) -> str:
    """Numeric en-AU date, dd/mm/yyyy."""
    return date.strftime("%d/%m/%Y")


def is_weekend(date: dt.date) -> bool:
    return date.weekday() in (SATURDAY, SUNDAY)


def _first_weekday(week_start: WeekStart) -> int:
    """``calendar`` first-weekday index: Monday=0, Sunday=6."""
    return 0 if week_start == "monday" else SUNDAY


def month_grid(year: int, month: int, week_start: WeekStart, rows: RowMode) -> MonthGrid:
    """Build the grid for one month.

    ``rows="auto"`` returns only the weeks the month touches (4 to 6).
    ``rows=6`` always returns six weeks, continuing into the following month.
    """
    if rows not in ("auto", 6):
        raise ValueError(f"rows must be 'auto' or 6, got {rows!r}")
    cal = calendar.Calendar(firstweekday=_first_weekday(week_start))
    raw_weeks: list[list[dt.date]] = [list(w) for w in cal.monthdatescalendar(year, month)]

    if rows == 6:
        next_day = raw_weeks[-1][-1] + dt.timedelta(days=1)
        while len(raw_weeks) < 6:
            raw_weeks.append([next_day + dt.timedelta(days=i) for i in range(7)])
            next_day += dt.timedelta(days=7)

    weeks: list[Week] = []
    for raw in raw_weeks:
        days = tuple(Day(d, in_month=(d.year == year and d.month == month)) for d in raw)
        monday = next(d for d in raw if d.weekday() == 0)
        weeks.append(Week(days=days, iso_week=monday.isocalendar().week))
    return MonthGrid(year=year, month=month, week_start=week_start, weeks=tuple(weeks))


def year_grids(year: int, week_start: WeekStart, rows: RowMode) -> tuple[MonthGrid, ...]:
    """Grids for all twelve months of ``year``, January first."""
    return tuple(month_grid(year, m, week_start, rows) for m in range(1, 13))


def is_leap_year(year: int) -> bool:
    return calendar.isleap(year)


def weekday_name(date: dt.date) -> str:
    """Full English weekday name for ``date``, e.g. 'Friday'."""
    return WEEKDAY_NAMES_EN_AU[date.weekday()]
