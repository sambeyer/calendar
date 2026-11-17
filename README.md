# birdcal

Builds a print-ready wall calendar PDF from bird photographs. One PDF page per month
(photo over a calendar grid), plus an optional cover and back page. Year-agnostic: the same
code builds any year from `calendars/<year>/`.

**Status:** stages 1–2 are done (config, dates, layout, draft and proof builds). The
photo pipeline, `release` and `reproduce` are not built yet. See [Not yet built](#not-yet-built).

## Setup

Python 3.13 and [uv](https://docs.astral.sh/uv/). `pdftoppm` (poppler) is only needed to
render PNG previews.

```sh
uv sync
uv run pytest           # tests
uv run mypy             # type check (strict)
uv run ruff check       # lint
```

## Commands

```sh
uv run birdcal validate 2027                # check config and data; lists printer placeholders
uv run birdcal build 2027 --mode draft      # placeholder images, guides on
uv run birdcal build 2027 --mode proof      # guides on (placeholder images until stage 3)
uv run birdcal build 2027 --mode print      # refused until real photos exist
```

Output goes to `build/<year>/calendar-<year>-<mode>.pdf` (git-ignored).

Exit codes: `0` ok (warnings allowed), `1` validation or build error, `2` configuration
could not be loaded.

Use `--root PATH` to point at a different repository root.

## Configuration

Settings are layered, lowest precedence first:

1. `config/defaults.yaml`: repo-wide defaults, including every print-spec placeholder.
2. `calendars/<year>/calendar.yaml`: per-year overrides. Must contain `year: <year>`.
3. `local.yaml` (git-ignored): machine-specific values, e.g. `paths.photos_dir`.

Unknown keys are errors, so typos fail loudly. Every length is in millimetres unless the
key ends in `_pt`.

Values that depend on the printer are marked `# TODO(printer)` in `defaults.yaml`.
`validate` and `build` list each one with its effective value and the file it came from.
`printer_spec_confirmed` must be set to `true` once they are confirmed. Until then,
`release` will refuse to run.

Declared but not implemented in v1, and rejected with a clear error if enabled: duplex
printing, `facing_pages` layout, CMYK colour, left-edge binding, school term dates.

## Per-year data

| File | Contents |
|---|---|
| `birds.yaml` | Exactly one entry per month 1–12: names, description, location, date taken, credit, and `photo` (file, `fit`, `focal_point`). Empty slots are allowed and show placeholder text. |
| `holidays.yaml` | Public holidays with date, name and weekday (the weekday must match the date). `status: proposed` until verified against an official source. |
| `events.yaml` | Personal dates: one-off (`date:`) or recurring (`month:` and `day:`). |

## Layout

- Content sits inside `safe_margin_mm`, below the binding keep-clear zone (`binding.keep_clear_mm`).
- Caption (bird name, scientific name, description, credit) goes below or beside the grid.
- Grid options: Monday or Sunday start, ISO week numbers, greyed adjacent days, weekend
  shading, auto (5 or 6) or fixed six rows, day names as `Mon`, `M` or `Monday`.
- Text shrinks to its minimum size, then the build fails naming the month and field. Text is
  never truncated.
- Guides (trim, bleed, safe area, keep-clear zone, binding holes, hanger) appear only in
  `draft` and `proof`. Trim marks, if enabled, appear in every mode.

## Not yet built

- **Photo pipeline:** `photos.lock` (SHA-256, pixel size and colour profile per photo),
  `check-photos` (effective PPI, crop safety), `lock-photos`, and lossless embedding of the
  original JPEG bytes with verification.
- **`release <year>`:** refuses unless `printer_spec_confirmed` is true, the holiday list is
  verified, the working tree is clean and validation passes. Then builds the print PDF,
  writes `build-info.json`, and creates an annotated tag `calendar-<year>[-rN]`. It never pushes.
- **`reproduce <year>`:** rebuilds a tag in a temporary worktree and compares SHA-256.
- **`new-year <year>`:** scaffolds `calendars/<year>/` from the previous year with empty bird slots.
- Deterministic output (`SOURCE_DATE_EPOCH`) and a regression hash test.
