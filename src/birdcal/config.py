"""Layered, schema-validated configuration.

Precedence (lowest to highest):
    config/defaults.yaml < calendars/<year>/calendar.yaml < local.yaml (git-ignored)

Every value that depends on the printer is marked ``# TODO(printer)`` in
``config/defaults.yaml``; :func:`load_config` reports the effective value and the
layer it came from for each of them on every load.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

TODO_PRINTER = "TODO(printer)"
DEFAULTS_RELPATH = Path("config/defaults.yaml")
LOCAL_RELPATH = Path("local.yaml")


class ConfigError(Exception):
    """Any configuration problem. The message names the file and the key."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- print spec -------------------------------------------------------------

Orientation = Literal["portrait", "landscape"]
BindingType = Literal["spiral", "wire_o", "saddle_stitch", "glue", "none"]
Edge = Literal["top", "left", "bottom", "middle"]
PageKind = Literal["cover", "photo_back", "month", "list", "blank"]


class Page(_Model):
    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)
    orientation: Orientation

    @model_validator(mode="after")
    def _orientation_matches_dimensions(self) -> Page:
        is_portrait = self.height_mm >= self.width_mm
        if (self.orientation == "portrait") != is_portrait:
            raise ValueError(
                f"orientation '{self.orientation}' contradicts "
                f"width_mm={self.width_mm} x height_mm={self.height_mm}"
            )
        return self


class TrimMarks(_Model):
    enabled: bool
    offset_mm: float = Field(ge=0)
    length_mm: float = Field(gt=0)
    line_width_pt: float = Field(gt=0)


class HolePattern(_Model):
    """Exactly one of pitch_mm, holes_per_inch or count must be set."""

    pitch_mm: float | None = Field(default=None, gt=0)
    holes_per_inch: float | None = Field(default=None, gt=0)
    count: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _exactly_one_spacing_rule(self) -> HolePattern:
        set_count = sum(v is not None for v in (self.pitch_mm, self.holes_per_inch, self.count))
        if set_count != 1:
            raise ValueError("set exactly one of pitch_mm, holes_per_inch or count")
        return self


class BindingHoles(_Model):
    diameter_mm: float = Field(gt=0)
    pattern: HolePattern
    edge_offset_mm: float = Field(gt=0, description="hole centre distance from the binding edge")
    end_margin_mm: float = Field(gt=0, description="hole centre distance from the ends of the edge")
    centre: bool = Field(description="centre the pattern along the edge instead of end_margin")


class Binding(_Model):
    type: BindingType
    edge: Edge
    keep_clear_mm: float = Field(ge=0)
    holes: BindingHoles | None = None

    @model_validator(mode="after")
    def _holes_required_for_punched_bindings(self) -> Binding:
        if self.type in ("spiral", "wire_o") and self.holes is None:
            raise ValueError(f"binding.holes is required when binding.type is '{self.type}'")
        return self


class Hanger(_Model):
    enabled: bool
    shape: Literal["hole", "notch"]
    diameter_mm: float = Field(gt=0)
    x_mm: float = Field(ge=0, description="centre, measured from the left trim edge")
    offset_mm: float = Field(ge=0, description="centre, measured from the top trim edge")
    pages: list[PageKind]


class Sides(_Model):
    mode: Literal["single", "duplex"]
    duplex_flip: Literal["long_edge", "short_edge"]
    back_content: Literal["blank", "next_month_photo"] = Field(
        description="what prints on the back of each month sheet"
    )
    final_back_photo: str | None = Field(
        default=None,
        min_length=1,
        description="file in photos_dir for the back of the last sheet (December); "
        "default: January's photo",
    )


class Colour(_Model):
    mode: Literal["rgb", "cmyk"]
    profile: str = Field(min_length=1)


# --- layout, photo, grid, text ----------------------------------------------


class Caption(_Model):
    placement: Literal["below_grid", "beside_grid"]


class Spacing(_Model):
    title_height_mm: float = Field(gt=0)
    header_height_mm: float = Field(gt=0)
    number_height_mm: float = Field(gt=0)
    gap_mm: float = Field(ge=0)
    caption_height_mm: float = Field(gt=0, description="caption block height when below the grid")
    caption_width_fraction: float = Field(gt=0, lt=1, description="caption share when beside")
    week_number_width_mm: float = Field(ge=0)


class Layout(_Model):
    mode: Literal["photo_over_grid", "facing_pages"]
    caption: Caption
    spacing: Spacing


class PpiPolicy(_Model):
    warn_below: float = Field(gt=0)
    good_at: float = Field(gt=0)

    @model_validator(mode="after")
    def _good_is_not_below_warn(self) -> PpiPolicy:
        if self.good_at < self.warn_below:
            raise ValueError("good_at must be >= warn_below")
        return self


class LowQuality(_Model):
    """Settings for ``build --low-quality``: photos are resampled and re-encoded."""

    ppi: float = Field(gt=0, description="resample photos to at most this effective PPI")
    jpeg_quality: int = Field(ge=1, le=100)


class PhotoDefaults(_Model):
    fit: Literal["fill", "fit"]
    border_mm: float = Field(ge=0)
    border_colour: str
    corner_radius_mm: float = Field(ge=0)
    ppi: PpiPolicy
    max_crop_fraction: float = Field(
        ge=0, lt=1, description="warn when fill crops more than this share of either axis"
    )
    low_quality: LowQuality


class HolidayBadge(_Model):
    """A rounded box behind the holiday names in a grid cell."""

    colour: str
    radius_mm: float = Field(ge=0)
    padding_mm: float = Field(ge=0)


class Grid(_Model):
    week_start: Literal["monday", "sunday"]
    week_numbers: bool
    show_adjacent_days: bool
    adjacent_day_colour: str
    weekend_shading: bool
    weekend_colour: str
    day_name_format: Literal["short", "initial", "full"]
    rows: Literal["auto", 6]
    notes_in_cells: bool
    line_width_pt: float = Field(gt=0)
    line_colour: str
    cell_padding_mm: float = Field(ge=0)
    holiday_badge: HolidayBadge


class FontRole(_Model):
    file: str = Field(min_length=1)
    size_pt: float = Field(gt=0)
    min_size_pt: float | None = Field(default=None, gt=0, description="shrink-to-fit floor")
    colour: str
    align: Literal["left", "centre", "right"]

    @model_validator(mode="after")
    def _min_not_above_size(self) -> FontRole:
        if self.min_size_pt is not None and self.min_size_pt > self.size_pt:
            raise ValueError("min_size_pt must not exceed size_pt")
        return self


class Fonts(_Model):
    month_title: FontRole
    day_numbers: FontRole
    day_names: FontRole
    bird_name: FontRole
    scientific_name: FontRole
    description: FontRole
    holiday_label: FontRole
    event_label: FontRole
    credit: FontRole
    back_heading: FontRole
    back_name: FontRole
    back_body: FontRole


class BackText(_Model):
    """Photographer bios and a donation appeal on the back of the last sheet."""

    enabled: bool
    position: Literal["top_left", "top_right", "bottom_left", "bottom_right"]
    width_mm: float = Field(gt=0)
    edge_mm: float = Field(ge=0, description="distance from the trim edge to the panel")
    padding_mm: float = Field(ge=0)
    gap_mm: float = Field(ge=0, description="space between the heading, names, bios and appeal")
    panel_colour: str
    panel_opacity: float = Field(ge=0, le=1, description="1 = solid panel behind the text")


class LabelStyle(_Model):
    prefix: str = ""
    suffix: str = ""


class Labels(_Model):
    holiday: LabelStyle
    event: LabelStyle


# --- top level ---------------------------------------------------------------


class Pages(_Model):
    cover: bool
    cover_month: int = Field(ge=1, le=12, description="whose photo is the cover")
    cover_photo: str | None = Field(
        default=None,
        min_length=1,
        description="file in photos_dir for the cover; default: the cover_month bird's photo",
    )
    back: bool


class Draft(_Model):
    placeholder_px_width: int = Field(ge=1)
    placeholder_px_height: int = Field(ge=1)


class SchoolTerms(_Model):
    enabled: bool


class Locale(_Model):
    language: Literal["en-AU"]
    holiday_region: Literal["AU-VIC"]


class Paths(_Model):
    photos_dir: str | None = None


class Config(_Model):
    year: int = Field(ge=2000, le=2100)
    printer_spec_confirmed: bool
    paths: Paths
    page: Page
    bleed_mm: float = Field(ge=0)
    safe_margin_mm: float = Field(ge=0)
    trim_marks: TrimMarks
    binding: Binding
    hanger: Hanger
    sides: Sides
    colour: Colour
    layout: Layout
    photo: PhotoDefaults
    grid: Grid
    fonts: Fonts
    back_text: BackText
    labels: Labels
    pages: Pages
    draft: Draft
    locale: Locale
    school_terms: SchoolTerms


# --- loading -----------------------------------------------------------------


@dataclass(frozen=True)
class UnconfirmedValue:
    key: str
    value: object
    layer: str


@dataclass(frozen=True)
class LoadedConfig:
    config: Config
    unconfirmed: tuple[UnconfirmedValue, ...]


def _read_yaml_mapping(path: Path, *, required: bool) -> dict[str, Any] | None:
    if not path.exists():
        if required:
            raise ConfigError(f"{path}: file not found")
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: YAML syntax error: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    return data


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge mappings; lists and scalars in ``override`` replace ``base``."""
    out: dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _leaf_paths(data: dict[str, Any], prefix: str = "") -> list[str]:
    paths: list[str] = []
    for key, value in data.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            paths.extend(_leaf_paths(value, dotted + "."))
        else:
            paths.append(dotted)
    return paths


def _lookup(data: dict[str, Any], dotted: str) -> object:
    node: object = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


_KEY_RE = re.compile(r"^(\s*)([A-Za-z_][\w-]*):(\s|$)")


def find_todo_keys(yaml_text: str) -> list[str]:
    """Return dotted key paths of every line carrying ``# TODO(printer)``.

    Uses indentation to build each key's full path, so nested keys are reported
    as e.g. ``binding.holes.diameter_mm``. Keep one key per line in the defaults.
    """
    found: list[str] = []
    stack: list[tuple[int, str]] = []
    for line in yaml_text.splitlines():
        match = _KEY_RE.match(line)
        if match is None:
            continue
        indent = len(match.group(1))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, match.group(2)))
        if TODO_PRINTER in line:
            found.append(".".join(name for _, name in stack))
    return found


def _source_of(dotted: str, provenance: dict[str, str]) -> str:
    """Return the layer that last set ``dotted`` (or its nearest ancestor)."""
    parts = dotted.split(".") if dotted else []
    while parts:
        key = ".".join(parts)
        if key in provenance:
            return provenance[key]
        parts.pop()
    return "merged config"


def _format_error(err: Any, provenance: dict[str, str]) -> str:
    loc = ".".join(str(p) for p in err["loc"])
    source = _source_of(loc, provenance)
    return f"{source}: {loc or '(root)'}: {err['msg']}"


def check_supported(config: Config) -> None:
    """Refuse configurations that are declared in the schema but not built in v1."""
    if config.layout.mode == "facing_pages":
        raise NotImplementedError(
            "layout.mode: facing_pages is declared but not implemented in v1; use 'photo_over_grid'"
        )
    if config.colour.mode == "cmyk":
        raise NotImplementedError(
            "colour.mode: cmyk is a stretch goal and not implemented in v1; use 'rgb'"
        )
    if config.binding.edge == "left":
        raise NotImplementedError(
            "binding.edge: left is not implemented in v1 (needs a landscape hole pattern); "
            "use top or bottom"
        )
    if config.school_terms.enabled:
        raise NotImplementedError("school_terms.enabled: true is not implemented in v1")


def load_config(root: Path, year: int) -> LoadedConfig:
    """Load and validate the layered configuration for ``year`` under repo ``root``."""
    defaults_path = root / DEFAULTS_RELPATH
    calendar_path = root / "calendars" / str(year) / "calendar.yaml"
    local_path = root / LOCAL_RELPATH

    layers: list[tuple[str, dict[str, Any]]] = []
    defaults_text = defaults_path.read_text(encoding="utf-8") if defaults_path.exists() else ""
    for path, required in ((defaults_path, True), (calendar_path, True), (local_path, False)):
        data = _read_yaml_mapping(path, required=required)
        if data is not None:
            layers.append((str(path.relative_to(root)), data))

    merged: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    for label, data in layers:
        merged = deep_merge(merged, data)
        for leaf in _leaf_paths(data):
            provenance[leaf] = label

    declared_year = merged.get("year")
    if declared_year != year:
        raise ConfigError(
            f"{calendar_path.relative_to(root)}: must set 'year: {year}' (found {declared_year!r})"
        )

    try:
        config = Config.model_validate(merged)
    except ValidationError as exc:
        details = "\n  ".join(_format_error(e, provenance) for e in exc.errors())
        raise ConfigError(f"invalid configuration:\n  {details}") from exc

    check_supported(config)

    unconfirmed = tuple(
        UnconfirmedValue(
            key=key,
            value=_lookup(merged, key),
            layer=_source_of(key, provenance),
        )
        for key in find_todo_keys(defaults_text)
    )
    return LoadedConfig(config=config, unconfirmed=unconfirmed)
