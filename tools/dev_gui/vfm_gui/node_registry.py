"""
node_registry.py — Per-node state tracking and three-layer identity mapping.

Maintains the mapping:
  MAC (hardware UUID) → CAN Node ID (bus address) → User Label (GUI only)

NodeState is updated from heartbeat frames and event frames received off the
CAN bus.  Staleness detection marks nodes offline when heartbeats stop arriving.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .protocol import (
    CanEvent,
    DispenseState,
    HeartbeatPayload,
    InputId,
    ServiceStatus,
    format_mac,
)

# A node is marked OFFLINE if no heartbeat arrives within this window.
DEFAULT_OFFLINE_TIMEOUT_S: float = 10.0


@dataclass
class NodeState:
    """Live state for one VFM node."""

    node_id: int
    label: str                              # user-editable (GUI-only)

    # Identity
    mac: Optional[bytes] = None             # 6-byte MAC from discovery
    discovery_state: str = "Pending"        # "Pending", "Announced", "Enabled"

    # Dispenser / sensor state (from heartbeat)
    dispense_state: DispenseState = DispenseState.Idle
    presence: bool = False
    pg1: bool = False                       # pellet in cup
    pg2: bool = False                       # actuator at home/down
    pg3: bool = False                       # dome opened
    fault_code: ServiceStatus = ServiceStatus.Ok
    dome_open_warning: bool = False         # PG3 open >30 s (from DomeOpenWarning)

    # Connectivity
    last_heartbeat_time: Optional[float] = None
    online: bool = False

    # Derived convenience
    @property
    def mac_str(self) -> str:
        return format_mac(self.mac) if self.mac else "—"

    @property
    def heartbeat_age_s(self) -> Optional[float]:
        if self.last_heartbeat_time is None:
            return None
        return time.time() - self.last_heartbeat_time

    @property
    def pg_bits(self) -> int:
        return (int(self.pg1) << 0) | (int(self.pg2) << 1) | (int(self.pg3) << 2)

    @property
    def status_label(self) -> str:
        if not self.online:
            return "OFFLINE"
        # SeekingAway groups with LOWERING for the tile.
        labels = {
            DispenseState.SeekingAway: "LOWERING",
        }
        return labels.get(self.dispense_state, self.dispense_state.name.upper())

    @property
    def status_color(self) -> tuple[int, int, int, int]:
        """RGBA color for the status indicator (0–255 each)."""
        if not self.online:
            return (120, 120, 120, 255)   # grey
        s = self.dispense_state
        if s == DispenseState.Fault:
            return (220, 50, 50, 255)     # red
        if s == DispenseState.Idle:
            return (60, 200, 80, 255)     # green
        if s in (DispenseState.Presented, DispenseState.AccessAttempt):
            return (50, 200, 220, 255)    # cyan
        if s == DispenseState.SeekingAway:
            return (60, 130, 220, 255)    # blue (homing)
        # Lowering / Loading / Raising
        return (60, 130, 220, 255)        # blue


class NodeRegistry:
    """
    Registry of all expected nodes.

    Pre-creates `num_nodes` slots on init (labels "Node 1"…"Node N").
    Nodes are populated with MAC and discovery state as discovery proceeds.
    """

    def __init__(self, num_nodes: int) -> None:
        assert 1 <= num_nodes <= 254, "num_nodes must be 1–254"
        self._nodes: Dict[int, NodeState] = {
            i: NodeState(node_id=i, label=f"Node {i}")
            for i in range(1, num_nodes + 1)
        }
        self._offline_timeout = DEFAULT_OFFLINE_TIMEOUT_S

    # ------------------------------------------------------------------
    # Registry access
    # ------------------------------------------------------------------

    def get(self, node_id: int) -> Optional[NodeState]:
        return self._nodes.get(node_id)

    def all_nodes(self) -> List[NodeState]:
        return list(self._nodes.values())

    def num_nodes(self) -> int:
        return len(self._nodes)

    # ------------------------------------------------------------------
    # Updates from CAN frames
    # ------------------------------------------------------------------

    def update_from_heartbeat(self, node_id: int, hb: HeartbeatPayload) -> None:
        """Apply a decoded heartbeat payload to the node's state."""
        node = self._get_or_create(node_id)
        node.dispense_state = hb.dispense_state
        node.presence = hb.presence
        node.pg1 = hb.pg1
        node.pg2 = hb.pg2
        node.pg3 = hb.pg3
        node.fault_code = hb.fault_code
        node.last_heartbeat_time = time.time()
        node.online = True
        if node.discovery_state == "Pending":
            # Node is heartbeating even without formal discovery (e.g. manual ID)
            node.discovery_state = "Enabled"

    def update_from_event(
        self,
        node_id: int,
        event: CanEvent,
        fault_code: Optional[ServiceStatus] = None,
    ) -> None:
        """Update node state based on a received event."""
        node = self._get_or_create(node_id)
        node.online = True
        # Mirror dispense state transitions from events for better responsiveness
        # (the next heartbeat will confirm the actual state anyway)
        state_map = {
            CanEvent.Lowering:        DispenseState.Lowering,
            CanEvent.Loading:         DispenseState.Loading,
            CanEvent.PelletLoaded:    DispenseState.Loading,
            CanEvent.Raising:         DispenseState.Raising,
            CanEvent.PelletPresented: DispenseState.Presented,
            CanEvent.AccessAttempt:   DispenseState.AccessAttempt,
            CanEvent.Fault:           DispenseState.Fault,
        }
        if event in state_map:
            node.dispense_state = state_map[event]
        if event == CanEvent.Fault and fault_code is not None:
            node.fault_code = fault_code
        if event == CanEvent.DomeOpenWarning:
            node.dome_open_warning = True

    def update_from_input(self, node_id: int, input_id: InputId, active: bool) -> None:
        """Apply an immediate InputChanged event without waiting for heartbeat."""
        node = self._get_or_create(node_id)
        node.online = True
        if input_id == InputId.PG1:
            node.pg1 = active
        elif input_id == InputId.PG2:
            node.pg2 = active
        elif input_id == InputId.PG3:
            node.pg3 = active
            if not active:
                node.dome_open_warning = False
        elif input_id == InputId.Presence:
            node.presence = active

    def clear_fault(self, node_id: int) -> None:
        node = self._get_or_create(node_id)
        node.fault_code = ServiceStatus.Ok
        if node.dispense_state == DispenseState.Fault:
            node.dispense_state = DispenseState.Idle
        node.dome_open_warning = False

    def register_node(self, node_id: int, mac: bytes, source: str = "ANNOUNCE") -> None:
        """Register a node's MAC address from discovery."""
        node = self._get_or_create(node_id)
        node.mac = mac
        node.discovery_state = "Enabled"

    # ------------------------------------------------------------------
    # User-facing operations
    # ------------------------------------------------------------------

    def set_label(self, node_id: int, label: str) -> None:
        """Rename a node's user label (GUI-only, never touches CAN)."""
        node = self._get_or_create(node_id)
        node.label = label.strip() or f"Node {node_id}"

    # ------------------------------------------------------------------
    # Staleness
    # ------------------------------------------------------------------

    def set_offline_timeout(self, seconds: float) -> None:
        self._offline_timeout = seconds

    def check_staleness(self) -> List[int]:
        """
        Mark nodes offline if their last heartbeat is older than the timeout.

        Returns list of node IDs that were just marked offline.
        """
        now = time.time()
        newly_offline = []
        for node in self._nodes.values():
            if node.online and node.last_heartbeat_time is not None:
                if (now - node.last_heartbeat_time) > self._offline_timeout:
                    node.online = False
                    newly_offline.append(node.node_id)
        return newly_offline

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_or_create(self, node_id: int) -> NodeState:
        """Return existing node or create a new slot (handles nodes outside expected range)."""
        if node_id not in self._nodes:
            self._nodes[node_id] = NodeState(node_id=node_id, label=f"Node {node_id}")
        return self._nodes[node_id]
