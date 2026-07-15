"""
protocol.py — VFM CAN protocol constants and frame helpers.

Python mirror of src/services/ServiceTypes.h and src/services/CanService.h.
All CAN ID arithmetic and payload encoding/decoding lives here so every other
module imports a single source of truth.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional


# ---------------------------------------------------------------------------
# Enumerations (mirror of ServiceTypes.h)
# ---------------------------------------------------------------------------

class CanCmd(IntEnum):
    """Commands sent from base station to a node (CAN ID 0x100 + nodeId)."""
    Ping      = 0x01
    Dispense  = 0x02
    Abort     = 0x03
    AssignId  = 0x04  # payload byte[0] = new nodeId
    SetConfig = 0x05  # payload TBD
    ReqStatus = 0x06
    ClearId   = 0x07  # clear NVS id; node re-enters discovery


class CanEvent(IntEnum):
    """Events sent from a node to the base station (CAN ID 0x300 + nodeId)."""
    PelletLoaded    = 0x01
    PelletPresented = 0x02
    AccessAttempt   = 0x03
    Fault           = 0x04
    Pong            = 0x05
    InputChanged    = 0x06


class InputId(IntEnum):
    """Inputs reported immediately by CanEvent.InputChanged."""
    PG1      = 0x01
    PG2      = 0x02
    PG3      = 0x03
    Presence = 0x04


class DispenseState(IntEnum):
    """Dispenser FSM states carried in heartbeat byte 0."""
    Idle        = 0
    Lowering    = 1  # M2 down until PG2
    Feeding     = 2  # M1 feeding pellet
    Raising     = 3  # M2 up by step count
    Presented   = 4  # Pellet at top; waits for Abort / next Dispense
    SeekingAway = 5  # M2 up until PG2 clears (was Taken)
    Fault       = 6  # Timeout / jam


class ServiceStatus(IntEnum):
    """Fault codes carried in heartbeat byte 5."""
    Ok             = 0
    NotInitialized = 1
    Timeout        = 2
    Jam            = 3
    InvalidData    = 4


# ---------------------------------------------------------------------------
# CAN ID constants (mirror of ServiceTypes.h)
# ---------------------------------------------------------------------------

CAN_CMD_BASE      = 0x100  # 0x100 + nodeId  (0x100 alone = broadcast to all)
CAN_CMD_BROADCAST = 0x100  # nodeId == 0 → all nodes
CAN_STATUS_BASE   = 0x200  # 0x200 + nodeId  (heartbeat)
CAN_EVENT_BASE    = 0x300  # 0x300 + nodeId  (events)

# Discovery frame IDs
CAN_ID_ANNOUNCE = 0x080  # node → base: MAC(6)
CAN_ID_ASSIGN   = 0x081  # base → node: MAC(6) + id(1)
CAN_ID_ACK      = 0x082  # node → base: MAC(6) + id(1)
CAN_ID_REJOIN   = 0x083  # node → base: MAC(6) + id(1)

DISCOVERY_IDS = {CAN_ID_ANNOUNCE, CAN_ID_ASSIGN, CAN_ID_ACK, CAN_ID_REJOIN}


# ---------------------------------------------------------------------------
# SetConfig sub-types (mirror of ServiceTypes.h ConfigType)
# ---------------------------------------------------------------------------
# SetConfig payload: [configType(1), value...]
CONFIG_HEARTBEAT_INTERVAL = 0x01  # value = uint16 LE, heartbeat interval in ms


# ---------------------------------------------------------------------------
# Heartbeat payload (mirror of CanService.h HeartbeatPayload)
# ---------------------------------------------------------------------------
# byte 0: DispenseState
# byte 1: pelletCountLo
# byte 2: pelletCountHi
# byte 3: presence (0/1)
# byte 4: pgBits  [bit2=PG3 | bit1=PG2 | bit0=PG1]
# byte 5: faultCode (ServiceStatus)
# byte 6-7: reserved

@dataclass
class HeartbeatPayload:
    dispense_state: DispenseState
    presence: bool
    pg1: bool           # bit 0 of pgBits — pellet in cup
    pg2: bool           # bit 1 of pgBits — actuator at home/down
    pg3: bool           # bit 2 of pgBits — dome opened
    fault_code: ServiceStatus

    @property
    def pg_bits(self) -> int:
        return (self.pg1 << 0) | (self.pg2 << 1) | (self.pg3 << 2)

    @property
    def dispense_state_str(self) -> str:
        try:
            return self.dispense_state.name
        except ValueError:
            return f"Unknown({self.dispense_state})"


def parse_heartbeat(data: bytes) -> Optional[HeartbeatPayload]:
    """Decode an 8-byte heartbeat payload. Returns None if data is malformed."""
    if len(data) < 6:
        return None
    try:
        state = DispenseState(data[0])
    except ValueError:
        state = DispenseState.Idle

    try:
        fault = ServiceStatus(data[5])
    except ValueError:
        fault = ServiceStatus.Ok

    pg = data[4]
    return HeartbeatPayload(
        dispense_state=state,
        presence=bool(data[3]),
        pg1=bool(pg & 0x01),
        pg2=bool(pg & 0x02),
        pg3=bool(pg & 0x04),
        fault_code=fault,
    )


# ---------------------------------------------------------------------------
# Event payload
# ---------------------------------------------------------------------------

@dataclass
class EventPayload:
    event: CanEvent
    raw_extra: bytes  # extra bytes beyond byte 0


@dataclass
class InputChangedPayload:
    input_id: InputId
    active: bool


def parse_event(data: bytes) -> Optional[EventPayload]:
    """Decode an event frame payload. Returns None if data is empty."""
    if not data:
        return None
    try:
        event = CanEvent(data[0])
    except ValueError:
        return None
    return EventPayload(event=event, raw_extra=data[1:])


def parse_input_changed(event: EventPayload) -> Optional[InputChangedPayload]:
    """Decode InputChanged extra bytes: inputId(1), active(0/1)."""
    if event.event != CanEvent.InputChanged or len(event.raw_extra) < 2:
        return None
    try:
        input_id = InputId(event.raw_extra[0])
    except ValueError:
        return None
    return InputChangedPayload(input_id=input_id, active=bool(event.raw_extra[1]))


# ---------------------------------------------------------------------------
# Discovery payload helpers
# ---------------------------------------------------------------------------

def parse_discovery(frame_id: int, data: bytes) -> Optional[dict]:
    """
    Decode a discovery frame.

    Returns a dict with keys:
      frame_id, mac (bytes), node_id (int, may be None for ANNOUNCE)
    """
    if frame_id == CAN_ID_ANNOUNCE:
        if len(data) < 6:
            return None
        return {"frame_id": frame_id, "mac": bytes(data[:6]), "node_id": None}
    elif frame_id in (CAN_ID_ASSIGN, CAN_ID_ACK, CAN_ID_REJOIN):
        if len(data) < 7:
            return None
        return {"frame_id": frame_id, "mac": bytes(data[:6]), "node_id": data[6]}
    return None


# ---------------------------------------------------------------------------
# Frame builders
# ---------------------------------------------------------------------------

def build_cmd_frame(node_id: int, cmd: CanCmd, payload: bytes = b"") -> tuple[int, bytes]:
    """
    Build a command frame.

    Returns (arbitration_id, data_bytes).
    node_id == 0 → broadcast (0x100).
    """
    arb_id = CAN_CMD_BASE + node_id  # 0x100 when node_id==0
    data = bytes([cmd.value]) + payload
    return arb_id, data[:8]  # CAN max 8 bytes


def build_setconfig_heartbeat(interval_ms: int) -> bytes:
    """
    Build the payload (after the SetConfig command byte) that sets the
    node's heartbeat emission interval.

    Payload: [CONFIG_HEARTBEAT_INTERVAL, ms_lo, ms_hi]
    """
    interval_ms = max(0, min(int(interval_ms), 0xFFFF))
    return bytes([CONFIG_HEARTBEAT_INTERVAL]) + struct.pack("<H", interval_ms)


def build_assign_frame(mac: bytes, node_id: int) -> tuple[int, bytes]:
    """Build a CAN_ID_ASSIGN frame: MAC(6) + nodeId(1)."""
    assert len(mac) == 6, "MAC must be 6 bytes"
    assert 1 <= node_id <= 254, "nodeId must be 1-254"
    return CAN_ID_ASSIGN, bytes(mac) + bytes([node_id])


def build_heartbeat_frame(node_id: int, hb: HeartbeatPayload) -> tuple[int, bytes]:
    """Build a heartbeat frame (for the simulator)."""
    arb_id = CAN_STATUS_BASE + node_id
    data = bytes([
        int(hb.dispense_state),
        0,                    # pelletCountLo (not displayed)
        0,                    # pelletCountHi
        int(hb.presence),
        hb.pg_bits,
        int(hb.fault_code),
        0,                    # reserved
        0,                    # reserved
    ])
    return arb_id, data


def build_event_frame(node_id: int, event: CanEvent, extra: bytes = b"") -> tuple[int, bytes]:
    """Build an event frame (for the simulator)."""
    arb_id = CAN_EVENT_BASE + node_id
    data = bytes([event.value]) + extra
    return arb_id, data[:8]


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def format_mac(mac: bytes) -> str:
    """Format a 6-byte MAC as 'AA:BB:CC:DD:EE:FF'."""
    return ":".join(f"{b:02X}" for b in mac)


def node_id_from_cmd_id(arb_id: int) -> Optional[int]:
    """Extract node ID from a command frame arbitration ID. None if not a cmd frame."""
    if CAN_CMD_BASE <= arb_id <= CAN_CMD_BASE + 254:
        return arb_id - CAN_CMD_BASE
    return None


def node_id_from_hb_id(arb_id: int) -> Optional[int]:
    """Extract node ID from a heartbeat frame arbitration ID."""
    if CAN_STATUS_BASE < arb_id <= CAN_STATUS_BASE + 254:
        return arb_id - CAN_STATUS_BASE
    return None


def node_id_from_event_id(arb_id: int) -> Optional[int]:
    """Extract node ID from an event frame arbitration ID."""
    if CAN_EVENT_BASE < arb_id <= CAN_EVENT_BASE + 254:
        return arb_id - CAN_EVENT_BASE
    return None


def classify_frame(arb_id: int) -> str:
    """
    Classify a received CAN frame by its arbitration ID.

    Returns one of: 'HEARTBEAT', 'EVENT', 'COMMAND', 'DISCOVERY', 'UNKNOWN'.
    """
    if arb_id in DISCOVERY_IDS:
        return "DISCOVERY"
    if CAN_STATUS_BASE < arb_id <= CAN_STATUS_BASE + 254:
        return "HEARTBEAT"
    if CAN_EVENT_BASE < arb_id <= CAN_EVENT_BASE + 254:
        return "EVENT"
    if CAN_CMD_BASE <= arb_id <= CAN_CMD_BASE + 254:
        return "COMMAND"
    return "UNKNOWN"
