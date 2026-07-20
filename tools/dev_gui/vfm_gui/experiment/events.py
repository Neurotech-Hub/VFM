"""
events.py — Normalized experiment event model.

Adapts raw CAN frames (via protocol.py) into NodeEvent objects that user
callbacks consume. Also derives higher-level events such as DOME_CLOSED
from PG3 edge transitions and NODE_ONLINE/OFFLINE from heartbeats.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional

from ..protocol import (
    CanEvent,
    InputId,
    ServiceStatus,
    classify_frame,
    node_id_from_event_id,
    node_id_from_hb_id,
    parse_event,
    parse_fault_code,
    parse_heartbeat,
    parse_input_changed,
)


class EventKind(Enum):
    """Normalized event kinds consumed by experiment callbacks."""

    # Direct CAN events
    PELLET_LOADED = auto()
    PELLET_PRESENTED = auto()
    ACCESS_ATTEMPT = auto()
    FAULT = auto()
    LOWERING = auto()
    LOADING = auto()
    RAISING = auto()
    DOME_OPEN_WARNING = auto()
    PRESENCE_CHANGED = auto()
    PG_CHANGED = auto()
    HEARTBEAT = auto()

    # Derived by the engine
    DOME_OPENED = auto()
    DOME_CLOSED = auto()
    NODE_ONLINE = auto()
    NODE_OFFLINE = auto()

    # Base-station / session
    BNC_IN = auto()
    SESSION_START = auto()
    SESSION_END = auto()
    TIMER = auto()


# Map CanEvent → EventKind for the direct (non-InputChanged) events.
_CAN_EVENT_TO_KIND: Dict[CanEvent, EventKind] = {
    CanEvent.PelletLoaded: EventKind.PELLET_LOADED,
    CanEvent.PelletPresented: EventKind.PELLET_PRESENTED,
    CanEvent.AccessAttempt: EventKind.ACCESS_ATTEMPT,
    CanEvent.Fault: EventKind.FAULT,
    CanEvent.Lowering: EventKind.LOWERING,
    CanEvent.Loading: EventKind.LOADING,
    CanEvent.Raising: EventKind.RAISING,
    CanEvent.DomeOpenWarning: EventKind.DOME_OPEN_WARNING,
}


@dataclass
class NodeEvent:
    """One normalized experiment event."""

    kind: EventKind
    node_id: int = 0  # 0 = session / base-station (BNC, SESSION_*)
    timestamp: float = 0.0
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _NodeTrack:
    """Per-node edge-tracking state used to derive higher-level events."""

    online: bool = False
    last_heartbeat: Optional[float] = None
    pg3: bool = False
    presence: bool = False
    pg1: bool = False
    pg2: bool = False


class EventNormalizer:
    """
    Stateful adapter: CAN frames → list[NodeEvent].

    Tracks per-node PG3 / presence / online status so it can emit derived
    events (DOME_OPENED/CLOSED, NODE_ONLINE/OFFLINE).
    """

    def __init__(self, online_timeout_s: float = 10.0) -> None:
        self._tracks: Dict[int, _NodeTrack] = {}
        self._online_timeout_s = online_timeout_s

    def _track(self, node_id: int) -> _NodeTrack:
        if node_id not in self._tracks:
            self._tracks[node_id] = _NodeTrack()
        return self._tracks[node_id]

    def frame_to_events(self, msg: Any, now: float) -> List[NodeEvent]:
        """
        Convert a python-can Message into zero or more NodeEvents.

        ``msg`` is expected to have ``.arbitration_id`` and ``.data``.
        """
        arb_id = int(msg.arbitration_id)
        data = bytes(msg.data)
        kind = classify_frame(arb_id)

        if kind == "HEARTBEAT":
            return self._from_heartbeat(arb_id, data, now)
        if kind == "EVENT":
            return self._from_event(arb_id, data, now)
        return []

    def check_staleness(self, now: float) -> List[NodeEvent]:
        """Emit NODE_OFFLINE for nodes that have gone silent."""
        out: List[NodeEvent] = []
        for node_id, track in self._tracks.items():
            if not track.online or track.last_heartbeat is None:
                continue
            if now - track.last_heartbeat > self._online_timeout_s:
                track.online = False
                out.append(
                    NodeEvent(
                        kind=EventKind.NODE_OFFLINE,
                        node_id=node_id,
                        timestamp=now,
                    )
                )
        return out

    def inject_bnc_in(
        self,
        channel: int,
        edge: str,
        now: float,
        high: bool = True,
    ) -> NodeEvent:
        """Build a BNC_IN event (called by the runner when GPIO fires)."""
        return NodeEvent(
            kind=EventKind.BNC_IN,
            node_id=0,
            timestamp=now,
            data={"channel": channel, "edge": edge, "high": high},
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _from_heartbeat(self, arb_id: int, data: bytes, now: float) -> List[NodeEvent]:
        node_id = node_id_from_hb_id(arb_id)
        if node_id is None:
            return []
        hb = parse_heartbeat(data)
        if hb is None:
            return []

        track = self._track(node_id)
        out: List[NodeEvent] = []

        if not track.online:
            track.online = True
            out.append(
                NodeEvent(
                    kind=EventKind.NODE_ONLINE,
                    node_id=node_id,
                    timestamp=now,
                )
            )
        track.last_heartbeat = now

        # Derive PG / presence edges from heartbeat snapshot (recovery path).
        out.extend(self._pg_edges(node_id, track, hb.pg1, hb.pg2, hb.pg3, now))
        if hb.presence != track.presence:
            track.presence = hb.presence
            out.append(
                NodeEvent(
                    kind=EventKind.PRESENCE_CHANGED,
                    node_id=node_id,
                    timestamp=now,
                    data={"active": hb.presence, "source": "heartbeat"},
                )
            )

        out.append(
            NodeEvent(
                kind=EventKind.HEARTBEAT,
                node_id=node_id,
                timestamp=now,
                data={
                    "dispense_state": hb.dispense_state,
                    "presence": hb.presence,
                    "pg1": hb.pg1,
                    "pg2": hb.pg2,
                    "pg3": hb.pg3,
                    "fault_code": hb.fault_code,
                },
            )
        )
        return out

    def _from_event(self, arb_id: int, data: bytes, now: float) -> List[NodeEvent]:
        node_id = node_id_from_event_id(arb_id)
        if node_id is None:
            return []
        payload = parse_event(data)
        if payload is None:
            return []

        # InputChanged → PRESENCE_CHANGED / PG_CHANGED (+ derived dome edges)
        if payload.event == CanEvent.InputChanged:
            return self._from_input_changed(node_id, payload, now)

        # Pong is identity-only; not an experiment event.
        if payload.event == CanEvent.Pong:
            return []

        kind = _CAN_EVENT_TO_KIND.get(payload.event)
        if kind is None:
            return []

        event_data: Dict[str, Any] = {}
        if payload.event == CanEvent.Fault:
            fault = parse_fault_code(payload)
            event_data["fault_code"] = fault if fault is not None else ServiceStatus.Ok
            if payload.raw_extra:
                event_data["raw_extra"] = bytes(payload.raw_extra)
        elif payload.raw_extra:
            # Milestone events carry pellet count as uint16 LE.
            if len(payload.raw_extra) >= 2:
                event_data["pellet_count"] = (
                    payload.raw_extra[0] | (payload.raw_extra[1] << 8)
                )
            event_data["raw_extra"] = bytes(payload.raw_extra)

        return [
            NodeEvent(
                kind=kind,
                node_id=node_id,
                timestamp=now,
                data=event_data,
            )
        ]

    def _from_input_changed(
        self,
        node_id: int,
        payload,
        now: float,
    ) -> List[NodeEvent]:
        ic = parse_input_changed(payload)
        if ic is None:
            return []

        track = self._track(node_id)
        out: List[NodeEvent] = []

        if ic.input_id == InputId.Presence:
            track.presence = ic.active
            out.append(
                NodeEvent(
                    kind=EventKind.PRESENCE_CHANGED,
                    node_id=node_id,
                    timestamp=now,
                    data={"active": ic.active, "source": "event"},
                )
            )
            return out

        # Photogate change
        if ic.input_id == InputId.PG1:
            track.pg1 = ic.active
            gate = "pg1"
        elif ic.input_id == InputId.PG2:
            track.pg2 = ic.active
            gate = "pg2"
        elif ic.input_id == InputId.PG3:
            prev = track.pg3
            track.pg3 = ic.active
            gate = "pg3"
            if ic.active and not prev:
                out.append(
                    NodeEvent(
                        kind=EventKind.DOME_OPENED,
                        node_id=node_id,
                        timestamp=now,
                    )
                )
            elif not ic.active and prev:
                out.append(
                    NodeEvent(
                        kind=EventKind.DOME_CLOSED,
                        node_id=node_id,
                        timestamp=now,
                    )
                )
        else:
            return out

        out.append(
            NodeEvent(
                kind=EventKind.PG_CHANGED,
                node_id=node_id,
                timestamp=now,
                data={"gate": gate, "active": ic.active, "source": "event"},
            )
        )
        return out

    def _pg_edges(
        self,
        node_id: int,
        track: _NodeTrack,
        pg1: bool,
        pg2: bool,
        pg3: bool,
        now: float,
    ) -> List[NodeEvent]:
        """Emit PG_CHANGED / DOME_* when heartbeat PG bits differ from track."""
        out: List[NodeEvent] = []
        if pg1 != track.pg1:
            track.pg1 = pg1
            out.append(
                NodeEvent(
                    kind=EventKind.PG_CHANGED,
                    node_id=node_id,
                    timestamp=now,
                    data={"gate": "pg1", "active": pg1, "source": "heartbeat"},
                )
            )
        if pg2 != track.pg2:
            track.pg2 = pg2
            out.append(
                NodeEvent(
                    kind=EventKind.PG_CHANGED,
                    node_id=node_id,
                    timestamp=now,
                    data={"gate": "pg2", "active": pg2, "source": "heartbeat"},
                )
            )
        if pg3 != track.pg3:
            prev = track.pg3
            track.pg3 = pg3
            out.append(
                NodeEvent(
                    kind=EventKind.PG_CHANGED,
                    node_id=node_id,
                    timestamp=now,
                    data={"gate": "pg3", "active": pg3, "source": "heartbeat"},
                )
            )
            if pg3 and not prev:
                out.append(
                    NodeEvent(
                        kind=EventKind.DOME_OPENED,
                        node_id=node_id,
                        timestamp=now,
                    )
                )
            elif not pg3 and prev:
                out.append(
                    NodeEvent(
                        kind=EventKind.DOME_CLOSED,
                        node_id=node_id,
                        timestamp=now,
                    )
                )
        return out


def frame_to_events(
    msg: Any,
    now: float,
    normalizer: Optional[EventNormalizer] = None,
) -> List[NodeEvent]:
    """
    Convenience wrapper around EventNormalizer.frame_to_events.

    Prefer keeping a long-lived EventNormalizer so derived edges work.
    """
    if normalizer is None:
        normalizer = EventNormalizer()
    return normalizer.frame_to_events(msg, now)
