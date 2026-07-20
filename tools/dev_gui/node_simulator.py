#!/usr/bin/env python3
"""
node_simulator.py — Simulates N VFM nodes on a SocketCAN interface.

Run alongside the GUI on vcan0 for hardware-free development and testing.

Usage:
    python node_simulator.py                        # 3 nodes on vcan0
    python node_simulator.py --interface vcan0 -n 3
    python node_simulator.py --fault-rate 0.1       # 10% chance of fault per dispense

Each simulated node:
  - Runs the discovery protocol (ANNOUNCE → waits for ASSIGN → sends ACK)
    OR immediately uses a pre-assigned ID with REJOIN if --skip-discovery
  - Sends heartbeats at 1 Hz
  - Responds to Ping with Pong
  - On Dispense: simulates the full event sequence with realistic timing
    Lowering → Loading → Loaded → Raising → PelletPresented → AccessAttempt
    (stays Presented until Abort / next Dispense)
  - On Abort: returns to Idle immediately

Press Ctrl+C to stop.

vcan0 setup (one-time on the Pi):
    sudo modprobe vcan
    sudo ip link add dev vcan0 type vcan
    sudo ip link set up vcan0
"""

from __future__ import annotations

import argparse
import random
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, Optional

try:
    import can
except ImportError:
    print("python-can is required: pip install python-can", file=sys.stderr)
    sys.exit(1)

# Import protocol helpers from the vfm_gui package if available,
# otherwise define the bare minimum here so the simulator is standalone.
try:
    from vfm_gui.protocol import (
        CanCmd,
        CanEvent,
        DispenseState,
        InputId,
        ServiceStatus,
        HeartbeatPayload,
        CAN_CMD_BASE,
        CAN_CMD_BROADCAST,
        CAN_ID_ANNOUNCE,
        CAN_ID_ASSIGN,
        CAN_ID_ACK,
        CAN_ID_REJOIN,
        CAN_STATUS_BASE,
        CAN_EVENT_BASE,
        build_heartbeat_frame,
        build_event_frame,
        parse_discovery,
    )
except ImportError:
    # Fallback minimal definitions — keeps the simulator usable even when
    # run from outside the package directory.
    from enum import IntEnum

    class CanCmd(IntEnum):
        Ping=0x01; Dispense=0x02; Abort=0x03; AssignId=0x04; SetConfig=0x05; ReqStatus=0x06; ClearId=0x07

    class CanEvent(IntEnum):
        PelletLoaded=0x01; PelletPresented=0x02; AccessAttempt=0x03; Fault=0x04
        Pong=0x05; InputChanged=0x06; Lowering=0x07; Loading=0x08; Raising=0x09
        DomeOpenWarning=0x0A

    class InputId(IntEnum):
        PG1=0x01; PG2=0x02; PG3=0x03; Presence=0x04

    class DispenseState(IntEnum):
        Idle=0; Lowering=1; Loading=2; Raising=3; Presented=4; SeekingAway=5; Fault=6; AccessAttempt=7

    class ServiceStatus(IntEnum):
        Ok=0; NotInitialized=1; Timeout=2; Jam=3; InvalidData=4

    from dataclasses import dataclass as _dataclass

    @_dataclass
    class HeartbeatPayload:
        dispense_state: "DispenseState"
        presence: bool
        pg1: bool
        pg2: bool
        pg3: bool
        fault_code: "ServiceStatus"

    CAN_CMD_BASE=0x100; CAN_CMD_BROADCAST=0x100; CAN_ID_ANNOUNCE=0x080
    CAN_ID_ASSIGN=0x081; CAN_ID_ACK=0x082; CAN_ID_REJOIN=0x083
    CAN_STATUS_BASE=0x200; CAN_EVENT_BASE=0x300

    def build_heartbeat_frame(node_id, hb):
        arb_id = CAN_STATUS_BASE + node_id
        pg_bits = (int(hb.pg1) << 0) | (int(hb.pg2) << 1) | (int(hb.pg3) << 2)
        data = bytes([int(hb.dispense_state), 0, 0, int(hb.presence), pg_bits, int(hb.fault_code), 0, 0])
        return arb_id, data

    def build_event_frame(node_id, event, extra=b""):
        return CAN_EVENT_BASE + node_id, bytes([int(event)]) + extra

    def parse_discovery(frame_id, data):
        if frame_id == CAN_ID_ANNOUNCE and len(data) >= 6:
            return {"frame_id": frame_id, "mac": bytes(data[:6]), "node_id": None}
        elif frame_id in (CAN_ID_ASSIGN, CAN_ID_ACK, CAN_ID_REJOIN) and len(data) >= 7:
            return {"frame_id": frame_id, "mac": bytes(data[:6]), "node_id": data[6]}
        return None


# ---------------------------------------------------------------------------
# Simulated node state machine
# ---------------------------------------------------------------------------

class SimNodePhase(Enum):
    WaitAssign   = auto()  # sent ANNOUNCE, waiting for ASSIGN
    Enabled      = auto()  # has a node ID, running normally
    Dispensing   = auto()  # mid-dispense sequence


@dataclass
class SimNode:
    index: int                   # 0-based index for generating unique MACs
    node_id: Optional[int] = None
    phase: SimNodePhase = SimNodePhase.WaitAssign

    # Dispenser state
    dispense_state: DispenseState = DispenseState.Idle
    presence: bool = False
    pg1: bool = False
    pg2: bool = False
    pg3: bool = False
    fault_code: ServiceStatus = ServiceStatus.Ok
    pg3_open_since: Optional[float] = None
    dome_warn_sent: bool = False

    # Timing
    last_heartbeat: float = field(default_factory=time.time)
    dispense_step_time: float = 0.0
    dispense_step: int = 0
    hb_interval: float = 5.0  # per-node heartbeat interval (s), configurable via SetConfig

    @property
    def mac(self) -> bytes:
        """Generate a deterministic fake MAC from the index."""
        return bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE, self.index + 1])

    def hb_payload(self) -> HeartbeatPayload:
        return HeartbeatPayload(
            dispense_state=self.dispense_state,
            presence=self.presence,
            pg1=self.pg1,
            pg2=self.pg2,
            pg3=self.pg3,
            fault_code=self.fault_code,
        )


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------

class NodeSimulator:
    """Runs N simulated VFM nodes on a SocketCAN interface."""

    # Dispense sequence timings (seconds after command received)
    LOADED_DELAY    = 1.0
    PRESENTED_DELAY = 2.0
    TAKEN_DELAY_MIN = 3.0
    TAKEN_DELAY_MAX = 5.0
    HB_INTERVAL     = 5.0  # default node heartbeat interval (s)
    DOME_WARN_DELAY = 3.0  # shorter than firmware 30s for sim demos
    CONFIG_HEARTBEAT_INTERVAL = 0x01

    def __init__(
        self,
        interface: str,
        num_nodes: int,
        bitrate: int,
        fault_rate: float = 0.0,
        skip_discovery: bool = False,
    ) -> None:
        self._interface = interface
        self._num_nodes = num_nodes
        self._bitrate = bitrate
        self._fault_rate = fault_rate
        self._skip_discovery = skip_discovery
        self._bus: Optional[can.BusABC] = None
        self._nodes: Dict[int, SimNode] = {}   # index → SimNode
        self._running = False

    def start(self) -> None:
        self._bus = can.interface.Bus(
            channel=self._interface,
            interface="socketcan",
            bitrate=self._bitrate,
        )
        self._running = True

        for i in range(self._num_nodes):
            node = SimNode(index=i, hb_interval=self.HB_INTERVAL)
            self._nodes[i] = node

        # Stagger announce/rejoin slightly so the base station can handle them
        # one at a time (as the real daisy-chain does sequentially).
        if self._skip_discovery:
            for i, node in self._nodes.items():
                node.node_id = i + 1
                node.phase = SimNodePhase.Enabled
                self._send_rejoin(node)
                time.sleep(0.1)
        else:
            # Only announce the first node; subsequent nodes announce after
            # the base station confirms the previous one (simulating AEI chain).
            # For simplicity in the simulator, we use a small delay between
            # announces and wait for ASSIGN before the next one announces.
            threading.Thread(
                target=self._sequential_announce,
                daemon=True,
            ).start()

        # Main loop in this thread
        self._run_loop()

    def stop(self) -> None:
        self._running = False
        if self._bus:
            self._bus.shutdown()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _sequential_announce(self) -> None:
        """Announce nodes one by one, waiting for ASSIGN before the next."""
        for i, node in sorted(self._nodes.items()):
            self._send_announce(node)
            # Wait until this node gets its ID (ASSIGN received in _run_loop)
            deadline = time.time() + 10.0
            while self._running and node.node_id is None and time.time() < deadline:
                time.sleep(0.05)
            if node.node_id is None:
                print(f"  [SIM] Node {i+1} timed out waiting for ASSIGN", flush=True)
            time.sleep(0.1)  # brief gap before next announce

    def _run_loop(self) -> None:
        """Main receive + heartbeat loop."""
        while self._running:
            # Receive frames
            msg = self._bus.recv(timeout=0.05)
            if msg is not None:
                self._handle_rx(msg)

            # Heartbeats + dispense step advances + dome-open warning
            now = time.time()
            for node in self._nodes.values():
                if node.phase == SimNodePhase.Enabled or node.phase == SimNodePhase.Dispensing:
                    if now - node.last_heartbeat >= node.hb_interval:
                        self._send_heartbeat(node)
                        node.last_heartbeat = now
                    if node.phase == SimNodePhase.Dispensing:
                        self._advance_dispense(node, now)
                    self._check_dome_open_warning(node, now)

    def _handle_rx(self, msg: can.Message) -> None:
        arb_id = msg.arbitration_id
        data   = bytes(msg.data)

        # Discovery: ASSIGN frame
        if arb_id == CAN_ID_ASSIGN:
            info = parse_discovery(arb_id, data)
            if info:
                for node in self._nodes.values():
                    if node.mac == info["mac"]:
                        node.node_id = info["node_id"]
                        node.phase   = SimNodePhase.Enabled
                        self._send_ack(node)
                        print(f"  [SIM] Node {node.index+1} assigned CAN ID {node.node_id}", flush=True)
                        break
            return

        # Command frames (broadcast or per-node)
        is_broadcast = (arb_id == CAN_CMD_BROADCAST)
        for node in self._nodes.values():
            if node.node_id is None:
                continue
            is_my_cmd = (arb_id == CAN_CMD_BASE + node.node_id)
            if not (is_broadcast or is_my_cmd):
                continue
            if not data:
                continue
            try:
                cmd = CanCmd(data[0])
            except ValueError:
                continue

            if cmd == CanCmd.Ping:
                # Pong carries the node's MAC — mirrors real firmware so the
                # GUI can confirm/refresh its MAC<->ID mapping from a live node.
                self._send_event(node, CanEvent.Pong, node.mac)
                print(f"  [SIM] Node {node.node_id}: status LED blink (Ping)", flush=True)

            elif cmd == CanCmd.Dispense:
                # Idle or Presented
                if node.dispense_state in (DispenseState.Idle, DispenseState.Presented):
                    node.dispense_state = DispenseState.Lowering
                    node.phase          = SimNodePhase.Dispensing
                    node.dispense_step  = 0
                    node.dispense_step_time = time.time()
                    node.pg1 = node.pg2 = node.pg3 = False
                    node.pg3_open_since = None
                    node.dome_warn_sent = False
                    self._send_event(node, CanEvent.Lowering)
                    print(f"  [SIM] Node {node.node_id}: Dispense started (Lowering)", flush=True)

            elif cmd == CanCmd.Abort:
                node.dispense_state = DispenseState.Idle
                node.phase          = SimNodePhase.Enabled
                node.fault_code     = ServiceStatus.Ok
                node.pg1 = node.pg2 = node.pg3 = False
                node.pg3_open_since = None
                node.dome_warn_sent = False
                print(f"  [SIM] Node {node.node_id}: Aborted", flush=True)

            elif cmd == CanCmd.ReqStatus:
                self._send_heartbeat(node)

            elif cmd == CanCmd.SetConfig and len(data) >= 2:
                if data[1] == self.CONFIG_HEARTBEAT_INTERVAL and len(data) >= 4:
                    ms = data[2] | (data[3] << 8)
                    node.hb_interval = ms / 1000.0
                    print(f"  [SIM] Node {node.node_id}: heartbeat interval set to {node.hb_interval:.2f}s", flush=True)

            elif cmd == CanCmd.AssignId and len(data) >= 2:
                old_id = node.node_id
                node.node_id = data[1]
                print(f"  [SIM] Node {node.index+1}: ID changed {old_id} → {node.node_id}", flush=True)

            elif cmd == CanCmd.ClearId:
                print(f"  [SIM] Node {node.node_id}: ClearId — NVS cleared, awaiting re-ASSIGN", flush=True)
                node.node_id = None
                node.phase = SimNodePhase.WaitAssign
                node.dispense_state = DispenseState.Idle
                node.pg1 = node.pg2 = node.pg3 = False
                # Re-announce so the base can re-assign (simulates WaitAEI→Announce)
                self._send_announce(node)

    def _advance_dispense(self, node: SimNode, now: float) -> None:
        elapsed = now - node.dispense_step_time

        # Step 0 → 1: PG2 home reached → Loading (M1 feeding)
        if node.dispense_step == 0 and elapsed >= self.LOADED_DELAY:
            if self._fault_rate > 0 and random.random() < self._fault_rate:
                node.dispense_state = DispenseState.Fault
                # Alternate Timeout vs Jam for typed Fault demos
                node.fault_code = (
                    ServiceStatus.Timeout if random.random() < 0.5 else ServiceStatus.Jam
                )
                node.phase = SimNodePhase.Enabled
                self._send_event(node, CanEvent.Fault, bytes([int(node.fault_code)]))
                print(f"  [SIM] Node {node.node_id}: FAULT {node.fault_code.name}", flush=True)
                return
            node.pg2 = True
            self._send_input_changed(node, InputId.PG2, True)
            node.dispense_state = DispenseState.Loading
            self._send_event(node, CanEvent.Loading)
            node.dispense_step      = 1
            node.dispense_step_time = now

        # Step 1 → 1b: PG1 drop → Loaded; wait clear before raise
        elif node.dispense_step == 1 and elapsed >= 0.5:
            node.pg1 = True
            self._send_input_changed(node, InputId.PG1, True)
            self._send_event(node, CanEvent.PelletLoaded)  # "Loaded"
            node.dispense_step      = 2
            node.dispense_step_time = now

        # Step 1b → 2: PG1 clear → Raising
        elif node.dispense_step == 2 and elapsed >= 0.2:
            node.pg1 = False
            self._send_input_changed(node, InputId.PG1, False)
            node.dispense_state = DispenseState.Raising
            self._send_event(node, CanEvent.Raising)
            node.dispense_step      = 3
            node.dispense_step_time = now

        # Step 3 → Presented after raise travel
        elif node.dispense_step == 3 and elapsed >= self.PRESENTED_DELAY:
            node.dispense_state = DispenseState.Presented
            self._send_event(node, CanEvent.PelletPresented)
            taken_delay = random.uniform(self.TAKEN_DELAY_MIN, self.TAKEN_DELAY_MAX)
            node._taken_delay = taken_delay
            node.dispense_step      = 4
            node.dispense_step_time = now

        # Step 4 → AccessAttempt after random delay; stay Presented (B2)
        elif node.dispense_step == 4 and elapsed >= getattr(node, "_taken_delay", self.TAKEN_DELAY_MAX):
            node.pg3 = True
            node.pg3_open_since = now
            node.dome_warn_sent = False
            self._send_input_changed(node, InputId.PG3, True)
            self._send_event(node, CanEvent.AccessAttempt)
            node.dispense_step = 5
            node.dispense_step_time = now

        # Step 5: hold PG3 open for DomeOpenWarning demos, then clear; stay Presented
        elif node.dispense_step == 5 and elapsed >= self.DOME_WARN_DELAY + 0.5:
            self._send_input_changed(node, InputId.PG3, False)
            node.pg3 = False
            node.pg3_open_since = None
            node.dome_warn_sent = False
            node.dispense_step = 6  # waiting for Abort / next Dispense
            print(f"  [SIM] Node {node.node_id}: AccessAttempt (still Presented)", flush=True)

    def _check_dome_open_warning(self, node: SimNode, now: float) -> None:
        """Emit one-shot DomeOpenWarning after continuous PG3 open (sim delay)."""
        if not node.pg3:
            node.pg3_open_since = None
            node.dome_warn_sent = False
            return
        if node.pg3_open_since is None:
            node.pg3_open_since = now
            return
        if node.dome_warn_sent:
            return
        if (now - node.pg3_open_since) < self.DOME_WARN_DELAY:
            return
        node.dome_warn_sent = True
        self._send_event(node, CanEvent.DomeOpenWarning)
        print(f"  [SIM] Node {node.node_id}: DomeOpenWarning", flush=True)

    # ------------------------------------------------------------------
    # Frame senders
    # ------------------------------------------------------------------

    def _send_announce(self, node: SimNode) -> None:
        msg = can.Message(
            arbitration_id=CAN_ID_ANNOUNCE,
            data=node.mac,
            is_extended_id=False,
        )
        self._bus.send(msg)
        print(f"  [SIM] Node {node.index+1}: ANNOUNCE MAC={node.mac.hex(':')}", flush=True)

    def _send_ack(self, node: SimNode) -> None:
        data = bytes(node.mac) + bytes([node.node_id])
        msg  = can.Message(arbitration_id=CAN_ID_ACK, data=data, is_extended_id=False)
        self._bus.send(msg)

    def _send_rejoin(self, node: SimNode) -> None:
        data = bytes(node.mac) + bytes([node.node_id])
        msg  = can.Message(arbitration_id=CAN_ID_REJOIN, data=data, is_extended_id=False)
        self._bus.send(msg)
        print(f"  [SIM] Node {node.index+1}: REJOIN id={node.node_id}", flush=True)

    def _send_heartbeat(self, node: SimNode) -> None:
        arb_id, data = build_heartbeat_frame(node.node_id, node.hb_payload())
        msg = can.Message(arbitration_id=arb_id, data=data, is_extended_id=False)
        self._bus.send(msg)

    def _send_event(self, node: SimNode, event: CanEvent, extra: bytes = b"") -> None:
        arb_id, data = build_event_frame(node.node_id, event, extra)
        msg = can.Message(arbitration_id=arb_id, data=data, is_extended_id=False)
        self._bus.send(msg)
        print(f"  [SIM] Node {node.node_id}: → {event.name}", flush=True)

    def _send_input_changed(self, node: SimNode, input_id: InputId, active: bool) -> None:
        self._send_event(node, CanEvent.InputChanged, bytes([int(input_id), int(active)]))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="VFM Node Simulator — fake VFM nodes on a SocketCAN interface",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--interface", "-i", default="vcan0",
                        help="SocketCAN interface")
    parser.add_argument("--bitrate", "-b", type=int, default=250_000,
                        help="CAN bitrate (ignored for vcan)")
    parser.add_argument("--nodes", "-n", type=int, default=3,
                        help="Number of nodes to simulate")
    parser.add_argument("--fault-rate", type=float, default=0.0, metavar="RATE",
                        help="Probability (0.0–1.0) of fault per dispense")
    parser.add_argument("--skip-discovery", action="store_true",
                        help="Use REJOIN instead of ANNOUNCE (nodes appear pre-assigned)")
    args = parser.parse_args()

    sim = NodeSimulator(
        interface=args.interface,
        num_nodes=args.nodes,
        bitrate=args.bitrate,
        fault_rate=args.fault_rate,
        skip_discovery=args.skip_discovery,
    )

    print(f"VFM Node Simulator — {args.nodes} node(s) on {args.interface}")
    print("Press Ctrl+C to stop.\n")
    try:
        sim.start()
    except KeyboardInterrupt:
        print("\nStopping simulator.")
        sim.stop()


if __name__ == "__main__":
    main()
