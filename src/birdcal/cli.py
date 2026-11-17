"""Command line interface.

Stage 1 provides ``validate``. Later stages add check-photos, lock-photos, build,
release and reproduce.

Exit codes: 0 ok (warnings allowed), 1 validation errors, 2 configuration could not be loaded.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from birdcal.config import Config, ConfigError, LoadedConfig, load_config
from birdcal.data import Issue, load_year_data, validate_year_data

EXIT_OK = 0
EXIT_VALIDATION = 1
EXIT_CONFIG = 2


def find_repo_root(start: Path) -> Path:
    """Walk up from ``start`` to the directory holding config/defaults.yaml."""
    for candidate in (start, *start.parents):
        if (candidate / "config" / "defaults.yaml").exists():
            return candidate
    raise ConfigError(f"no config/defaults.yaml found at or above {start}")


def font_issues(config: Config, root: Path) -> list[Issue]:
    issues: list[Issue] = []
    for role, font in config.fonts.model_dump().items():
        path = root / font["file"]
        if not path.is_file():
            issues.append(Issue("error", f"fonts.{role}", f"font file not found: {font['file']}"))
    return issues


def report_unconfirmed(loaded: LoadedConfig) -> None:
    """Print every printer placeholder, its effective value and where it came from."""
    if loaded.config.printer_spec_confirmed:
        print("printer_spec_confirmed: true")
    else:
        print("printer_spec_confirmed: false (release is blocked)")
    print(f"Unconfirmed printer values ({len(loaded.unconfirmed)}):")
    for item in loaded.unconfirmed:
        print(f"  {item.key} = {item.value!r}   [from {item.layer}]")


def cmd_validate(root: Path, year: int) -> int:
    try:
        loaded = load_config(root, year)
    except (ConfigError, NotImplementedError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    report_unconfirmed(loaded)

    calendar_dir = root / "calendars" / str(year)
    try:
        data = load_year_data(calendar_dir, year)
    except ConfigError as exc:
        print(f"data error: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    issues = validate_year_data(data) + font_issues(loaded.config, root)
    print()
    if not issues:
        print(f"{year}: no issues")
    for issue in issues:
        print(issue)

    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]
    print()
    print(f"{year}: {len(errors)} error(s), {len(warnings)} warning(s)")
    return EXIT_VALIDATION if errors else EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="birdcal", description="Bird photo calendar builder")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="repository root (default: nearest ancestor of the current directory with config/)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="check configuration and data for a year")
    validate.add_argument("year", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = args.root.resolve() if args.root else find_repo_root(Path.cwd().resolve())
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    if args.command == "validate":
        return cmd_validate(root, args.year)
    raise AssertionError(f"unhandled command {args.command!r}")  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
