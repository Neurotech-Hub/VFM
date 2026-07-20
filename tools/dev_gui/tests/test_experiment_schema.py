"""Tests for experiment JSON schema loading and build_experiment."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vfm_gui.experiment.schema import (
    DEFAULT_EXPERIMENTS_DIR,
    build_experiment,
    load_experiment_def,
    load_experiment_defs,
)


def test_load_free_feeding_json() -> None:
    defs = load_experiment_defs(DEFAULT_EXPERIMENTS_DIR)
    assert defs, "expected at least free_feeding.json"
    ff = next(d for d in defs if d.name == "free_feeding")
    assert ff.label == "Free Feeding"
    assert ff.template == "free_feeding"
    keys = {p.key for p in ff.parameters}
    assert keys == {
        "reload_delay_s",
        "minutes",
        "max_pellets",
        "pulse_bnc_on_dispense",
    }
    by_key = {p.key: p for p in ff.parameters}
    assert by_key["reload_delay_s"].type == "float"
    assert by_key["reload_delay_s"].default == 2.0
    assert by_key["max_pellets"].type == "int"
    assert by_key["pulse_bnc_on_dispense"].type == "bool"


def test_build_experiment_maps_zero_cap_and_duration() -> None:
    ff = load_experiment_def(DEFAULT_EXPERIMENTS_DIR / "free_feeding.json")
    exp = build_experiment(
        ff,
        params={
            "reload_delay_s": 1.5,
            "minutes": 2.0,
            "max_pellets": 0,
            "pulse_bnc_on_dispense": False,
        },
        nodes=[1, 2],
    )
    assert exp.name == "free_feeding"
    assert exp.nodes == [1, 2]
    assert exp._end_pellets is None
    assert exp._end_after_s == 120.0


def test_build_experiment_pellet_cap() -> None:
    ff = load_experiment_def(DEFAULT_EXPERIMENTS_DIR / "free_feeding.json")
    exp = build_experiment(
        ff,
        params={"max_pellets": 10, "minutes": 0},
        nodes=[1],
    )
    assert exp._end_pellets == 10
    assert exp._end_after_s is None
