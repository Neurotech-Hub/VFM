"""Tests for vfm_gui.protocol — frame encoding/decoding round-trips."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from vfm_gui.protocol import (
    CanCmd,
    CanEvent,
    DispenseState,
    ServiceStatus,
    CAN_CMD_BASE,
    CAN_STATUS_BASE,
    CAN_EVENT_BASE,
    CAN_ID_ANNOUNCE,
    CAN_ID_ASSIGN,
    CAN_ID_ACK,
    CAN_ID_REJOIN,
    build_cmd_frame,
    build_assign_frame,
    build_heartbeat_frame,
    build_event_frame,
    parse_heartbeat,
    parse_event,
    parse_discovery,
    classify_frame,
    format_mac,
    node_id_from_hb_id,
    node_id_from_event_id,
    HeartbeatPayload,
)


class TestBuildCmdFrame:
    def test_unicast(self):
        arb_id, data = build_cmd_frame(3, CanCmd.Dispense)
        assert arb_id == CAN_CMD_BASE + 3
        assert data[0] == CanCmd.Dispense

    def test_broadcast(self):
        arb_id, data = build_cmd_frame(0, CanCmd.Abort)
        assert arb_id == CAN_CMD_BASE  # 0x100
        assert data[0] == CanCmd.Abort

    def test_assign_id_payload(self):
        arb_id, data = build_cmd_frame(5, CanCmd.AssignId, bytes([7]))
        assert arb_id == CAN_CMD_BASE + 5
        assert data[0] == CanCmd.AssignId
        assert data[1] == 7

    def test_max_8_bytes(self):
        _, data = build_cmd_frame(1, CanCmd.SetConfig, bytes(range(20)))
        assert len(data) <= 8


class TestBuildAssignFrame:
    def test_basic(self):
        mac = bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0x01])
        arb_id, data = build_assign_frame(mac, 1)
        assert arb_id == CAN_ID_ASSIGN
        assert data[:6] == mac
        assert data[6] == 1

    def test_various_ids(self):
        mac = bytes([0x11] * 6)
        for nid in [1, 127, 254]:
            _, data = build_assign_frame(mac, nid)
            assert data[6] == nid

    def test_bad_mac_length(self):
        with pytest.raises(AssertionError):
            build_assign_frame(bytes(5), 1)

    def test_bad_node_id(self):
        mac = bytes(6)
        with pytest.raises(AssertionError):
            build_assign_frame(mac, 0)
        with pytest.raises(AssertionError):
            build_assign_frame(mac, 255)


class TestParseHeartbeat:
    def _make_data(self, state=0, presence=0, pg=0, fault=0):
        return bytes([state, 0, 0, presence, pg, fault, 0, 0])

    def test_idle(self):
        hb = parse_heartbeat(self._make_data(state=0))
        assert hb is not None
        assert hb.dispense_state == DispenseState.Idle
        assert hb.presence is False
        assert hb.pg1 is False

    def test_pg_bits(self):
        hb = parse_heartbeat(self._make_data(pg=0b101))  # PG1 + PG3
        assert hb.pg1 is True
        assert hb.pg2 is False
        assert hb.pg3 is True

    def test_presence(self):
        hb = parse_heartbeat(self._make_data(presence=1))
        assert hb.presence is True

    def test_fault_state(self):
        hb = parse_heartbeat(self._make_data(state=6, fault=3))  # Fault, Jam
        assert hb.dispense_state == DispenseState.Fault
        assert hb.fault_code == ServiceStatus.Jam

    def test_too_short(self):
        assert parse_heartbeat(bytes(3)) is None

    def test_unknown_state_defaults_to_idle(self):
        hb = parse_heartbeat(bytes([0xFF, 0, 0, 0, 0, 0, 0, 0]))
        assert hb is not None
        assert hb.dispense_state == DispenseState.Idle


class TestParseEvent:
    def test_pellet_loaded(self):
        ev = parse_event(bytes([CanEvent.PelletLoaded]))
        assert ev is not None
        assert ev.event == CanEvent.PelletLoaded

    def test_pong(self):
        ev = parse_event(bytes([CanEvent.Pong]))
        assert ev.event == CanEvent.Pong

    def test_extra_bytes(self):
        ev = parse_event(bytes([CanEvent.PelletLoaded, 0x12, 0x00]))
        assert ev.raw_extra == bytes([0x12, 0x00])

    def test_empty(self):
        assert parse_event(b"") is None

    def test_unknown_event(self):
        assert parse_event(bytes([0xFF])) is None


class TestParseDiscovery:
    def test_announce(self):
        mac = bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0x01])
        info = parse_discovery(CAN_ID_ANNOUNCE, mac)
        assert info is not None
        assert info["mac"] == mac
        assert info["node_id"] is None

    def test_assign(self):
        mac = bytes([0x11] * 6)
        data = mac + bytes([3])
        info = parse_discovery(CAN_ID_ASSIGN, data)
        assert info["mac"] == mac
        assert info["node_id"] == 3

    def test_rejoin(self):
        mac = bytes([0x22] * 6)
        data = mac + bytes([7])
        info = parse_discovery(CAN_ID_REJOIN, data)
        assert info["node_id"] == 7

    def test_announce_too_short(self):
        assert parse_discovery(CAN_ID_ANNOUNCE, bytes(3)) is None

    def test_assign_too_short(self):
        assert parse_discovery(CAN_ID_ASSIGN, bytes(5)) is None


class TestClassifyFrame:
    def test_heartbeat(self):
        assert classify_frame(CAN_STATUS_BASE + 1) == "HEARTBEAT"
        assert classify_frame(CAN_STATUS_BASE + 9) == "HEARTBEAT"

    def test_event(self):
        assert classify_frame(CAN_EVENT_BASE + 1) == "EVENT"

    def test_command(self):
        assert classify_frame(CAN_CMD_BASE)      == "COMMAND"  # broadcast
        assert classify_frame(CAN_CMD_BASE + 3)  == "COMMAND"

    def test_discovery(self):
        for fid in [CAN_ID_ANNOUNCE, CAN_ID_ASSIGN, CAN_ID_ACK, CAN_ID_REJOIN]:
            assert classify_frame(fid) == "DISCOVERY"

    def test_unknown(self):
        assert classify_frame(0x001) == "UNKNOWN"
        assert classify_frame(0x7FF) == "UNKNOWN"


class TestNodeIdExtraction:
    def test_hb(self):
        assert node_id_from_hb_id(CAN_STATUS_BASE + 5) == 5
        assert node_id_from_hb_id(CAN_STATUS_BASE) is None  # base itself = node 0, invalid

    def test_event(self):
        assert node_id_from_event_id(CAN_EVENT_BASE + 2) == 2


class TestFormatMac:
    def test_format(self):
        mac = bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0x01])
        assert format_mac(mac) == "AA:BB:CC:DD:EE:01"

    def test_zeros(self):
        assert format_mac(bytes(6)) == "00:00:00:00:00:00"
