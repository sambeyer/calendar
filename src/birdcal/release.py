"""Release and reproduce.

``release`` builds the print PDF from a clean, committed state, writes ``build-info.json``
and creates an annotated tag whose message carries that info. It never pushes.

``reproduce`` checks out a tag into a temporary worktree, rebuilds, and compares the result
with the build-info recorded in the tag. It reports a match, or the differing fields.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from birdcal.build import BuildError, build_calendar, prepare
from birdcal.config import Config
from birdcal.photos import LOCK_FILENAME

TAG_PREFIX = "calendar"
INFO_BEGIN = "--- build-info (json) ---"
INFO_END = "--- end build-info ---"
LIBRARIES = ("pydantic", "pyyaml", "reportlab", "pypdf", "pillow")


class ReleaseError(Exception):
    """A release or reproduce step refused to continue. The message says why."""


def _git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise ReleaseError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def commit_sha(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD")


def is_dirty(root: Path) -> bool:
    """True if any tracked or untracked (non-ignored) file has changes."""
    return bool(_git(root, "status", "--porcelain", "--untracked-files=normal"))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def config_hash(cfg: Config) -> str:
    """Hash of the effective config. ``paths`` is excluded: it is machine-specific."""
    dump = cfg.model_dump(mode="json", exclude={"paths"})
    canonical = json.dumps(dump, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256_bytes(canonical)


def library_versions(root: Path) -> dict[str, str]:
    """Versions of the runtime libraries, read from the committed uv.lock."""
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    wanted = {name.lower() for name in LIBRARIES}
    found = {
        str(pkg["name"]): str(pkg["version"])
        for pkg in lock.get("package", [])
        if str(pkg["name"]).lower() in wanted
    }
    return dict(sorted(found.items()))


def build_info(root: Path, year: int, pdf: Path, cfg: Config) -> dict[str, Any]:
    calendar_dir = root / "calendars" / str(year)
    fonts = {
        Path(role_font["file"]).name: _sha256_file(root / role_font["file"])
        for role_font in cfg.fonts.model_dump().values()
    }
    lock_path = calendar_dir / LOCK_FILENAME
    return {
        "year": year,
        "commit": commit_sha(root),
        "config_sha256": config_hash(cfg),
        "photos_lock_sha256": _sha256_file(lock_path) if lock_path.exists() else None,
        "font_sha256": dict(sorted(fonts.items())),
        "uv_lock_sha256": _sha256_file(root / "uv.lock"),
        "python": platform.python_version(),
        "libraries": library_versions(root),
        "pdf_sha256": _sha256_file(pdf),
        "print": {
            "page_mm": [cfg.page.width_mm, cfg.page.height_mm],
            "bleed_mm": cfg.bleed_mm,
            "safe_margin_mm": cfg.safe_margin_mm,
            "trim_marks": cfg.trim_marks.enabled,
            "binding": cfg.binding.type,
            "binding_edge": cfg.binding.edge,
            "keep_clear_mm": cfg.binding.keep_clear_mm,
            "colour_mode": cfg.colour.mode,
            "colour_profile": cfg.colour.profile,
        },
    }


def next_tag_name(root: Path, year: int) -> str:
    """calendar-<year>, then calendar-<year>-r2, -r3, and so on."""
    existing = set(_git(root, "tag", "--list", f"{TAG_PREFIX}-{year}*").split())
    base = f"{TAG_PREFIX}-{year}"
    if base not in existing:
        return base
    n = 2
    while f"{base}-r{n}" in existing:
        n += 1
    return f"{base}-r{n}"


def _tag_message(name: str, info: dict[str, Any]) -> str:
    p = info["print"]
    lines = [
        f"{name}: print PDF sha256 {info['pdf_sha256']}",
        "",
        (
            f"page: {p['page_mm'][0]} x {p['page_mm'][1]} mm, bleed {p['bleed_mm']} mm, "
            f"safe margin {p['safe_margin_mm']} mm"
        ),
        f"binding: {p['binding']} on {p['binding_edge']}, keep-clear {p['keep_clear_mm']} mm",
        f"colour: {p['colour_mode']} / {p['colour_profile']}",
        "",
        INFO_BEGIN,
        json.dumps(info, indent=2, sort_keys=True),
        INFO_END,
        "",
    ]
    return "\n".join(lines)


@dataclass(frozen=True)
class ReleaseResult:
    tag: str
    pdf: Path
    info_path: Path
    info: dict[str, Any]


def check_release_gates(root: Path, year: int) -> Config:
    """Every reason a release must refuse. Returns the config if all gates pass."""
    if not (root / ".git").exists():
        raise ReleaseError("not a git repository: releases need a commit to tag")
    if is_dirty(root):
        raise ReleaseError("working tree has uncommitted changes; commit or stash first")
    try:
        inputs, _ = prepare(root, year, "print")
    except BuildError as exc:
        raise ReleaseError(str(exc)) from exc
    cfg = inputs.config
    if not cfg.printer_spec_confirmed:
        raise ReleaseError(
            "printer_spec_confirmed is false; confirm the printer spec in config first"
        )
    issues = [w for w in inputs.warnings if "not yet verified" in w]
    if issues:
        raise ReleaseError("holidays are not verified: " + issues[0])
    return cfg


def release(root: Path, year: int) -> ReleaseResult:
    """Build the print PDF, write build-info.json, and create an annotated tag. Never pushes."""
    cfg = check_release_gates(root, year)
    commit_sha(root)  # must have a commit
    result = build_calendar(root, year, "print")
    out_dir = result.path.parent
    info = build_info(root, year, result.path, cfg)
    info_path = out_dir / "build-info.json"
    info_path.write_text(json.dumps(info, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    name = next_tag_name(root, year)
    message_file = out_dir / "tag-message.txt"
    message_file.write_text(_tag_message(name, info), encoding="utf-8")
    _git(root, "tag", "-a", name, "-F", str(message_file))
    return ReleaseResult(tag=name, pdf=result.path, info_path=info_path, info=info)


def parse_tag_info(message: str) -> dict[str, Any]:
    pattern = re.compile(re.escape(INFO_BEGIN) + r"\n(.*?)\n" + re.escape(INFO_END), re.DOTALL)
    match = pattern.search(message)
    if match is None:
        raise ReleaseError("tag message has no build-info block")
    loaded: dict[str, Any] = json.loads(match.group(1))
    return loaded


def latest_tag(root: Path, year: int) -> str:
    tags = _git(root, "tag", "--list", f"{TAG_PREFIX}-{year}*").split()
    if not tags:
        raise ReleaseError(f"no calendar-{year} tags; run `birdcal release {year}` first")

    def revision(tag: str) -> int:
        m = re.search(r"-r(\d+)$", tag)
        return int(m.group(1)) if m else 1

    return max(tags, key=revision)


@dataclass(frozen=True)
class ReproduceResult:
    tag: str
    matched: bool
    differences: list[str]
    recorded: dict[str, Any]
    rebuilt: dict[str, Any]


def _diff(recorded: Any, rebuilt: Any, prefix: str = "") -> list[str]:
    if isinstance(recorded, dict) and isinstance(rebuilt, dict):
        out: list[str] = []
        for key in sorted(set(recorded) | set(rebuilt)):
            out += _diff(recorded.get(key), rebuilt.get(key), f"{prefix}{key}.")
        return out
    if recorded != rebuilt:
        return [f"{prefix.rstrip('.')}: recorded {recorded!r}, rebuilt {rebuilt!r}"]
    return []


def rebuild_in_tree(tree: Path, year: int) -> dict[str, Any]:
    """Run the tree's own code, under its own locked environment, and return its build-info.

    Runs ``birdcal _rebuild`` via ``uv run --frozen --project <tree>``, so the checked-out tag's
    source and uv.lock are what get executed, not whatever is installed here.
    """
    uv = shutil.which("uv")
    if uv is None:
        raise ReleaseError("reproduce needs uv on PATH to run the tag's locked environment")
    proc = subprocess.run(
        [
            uv,
            "run",
            "--frozen",
            "--project",
            str(tree),
            "birdcal",
            "--root",
            str(tree),
            "_rebuild",
            str(year),
        ],
        capture_output=True,
        text=True,
        cwd=tree,
        check=False,
    )
    if proc.returncode != 0:
        raise ReleaseError(f"rebuild in tag tree failed: {proc.stderr.strip()[-800:]}")
    rebuilt: dict[str, Any] = json.loads(proc.stdout)
    return rebuilt


def reproduce(root: Path, year: int, tag: str | None = None) -> ReproduceResult:
    """Rebuild ``tag`` (default: latest for the year) in a temporary worktree and compare."""
    tag = tag or latest_tag(root, year)
    message = _git(root, "tag", "-l", "--format=%(contents)", tag)
    recorded = parse_tag_info(message)
    photos_dir = load_photos_dir(root, year)

    tmp = Path(tempfile.mkdtemp(prefix="birdcal-repro-"))
    worktree = tmp / "tree"
    try:
        _git(root, "worktree", "add", "--detach", str(worktree), tag)
        if photos_dir is not None:
            # local.yaml is git-ignored, so the worktree needs the same photos location.
            (worktree / "local.yaml").write_text(
                f"paths: {{photos_dir: '{photos_dir}'}}\n", encoding="utf-8"
            )
        rebuilt = rebuild_in_tree(worktree, year)
    finally:
        _git(root, "worktree", "remove", "--force", str(worktree), check=False)
        shutil.rmtree(tmp, ignore_errors=True)

    differences = _diff(recorded, rebuilt)
    return ReproduceResult(
        tag=tag,
        matched=not differences,
        differences=differences,
        recorded=recorded,
        rebuilt=rebuilt,
    )


def load_photos_dir(root: Path, year: int) -> Path | None:
    from birdcal.config import load_config

    value = load_config(root, year).config.paths.photos_dir
    return Path(value).expanduser() if value else None
