"""
free_feeding.py — Free-feeding (continuous reload) experiment template.

Behavior:
  1. On session start, dispense a pellet on every configured node.
  2. On CatchAttempt (retrieval attempt), log it.
  3. On dome close (PG3 cleared after being open), wait ``reload_delay_s``
     then re-dispense that node.
  4. On Fault (jam / timeout), the faulted node is **halted** (latched) and
     stops reloading; the other nodes keep free-feeding. The node resumes only
     after an operator **Recover** (``on_recover`` re-dispenses it).
  5. End after ``duration`` and/or when total pellets presented reaches
     ``max_pellets``.

Usage::

    from sfm_gui.experiment.templates.free_feeding import build

    exp = build(nodes=[1, 2, 3], reload_delay_s=2.0, hours=12)
    exp.run(interface="vcan0")
"""

from __future__ import annotations

from typing import Optional, Sequence

from ..runner import Experiment


def build(
    nodes: Optional[Sequence[int]] = None,
    *,
    name: str = "free_feeding",
    reload_delay_s: float = 30.0,
    hours: float = 0.0,
    minutes: float = 0.0,
    seconds: float = 0.0,
    max_pellets: Optional[int] = None,
) -> Experiment:
    """
    Build a free-feeding Experiment.

    Parameters
    ----------
    nodes:
        Node IDs to use. Defaults to [1, 2, 3].
    reload_delay_s:
        Seconds to wait after dome close before re-dispensing.
    hours / minutes / seconds:
        Session duration (combined). 0 = no duration limit.
    max_pellets:
        End when this many pellets have been presented (None = no cap).
    """
    node_list = list(nodes) if nodes else [1, 2, 3]
    exp = Experiment(nodes=node_list, name=name)

    @exp.on_start
    def _start(ctx):
        ctx.log("free_feeding_start", nodes=ctx.nodes, reload_delay_s=reload_delay_s)
        for n in ctx.nodes:
            ctx.dispense(n)

    @exp.on_catch_attempt
    def _attempted(ctx, ev):
        ctx.incr("retrieval_attempts")
        ctx.log("retrieval_attempt", node=ev.node_id)

    @exp.on_dome_closed
    def _reload(ctx, ev):
        if ctx.stop_requested:
            return
        node_id = ev.node_id
        ctx.log("dome_closed", node=node_id)

        def _do_reload():
            if ctx.stop_requested:
                return
            ctx.log("reload_dispense", node=node_id)
            ctx.dispense(node_id)

        if reload_delay_s <= 0:
            _do_reload()
        else:
            # Node-scoped so a fault on this node cancels its pending reload.
            ctx.after(reload_delay_s, _do_reload, node=node_id)

    @exp.on_pellet_presented
    def _presented(ctx, ev):
        # Runner already incr("pellets"); just log for the experiment CSV.
        ctx.log(
            "pellet_presented",
            node=ev.node_id,
            total=ctx.counter("pellets"),
        )

    @exp.on_fault
    def _fault(ctx, ev):
        """
        Jam/timeout on a node = no pellet delivered. The runner has already
        halted just this node (cancels its reload, makes its dispenses no-ops);
        the other nodes keep free-feeding. The node stays latched until an
        operator Recover — we only log here.
        """
        fault_code = ev.data.get("fault_code")
        ctx.log("fault", node=ev.node_id, fault_code=fault_code)

    @exp.on_recover
    def _recovered(ctx, ev):
        """Operator cleared the fault — resume this node's feeding cycle."""
        ctx.log("recovered", node=ev.node_id)
        ctx.dispense(ev.node_id)

    @exp.on_end
    def _end(ctx):
        ctx.log(
            "free_feeding_end",
            pellets=ctx.counter("pellets"),
            retrieval_attempts=ctx.counter("retrieval_attempts"),
            elapsed_s=round(ctx.elapsed(), 3),
        )

    exp.end_after(hours=hours, minutes=minutes, seconds=seconds, pellets=max_pellets)
    return exp


# Alias matching the plan's "free_feeding" naming.
def free_feeding(*args, **kwargs) -> Experiment:
    """Alias for :func:`build`."""
    return build(*args, **kwargs)
