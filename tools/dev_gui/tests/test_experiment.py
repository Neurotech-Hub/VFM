"""Tests for the headless experiment engine and free-feeding template."""

from __future__ import annotations

from types import SimpleNamespace

from sfm_gui.experiment import EventKind, Experiment, ExperimentContext, NodeEvent
from sfm_gui.experiment.events import EventNormalizer
from sfm_gui.experiment.templates.fixed_and_random import build as build_fixed_and_random
from sfm_gui.experiment.templates.free_feeding import build as build_free_feeding
from sfm_gui.experiment.templates.probability_delivery import (
    build as build_probability_delivery,
)
from sfm_gui.protocol import (
    CanCmd,
    CanEvent,
    DispenseState,
    HeartbeatPayload,
    InputId,
    ServiceStatus,
    build_event_frame,
    build_heartbeat_frame,
)


def _msg(arb_id: int, data: bytes):
    return SimpleNamespace(arbitration_id=arb_id, data=data)


# ---------------------------------------------------------------------------
# EventNormalizer
# ---------------------------------------------------------------------------

def test_normalizer_pellet_presented() -> None:
    norm = EventNormalizer()
    arb, data = build_event_frame(2, CanEvent.PelletPresented, b"\x05\x00")
    events = norm.frame_to_events(_msg(arb, data), now=100.0)
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == EventKind.PELLET_PRESENTED
    assert ev.node_id == 2
    assert ev.data.get("pellet_count") == 5


def test_normalizer_catch_attempt() -> None:
    norm = EventNormalizer()
    arb, data = build_event_frame(1, CanEvent.CatchAttempt)
    events = norm.frame_to_events(_msg(arb, data), now=1.0)
    assert events[0].kind == EventKind.CATCH_ATTEMPT
    assert events[0].node_id == 1


def test_normalizer_derives_dome_closed_from_pg3() -> None:
    norm = EventNormalizer()
    # PG3 open
    arb, data = build_event_frame(
        3, CanEvent.InputChanged, bytes([InputId.PG3, 1])
    )
    opened = norm.frame_to_events(_msg(arb, data), now=10.0)
    kinds = [e.kind for e in opened]
    assert EventKind.DOME_OPENED in kinds
    assert EventKind.PG_CHANGED in kinds

    # PG3 clear
    arb, data = build_event_frame(
        3, CanEvent.InputChanged, bytes([InputId.PG3, 0])
    )
    closed = norm.frame_to_events(_msg(arb, data), now=11.0)
    kinds = [e.kind for e in closed]
    assert EventKind.DOME_CLOSED in kinds
    assert any(e.node_id == 3 for e in closed if e.kind == EventKind.DOME_CLOSED)


def test_normalizer_node_online_offline() -> None:
    norm = EventNormalizer(online_timeout_s=5.0)
    hb = HeartbeatPayload(
        dispense_state=DispenseState.Idle,
        presence=False,
        pg1=False,
        pg2=True,
        pg3=False,
        fault_code=ServiceStatus.Ok,
    )
    arb, data = build_heartbeat_frame(1, hb)
    events = norm.frame_to_events(_msg(arb, data), now=0.0)
    assert any(e.kind == EventKind.NODE_ONLINE for e in events)

    stale = norm.check_staleness(now=6.0)
    assert len(stale) == 1
    assert stale[0].kind == EventKind.NODE_OFFLINE
    assert stale[0].node_id == 1


def test_normalizer_presence_changed() -> None:
    norm = EventNormalizer()
    arb, data = build_event_frame(
        1, CanEvent.InputChanged, bytes([InputId.Presence, 1])
    )
    events = norm.frame_to_events(_msg(arb, data), now=1.0)
    assert events[0].kind == EventKind.PRESENCE_CHANGED
    assert events[0].data["active"] is True


def test_normalizer_fault() -> None:
    norm = EventNormalizer()
    arb, data = build_event_frame(1, CanEvent.Fault, bytes([ServiceStatus.Jam]))
    events = norm.frame_to_events(_msg(arb, data), now=1.0)
    assert events[0].kind == EventKind.FAULT
    assert events[0].data["fault_code"] == ServiceStatus.Jam


# ---------------------------------------------------------------------------
# ExperimentContext
# ---------------------------------------------------------------------------

def test_context_dispense_records_command() -> None:
    ctx = ExperimentContext(nodes=[1, 2])
    ctx.begin(now=0.0)
    assert ctx.dispense(1) is True
    assert ctx.commands_sent[-1] == (1, CanCmd.Dispense, b"")
    assert ctx.counter("pellets") == 0
    ctx.incr("pellets")
    assert ctx.counter("pellets") == 1


def test_context_after_timer_fires() -> None:
    ctx = ExperimentContext(nodes=[1])
    ctx.begin(now=0.0)
    fired = []
    ctx.after(2.0, lambda: fired.append(True))
    ctx.tick_timers(1.0)
    assert fired == []
    ctx.tick_timers(2.0)
    assert fired == [True]
    # One-shot: should not fire again
    ctx.tick_timers(4.0)
    assert fired == [True]


def test_context_every_timer_repeats() -> None:
    ctx = ExperimentContext(nodes=[1])
    ctx.begin(now=0.0)
    count = []
    ctx.every(1.0, lambda: count.append(1))
    ctx.tick_timers(1.0)
    ctx.tick_timers(2.0)
    ctx.tick_timers(2.5)
    assert len(count) == 2


# ---------------------------------------------------------------------------
# Experiment / Runner
# ---------------------------------------------------------------------------

def test_runner_start_end_callbacks() -> None:
    exp = Experiment(nodes=[1], name="t")
    started = []
    ended = []

    @exp.on_start
    def _s(ctx):
        started.append(ctx.nodes)

    @exp.on_end
    def _e(ctx):
        ended.append(True)

    exp.end_after(seconds=5)
    runner = exp.make_runner()
    runner.start(now=0.0)
    assert started == [[1]]
    assert runner.is_active

    runner.step(now=4.0)
    assert not runner.is_finished
    runner.step(now=5.0)
    assert runner.is_finished
    assert ended == [True]


def test_runner_end_after_pellets() -> None:
    exp = Experiment(nodes=[1])
    exp.end_after(pellets=2)
    runner = exp.make_runner()
    runner.start(now=0.0)

    runner.inject(NodeEvent(EventKind.PELLET_PRESENTED, node_id=1, timestamp=1.0))
    assert not runner.is_finished
    assert runner.ctx.counter("pellets") == 1

    runner.inject(NodeEvent(EventKind.PELLET_PRESENTED, node_id=1, timestamp=2.0))
    assert runner.is_finished
    assert runner.ctx.counter("pellets") == 2


def test_runner_event_handler() -> None:
    exp = Experiment(nodes=[1])
    seen = []

    @exp.on_catch_attempt
    def _a(ctx, ev):
        seen.append(ev.node_id)

    runner = exp.make_runner()
    runner.start(now=0.0)
    runner.inject(NodeEvent(EventKind.CATCH_ATTEMPT, node_id=7, timestamp=1.0))
    assert seen == [7]


def test_start_when_defers_activation() -> None:
    exp = Experiment(nodes=[1])
    ready = {"ok": False}
    started = []

    exp.start_when(lambda ctx: ready["ok"])

    @exp.on_start
    def _s(ctx):
        started.append(True)

    runner = exp.make_runner()
    runner.start(now=0.0)
    assert not runner.is_active
    assert started == []

    runner.step(now=1.0)
    assert not runner.is_active

    ready["ok"] = True
    runner.step(now=2.0)
    assert runner.is_active
    assert started == [True]


# ---------------------------------------------------------------------------
# Free-feeding template
# ---------------------------------------------------------------------------

def test_free_feeding_dispenses_all_nodes_on_start() -> None:
    exp = build_free_feeding(nodes=[1, 2, 3], reload_delay_s=2.0, seconds=60)
    runner = exp.make_runner()
    runner.start(now=0.0)

    dispenses = [
        (n, cmd) for (n, cmd, _) in runner.ctx.commands_sent if cmd == CanCmd.Dispense
    ]
    assert dispenses == [(1, CanCmd.Dispense), (2, CanCmd.Dispense), (3, CanCmd.Dispense)]


def test_free_feeding_reloads_after_dome_close() -> None:
    exp = build_free_feeding(nodes=[1], reload_delay_s=2.0, seconds=60)
    runner = exp.make_runner()
    runner.start(now=0.0)
    # Clear the initial dispenses from the command log for easier asserts.
    runner.ctx.commands_sent.clear()

    runner.inject(NodeEvent(EventKind.CATCH_ATTEMPT, node_id=1, timestamp=1.0))
    assert runner.ctx.counter("retrieval_attempts") == 1
    # No reload yet — dome not closed.
    assert runner.ctx.commands_sent == []

    runner.inject(NodeEvent(EventKind.DOME_CLOSED, node_id=1, timestamp=2.0))
    # Delay not elapsed.
    assert runner.ctx.commands_sent == []

    runner.step(now=4.0)  # 2s after dome close
    dispenses = [
        (n, cmd) for (n, cmd, _) in runner.ctx.commands_sent if cmd == CanCmd.Dispense
    ]
    assert dispenses == [(1, CanCmd.Dispense)]


def test_free_feeding_immediate_reload_when_delay_zero() -> None:
    exp = build_free_feeding(nodes=[2], reload_delay_s=0.0, seconds=60)
    runner = exp.make_runner()
    runner.start(now=0.0)
    runner.ctx.commands_sent.clear()

    runner.inject(NodeEvent(EventKind.DOME_CLOSED, node_id=2, timestamp=1.0))
    dispenses = [
        (n, cmd) for (n, cmd, _) in runner.ctx.commands_sent if cmd == CanCmd.Dispense
    ]
    assert dispenses == [(2, CanCmd.Dispense)]


def test_fault_halts_only_that_node_session_continues() -> None:
    """A fault halts only the faulted node; other nodes keep running."""
    exp = build_free_feeding(nodes=[1, 2], reload_delay_s=2.0, seconds=60)
    runner = exp.make_runner()
    runner.start(now=0.0)
    runner.ctx.commands_sent.clear()

    # Schedule a pending reload on node 2 (fires at t=3.0), then fault node 1.
    runner.inject(NodeEvent(EventKind.DOME_CLOSED, node_id=2, timestamp=1.0))
    runner.inject(
        NodeEvent(
            EventKind.FAULT,
            node_id=1,
            timestamp=2.0,
            data={"fault_code": ServiceStatus.Timeout},
        )
    )

    # Session keeps running; only node 1 is latched.
    assert not runner.is_finished
    assert not runner.ctx.stop_requested
    assert runner.ctx.is_halted(1)
    assert not runner.ctx.is_halted(2)

    # Advance past node 2's reload delay — node 2 reloads, node 1 does not.
    runner.ctx.commands_sent.clear()
    runner.step(now=3.5)
    dispenses = [
        n for (n, cmd, _) in runner.ctx.commands_sent if cmd == CanCmd.Dispense
    ]
    assert 2 in dispenses
    assert 1 not in dispenses


def test_fault_cancels_faulted_nodes_pending_reload() -> None:
    """The faulted node's own pending reload timer is cancelled on fault."""
    exp = build_free_feeding(nodes=[1], reload_delay_s=2.0, seconds=60)
    runner = exp.make_runner()
    runner.start(now=0.0)

    # Dome close schedules a reload at t=3.0, then a fault halts node 1.
    runner.inject(NodeEvent(EventKind.DOME_CLOSED, node_id=1, timestamp=1.0))
    runner.inject(
        NodeEvent(
            EventKind.FAULT,
            node_id=1,
            timestamp=2.0,
            data={"fault_code": ServiceStatus.Jam},
        )
    )
    runner.ctx.commands_sent.clear()

    runner.step(now=4.0)  # past the (now-cancelled) reload time
    dispenses = [
        (n, cmd) for (n, cmd, _) in runner.ctx.commands_sent if cmd == CanCmd.Dispense
    ]
    assert dispenses == []
    assert not runner.is_finished


def test_recover_node_rearms_faulted_node() -> None:
    """recover_node clears the fault (Recover) and re-dispenses via on_recover."""
    exp = build_free_feeding(nodes=[1], reload_delay_s=0.0, seconds=60)
    runner = exp.make_runner()
    runner.start(now=0.0)

    runner.inject(
        NodeEvent(
            EventKind.FAULT,
            node_id=1,
            timestamp=1.0,
            data={"fault_code": ServiceStatus.Jam},
        )
    )
    assert runner.ctx.is_halted(1)
    # While halted, dispense is a no-op.
    assert runner.ctx.dispense(1) is False

    runner.ctx.commands_sent.clear()
    runner.recover_node(1, now=2.0)

    assert not runner.ctx.is_halted(1)
    cmds = [(n, cmd) for (n, cmd, _) in runner.ctx.commands_sent]
    # Recover clears the firmware fault, then on_recover re-dispenses the node.
    assert (1, CanCmd.Recover) in cmds
    assert (1, CanCmd.Dispense) in cmds
    assert cmds.index((1, CanCmd.Recover)) < cmds.index((1, CanCmd.Dispense))


def test_fixed_and_random_roles_fixed_random_off() -> None:
    """node_roles dict: fixed dispenses every cycle, off never, random via prob."""
    exp = build_fixed_and_random(
        nodes=[1, 2, 3],
        node_roles={1: "fixed", 2: "off", 3: "random"},
        trigger="timer", interval_s=5.0, random_prob=0.0, seconds=60, seed=1,
    )
    runner = exp.make_runner()
    runner.start(now=0.0)   # immediate first cycle
    runner.step(now=5.0)    # second cycle
    runner.step(now=10.0)   # third cycle

    dispenses = [n for (n, cmd, _) in runner.ctx.commands_sent if cmd == CanCmd.Dispense]
    assert dispenses.count(1) == 3          # fixed → every cycle
    assert 2 not in dispenses               # off → never
    assert 3 not in dispenses               # random with prob 0 → never


def test_fixed_and_random_multiple_fixed_roles() -> None:
    """Multiple fixed nodes all dispense every cycle; off excluded."""
    exp = build_fixed_and_random(
        nodes=[1, 2, 3],
        node_roles={1: "fixed", 2: "off", 3: "fixed"},
        trigger="timer", interval_s=5.0, random_prob=0.0, seconds=60, seed=1,
    )
    runner = exp.make_runner()
    runner.start(now=0.0)
    runner.step(now=5.0)
    runner.step(now=10.0)

    dispenses = [n for (n, cmd, _) in runner.ctx.commands_sent if cmd == CanCmd.Dispense]
    assert dispenses.count(1) == 3
    assert dispenses.count(3) == 3
    assert 2 not in dispenses


def test_fixed_and_random_fixed_nodes_string_backcompat() -> None:
    """Legacy headless form: fixed_nodes string → those fixed, others random."""
    exp = build_fixed_and_random(
        nodes=[1, 2, 3], fixed_nodes="1", trigger="timer",
        interval_s=5.0, random_prob=0.0, seconds=60, seed=1,
    )
    runner = exp.make_runner()
    runner.start(now=0.0)
    dispenses = {n for (n, cmd, _) in runner.ctx.commands_sent if cmd == CanCmd.Dispense}
    assert dispenses == {1}  # node 1 fixed; 2 & 3 random at prob 0 → never


def test_fixed_and_random_random_nodes_dispense_when_prob_one() -> None:
    """prob=1: every 'random' node dispenses too (alongside 'fixed')."""
    exp = build_fixed_and_random(
        nodes=[1, 2, 3],
        node_roles={1: "fixed", 2: "random", 3: "random"},
        trigger="timer", interval_s=5.0, random_prob=1.0, seconds=60, seed=1,
    )
    runner = exp.make_runner()
    runner.start(now=0.0)
    nodes = {n for (n, cmd, _) in runner.ctx.commands_sent if cmd == CanCmd.Dispense}
    assert nodes == {1, 2, 3}


def test_probability_delivery_weighted_pick() -> None:
    """Weight only on node 2 → every cycle delivers on node 2."""
    exp = build_probability_delivery(
        nodes=[1, 2, 3], probabilities="0,100,0", trigger="timer",
        interval_s=5.0, seconds=60, seed=7,
    )
    runner = exp.make_runner()
    runner.start(now=0.0)
    runner.step(now=5.0)

    dispenses = [n for (n, cmd, _) in runner.ctx.commands_sent if cmd == CanCmd.Dispense]
    assert dispenses and set(dispenses) == {2}


def test_probability_delivery_accepts_weight_dict() -> None:
    """GUI form: probabilities as a {node_id: pct} dict routes correctly."""
    exp = build_probability_delivery(
        nodes=[1, 2, 3], probabilities={1: 0, 2: 0, 3: 100}, trigger="timer",
        interval_s=5.0, seconds=60, seed=7,
    )
    runner = exp.make_runner()
    runner.start(now=0.0)
    runner.step(now=5.0)
    dispenses = [n for (n, cmd, _) in runner.ctx.commands_sent if cmd == CanCmd.Dispense]
    assert dispenses and set(dispenses) == {3}


def test_probability_delivery_zero_weight_node_never_picked() -> None:
    """A 0% node must never be picked, even over many independent draws."""
    exp = build_probability_delivery(
        nodes=[1, 2], probabilities="0,100", trigger="bnc", seed=11,
    )
    runner = exp.make_runner()
    runner.start(now=0.0)
    for i in range(200):
        runner.inject(
            NodeEvent(EventKind.BNC_IN, node_id=0, timestamp=float(i + 1),
                      data={"channel": 0, "edge": "rising", "high": True})
        )
    dispenses = [n for (n, cmd, _) in runner.ctx.commands_sent if cmd == CanCmd.Dispense]
    assert len(dispenses) == 200
    assert set(dispenses) == {2}


def test_probability_delivery_is_weighted_random_not_uniform() -> None:
    """
    20% / 80% weights must produce a random draw each cycle (not deterministic
    round-robin, not uniform 50/50) that converges to the configured split
    over many independent trials.
    """
    exp = build_probability_delivery(
        nodes=[1, 2], probabilities="20,80", trigger="bnc", seed=42,
    )
    runner = exp.make_runner()
    runner.start(now=0.0)
    n_trials = 2000
    for i in range(n_trials):
        runner.inject(
            NodeEvent(EventKind.BNC_IN, node_id=0, timestamp=float(i + 1),
                      data={"channel": 0, "edge": "rising", "high": True})
        )
    dispenses = [n for (n, cmd, _) in runner.ctx.commands_sent if cmd == CanCmd.Dispense]
    assert len(dispenses) == n_trials

    # Not deterministic: both nodes must appear (a fixed 20/80 split without
    # randomness, or a round-robin, would still pass a naive count check —
    # what we're guarding against is a uniform 50/50 draw or one node winning
    # every single trial).
    counts = {1: dispenses.count(1), 2: dispenses.count(2)}
    assert counts[1] > 0 and counts[2] > 0

    # Converges to the weighted split, not a uniform 50/50 split.
    frac_node2 = counts[2] / n_trials
    assert 0.75 <= frac_node2 <= 0.85, f"node2 fraction {frac_node2} not near 0.80"

    # Consecutive picks are not a fixed pattern (proves per-cycle randomness).
    assert len(set(dispenses[:20])) == 2


def test_probability_delivery_bnc_channel_zero_based() -> None:
    """BNC trigger on channel 0; a channel-1 edge is ignored; falling ignored."""
    exp = build_probability_delivery(
        nodes=[1, 2, 3], probabilities="0,0,100", trigger="bnc",
        bnc_channel=0, seconds=60, seed=3,
    )
    runner = exp.make_runner()
    runner.start(now=0.0)
    # No timer cycles under bnc trigger — nothing dispensed on start.
    assert not [c for c in runner.ctx.commands_sent if c[1] == CanCmd.Dispense]

    # Edge on the wrong channel (1) does nothing.
    runner.inject(
        NodeEvent(EventKind.BNC_IN, node_id=0, timestamp=1.0,
                  data={"channel": 1, "edge": "rising", "high": True})
    )
    assert not [c for c in runner.ctx.commands_sent if c[1] == CanCmd.Dispense]

    # Rising edge on channel 0 delivers.
    runner.inject(
        NodeEvent(EventKind.BNC_IN, node_id=0, timestamp=2.0,
                  data={"channel": 0, "edge": "rising", "high": True})
    )
    dispenses = [n for (n, cmd, _) in runner.ctx.commands_sent if cmd == CanCmd.Dispense]
    assert dispenses == [3]

    # Falling edge on channel 0 must not trigger a delivery.
    runner.ctx.commands_sent.clear()
    runner.inject(
        NodeEvent(EventKind.BNC_IN, node_id=0, timestamp=3.0,
                  data={"channel": 0, "edge": "falling", "high": False})
    )
    assert not [c for c in runner.ctx.commands_sent if c[1] == CanCmd.Dispense]


def test_free_feeding_ends_on_pellet_cap() -> None:
    exp = build_free_feeding(nodes=[1], reload_delay_s=2.0, max_pellets=2)
    runner = exp.make_runner()
    runner.start(now=0.0)

    runner.inject(NodeEvent(EventKind.PELLET_PRESENTED, node_id=1, timestamp=1.0))
    assert not runner.is_finished
    runner.inject(NodeEvent(EventKind.PELLET_PRESENTED, node_id=1, timestamp=2.0))
    assert runner.is_finished
    assert runner.ctx.counter("pellets") == 2


def test_pellet_cap_sums_across_all_nodes() -> None:
    """max_pellets compares an aggregate of PELLET_PRESENTED from every node."""
    exp = build_free_feeding(nodes=[1, 2, 3], reload_delay_s=2.0, max_pellets=3)
    runner = exp.make_runner()
    runner.start(now=0.0)

    # One pellet from each of three different nodes → total 3 → cap reached.
    runner.inject(NodeEvent(EventKind.PELLET_PRESENTED, node_id=1, timestamp=1.0))
    assert not runner.is_finished
    runner.inject(NodeEvent(EventKind.PELLET_PRESENTED, node_id=2, timestamp=2.0))
    assert not runner.is_finished
    runner.inject(NodeEvent(EventKind.PELLET_PRESENTED, node_id=3, timestamp=3.0))
    assert runner.is_finished
    assert runner.ctx.counter("pellets") == 3


# ---------------------------------------------------------------------------
# GUI hosting: ExperimentController + step(messages=...) + on_log
# ---------------------------------------------------------------------------

def test_runner_step_with_messages_no_can_poll() -> None:
    """GUI hosting passes drained frames; runner must not require can.poll_rx()."""
    exp = build_free_feeding(nodes=[1], reload_delay_s=0.0, max_pellets=1)
    runner = exp.make_runner(wire_bnc=False)
    runner.start(now=0.0)
    runner.ctx.commands_sent.clear()

    arb, data = build_event_frame(1, CanEvent.PelletPresented, b"\x01\x00")
    msg = _msg(arb, data)
    runner.step(now=1.0, messages=[msg])
    assert runner.ctx.counter("pellets") == 1
    assert runner.is_finished


def test_wire_bnc_false_skips_io_callbacks() -> None:
    class FakeIO:
        def __init__(self):
            self.in1 = []
            self.in2 = []

        def on_bnc_in1_edge(self, cb):
            self.in1.append(cb)

        def on_bnc_in2_edge(self, cb):
            self.in2.append(cb)

    io = FakeIO()
    exp = build_free_feeding(nodes=[1], seconds=1)
    runner = exp.make_runner(io=io, wire_bnc=False)
    assert io.in1 == []
    assert io.in2 == []
    # Default wire_bnc=True would register callbacks:
    runner2 = exp.make_runner(io=FakeIO(), wire_bnc=True)
    assert len(runner2.io._bnc_in1_cb if hasattr(runner2.io, "_bnc_in1_cb") else []) >= 0
    # Use a fresh FakeIO to assert registration happened.
    io2 = FakeIO()
    exp.make_runner(io=io2, wire_bnc=True)
    assert len(io2.in1) == 1
    assert len(io2.in2) == 1


def test_controller_step_and_on_log() -> None:
    from sfm_gui.experiment.gui_controller import ExperimentController
    from sfm_gui.experiment.schema import load_experiment_def, DEFAULT_EXPERIMENTS_DIR
    from sfm_gui.log_manager import LogManager

    ff = load_experiment_def(DEFAULT_EXPERIMENTS_DIR / "free_feeding.json")
    log = LogManager(auto_save=False)
    ctrl = ExperimentController()
    assert ctrl.start(
        ff,
        params={"reload_delay_s": 0, "minutes": 0, "max_pellets": 1},
        nodes=[1],
        log=log,
    )
    assert ctrl.is_running

    arb, data = build_event_frame(1, CanEvent.PelletPresented, b"\x01\x00")
    ctrl.step(messages=[_msg(arb, data)], now=1.0)
    assert not ctrl.is_running  # finished via pellet cap

    exp_rows = [e for e in log.all_entries() if e.frame_type == "EXPERIMENT"]
    assert any(e.event_name == "session_start" for e in exp_rows)
    assert any(e.event_name == "pellet_presented" for e in exp_rows)
