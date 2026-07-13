#!/usr/bin/env python3
"""
test_hat.py — Interactive hardware validation for the VFM base station CAN HAT.

This is NOT a pytest suite (despite the filename, chosen to match the plan) —
it is a manual, interactive checklist you run directly on the Raspberry Pi
with the HAT (and ideally at least one VFM node) connected:

    cd tools/dev_gui
    python tests/test_hat.py
    python tests/test_hat.py --skip can,button      # skip specific sections
    python tests/test_hat.py --interface can0

Each check prints instructions, waits for you to confirm what you observed
(multimeter, scope, LED, candump, ...), and a pass/fail summary is printed at
the end. None of the functions here are named ``test_*``, so pytest will not
try to auto-collect and run them as automated tests.

Sections (see --skip):
  can         SPI/MCP2515 interface up, loopback self-test, live node discovery
  aeo         AEO (GPIO27) daisy-chain enable output
  bnc_out     BNC OUT (GPIO6) idle/drive/pulse timing
  bnc_in      BNC IN 1 / BNC IN 2 (GPIO12/13) idle level + edge detection
  button      User button (GPIO3)
  full_loop   End-to-end: BNC IN 1 -> CAN broadcast dispense -> BNC OUT pulse
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

# Allow running this script directly (`python tests/test_hat.py`) without
# installing the vfm_gui package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vfm_gui.io_manager import IOManager  # noqa: E402

RESULTS: list[tuple[str, bool]] = []


def _record(name: str, ok: bool) -> None:
    RESULTS.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n")


def _prompt_yes(question: str) -> bool:
    answer = input(f"{question} [y/N]: ").strip().lower()
    return answer in ("y", "yes")


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------------------
# CAN / MCP2515
# ---------------------------------------------------------------------------

def check_can_interface_up(interface: str) -> None:
    _section("CAN interface")
    result = subprocess.run(["ip", "link", "show", interface], capture_output=True, text=True)
    print(result.stdout or result.stderr)
    up = result.returncode == 0 and "UP" in result.stdout
    _record(f"{interface} exists and is UP", up)
    if not up:
        print(
            f"  Hint: check /boot/firmware/config.txt for the mcp2515-can0 overlay\n"
            f"  and see tools/dev_gui/deploy/README.md for full bring-up steps."
        )


def check_can_loopback(interface: str) -> None:
    _section("CAN loopback (no nodes required)")
    try:
        import can
    except ImportError:
        print("  python-can not installed — skipping.")
        _record("python-can available", False)
        return

    if not _prompt_yes(
        f"This will temporarily reconfigure {interface} into loopback mode. Continue?"
    ):
        _record("Loopback TX/RX matches", False)
        return

    try:
        subprocess.run(["sudo", "ip", "link", "set", interface, "down"], capture_output=True)
        subprocess.run(
            ["sudo", "ip", "link", "set", interface, "type", "can",
             "bitrate", "250000", "loopback", "on"],
            capture_output=True, check=True,
        )
        subprocess.run(["sudo", "ip", "link", "set", interface, "up"], capture_output=True, check=True)

        bus = can.interface.Bus(channel=interface, interface="socketcan")
        msg = can.Message(arbitration_id=0x123, data=[0xDE, 0xAD, 0xBE, 0xEF], is_extended_id=False)
        bus.send(msg)
        echoed = bus.recv(timeout=1.0)
        bus.shutdown()
        ok = echoed is not None and bytes(echoed.data) == bytes(msg.data)
        _record("Loopback TX/RX matches", ok)
    except Exception as exc:
        print(f"  Error: {exc}")
        _record("Loopback TX/RX matches", False)
    finally:
        subprocess.run(["sudo", "ip", "link", "set", interface, "down"], capture_output=True)
        subprocess.run(
            ["sudo", "ip", "link", "set", interface, "type", "can",
             "bitrate", "250000", "loopback", "off"],
            capture_output=True,
        )
        subprocess.run(["sudo", "ip", "link", "set", interface, "up"], capture_output=True)


def check_can_node_discovery(interface: str) -> None:
    _section("Live node discovery (power at least one VFM node)")
    if not _prompt_yes("Is at least one VFM node powered and connected to the CAN bus?"):
        _record("Node discovery frames observed", False)
        return
    print(f"  Run `candump {interface}` in another terminal now and watch for discovery frames.")
    ok = _prompt_yes("Did you see ANNOUNCE (080) or REJOIN (083) frames?")
    _record("Node discovery frames observed", ok)


# ---------------------------------------------------------------------------
# AEO
# ---------------------------------------------------------------------------

def check_aeo(io: IOManager) -> None:
    _section("AEO (GPIO27) — daisy-chain enable output")
    print(f"  GPIO backend: {io.backend or 'simulation (no hardware detected)'}")

    io.drive_aeo(True)
    ok_high = _prompt_yes("Multimeter/scope on the AEO test point — reading HIGH (~3.3V)?")
    io.drive_aeo(False)
    ok_low = _prompt_yes("Reading LOW (~0V) now?")
    _record("AEO drives HIGH/LOW correctly", ok_high and ok_low)


# ---------------------------------------------------------------------------
# BNC OUT
# ---------------------------------------------------------------------------

def check_bnc_out(io: IOManager) -> None:
    _section("BNC OUT (GPIO6)")

    io.set_bnc_out(False)
    ok_idle = _prompt_yes("BNC OUT idle — indicator LED off, ~0V on the BNC connector?")

    io.set_bnc_out(True)
    ok_high = _prompt_yes("LED on, ~5V (TTL) on the BNC connector?")
    io.set_bnc_out(False)

    print("  Firing a 500 us test pulse now (scope recommended)...")
    io.pulse_bnc_out(500)
    time.sleep(0.1)
    ok_pulse = _prompt_yes("Did you observe a clean ~500 us pulse?")

    _record("BNC OUT idle state correct", ok_idle)
    _record("BNC OUT drive HIGH correct", ok_high)
    _record("BNC OUT pulse timing looks correct", ok_pulse)


# ---------------------------------------------------------------------------
# BNC IN
# ---------------------------------------------------------------------------

def check_bnc_in(io: IOManager, which: int) -> None:
    _section(f"BNC IN {which}")
    edge_seen = {"flag": False}

    def _on_edge() -> None:
        edge_seen["flag"] = True

    if which == 1:
        io.on_bnc_in1_edge(_on_edge)
        read_fn = io.read_bnc_in1
    else:
        io.on_bnc_in2_edge(_on_edge)
        read_fn = io.read_bnc_in2

    idle_level = "HIGH" if read_fn() else "LOW"
    print(f"  Idle level (no signal applied): {idle_level}")
    ok_idle = _prompt_yes("Is that the expected idle state (LOW, no signal applied)?")

    input(f"  Apply a TTL HIGH pulse (or connect a signal) to BNC IN {which} now, then press Enter...")
    time.sleep(0.3)
    ok_edge = edge_seen["flag"]
    print(f"  Edge detected: {ok_edge}")
    print(f"  Level after signal: {'HIGH' if read_fn() else 'LOW'}")

    _record(f"BNC IN {which} idle level correct", ok_idle)
    _record(f"BNC IN {which} edge detection fired", ok_edge)


# ---------------------------------------------------------------------------
# Button
# ---------------------------------------------------------------------------

def check_button(io: IOManager) -> None:
    _section("User button (GPIO3)")
    pressed = {"flag": False}
    io.on_button_press(lambda: pressed.__setitem__("flag", True))
    input("  Press the user button now, then press Enter here...")
    _record("Button press detected", pressed["flag"])


# ---------------------------------------------------------------------------
# Full loop: BNC IN -> CAN -> BNC OUT
# ---------------------------------------------------------------------------

def check_full_loop(io: IOManager, interface: str) -> None:
    _section("Full loop: BNC IN 1 -> CAN broadcast dispense -> BNC OUT pulse")
    if not _prompt_yes("Do you have a CAN bus with at least one node ready?"):
        _record("Full BNC IN -> CAN -> BNC OUT loop", False)
        return

    try:
        import can
    except ImportError:
        print("  python-can not installed — skipping.")
        _record("Full BNC IN -> CAN -> BNC OUT loop", False)
        return

    bus = can.interface.Bus(channel=interface, interface="socketcan")
    fired = {"flag": False}

    def _on_in1() -> None:
        fired["flag"] = True
        msg = can.Message(arbitration_id=0x100, data=[0x02], is_extended_id=False)  # Dispense broadcast
        bus.send(msg)
        io.pulse_bnc_out(200)

    io.on_bnc_in1_edge(_on_in1)
    input("  Apply a pulse to BNC IN 1 now, then press Enter here...")
    time.sleep(0.5)
    bus.shutdown()

    ok_edge = fired["flag"]
    ok = ok_edge and _prompt_yes("Did the node(s) dispense and did BNC OUT pulse?")
    _record("Full BNC IN -> CAN -> BNC OUT loop", ok)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

SECTIONS = ("can", "aeo", "bnc_out", "bnc_in", "button", "full_loop")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="VFM base station HAT interactive hardware validation",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--interface", "-i", default="can0", help="SocketCAN interface")
    parser.add_argument(
        "--skip", default="", metavar="SECTIONS",
        help=f"Comma-separated sections to skip: {','.join(SECTIONS)}",
    )
    args = parser.parse_args()
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}

    print("VFM Base Station HAT Test\n" + "=" * 40)

    io = IOManager()
    io.begin()
    print(f"IOManager backend: {io.backend or 'simulation (no GPIO hardware detected)'}")

    try:
        if "can" not in skip:
            check_can_interface_up(args.interface)
            check_can_loopback(args.interface)
            check_can_node_discovery(args.interface)
        if "aeo" not in skip:
            check_aeo(io)
        if "bnc_out" not in skip:
            check_bnc_out(io)
        if "bnc_in" not in skip:
            check_bnc_in(io, 1)
            check_bnc_in(io, 2)
        if "button" not in skip:
            check_button(io)
        if "full_loop" not in skip:
            check_full_loop(io, args.interface)
    finally:
        io.shutdown()

    print("\n" + "=" * 40)
    print("Summary")
    print("=" * 40)
    passed = 0
    for name, ok in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        passed += int(ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} checks passed.")
    sys.exit(0 if total and passed == total else 1)


if __name__ == "__main__":
    main()
