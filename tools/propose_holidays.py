"""Propose AU-VIC public holidays for a year using the `holidays` library.

This writes a PROPOSAL only. The committed calendars/<year>/holidays.yaml is the
authoritative file and must be checked against the official Victorian Government list
before its status is changed to 'verified'. Run with:

    uv run python tools/propose_holidays.py 2027 > /path/to/proposal.yaml
"""

from __future__ import annotations

import sys

import holidays


def main(argv: list[str]) -> int:
    year = int(argv[1])
    found = holidays.country_holidays("AU", subdiv="VIC", years=year)
    print(
        f"# Proposed by tools/propose_holidays.py from the `holidays` library ({holidays.__version__})."
    )
    print("# Not verified against an official source.")
    print(f"year: {year}")
    print("region: AU-VIC")
    print("status: proposed")
    print("source: null")
    print("checked_on: null")
    print("holidays:")
    for day, name in sorted(found.items()):
        print(f"  - {{date: {day.isoformat()}, name: {name!r}, weekday: {day.strftime('%A')}}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
