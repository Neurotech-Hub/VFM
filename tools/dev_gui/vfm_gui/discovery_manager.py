"""
discovery_manager.py — Base station side of the AEI/AEO discovery protocol.

The base station drives the AEO GPIO HIGH to enable the first node, then
listens for ANNOUNCE (new node, no saved ID) and REJOIN (returning node,
saved ID) frames on the CAN bus.

Discovery flow per node slot:
  1. AEO pin driven HIGH (done once at start, propagates through daisy chain)
  2. Node sends ANNOUNCE(MAC) on 0x080
  3. Base sends ASSIGN(MAC, nextId) on 0x081
  4. Node sends ACK(MAC, id) on 0x082 → node is registered, moves to Enabled
  5. Repeat for next node (their AEI goes HIGH after upstream AEO rises)

For returning nodes (NVS has saved ID):
  Node sends REJOIN(MAC, savedId) on 0x083 → base registers immediately.

GPIO note:
  AEO (GPIO27) is driven through the shared IOManager (see io_manager.py),
  DiscoveryManager just calls io_manager.drive_aeo(). 
  On vcan0 / dev machines with no GPIO hardware,
  IOManager degrades to a no-op automatically.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Callable, Dict, List, Optional

from .protocol import (
    CAN_ID_ANNOUNCE,
    CAN_ID_ASSIGN,
    CAN_ID_ACK,
    CAN_ID_REJOIN,
    format_mac,
    parse_discovery,
)

if TYPE_CHECKING:
    from .io_manager import IOManager

# How long to wait for new ANNOUNCE/REJOIN before declaring discovery complete.
# Generous for real hardware: nodes stay in WaitAEI until AEO is wired/driven.
DISCOVERY_IDLE_TIMEOUT_S: float = 30.0


class DiscoveryPhase(Enum):
    Idle       = auto()  # not started
    Running    = auto()  # AEO driven HIGH, waiting for nodes
    Complete   = auto()  # no more nodes announcing


@dataclass
class DiscoveredNode:
    node_id: int
    mac: bytes
    source: str  # "ANNOUNCE" or "REJOIN"
    timestamp: float = field(default_factory=time.time)


class DiscoveryManager:
    """
    Manages the AEI/AEO discovery handshake from the base station side.

    Usage::

        dm = DiscoveryManager(can_manager, io_manager)
        dm.on_node_discovered(lambda node: registry.register_node(node.node_id, node.mac))
        dm.start()
        # In render loop:
        dm.handle_frame(msg)  # for each incoming CAN message
    """

    def __init__(self, can_manager, io_manager: "IOManager") -> None:
        self._can = can_manager
        self._io = io_manager
        self._phase = DiscoveryPhase.Idle
        self._next_id: int = 1
        self._last_activity: float = 0.0
        self._pending_assign: Optional[bytes] = None  # MAC waiting for ACK
        self._pending_assign_id: Optional[int] = None
        self._discovered: List[DiscoveredNode] = []
        self._node_callback: Optional[Callable[[DiscoveredNode], None]] = None
        self._complete_callback: Optional[Callable[[], None]] = None

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def on_node_discovered(self, cb: Callable[[DiscoveredNode], None]) -> None:
        """Called each time a node completes discovery (ANNOUNCE+ACK or REJOIN)."""
        self._node_callback = cb

    def on_complete(self, cb: Callable[[], None]) -> None:
        """Called when discovery times out with no new nodes."""
        self._complete_callback = cb

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, start_id: int = 1, clear_first: bool = False) -> None:
        """
        Begin discovery.  Drives AEO HIGH to enable the first node.

        Args:
            start_id: first CAN Node ID to assign (usually 1).
            clear_first: broadcast ClearId before pulsing AEO so nodes with
                         saved NVS IDs re-enter WaitAEI and re-announce.
        """
        self._next_id = start_id
        self._discovered.clear()
        self._pending_assign = None
        self._pending_assign_id = None
        self._last_activity = time.time()
        self._phase = DiscoveryPhase.Running

        if clear_first:
            from .protocol import CanCmd
            self._can.send_broadcast(CanCmd.ClearId)
            time.sleep(0.05)

        # Pulse AEO so nodes see a rising edge on AEI and begin discovery.
        self._io.drive_aeo(False)
        time.sleep(0.05)
        self._io.drive_aeo(True)

    def reset(self) -> None:
        """Reset and restart discovery from scratch (clears node NVS IDs)."""
        self._phase = DiscoveryPhase.Idle
        self.start(clear_first=True)

    def rediscover(self) -> None:
        """
        Re-open the discovery window WITHOUT clearing any node's saved NVS
        ID. Existing assignments are preserved: already-discovered nodes are
        kept, and new IDs continue from one past the highest known ID.
        Returning nodes simply REJOIN; only genuinely new nodes ANNOUNCE.
        """
        self._next_id = max(
            [self._next_id] + [n.node_id for n in self._discovered]
        ) + 1 if self._discovered else self._next_id
        self._pending_assign = None
        self._pending_assign_id = None
        self._last_activity = time.time()
        self._phase = DiscoveryPhase.Running

        # Pulse AEO so nodes see a rising edge on AEI and begin discovery.
        # No ClearId broadcast here — saved NVS IDs are left intact.
        self._io.drive_aeo(False)
        time.sleep(0.05)
        self._io.drive_aeo(True)

    def stop(self) -> None:
        """Abort discovery (does not drive AEO LOW — nodes keep their IDs)."""
        self._phase = DiscoveryPhase.Complete

    # ------------------------------------------------------------------
    # Frame handler — call this from the render loop for every incoming frame
    # ------------------------------------------------------------------

    def handle_frame(self, arb_id: int, data: bytes) -> bool:
        """
        Process an incoming CAN frame.

        Returns True if the frame was a discovery frame and was handled.
        """
        if arb_id not in (CAN_ID_ANNOUNCE, CAN_ID_ACK, CAN_ID_REJOIN):
            return False

        # Late arrivals after idle timeout: reopen the window so real
        # CanNode firmware that announced slowly still gets an ASSIGN.
        if self._phase == DiscoveryPhase.Complete:
            if arb_id in (CAN_ID_ANNOUNCE, CAN_ID_REJOIN, CAN_ID_ACK):
                self._phase = DiscoveryPhase.Running
            else:
                return False
        elif self._phase != DiscoveryPhase.Running:
            return False

        info = parse_discovery(arb_id, data)
        if info is None:
            return False

        self._last_activity = time.time()

        if arb_id == CAN_ID_ANNOUNCE:
            self._handle_announce(info["mac"])
        elif arb_id == CAN_ID_ACK:
            self._handle_ack(info["mac"], info["node_id"])
        elif arb_id == CAN_ID_REJOIN:
            self._handle_rejoin(info["mac"], info["node_id"])

        return True

    def tick(self) -> None:
        """
        Call once per render frame to check for discovery timeout.
        Fires on_complete when no new nodes have announced for DISCOVERY_IDLE_TIMEOUT_S.
        """
        if self._phase != DiscoveryPhase.Running:
            return
        if time.time() - self._last_activity > DISCOVERY_IDLE_TIMEOUT_S:
            self._phase = DiscoveryPhase.Complete
            if self._complete_callback:
                self._complete_callback()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def phase(self) -> DiscoveryPhase:
        return self._phase

    @property
    def is_running(self) -> bool:
        return self._phase == DiscoveryPhase.Running

    @property
    def is_complete(self) -> bool:
        return self._phase == DiscoveryPhase.Complete

    @property
    def discovered_nodes(self) -> List[DiscoveredNode]:
        return list(self._discovered)

    @property
    def next_node_id(self) -> int:
        return self._next_id

    @property
    def pending_mac(self) -> Optional[bytes]:
        return self._pending_assign

    @property
    def pending_id(self) -> Optional[int]:
        return self._pending_assign_id

    # ------------------------------------------------------------------
    # Internal handlers
    # ------------------------------------------------------------------

    def _handle_announce(self, mac: bytes) -> None:
        """New node, no saved ID — assign the next available ID."""
        # Retransmit ASSIGN if the node is retrying for the same pending MAC
        if self._pending_assign == mac and self._pending_assign_id is not None:
            self._can.send_assign(mac, self._pending_assign_id)
            return
        node_id = self._next_id
        self._next_id += 1
        self._pending_assign = mac
        self._pending_assign_id = node_id
        self._can.send_assign(mac, node_id)

    def _handle_ack(self, mac: bytes, node_id: Optional[int]) -> None:
        """Node confirmed receipt of ASSIGN."""
        if self._pending_assign and mac == self._pending_assign:
            self._pending_assign = None
            self._pending_assign_id = None
            if node_id is None:
                return
            node = DiscoveredNode(node_id=node_id, mac=mac, source="ANNOUNCE")
            self._discovered.append(node)
            if self._node_callback:
                self._node_callback(node)

    def _handle_rejoin(self, mac: bytes, node_id: int) -> None:
        """Returning node — already has a saved ID from NVS."""
        if node_id >= self._next_id:
            self._next_id = node_id + 1
        node = DiscoveredNode(node_id=node_id, mac=mac, source="REJOIN")
        self._discovered.append(node)
        if self._node_callback:
            self._node_callback(node)
