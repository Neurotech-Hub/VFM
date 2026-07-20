"""
context.py — User-facing ExperimentContext passed into callbacks.

Provides actions (dispense, abort, BNC pulse), timers (after / every),
named counters, elapsed time, and experiment-level logging.
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from ..protocol import CanCmd, build_setconfig_heartbeat

if TYPE_CHECKING:
    from ..can_manager import CanManager
    from ..io_manager import IOManager


TimerCallback = Callable[[], None]


@dataclass
class _Timer:
    fire_at: float
    callback: TimerCallback
    interval: Optional[float] = None  # None = one-shot; else repeating
    cancelled: bool = False


@dataclass
class ExperimentLogEntry:
    """One experiment-level log row (distinct from raw CAN LogEntry)."""

    timestamp: float
    name: str
    node_id: int = 0
    fields: Dict[str, Any] = field(default_factory=dict)

    @property
    def timestamp_iso(self) -> str:
        return datetime.fromtimestamp(self.timestamp).isoformat(timespec="milliseconds")


class ExperimentContext:
    """
    Surface available to user callbacks.

    Constructed by ExperimentRunner and passed as the first argument to
    every registered handler. Actions go through CanManager / IOManager;
    timers and counters live on this object.
    """

    CSV_HEADER = [
        "timestamp_iso",
        "timestamp_ms",
        "elapsed_s",
        "name",
        "node_id",
        "fields",
    ]

    def __init__(
        self,
        nodes: List[int],
        can: Optional["CanManager"] = None,
        io: Optional["IOManager"] = None,
        log_dir: Optional[str] = None,
        session_name: str = "experiment",
    ) -> None:
        self.nodes: List[int] = list(nodes)
        self._can = can
        self._io = io
        self._start_time: Optional[float] = None
        self._now: float = time.time()  # updated each runner step
        self._counters: Dict[str, int] = {}
        self._timers: List[_Timer] = []
        self._log_entries: List[ExperimentLogEntry] = []
        self._csv_file = None
        self._csv_writer = None
        self._log_path: Optional[Path] = None
        self._session_name = session_name
        self._log_dir = log_dir
        # Optional sink so a GUI host can mirror experiment log rows.
        self.on_log: Optional[Callable[[ExperimentLogEntry], None]] = None
        # Commands issued during the session (for tests / inspection).
        self.commands_sent: List[tuple] = []
        # Set by stop(); the runner ends the session on the next end-check.
        self._stop_requested: bool = False
        self._stop_reason: str = ""

    # ------------------------------------------------------------------
    # Lifecycle (called by runner)
    # ------------------------------------------------------------------

    def bind(self, can: Optional["CanManager"], io: Optional["IOManager"] = None) -> None:
        self._can = can
        if io is not None:
            self._io = io

    def set_now(self, now: float) -> None:
        """Advance the context clock (called by the runner each step)."""
        self._now = now

    def begin(self, now: Optional[float] = None) -> None:
        """Mark session start and open the experiment CSV if configured."""
        self._now = now if now is not None else time.time()
        self._start_time = self._now
        self._open_csv()

    def end(self) -> None:
        """Close the experiment CSV."""
        self._close_csv()

    def stop(self, reason: str = "stopped") -> None:
        """
        Request that the runner end the session as soon as possible.

        Cancels pending timers immediately so reload/dispense callbacks
        cannot fire after a fault or other stop condition.
        """
        if self._stop_requested:
            return
        self._stop_requested = True
        self._stop_reason = str(reason)
        self.cancel_all_timers()
        self.log("stop_requested", reason=self._stop_reason)

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    @property
    def stop_reason(self) -> str:
        return self._stop_reason

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def dispense(self, node: int) -> bool:
        """Send Dispense to one node."""
        return self._send(node, CanCmd.Dispense)

    def abort(self, node: int) -> bool:
        """Send Abort to one node."""
        return self._send(node, CanCmd.Abort)

    def broadcast_dispense(self) -> bool:
        """Send Dispense to all nodes (broadcast)."""
        return self._send(0, CanCmd.Dispense)

    def broadcast_abort(self) -> bool:
        """Send Abort to all nodes (broadcast)."""
        return self._send(0, CanCmd.Abort)

    def set_heartbeat_interval(self, node: int, ms: int) -> bool:
        """SetConfig HeartbeatInterval for one node."""
        payload = build_setconfig_heartbeat(ms)
        return self._send(node, CanCmd.SetConfig, payload)

    def bnc_pulse(self, duration_us: int = 100) -> None:
        """Pulse BNC OUT. No-op when no IOManager is bound."""
        if self._io is not None:
            self._io.pulse_bnc_out(duration_us)
        self.log("bnc_pulse", duration_us=duration_us)

    # ------------------------------------------------------------------
    # Timers
    # ------------------------------------------------------------------

    def after(self, seconds: float, callback: TimerCallback) -> _Timer:
        """Schedule a one-shot callback after ``seconds`` (uses runner clock)."""
        timer = _Timer(fire_at=self._now + max(0.0, float(seconds)), callback=callback)
        self._timers.append(timer)
        return timer

    def every(self, seconds: float, callback: TimerCallback) -> _Timer:
        """Schedule a repeating callback every ``seconds`` (uses runner clock)."""
        interval = max(0.001, float(seconds))
        timer = _Timer(fire_at=self._now + interval, callback=callback, interval=interval)
        self._timers.append(timer)
        return timer

    def cancel_timer(self, timer: _Timer) -> None:
        timer.cancelled = True

    def cancel_all_timers(self) -> None:
        """Cancel every pending one-shot / repeating timer."""
        for timer in self._timers:
            timer.cancelled = True
        self._timers = []

    def tick_timers(self, now: float) -> None:
        """Fire due timers. Called by the runner each step."""
        if self._stop_requested:
            return
        self._now = now
        due = [t for t in self._timers if not t.cancelled and t.fire_at <= now]
        for timer in due:
            try:
                timer.callback()
            except Exception as exc:  # noqa: BLE001 — user callbacks
                self.log("timer_error", error=str(exc))
            if timer.interval is not None and not timer.cancelled:
                timer.fire_at = now + timer.interval
            else:
                timer.cancelled = True
        self._timers = [t for t in self._timers if not t.cancelled]

    # ------------------------------------------------------------------
    # Counters / elapsed
    # ------------------------------------------------------------------

    def counter(self, name: str) -> int:
        """Return the current value of a named counter (default 0)."""
        return self._counters.get(name, 0)

    def incr(self, name: str, amount: int = 1) -> int:
        """Increment a named counter and return the new value."""
        self._counters[name] = self._counters.get(name, 0) + amount
        return self._counters[name]

    def set_counter(self, name: str, value: int) -> None:
        self._counters[name] = int(value)

    def elapsed(self) -> float:
        """Seconds since session begin (uses runner clock). 0.0 before begin()."""
        if self._start_time is None:
            return 0.0
        return max(0.0, self._now - self._start_time)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log(self, name: str, node: int = 0, **fields: Any) -> None:
        """Append an experiment-level log entry (and write to CSV if open)."""
        ts = self._now
        entry = ExperimentLogEntry(
            timestamp=ts,
            name=name,
            node_id=node,
            fields=dict(fields),
        )
        self._log_entries.append(entry)
        if self._csv_writer is not None:
            field_str = " ".join(f"{k}={v}" for k, v in fields.items())
            self._csv_writer.writerow(
                [
                    entry.timestamp_iso,
                    int(ts * 1000),
                    f"{self.elapsed():.3f}",
                    name,
                    node,
                    field_str,
                ]
            )
            if self._csv_file is not None:
                self._csv_file.flush()
        if self.on_log is not None:
            try:
                self.on_log(entry)
            except Exception:  # noqa: BLE001 — GUI sink must not break callbacks
                pass

    @property
    def log_entries(self) -> List[ExperimentLogEntry]:
        return list(self._log_entries)

    @property
    def log_path(self) -> Optional[Path]:
        return self._log_path

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _send(self, node_id: int, cmd: CanCmd, payload: bytes = b"") -> bool:
        self.commands_sent.append((node_id, cmd, payload))
        self.log(
            "command",
            node=node_id,
            cmd=cmd.name,
            payload_hex=payload.hex() if payload else "",
        )
        if self._can is None:
            return True  # dry-run / test mode
        return self._can.send_command(node_id, cmd, payload)

    def _open_csv(self) -> None:
        if not self._log_dir:
            return
        log_dir = Path(self._log_dir).expanduser()
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in self._session_name)
        self._log_path = log_dir / f"experiment_{safe_name}_{stamp}.csv"
        self._csv_file = open(self._log_path, "w", newline="", encoding="utf-8")
        self._csv_writer = csv.writer(self._csv_file)
        self._csv_writer.writerow(self.CSV_HEADER)

    def _close_csv(self) -> None:
        if self._csv_file is not None:
            self._csv_file.close()
            self._csv_file = None
            self._csv_writer = None
