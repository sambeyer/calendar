from __future__ import annotations

from pathlib import Path

import pytest
from conftest import MakeRepo

from birdcal.config import ConfigError, find_todo_keys, load_config


def test_repo_config_loads_for_2027(repo_root: Path) -> None:
    loaded = load_config(repo_root, 2027)
    assert loaded.config.year == 2027
    assert loaded.config.printer_spec_confirmed is False
    assert loaded.config.page.width_mm == 297
    assert loaded.config.page.orientation == "portrait"


def test_every_todo_printer_key_is_a_leaf_value(repo_root: Path) -> None:
    """Block-level TODOs would hide the real key; each marker must sit on a leaf."""
    loaded = load_config(repo_root, 2027)
    keys = {u.key for u in loaded.unconfirmed}
    assert "bleed_mm" in keys
    assert "binding.holes.pattern.pitch_mm" in keys
    assert "printer_spec_confirmed" in keys
    for item in loaded.unconfirmed:
        assert item.value is not None, f"{item.key} resolved to nothing"
        assert not isinstance(item.value, dict), f"{item.key} is a block, not a leaf"


def test_find_todo_keys_builds_nested_paths() -> None:
    text = (
        "page:\n"
        "  width_mm: 297  # TODO(printer)\n"
        "  height_mm: 420\n"
        "binding:\n"
        "  holes:\n"
        "    diameter_mm: 4.8  # TODO(printer)\n"
        "bleed_mm: 3  # TODO(printer)\n"
    )
    assert find_todo_keys(text) == ["page.width_mm", "binding.holes.diameter_mm", "bleed_mm"]


def test_calendar_layer_overrides_defaults_and_is_reported(make_repo: MakeRepo) -> None:
    root = make_repo(calendar_yaml="year: 2027\nbleed_mm: 5\n")
    loaded = load_config(root, 2027)
    assert loaded.config.bleed_mm == 5
    bleed = next(u for u in loaded.unconfirmed if u.key == "bleed_mm")
    assert bleed.layer == "calendars/2027/calendar.yaml"


def test_local_layer_sets_machine_paths_and_wins(make_repo: MakeRepo) -> None:
    root = make_repo(
        calendar_yaml="year: 2027\npaths: {photos_dir: /repo-file}\n",
        local_yaml="paths: {photos_dir: /home/me/photos}\n",
    )
    loaded = load_config(root, 2027)
    assert loaded.config.paths.photos_dir == "/home/me/photos"


def test_schema_error_names_file_and_key(make_repo: MakeRepo) -> None:
    root = make_repo(calendar_yaml="year: 2027\npage: {width_mm: -1}\n")
    with pytest.raises(ConfigError) as info:
        load_config(root, 2027)
    message = str(info.value)
    assert "calendars/2027/calendar.yaml: page.width_mm" in message
    assert "greater than 0" in message


def test_unknown_key_is_rejected_so_typos_fail_loudly(make_repo: MakeRepo) -> None:
    root = make_repo(calendar_yaml="year: 2027\nbleed: 3\n")
    with pytest.raises(ConfigError, match="bleed"):
        load_config(root, 2027)


def test_orientation_must_match_dimensions(make_repo: MakeRepo) -> None:
    root = make_repo(calendar_yaml="year: 2027\npage: {orientation: landscape}\n")
    with pytest.raises(ConfigError, match="contradicts"):
        load_config(root, 2027)


def test_year_in_calendar_must_match_directory(make_repo: MakeRepo) -> None:
    root = make_repo(calendar_yaml="year: 2028\n")
    with pytest.raises(ConfigError, match="must set 'year: 2027'"):
        load_config(root, 2027)


def test_spiral_binding_requires_holes(make_repo: MakeRepo) -> None:
    root = make_repo(calendar_yaml="year: 2027\nbinding: {holes: null}\n")
    with pytest.raises(ConfigError, match="binding.holes is required"):
        load_config(root, 2027)


def test_hole_pattern_takes_exactly_one_spacing_rule(make_repo: MakeRepo) -> None:
    root = make_repo(
        calendar_yaml=("year: 2027\nbinding:\n  holes:\n    pattern: {pitch_mm: 8.47, count: 6}\n")
    )
    # The partial override merges with defaults, so pattern keeps holes_per_inch/count from
    # defaults; the conflict is then pitch_mm plus count.
    with pytest.raises(ConfigError, match="exactly one"):
        load_config(root, 2027)


def test_min_font_size_cannot_exceed_size(make_repo: MakeRepo) -> None:
    root = make_repo(
        calendar_yaml="year: 2027\nfonts: {credit: {min_size_pt: 99}}\n",
    )
    with pytest.raises(ConfigError, match="min_size_pt must not exceed size_pt"):
        load_config(root, 2027)


@pytest.mark.parametrize(
    "override",
    [
        "sides: {mode: duplex}",
        "layout: {mode: facing_pages}",
        "colour: {mode: cmyk}",
        "school_terms: {enabled: true}",
    ],
)
def test_declared_but_unbuilt_features_raise_not_implemented(
    make_repo: MakeRepo,
    override: str,
) -> None:
    root = make_repo(calendar_yaml=f"year: 2027\n{override}\n")
    with pytest.raises(NotImplementedError):
        load_config(root, 2027)
