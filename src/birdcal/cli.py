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


def cmd_build(root: Path, year: int, mode: str) -> int:
    from birdcal.build import BuildError, build_calendar

    try:
        loaded = load_config(root, year)
    except (ConfigError, NotImplementedError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    report_unconfirmed(loaded)
    print()
    try:
        result = build_calendar(root, year, mode)  # type: ignore[arg-type]
    except BuildError as exc:
        print(f"build failed: {exc}", file=sys.stderr)
        return EXIT_VALIDATION
    for warning in result.warnings:
        print(warning if warning.startswith(("WARNING", "ERROR")) else f"WARNING {warning}")
    print(f"wrote {result.path} ({result.pages} pages, mode={result.mode})")
    return EXIT_OK


def _photos_dir(root: Path, year: int) -> Path:
    from birdcal.config import load_config as _load

    cfg = _load(root, year).config
    if not cfg.paths.photos_dir:
        raise ConfigError("paths.photos_dir is not set; add it to local.yaml")
    return Path(cfg.paths.photos_dir).expanduser()


def cmd_lock_photos(root: Path, year: int) -> int:
    from birdcal.data import load_year_data, photo_files
    from birdcal.photos import LOCK_FILENAME, PhotoError, lock_photos

    try:
        photos_dir = _photos_dir(root, year)
        calendar_dir = root / "calendars" / str(year)
        cfg = load_config(root, year).config
        data = load_year_data(calendar_dir, year)
        files = photo_files(data.birds, cfg.pages.cover_photo, cfg.sides.final_back_photo)
        records = lock_photos(photos_dir, files, calendar_dir / LOCK_FILENAME, year)
    except (ConfigError, PhotoError) as exc:
        print(f"lock failed: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    for r in records:
        profile = r.colour_profile or "untagged"
        print(f"  {r.file}  {r.width}x{r.height}  {profile}  sha256 {r.sha256[:12]}...")
    print(f"wrote calendars/{year}/{LOCK_FILENAME} ({len(records)} photos)")
    return EXIT_OK


def cmd_check_photos(root: Path, year: int) -> int:
    from birdcal.build import BuildError, photo_findings, plan_pages, prepare

    try:
        inputs, statuses = prepare(root, year, "proof")
    except BuildError as exc:
        print(f"check failed: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    for status in statuses.values():
        if status.problem:
            print(f"  {status.file}: {status.problem}")
    _, plans = plan_pages(root, inputs.config, inputs.data, statuses)
    findings = photo_findings(plans, inputs.config, inputs.locks)
    warned = 0
    print(
        f"{'month':>5}  {'file':<24} {'px':>13}  {'box mm':>13}  {'ppi':>6}  {'crop':>5}  profile"
    )
    for f in findings:
        print(
            f"{f.month:>5}  {f.file:<24} {f.px_w:>6}x{f.px_h:<6} "
            f"{f.box_w_mm:>6.0f}x{f.box_h_mm:<6.0f} {f.ppi:>6.0f}  {f.crop_fraction:>5.0%}  "
            f"{f.colour_profile or 'untagged'}"
        )
        for w in f.warnings:
            warned += 1
            print(f"       WARNING {w}")
    print(f"{len(findings)} photo(s) placed, {warned} warning(s)")
    return EXIT_OK


def cmd_rebuild(root: Path, year: int) -> int:
    """Internal: build the print PDF and print its build-info as JSON (used by reproduce)."""
    import json

    from birdcal.build import BuildError as _BuildError
    from birdcal.build import build_calendar as _build
    from birdcal.release import build_info

    try:
        result = _build(root, year, "print")
        cfg = load_config(root, year).config
        info = build_info(root, year, result.path, cfg)
    except (_BuildError, ConfigError) as exc:
        print(f"rebuild failed: {exc}", file=sys.stderr)
        return EXIT_VALIDATION
    print(json.dumps(info, sort_keys=True))
    return EXIT_OK


def cmd_release(root: Path, year: int) -> int:
    from birdcal.release import ReleaseError, release

    try:
        result = release(root, year)
    except ReleaseError as exc:
        print(f"release refused: {exc}", file=sys.stderr)
        return EXIT_VALIDATION
    print(f"tagged {result.tag} (not pushed)")
    print(f"pdf:  {result.pdf}")
    print(f"info: {result.info_path}")
    print(f"pdf sha256: {result.info['pdf_sha256']}")
    return EXIT_OK


def cmd_reproduce(root: Path, year: int, tag: str | None) -> int:
    from birdcal.release import ReleaseError, reproduce

    try:
        result = reproduce(root, year, tag)
    except ReleaseError as exc:
        print(f"reproduce failed: {exc}", file=sys.stderr)
        return EXIT_VALIDATION
    if result.matched:
        print(
            f"MATCH: {result.tag} rebuilds byte-for-byte (pdf sha256 "
            f"{result.recorded['pdf_sha256']})"
        )
        return EXIT_OK
    print(f"MISMATCH: {result.tag} differs from its recorded build-info:")
    for line in result.differences:
        print(f"  {line}")
    return EXIT_VALIDATION


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
    lock = sub.add_parser("lock-photos", help="hash photos and write calendars/<year>/photos.lock")
    lock.add_argument("year", type=int)
    rel = sub.add_parser(
        "release", help="build the print PDF, write build-info, tag (never pushes)"
    )
    rel.add_argument("year", type=int)
    rep = sub.add_parser("reproduce", help="rebuild a calendar tag and compare with its build-info")
    rep.add_argument("year", type=int)
    rep.add_argument("--tag", default=None, help="tag to rebuild (default: latest for the year)")
    rebuild = sub.add_parser("_rebuild", help=argparse.SUPPRESS)
    rebuild.add_argument("year", type=int)
    check = sub.add_parser("check-photos", help="report PPI, crop and colour for each placed photo")
    check.add_argument("year", type=int)
    build = sub.add_parser("build", help="build the calendar PDF")
    build.add_argument("year", type=int)
    build.add_argument("--mode", choices=["draft", "proof", "print"], default="draft")
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
    if args.command == "build":
        return cmd_build(root, args.year, args.mode)
    if args.command == "lock-photos":
        return cmd_lock_photos(root, args.year)
    if args.command == "check-photos":
        return cmd_check_photos(root, args.year)
    if args.command == "release":
        return cmd_release(root, args.year)
    if args.command == "reproduce":
        return cmd_reproduce(root, args.year, args.tag)
    if args.command == "_rebuild":
        return cmd_rebuild(root, args.year)
    raise AssertionError(f"unhandled command {args.command!r}")  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
