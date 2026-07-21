"""Tests for experiment JSON schema loading and build_experiment."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sfm_gui.experiment.schema import (
    DEFAULT_EXPERIMENTS_DIR,
    ExperimentDef,
    ExperimentParam,
    build_experiment,
    coerce_param_value,
    load_experiment_def,
    load_experiment_defs,
    param_visible,
)


def test_load_free_feeding_json() -> None:
    defs = load_experiment_defs(DEFAULT_EXPERIMENTS_DIR)
    assert defs, "expected at least free_feeding.json"
    ff = next(d for d in defs if d.name == "free_feeding")
    assert ff.label == "Free Feeding"
    assert ff.template == "free_feeding"
    keys = {p.key for p in ff.parameters}
    assert keys == {
        "nodes",
        "reload_delay_s",
        "minutes",
        "max_pellets",
    }
    by_key = {p.key: p for p in ff.parameters}
    assert by_key["nodes"].type == "nodes"
    assert by_key["reload_delay_s"].type == "int"
    assert by_key["reload_delay_s"].default == 30
    assert by_key["max_pellets"].type == "int"


def test_build_experiment_maps_zero_cap_and_duration() -> None:
    ff = load_experiment_def(DEFAULT_EXPERIMENTS_DIR / "free_feeding.json")
    exp = build_experiment(
        ff,
        params={
            "reload_delay_s": 1,
            "minutes": 2.0,
            "max_pellets": 0,
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


# ---------------------------------------------------------------------------
# Declarative param types (nodes / node_number / node_choice) + visible_when
# ---------------------------------------------------------------------------

def test_new_param_types_parse() -> None:
    defs = {d.name: d for d in load_experiment_defs(DEFAULT_EXPERIMENTS_DIR)}
    prob = {p.key: p for p in defs["probability_delivery"].parameters}
    assert prob["probabilities"].type == "node_number"
    assert prob["probabilities"].is_node_param
    assert prob["interval_s"].visible_when == {"trigger": "timer"}
    assert prob["bnc_channel"].visible_when == {"trigger": "bnc"}
    assert prob["bnc_channel"].default == 0

    far = {p.key: p for p in defs["fixed_and_random"].parameters}
    assert far["node_roles"].type == "node_choice"
    assert far["node_roles"].options == ["off", "fixed", "random"]


def test_param_visible_evaluates_condition() -> None:
    p = ExperimentParam(key="interval_s", label="", type="float",
                        visible_when={"trigger": "timer"})
    assert param_visible(p, {"trigger": "timer"}) is True
    assert param_visible(p, {"trigger": "bnc"}) is False
    # No condition → always visible.
    assert param_visible(ExperimentParam(key="x", label="", type="int"), {}) is True


def test_coerce_node_params_pass_through_dicts() -> None:
    num = ExperimentParam(key="probabilities", label="", type="node_number")
    assert coerce_param_value(num, {1: 20, 2: 80}) == {1: 20, 2: 80}
    ch = ExperimentParam(key="node_roles", label="", type="node_choice",
                         options=["off", "fixed", "random"])
    assert coerce_param_value(ch, {1: "fixed"}) == {1: "fixed"}
    nodes = ExperimentParam(key="nodes", label="", type="nodes")
    assert coerce_param_value(nodes, [1, 3]) == [1, 3]


def test_nodes_param_becomes_nodes_arg_not_kwarg() -> None:
    """A 'nodes'-typed param supplies nodes=, and is not forwarded as a kwarg."""
    exp_def = ExperimentDef(
        name="free_feeding", label="", template="free_feeding",
        parameters=[
            ExperimentParam(key="nodes", label="", type="nodes"),
            ExperimentParam(key="reload_delay_s", label="", type="int", default=30),
        ],
    )
    # Passing a bogus dict as the 'nodes' param value must not reach build() —
    # build_experiment consumes it into nodes=. If it leaked as a kwarg,
    # free_feeding.build() would receive an unexpected 'nodes' keyword twice.
    exp = build_experiment(
        exp_def,
        params={"nodes": [9, 9], "reload_delay_s": 5},
        nodes=[1, 2],
    )
    assert exp.nodes == [1, 2]


def test_node_choice_dict_flows_to_builder() -> None:
    fr = load_experiment_def(DEFAULT_EXPERIMENTS_DIR / "fixed_and_random.json")
    exp = build_experiment(
        fr,
        params={
            "node_roles": {1: "fixed", 2: "off", 3: "random"},
            "trigger": "timer", "interval_s": 5.0, "random_prob": 0.0,
            "minutes": 0, "max_pellets": 0,
        },
        nodes=[1, 2, 3],
    )
    assert exp.name == "fixed_and_random"
    assert exp.nodes == [1, 2, 3]
