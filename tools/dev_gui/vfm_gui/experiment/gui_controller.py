"""
gui_controller.py — Hosts ExperimentRunner inside the DearPyGui app.

The GUI owns CAN polling and BNC edge callbacks. This controller builds an
experiment from a JSON schema + params, steps it with already-drained CAN
messages, and mirrors experiment log rows into the GUI LogManager.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from ..log_manager import LogEntry, LogManager
from .context import ExperimentLogEntry
from .runner import ExperimentRunner
from .schema import ExperimentDef, build_experiment

if TYPE_CHECKING:
    from ..can_manager import CanManager
    from ..io_manager import IOManager


class ExperimentController:
    """GUI-side host for one experiment session at a time."""

    def __init__(self) -> None:
        self._runner: Optional[ExperimentRunner] = None
        self._exp_def: Optional[ExperimentDef] = None
        self._gui_log: Optional[LogManager] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        return self._runner is not None and self._runner.is_active

    @property
    def is_running(self) -> bool:
        """True while a session has been started and not yet finished."""
        r = self._runner
        return r is not None and r._started and not r.is_finished

    @property
    def runner(self) -> Optional[ExperimentRunner]:
        return self._runner

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(
        self,
        exp_def: ExperimentDef,
        params: Dict[str, Any],
        nodes: Sequence[int],
        can: Optional["CanManager"] = None,
        io: Optional["IOManager"] = None,
        log: Optional[LogManager] = None,
        log_dir: Optional[str] = None,
    ) -> bool:
        """
        Build and start an experiment. Returns False if one is already running.
        Does not open CAN — the GUI session already owns the bus.
        """
        if self.is_running:
            return False

        exp = build_experiment(exp_def, params=params, nodes=nodes)
        runner = exp.make_runner(
            can=can,
            io=io,
            log_dir=log_dir,
            wire_bnc=False,
        )
        self._gui_log = log
        runner.ctx.on_log = self._on_experiment_log
        self._runner = runner
        self._exp_def = exp_def
        runner.start()
        return True

    def stop(self) -> None:
        """End the session (fires on_end, closes experiment CSV)."""
        if self._runner is None:
            return
        self._runner.close()
        self._runner = None
        self._exp_def = None

    def step(
        self,
        messages: Optional[Sequence[Any]] = None,
        now: Optional[float] = None,
    ) -> None:
        """Forward one tick with already-drained CAN frames. No-op when idle."""
        if self._runner is None or self._runner.is_finished:
            if self._runner is not None and self._runner.is_finished:
                # Keep finished runner briefly so status_line can show reason,
                # but do not keep stepping.
                return
            return
        self._runner.step(now=now, messages=messages if messages is not None else [])

    def forward_bnc(
        self,
        which: str,
        ts: Optional[float] = None,
        high: bool = True,
    ) -> None:
        """Forward a GUI BNC IN edge into the running experiment."""
        if self._runner is None or self._runner.is_finished:
            return
        channel = 1 if which.upper() in ("IN1", "1", "BNC_IN1") else 2
        edge = "rising" if high else "falling"
        self._runner.feed_bnc_in(channel, edge, now=ts, high=high)

    def status_line(self) -> str:
        """Short status for the experiment panel."""
        r = self._runner
        if r is None:
            return "Idle"
        if r.is_finished:
            reason = r.ctx.stop_reason or "ended"
            return (
                f"Finished · pellets={r.ctx.counter('pellets')} "
                f"elapsed={r.ctx.elapsed():.1f}s · {reason}"
            )
        if r.is_active:
            name = self._exp_def.label if self._exp_def else r.experiment.name
            return (
                f"Running: {name} · pellets={r.ctx.counter('pellets')} "
                f"elapsed={r.ctx.elapsed():.1f}s"
            )
        if r._started:
            return "Waiting for start condition…"
        return "Idle"

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_experiment_log(self, entry: ExperimentLogEntry) -> None:
        if self._gui_log is None:
            return
        field_str = " ".join(f"{k}={v}" for k, v in entry.fields.items())
        self._gui_log.add(
            LogEntry(
                timestamp=entry.timestamp,
                direction="SYS",
                node_id=entry.node_id,
                frame_type="EXPERIMENT",
                event_name=entry.name,
                raw_id=0,
                raw_data=b"",
                details=field_str,
            )
        )
