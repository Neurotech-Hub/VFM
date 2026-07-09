"""
can_manager.py — SocketCAN wrapper with threaded RX loop.

Opens a python-can Bus on the given SocketCAN interface and runs a daemon
thread that drains received frames into a thread-safe queue.  The GUI's
render callback calls poll_rx() each frame to retrieve buffered messages
without ever blocking the UI thread.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable, List, Optional

try:
    import can
except ImportError as e:
    raise ImportError("python-can is required: pip install python-can") from e

from .protocol import (
    CanCmd,
    CAN_CMD_BASE,
    CAN_CMD_BROADCAST,
    CAN_ID_ASSIGN,
    build_cmd_frame,
    build_assign_frame,
)


class CanManager:
    """
    Thin SocketCAN wrapper for the VFM base station.

    Usage::

        mgr = CanManager(interface="can0", bitrate=250_000)
        mgr.start()
        # ... in render loop:
        for msg in mgr.poll_rx():
            process(msg)
        mgr.stop()
    """

    def __init__(self, interface: str = "can0", bitrate: int = 250_000) -> None:
        self._interface = interface
        self._bitrate = bitrate
        self._bus: Optional[can.BusABC] = None
        self._rx_queue: queue.Queue[can.Message] = queue.Queue(maxsize=512)
        self._rx_thread: Optional[threading.Thread] = None
        self._running = False
        self._error_callback: Optional[Callable[[Exception], None]] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Open the CAN interface and start the RX daemon thread."""
        self._bus = can.interface.Bus(
            channel=self._interface,
            interface="socketcan",
            bitrate=self._bitrate,
        )
        self._running = True
        self._rx_thread = threading.Thread(
            target=self._rx_loop,
            name="vfm-can-rx",
            daemon=True,
        )
        self._rx_thread.start()

    def stop(self) -> None:
        """Stop the RX thread and close the CAN interface cleanly."""
        self._running = False
        if self._rx_thread is not None:
            self._rx_thread.join(timeout=2.0)
            self._rx_thread = None
        if self._bus is not None:
            self._bus.shutdown()
            self._bus = None

    @property
    def is_open(self) -> bool:
        return self._bus is not None and self._running

    # ------------------------------------------------------------------
    # Receive
    # ------------------------------------------------------------------

    def poll_rx(self) -> List[can.Message]:
        """
        Drain all currently buffered received messages.

        Call this once per GUI render frame.  Never blocks.
        """
        messages: List[can.Message] = []
        while True:
            try:
                messages.append(self._rx_queue.get_nowait())
            except queue.Empty:
                break
        return messages

    def on_error(self, callback: Callable[[Exception], None]) -> None:
        """Register a callback for RX thread errors."""
        self._error_callback = callback

    # ------------------------------------------------------------------
    # Transmit helpers
    # ------------------------------------------------------------------

    def send_command(
        self,
        node_id: int,
        cmd: CanCmd,
        payload: bytes = b"",
    ) -> bool:
        """
        Send a command frame to a specific node (or broadcast if node_id == 0).

        Returns True if the frame was queued successfully.
        """
        arb_id, data = build_cmd_frame(node_id, cmd, payload)
        return self._send(arb_id, data)

    def send_broadcast(self, cmd: CanCmd, payload: bytes = b"") -> bool:
        """Send a broadcast command (CAN ID 0x100, reaches all nodes)."""
        return self.send_command(0, cmd, payload)

    def send_assign(self, mac: bytes, node_id: int) -> bool:
        """
        Send a discovery ASSIGN frame: base → node.

        Payload: MAC(6) + nodeId(1) on CAN ID 0x081.
        """
        arb_id, data = build_assign_frame(mac, node_id)
        return self._send(arb_id, data)

    def send_raw(self, arb_id: int, data: bytes) -> bool:
        """Send a raw CAN frame — for the 'raw frame' debug panel."""
        return self._send(arb_id, data[:8])

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _send(self, arb_id: int, data: bytes) -> bool:
        if self._bus is None:
            return False
        msg = can.Message(
            arbitration_id=arb_id,
            data=data,
            is_extended_id=False,  # 11-bit standard IDs
        )
        try:
            self._bus.send(msg, timeout=0.02)
            return True
        except can.CanError:
            return False

    def _rx_loop(self) -> None:
        """Daemon thread: read frames and push to the queue."""
        while self._running:
            try:
                msg = self._bus.recv(timeout=0.1)  # 100 ms poll
                if msg is not None:
                    try:
                        self._rx_queue.put_nowait(msg)
                    except queue.Full:
                        # Drop oldest to make room
                        try:
                            self._rx_queue.get_nowait()
                            self._rx_queue.put_nowait(msg)
                        except queue.Empty:
                            pass
            except Exception as exc:
                if self._running and self._error_callback:
                    self._error_callback(exc)
