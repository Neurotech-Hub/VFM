"""
probability_delivery.py — Each trigger delivers a pellet on ONE node, chosen by
weighted probability.

Example: for a 3-node setup with weights 20/30/50 each cycle picks node 1 ~20%
of the time, node 2 ~30%, node 3 ~50% — an independent weighted-random draw
every cycle (not a fixed split). Weights are normalized, so they need not sum
to 100; a weight of 0 means that node is never picked (inactive). In the GUI
weights come from a per-node % input (a dict); headless callers may pass a
comma-separated string mapped to ``nodes`` in order.

The trigger is either a periodic timer (``interval_s``) or a BNC IN rising edge
on ``bnc_channel`` (0 = first BNC input, 1 = second).

Usage::

    from sfm_gui.experiment.templates.probability_delivery import build

    exp = build(nodes=[1, 2, 3], probabilities="20,30,50",
                trigger="timer", interval_s=10.0)
    exp.run(interface="vcan0")
"""

from __future__ import annotations

import random
from typing import Any, List, Optional, Sequence

from ..runner import Experiment


def _weights_for_nodes(spec: Any, node_list: Sequence[int]) -> List[float]:
    """
    Resolve a weight spec into one non-negative weight per node in ``node_list``.

    ``spec`` may be:
      * a dict ``{node_id: weight}`` (from the GUI's per-node % inputs);
      * a comma-separated string ``"20,80"`` mapped to ``node_list`` in order;
      * a sequence of numbers mapped to ``node_list`` in order.
    Missing entries default to an equal share; an all-zero result → uniform.
    """
    n = len(node_list)
    weights: List[float] = []
    if isinstance(spec, dict):
        for node_id in node_list:
            try:
                weights.append(max(0.0, float(spec.get(node_id, 0.0))))
            except (TypeError, ValueError):
                weights.append(0.0)
    else:
        if isinstance(spec, str):
            parts = [p.strip() for p in spec.split(",") if p.strip() != ""]
        elif isinstance(spec, (list, tuple)):
            parts = list(spec)
        else:
            parts = []
        for p in parts:
            try:
                weights.append(max(0.0, float(p)))
            except (TypeError, ValueError):
                weights.append(0.0)
        if len(weights) < n:
            weights += [1.0] * (n - len(weights))  # pad missing with equal share
        weights = weights[:n]
    if sum(weights) <= 0:
        weights = [1.0] * n  # all-zero → uniform
    return weights


def build(
    nodes: Optional[Sequence[int]] = None,
    *,
    name: str = "probability_delivery",
    probabilities: Any = "",
    trigger: str = "timer",          # "timer" | "bnc"
    interval_s: float = 10.0,
    bnc_channel: int = 0,
    hours: float = 0.0,
    minutes: float = 0.0,
    seconds: float = 0.0,
    max_pellets: Optional[int] = None,
    seed: Optional[int] = None,
) -> Experiment:
    """
    Build a probability-based single-delivery Experiment.

    Parameters
    ----------
    nodes:
        Node IDs to use. Defaults to [1, 2, 3].
    probabilities:
        Per-node weights. A dict ``{node_id: weight}`` (GUI), a comma-separated
        string ``"20,80"`` mapped to ``nodes`` in order, or a sequence of
        numbers. A weight of 0 means that node is never picked. Empty = uniform.
    trigger / interval_s / bnc_channel:
        "timer" fires every ``interval_s``; "bnc" fires on a BNC IN rising edge
        on channel ``bnc_channel`` (0 = first BNC input, 1 = second).
    seed:
        Optional RNG seed for reproducible runs (tests). Falsy = nondeterministic.
    """
    node_list = list(nodes) if nodes else [1, 2, 3]
    exp = Experiment(nodes=node_list, name=name)
    rng = random.Random(seed) if seed else random.Random()
    weights = _weights_for_nodes(probabilities, node_list)

    def _cycle(ctx) -> None:
        ctx.incr("cycles")
        # Weighted pick of a single node from the current node list.
        target = rng.choices(ctx.nodes, weights=weights[: len(ctx.nodes)], k=1)[0]
        ctx.log("probability_pick", node=target)
        ctx.dispense(target)

    @exp.on_start
    def _start(ctx):
        ctx.log(
            "probability_delivery_start",
            nodes=ctx.nodes, weights=weights, trigger=trigger,
        )
        if trigger == "timer":
            _cycle(ctx)  # fire an immediate first cycle
            ctx.every(max(0.001, float(interval_s)), lambda: _cycle(ctx))

    @exp.on_bnc_in
    def _bnc(ctx, ev):
        if trigger != "bnc":
            return
        if ev.data.get("edge") != "rising":
            return
        if ev.data.get("channel") not in (None, bnc_channel):
            return
        _cycle(ctx)

    @exp.on_fault
    def _fault(ctx, ev):
        ctx.log("fault", node=ev.node_id, fault_code=ev.data.get("fault_code"))

    @exp.on_recover
    def _recovered(ctx, ev):
        ctx.log("recovered", node=ev.node_id)

    @exp.on_end
    def _end(ctx):
        ctx.log(
            "probability_delivery_end",
            cycles=ctx.counter("cycles"),
            pellets=ctx.counter("pellets"),
            elapsed_s=round(ctx.elapsed(), 3),
        )

    exp.end_after(hours=hours, minutes=minutes, seconds=seconds, pellets=max_pellets)
    return exp
