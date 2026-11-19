# birdcal

Builds a print-ready wall calendar PDF from bird photographs. Landscape sheets, bound along
the top long edge and duplex printed. Each photo fills a whole page, and each month's calendar
grid is on the page after the photo, so every sheet's back shows the next month's photo.
Year-agnostic: the same code builds any year from `calendars/<year>/`.

**Status:** all planned stages are built: config, layout, photo pipeline, release and
reproduce. The first real print needs the printer spec confirmed and the holiday list verified.

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
uv run birdcal validate 2027                 # check config and data; lists printer placeholders
uv run birdcal lock-photos 2027              # hash the photos and write calendars/2027/photos.lock
uv run birdcal check-photos 2027             # PPI, crop and colour for each placed photo
uv run birdcal build 2027 --mode draft       # placeholders allowed; guides on
uv run birdcal build 2027 --mode proof       # real photos where present; guides on
uv run birdcal build 2027 --mode print       # real photos only; no guides
uv run birdcal release 2027                  # print PDF, build-info.json, annotated tag (never pushes)
uv run birdcal reproduce 2027 [--tag TAG]    # rebuild the latest (or given) tag and compare
```

Output goes to `build/<year>/` (git-ignored): `calendar-<year>-<mode>.pdf`, plus
`build-info.json` and `tag-message.txt` after a release.

Exit codes: `0` ok (warnings allowed), `1` validation, build or release refused, `2`
configuration could not be loaded.

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

Declared but not implemented in v1, and rejected with a clear error if enabled: `facing_pages`
layout, CMYK colour, left-edge binding, school term dates.

## Photos

- Photos stay out of git. Set `paths.photos_dir` in `local.yaml`.
- `calendars/<year>/photos.lock` records each photo's SHA-256, pixel size, colour profile and
  EXIF orientation. Every build checks the files against it. A changed or unlocked file is an
  error in every mode. A missing file is a placeholder in draft and proof, and refuses print.
- Photos must be 8-bit RGB JPEGs with EXIF orientation 1 (bake rotation in at export).
- The JPEG bytes are embedded unchanged (`DCTDecode`), cropped by a clip path, never
  resampled or upscaled. Each build checks the PDF's embedded streams against the source hashes
  and removes the output if they differ.
- `check-photos` warns when effective PPI is below `photo.ppi.warn_below` (200), or when a fill
  crop removes more than `photo.max_crop_fraction` (15%) of an axis. It also warns when the
  profile is not `colour.profile`.
- Colour: reportlab cannot carry the JPEG's ICC profile, so images are declared `DeviceRGB`.
  Export from Lightroom in sRGB. `colour.profile` must match the profile name `check-photos`
  reports.

## Release and reproduce

`release` refuses unless all of these hold: a clean git tree, `printer_spec_confirmed: true`,
the holiday list verified, validation without errors, and every print photo verified. It then
builds the print PDF, writes `build/<year>/build-info.json` (commit, config hash, `photos.lock`
hash, font hashes, `uv.lock` hash, Python and library versions, PDF SHA-256) and creates an
annotated tag: `calendar-<year>`, then `-r2`, `-r3`… for re-issues. The tag message holds the
PDF hash, the key print values and the full build-info, so any clone can check it.

`reproduce` checks out the tag in a temporary worktree and runs that tag's own code under its
`uv.lock` (`uv run --frozen`). It rebuilds and reports MATCH, or each field that differs.
Output is deterministic: the same inputs give the same bytes, and `SOURCE_DATE_EPOCH` sets the
PDF dates (default: 2000-01-01).

## Per-year data

| File | Contents |
|---|---|
| `birds.yaml` | Exactly one entry per month 1–12: names, description, location, date taken, credit, and `photo` (file, `fit`, `focal_point`). Empty slots are allowed and show placeholder text. |
| `holidays.yaml` | Public holidays with date, name and weekday (the weekday must match the date). `status: proposed` until verified against an official source. |
| `events.yaml` | Personal dates: one-off (`date:`) or recurring (`month:` and `day:`). |
| `back.yaml` | Text for the back of the calendar: two photographers (`name`, `bio`) and the donation `appeal`. Optional; missing text shows "to come" placeholders in draft and proof, and print is refused until it is complete. |

## Layout

- **Sheet:** A4-ish landscape (297 × 210 mm placeholder, exact size TBD), with 3 mm bleed and
  trim marks. Sheets are bound along the top, which is the long edge.
- **Two kinds of page:**
  - **Photo pages** are full bleed: the photo covers the whole page, bleed included. Holes are
    punched through the photo.
  - **Grid pages** keep `binding.keep_clear_mm` (17 mm) clear at the top for the binding holes, with the month
    grid under the strip, with the caption (bird name, scientific name, location, photo credit and
    a description of at most two lines) at the bottom of the
    page and the grid spanning the full width (`layout.caption.placement`).
- **Order:** the cover, then the cover's back (January's photo), then January's grid. Each grid
  page's back is the next month's photo, so February's photo is the back of January's grid.
  December's grid is followed by the back of the calendar: `sides.final_back_photo` if set, otherwise
  January's photo again.
- **Back text:** the last sheet's back carries the photographers' bios and the donation appeal, on a
  translucent white panel with dark text. The panel sits in the corner set by `back_text.position`
  (default top left), inside the safe margin, and clear of the hanger hole and the binding band.
  Text shrinks to its minimum size, then the build fails naming the field. Duplex only.
  The holiday page and its blank back follow when enabled. Single-sided prints one sheet per
  month.
- **Binding holes:** along the top edge of the cover and the grid pages, starting 5 mm from the edge
  (outer edge of each hole; `binding.holes.edge_offset_mm` is the centre, 7.4 mm), spaced by `pattern`. Every hole is kept. On the photo backs
  the photo stays upright, but the holes sit along the bottom edge, so they are at the bottom
  when the sheet is flipped up. The holes are shown only in draft and proof.
- **Hanger:** a hole at the centre of the top of every photo back (every second page), 4.5 mm
  down, above the binding row and never in it. The cover and grid pages have none.
  `hanger.pages` lists the page types that get one. The build refuses a hanger that overlaps a
  binding hole.
- **Duplex:** `sides.mode: duplex` with `duplex_flip: long_edge`. The binding is along the long
  edge, so each sheet turns over along its long edge, like a notepad page turned from the top.
- Grid options: Sunday (default) or Monday start, ISO week numbers, greyed adjacent days, weekend
  shading, auto (5 or 6) or fixed six rows, day names as `Mon`, `M` or `Monday`.
- Text shrinks to its minimum size, then the build fails naming the month and field. Text is
  never truncated.
- Photos are fill or fit per bird (`birds.yaml`). On a full-page photo, a 3:2 photo with `fill`
  crops about 6% of its width. `fit` shows the whole photo with white bars at the edges.

## Not yet built

- **`new-year <year>`:** scaffolds `calendars/<year>/` from the previous year with empty bird slots.
- **CMYK, `facing_pages`, left-edge binding, school terms:** declared, not implemented.
