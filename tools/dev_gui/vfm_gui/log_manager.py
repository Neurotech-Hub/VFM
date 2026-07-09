"""
log_manager.py — In-memory event ring buffer with CSV auto-save.

Every CAN frame (received or sent) is recorded as a LogEntry.  The display
buffer is a fixed-size deque (ring buffer); the CSV file grows unbounded for
the session and is never truncated.

Performance note:
  At 9 nodes × 1 Hz heartbeat + events ≈ ~36 msgs/sec peak.
  A 1,000-entry deque update is O(1) amortized.  CSV writes use line-buffered
  IO so each append is a single fwrite — no performance concern.
"""

from __future__ import annotations

import csv
import os
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Deque, List, Optional


@dataclass
class LogEntry:
    """One logged CAN frame (sent or received)."""

    timestamp: float                # time.time()
    direction: str                  # "TX" or "RX"
    node_id: int                    # 0 = broadcast / discovery
    frame_type: str                 # "COMMAND", "EVENT", "HEARTBEAT", "DISCOVERY", "UNKNOWN"
    event_name: str                 # Human-readable name e.g. "Dispense", "PelletLoaded"
    raw_id: int                     # CAN arbitration ID
    raw_data: bytes                 # CAN payload
    details: str = ""               # Extra human-readable context

    @property
    def timestamp_str(self) -> str:
        dt = datetime.fromtimestamp(self.timestamp)
        return dt.strftime("%H:%M:%S.%f")[:-3]   # HH:MM:SS.mmm

    @property
    def timestamp_iso(self) -> str:
        return datetime.fromtimestamp(self.timestamp).isoformat(timespec="milliseconds")

    @property
    def timestamp_ms(self) -> int:
        return int(self.timestamp * 1000)

    @property
    def raw_data_hex(self) -> str:
        return " ".join(f"{b:02X}" for b in self.raw_data)

    @property
    def raw_id_hex(self) -> str:
        return f"0x{self.raw_id:03X}"


class LogManager:
    """
    In-memory log buffer + optional CSV auto-save.

    Usage::

        lm = LogManager(log_dir="~/vfm_logs", auto_save=True)
        lm.add(LogEntry(...))
        entries = lm.get_filtered(show_heartbeats=False)
        lm.export("~/Desktop/my_session.csv")
    """

    CSV_HEADER = [
        "timestamp_iso",
        "timestamp_ms",
        "direction",
        "node_id",
        "frame_type",
        "event_name",
        "raw_id_hex",
        "raw_data_hex",
        "details",
    ]

    def __init__(
        self,
        max_entries: int = 1000,
        log_dir: str = "~/vfm_logs",
        auto_save: bool = True,
    ) -> None:
        self._max_entries = max_entries
        self._buffer: Deque[LogEntry] = deque(maxlen=max_entries)
        self._auto_save = auto_save
        self._csv_path: Optional[Path] = None
        self._csv_file = None
        self._csv_writer = None

        if auto_save:
            self._open_csv(log_dir)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def add(self, entry: LogEntry) -> None:
        """Append an entry to the ring buffer and (optionally) CSV."""
        self._buffer.append(entry)
        if self._auto_save and self._csv_writer is not None:
            self._csv_writer.writerow(self._entry_to_row(entry))
            self._csv_file.flush()

    # ------------------------------------------------------------------
    # Read / filter
    # ------------------------------------------------------------------

    def get_filtered(
        self,
        node_id: Optional[int] = None,
        frame_type: Optional[str] = None,
        show_heartbeats: bool = False,
    ) -> List[LogEntry]:
        """
        Return a filtered view of the buffer (newest-first).

        Args:
            node_id:        Filter to a specific node; None = all nodes.
            frame_type:     Filter to a type ("EVENT", "COMMAND", etc.); None = all.
            show_heartbeats: Include HEARTBEAT frames (hidden by default).
        """
        result = []
        for entry in reversed(self._buffer):
            if not show_heartbeats and entry.frame_type == "HEARTBEAT":
                continue
            if node_id is not None and entry.node_id != node_id:
                continue
            if frame_type is not None and entry.frame_type != frame_type:
                continue
            result.append(entry)
        return result

    def all_entries(self) -> List[LogEntry]:
        """All buffered entries, oldest-first."""
        return list(self._buffer)

    @property
    def total_count(self) -> int:
        return len(self._buffer)

    @property
    def csv_path(self) -> Optional[Path]:
        return self._csv_path

    # ------------------------------------------------------------------
    # Management
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Clear the display buffer (CSV file is NOT truncated — it keeps growing)."""
        self._buffer.clear()

    def export(self, filepath: str) -> Path:
        """
        Write the current buffer to a new CSV file.

        Returns the Path of the written file.
        """
        path = Path(filepath).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(self.CSV_HEADER)
            for entry in self._buffer:
                writer.writerow(self._entry_to_row(entry))
        return path

    def close(self) -> None:
        """Flush and close the CSV file."""
        if self._csv_file is not None:
            self._csv_file.flush()
            self._csv_file.close()
            self._csv_file = None
            self._csv_writer = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _open_csv(self, log_dir: str) -> None:
        dir_path = Path(log_dir).expanduser().resolve()
        dir_path.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._csv_path = dir_path / f"session_{timestamp}.csv"
        self._csv_file = open(self._csv_path, "w", newline="", buffering=1)
        self._csv_writer = csv.writer(self._csv_file)
        self._csv_writer.writerow(self.CSV_HEADER)

    @staticmethod
    def _entry_to_row(entry: LogEntry) -> list:
        return [
            entry.timestamp_iso,
            entry.timestamp_ms,
            entry.direction,
            entry.node_id,
            entry.frame_type,
            entry.event_name,
            entry.raw_id_hex,
            entry.raw_data_hex,
            entry.details,
        ]
