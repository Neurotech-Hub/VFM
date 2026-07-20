"""
runner.py — Experiment registration API and ExperimentRunner tick loop.

Users build an Experiment (decorators + start/end conditions), then either:
  - exp.run(interface=...) for a headless blocking session, or
  - runner.step(now) each frame when hosted by the GUI.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import (
    Any,
    Callable,
    DefaultDict,
    List,
    Optional,
    Sequence,
    Union,
)

from ..can_manager import CanManager
from ..io_manager import IOManager
from .context import ExperimentContext
from .events import EventKind, EventNormalizer, NodeEvent

# Callback signatures:
#   on_start / on_end:  (ctx) -> None
#   on(event):          (ctx, ev) -> None
#   start/end when:     (ctx) -> bool   OR a NodeEvent predicate
StartEndCb = Callable[[ExperimentContext], None]
EventCb = Callable[[ExperimentContext, NodeEvent], None]
ConditionFn = Callable[[ExperimentContext], bool]


class Experiment:
    """
    Declarative experiment definition + callback registration.

    Example::

        exp = Experiment(nodes=[1, 2, 3], name="free_feeding")

        @exp.on_start
        def start(ctx):
            for n in ctx.nodes:
                ctx.dispense(n)

        @exp.on(EventKind.ACCESS_ATTEMPT)
        def attempted(ctx, ev):
            ctx.log("retrieval_attempt", node=ev.node_id)

        exp.end_after(hours=12)
        exp.run(interface="vcan0")
    """

    def __init__(
        self,
        nodes: Optional[Sequence[int]] = None,
        name: str = "experiment",
    ) -> None:
        self.name = name
        self.nodes: List[int] = list(nodes) if nodes else []
        self._on_start: List[StartEndCb] = []
        self._on_end: List[StartEndCb] = []
        self._handlers: DefaultDict[EventKind, List[EventCb]] = defaultdict(list)
        self._start_when: Optional[ConditionFn] = None
        self._end_when: Optional[ConditionFn] = None
        self._end_after_s: Optional[float] = None
        self._end_pellets: Optional[int] = None

    # ------------------------------------------------------------------
    # Decorators / registration
    # ------------------------------------------------------------------

    def on_start(self, fn: StartEndCb) -> StartEndCb:
        """Register a callback fired once when the session becomes active."""
        self._on_start.append(fn)
        return fn

    def on_end(self, fn: StartEndCb) -> StartEndCb:
        """Register a callback fired once when the session ends."""
        self._on_end.append(fn)
        return fn

    def on(self, kind: EventKind) -> Callable[[EventCb], EventCb]:
        """Register a callback for a specific EventKind."""

        def decorator(fn: EventCb) -> EventCb:
            self._handlers[kind].append(fn)
            return fn

        return decorator

    # Sugar decorators for the most common event kinds -------------------

    def on_access_attempt(self, fn: EventCb) -> EventCb:
        return self.on(EventKind.ACCESS_ATTEMPT)(fn)

    def on_dome_closed(self, fn: EventCb) -> EventCb:
        return self.on(EventKind.DOME_CLOSED)(fn)

    def on_dome_opened(self, fn: EventCb) -> EventCb:
        return self.on(EventKind.DOME_OPENED)(fn)

    def on_pellet_presented(self, fn: EventCb) -> EventCb:
        return self.on(EventKind.PELLET_PRESENTED)(fn)

    def on_pellet_loaded(self, fn: EventCb) -> EventCb:
        return self.on(EventKind.PELLET_LOADED)(fn)

    def on_fault(self, fn: EventCb) -> EventCb:
        return self.on(EventKind.FAULT)(fn)

    def on_bnc_in(self, fn: EventCb) -> EventCb:
        return self.on(EventKind.BNC_IN)(fn)

    def on_presence_changed(self, fn: EventCb) -> EventCb:
        return self.on(EventKind.PRESENCE_CHANGED)(fn)

    # ------------------------------------------------------------------
    # Start / end conditions
    # ------------------------------------------------------------------

    def start_when(self, condition: ConditionFn) -> "Experiment":
        """Defer SESSION_START until ``condition(ctx)`` returns True."""
        self._start_when = condition
        return self

    def end_when(self, condition: ConditionFn) -> "Experiment":
        """End the session when ``condition(ctx)`` returns True."""
        self._end_when = condition
        return self

    def end_after(
        self,
        *,
        hours: float = 0.0,
        minutes: float = 0.0,
        seconds: float = 0.0,
        pellets: Optional[int] = None,
    ) -> "Experiment":
        """
        End after a duration and/or total pellet count.

        Duration uses ``hours`` + ``minutes`` + ``seconds``. Pellet count
        tracks the ``pellets`` counter (incremented by templates on
        PELLET_PRESENTED, or manually via ``ctx.incr("pellets")``).
        """
        total = float(hours) * 3600.0 + float(minutes) * 60.0 + float(seconds)
        if total > 0:
            self._end_after_s = total
        if pellets is not None:
            self._end_pellets = int(pellets)
        return self

    # ------------------------------------------------------------------
    # Run helpers
    # ------------------------------------------------------------------

    def run(
        self,
        interface: str = "can0",
        bitrate: int = 250_000,
        log_dir: Optional[str] = None,
        use_io: bool = True,
        poll_hz: float = 50.0,
    ) -> ExperimentContext:
        """
        Blocking headless run against a SocketCAN interface.

        Returns the ExperimentContext after the session ends.
        """
        can = CanManager(interface=interface, bitrate=bitrate)
        io: Optional[IOManager] = None
        if use_io:
            try:
                io = IOManager()
            except Exception:  # noqa: BLE001
                io = None

        runner = ExperimentRunner(self, can=can, io=io, log_dir=log_dir)
        return runner.run_blocking(poll_hz=poll_hz)

    def make_runner(
        self,
        can: Optional[CanManager] = None,
        io: Optional[IOManager] = None,
        log_dir: Optional[str] = None,
        wire_bnc: bool = True,
    ) -> "ExperimentRunner":
        """Build a runner for GUI hosting or synthetic testing."""
        return ExperimentRunner(
            self, can=can, io=io, log_dir=log_dir, wire_bnc=wire_bnc
        )


class ExperimentRunner:
    """
    Owns the tick loop: poll CAN → normalize → dispatch → timers → start/end.

    Use ``run_blocking()`` for headless scripts, or call ``step(now)`` each
    frame when hosted by the DearPyGui app.
    """

    def __init__(
        self,
        experiment: Experiment,
        can: Optional[CanManager] = None,
        io: Optional[IOManager] = None,
        log_dir: Optional[str] = None,
        wire_bnc: bool = True,
    ) -> None:
        self.experiment = experiment
        self.can = can
        self.io = io
        self.ctx = ExperimentContext(
            nodes=experiment.nodes,
            can=can,
            io=io,
            log_dir=log_dir,
            session_name=experiment.name,
        )
        self.normalizer = EventNormalizer()
        self._active = False
        self._finished = False
        self._started = False
        self._owns_can = False
        self._bnc_queue: List[NodeEvent] = []

        # GUI hosts already own BNC edge callbacks; skip double-wiring.
        if wire_bnc and io is not None:
            self._wire_bnc(io)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def is_finished(self) -> bool:
        return self._finished

    def start(self, now: Optional[float] = None) -> None:
        """
        Begin watching start conditions (or activate immediately if none).

        Call once before stepping. Does not open CAN — call ``open()`` first
        if you need a live bus.
        """
        if self._started:
            return
        self._started = True
        now = now if now is not None else time.time()
        self.ctx.set_now(now)
        # If no start_when, activate immediately.
        if self.experiment._start_when is None:
            self._activate(now)

    def open(self) -> None:
        """Open the CanManager if bound and not already open."""
        if self.can is not None and not self.can.is_open:
            self.can.start()
            self._owns_can = True

    def close(self) -> None:
        """Stop the session (if still running) and close owned resources."""
        if self._active and not self._finished:
            self._deactivate(time.time())
        if self._owns_can and self.can is not None:
            self.can.stop()
            self._owns_can = False

    def inject(self, events: Union[NodeEvent, Sequence[NodeEvent]]) -> None:
        """
        Feed synthetic NodeEvents (for unit tests). Dispatched on next step,
        or immediately if already active / waiting on start.
        """
        if isinstance(events, NodeEvent):
            events = [events]
        # Process immediately so tests don't need an extra step for dispatch.
        now = events[0].timestamp if events else time.time()
        self.ctx.set_now(now)
        if not self._started:
            self.start(now)
        self._dispatch_all(list(events), now)
        self._check_end(now)

    def feed_bnc_in(
        self,
        channel: int,
        edge: str,
        now: Optional[float] = None,
        high: bool = True,
    ) -> None:
        """Queue a BNC_IN event (GUI forwards edges when wire_bnc=False)."""
        ts = now if now is not None else time.time()
        self._bnc_queue.append(
            self.normalizer.inject_bnc_in(channel, edge, ts, high=high)
        )

    def step(
        self,
        now: Optional[float] = None,
        messages: Optional[Sequence[Any]] = None,
    ) -> None:
        """
        One tick: normalize CAN frames, dispatch, fire timers, check end.

        When ``messages`` is provided (GUI hosting), those frames are used
        instead of calling ``can.poll_rx()`` — the host already drained the
        shared RX queue. Safe to call from a GUI render loop. No-op after
        the session ends.
        """
        if self._finished:
            return
        if not self._started:
            self.start(now)

        now = now if now is not None else time.time()
        self.ctx.set_now(now)

        events: List[NodeEvent] = []

        # Drain BNC queue (edges from IOManager callbacks or feed_bnc_in).
        if self._bnc_queue:
            events.extend(self._bnc_queue)
            self._bnc_queue = []

        if messages is not None:
            for msg in messages:
                events.extend(self.normalizer.frame_to_events(msg, now))
            events.extend(self.normalizer.check_staleness(now))
        elif self.can is not None and self.can.is_open:
            for msg in self.can.poll_rx():
                events.extend(self.normalizer.frame_to_events(msg, now))
            events.extend(self.normalizer.check_staleness(now))

        self._dispatch_all(events, now)

        if self._active:
            self.ctx.tick_timers(now)

        self._check_end(now)

    def run_blocking(self, poll_hz: float = 50.0) -> ExperimentContext:
        """Open CAN, run until the session ends, then clean up."""
        self.open()
        self.start()
        interval = 1.0 / max(1.0, float(poll_hz))
        try:
            while not self._finished:
                self.step()
                time.sleep(interval)
        except KeyboardInterrupt:
            if self._active and not self._finished:
                self._deactivate(time.time())
        finally:
            self.close()
        return self.ctx

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _wire_bnc(self, io: IOManager) -> None:
        def on_in1() -> None:
            high = True
            try:
                high = io.read_bnc_in1()
            except Exception:  # noqa: BLE001
                pass
            edge = "rising" if high else "falling"
            self._bnc_queue.append(
                self.normalizer.inject_bnc_in(1, edge, time.time(), high=high)
            )

        def on_in2() -> None:
            high = True
            try:
                high = io.read_bnc_in2()
            except Exception:  # noqa: BLE001
                pass
            edge = "rising" if high else "falling"
            self._bnc_queue.append(
                self.normalizer.inject_bnc_in(2, edge, time.time(), high=high)
            )

        io.on_bnc_in1_edge(on_in1)
        io.on_bnc_in2_edge(on_in2)

    def _activate(self, now: float) -> None:
        if self._active or self._finished:
            return
        self._active = True
        self.ctx.begin(now)
        start_ev = NodeEvent(kind=EventKind.SESSION_START, timestamp=now)
        self.ctx.log(
            "session_start",
            experiment=self.experiment.name,
            nodes=self.ctx.nodes,
        )
        for cb in self.experiment._on_start:
            self._safe_call_start(cb)
        self._fire_handlers(start_ev)

    def _deactivate(self, now: float) -> None:
        if self._finished:
            return
        was_active = self._active
        self._active = False
        self._finished = True
        end_ev = NodeEvent(kind=EventKind.SESSION_END, timestamp=now)
        if was_active:
            self.ctx.set_now(now)
            end_fields = {
                "elapsed_s": round(self.ctx.elapsed(), 3),
                "pellets": self.ctx.counter("pellets"),
            }
            if self.ctx.stop_reason:
                end_fields["reason"] = self.ctx.stop_reason
            self.ctx.log("session_end", **end_fields)
            self._fire_handlers(end_ev)
            for cb in self.experiment._on_end:
                self._safe_call_start(cb)
            self.ctx.end()

    def _dispatch_all(self, events: List[NodeEvent], now: float) -> None:
        # Waiting for start_when?
        if self._started and not self._active and not self._finished:
            for ev in events:
                self._fire_handlers(ev)  # allow BNC_IN etc. before start
            if self.experiment._start_when is not None:
                try:
                    if self.experiment._start_when(self.ctx):
                        self._activate(now)
                except Exception as exc:  # noqa: BLE001
                    self.ctx.log("start_when_error", error=str(exc))
            return

        if not self._active:
            return

        for ev in events:
            if self.ctx.stop_requested:
                break
            # Auto-count pellets presented for end_after(pellets=...).
            if ev.kind == EventKind.PELLET_PRESENTED:
                self.ctx.incr("pellets")
            self._fire_handlers(ev)

    def _fire_handlers(self, ev: NodeEvent) -> None:
        for cb in self.experiment._handlers.get(ev.kind, []):
            try:
                cb(self.ctx, ev)
            except Exception as exc:  # noqa: BLE001 — isolate user callbacks
                self.ctx.log(
                    "handler_error",
                    node=ev.node_id,
                    kind=ev.kind.name,
                    error=str(exc),
                )

    def _safe_call_start(self, cb: StartEndCb) -> None:
        try:
            cb(self.ctx)
        except Exception as exc:  # noqa: BLE001
            self.ctx.log("callback_error", error=str(exc))

    def _check_end(self, now: float) -> None:
        if not self._active or self._finished:
            return
        if self.ctx.stop_requested:
            self._deactivate(now)
            return
        exp = self.experiment
        if exp._end_after_s is not None and self.ctx.elapsed() >= exp._end_after_s:
            self._deactivate(now)
            return
        if exp._end_pellets is not None and self.ctx.counter("pellets") >= exp._end_pellets:
            self._deactivate(now)
            return
        if exp._end_when is not None:
            try:
                if exp._end_when(self.ctx):
                    self._deactivate(now)
            except Exception as exc:  # noqa: BLE001
                self.ctx.log("end_when_error", error=str(exc))
