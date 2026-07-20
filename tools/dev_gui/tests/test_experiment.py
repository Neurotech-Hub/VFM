"""Tests for the headless experiment engine and free-feeding template."""

from __future__ import annotations

from types import SimpleNamespace

from vfm_gui.experiment import EventKind, Experiment, ExperimentContext, NodeEvent
from vfm_gui.experiment.events import EventNormalizer
from vfm_gui.experiment.templates.free_feeding import build as build_free_feeding
from vfm_gui.protocol import (
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


def test_normalizer_access_attempt() -> None:
    norm = EventNormalizer()
    arb, data = build_event_frame(1, CanEvent.AccessAttempt)
    events = norm.frame_to_events(_msg(arb, data), now=1.0)
    assert events[0].kind == EventKind.ACCESS_ATTEMPT
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

    @exp.on_access_attempt
    def _a(ctx, ev):
        seen.append(ev.node_id)

    runner = exp.make_runner()
    runner.start(now=0.0)
    runner.inject(NodeEvent(EventKind.ACCESS_ATTEMPT, node_id=7, timestamp=1.0))
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

    runner.inject(NodeEvent(EventKind.ACCESS_ATTEMPT, node_id=1, timestamp=1.0))
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


def test_free_feeding_fault_sends_abort() -> None:
    exp = build_free_feeding(nodes=[1], reload_delay_s=2.0, seconds=60)
    runner = exp.make_runner()
    runner.start(now=0.0)
    runner.ctx.commands_sent.clear()

    runner.inject(
        NodeEvent(
            EventKind.FAULT,
            node_id=1,
            timestamp=1.0,
            data={"fault_code": ServiceStatus.Timeout},
        )
    )
    aborts = [
        (n, cmd) for (n, cmd, _) in runner.ctx.commands_sent if cmd == CanCmd.Abort
    ]
    assert aborts == [(1, CanCmd.Abort)]


def test_free_feeding_ends_on_pellet_cap() -> None:
    exp = build_free_feeding(nodes=[1], reload_delay_s=2.0, max_pellets=2)
    runner = exp.make_runner()
    runner.start(now=0.0)

    runner.inject(NodeEvent(EventKind.PELLET_PRESENTED, node_id=1, timestamp=1.0))
    assert not runner.is_finished
    runner.inject(NodeEvent(EventKind.PELLET_PRESENTED, node_id=1, timestamp=2.0))
    assert runner.is_finished
    assert runner.ctx.counter("pellets") == 2
