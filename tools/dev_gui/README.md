# VFM Developer GUI

Python desktop application (DearPyGui) for controlling and monitoring VFM foraging modules over the CAN bus from the Raspberry Pi 5 base station.

## Requirements

- Raspberry Pi 5 with the station HAT (`can0`)
- Python 3.9+
- Monitor attached (or Raspberry Pi Remote for headless access)

## First-time hardware bring-up

Before running the GUI against real hardware, configure the CAN
controller device tree overlay and bring up `can0` — see
[deploy/README.md](deploy/README.md). This only needs to be done once per Pi.

## Install

```bash
cd tools/dev_gui
pip install -r requirements.txt
```

## Run

```bash
# Real hardware (can0):
python run.py

# Virtual CAN for testing (no hardware needed):
sudo modprobe vcan
sudo ip link add dev vcan0 type vcan
sudo ip link set up vcan0

# Terminal 1 — simulate 3 nodes:
python node_simulator.py --interface vcan0 --nodes 3

# Terminal 2 — start GUI:
python run.py --interface vcan0 --nodes 3
```

## CLI options

```
python run.py --help

  --interface, -i  SocketCAN interface name  (default: can0)
  --bitrate,   -b  CAN bitrate in bps        (default: 250000)
  --nodes,     -n  Number of expected nodes  (default: 9)
  --log-dir        Directory for CSV logs    (default: ~/vfm_logs)
```

All CLI arguments pre-fill the setup screen; you can still edit them before clicking **Start Session**.

## Node simulator options

Bring up `vcan0` first (required once per boot; does not persist across reboots):

```bash
sudo modprobe vcan
sudo ip link add dev vcan0 type vcan
sudo ip link set up vcan0
```

```
python node_simulator.py --help

  --interface, -i  SocketCAN interface       (default: vcan0)
  --nodes,     -n  Number of nodes           (default: 3)
  --fault-rate     Fault probability 0.0–1.0 (default: 0.0)
  --skip-discovery Use REJOIN instead of ANNOUNCE
```

## Tests

```bash
cd tools/dev_gui
pip install pytest
python -m pytest tests/ -v
```

## Base station I/O (BNC, AEO, button)

The main screen includes a **BNC / Sync I/O** panel between the node grid and
the event log:

- **BNC IN 1 / BNC IN 2** — configurable label, edge (rising/falling/both),
  and a free-text "action" placeholder. A handful of convenience keywords
  (`dispense_all`, `abort_all`, `ping_all`, `reqstatus_all`) are dispatched
  automatically when enabled; any other value is just logged, ready to be
  wired to real behaviour later.
- **BNC OUT** — configurable label, pulse width (microseconds), and a
  free-text "trigger" placeholder matched against incoming CAN event names
  (or `any_event` to match all events). A "Manual Pulse" button fires a
  one-shot pulse for bench testing regardless of the enable state.

All GPIO for BNC I/O, the user button, and AEO (daisy-chain discovery enable)
is centralized in [vfm_gui/io_manager.py](vfm_gui/io_manager.py). It degrades
to a harmless simulation mode automatically when no GPIO backend
(`gpiod`/`RPi.GPIO`) or hardware is available — e.g. when developing against
`vcan0` on a non-Pi machine.

## File structure

```
tools/dev_gui/
├── requirements.txt
├── run.py                    # Entry point
├── node_simulator.py         # Fake VFM nodes for vcan0 testing
├── deploy/                   # One-time Pi setup: MCP2515 device tree overlay,
│                              # systemd-networkd unit — see deploy/README.md
├── tests/
│   ├── test_hat.py           # Interactive hardware validation (not pytest)
│   └── test_*.py             # Automated unit tests (pytest)
└── vfm_gui/
    ├── protocol.py           # CAN protocol constants + parsers
    ├── can_manager.py        # SocketCAN wrapper (threaded RX)
    ├── io_manager.py         # BNC I/O, button, AEO GPIO (non-CAN)
    ├── discovery_manager.py  # ANNOUNCE/ASSIGN/REJOIN via IOManager.drive_aeo()
    ├── node_registry.py      # Per-node state + identity mapping
    ├── log_manager.py        # Ring buffer + CSV auto-save
    └── app.py                # DearPyGui screens + render loop
```

## CAN frame reference

| Direction      | CAN ID           | Content                        |
|---------------|------------------|-------------------------------|
| base → node   | `0x100 + nodeId` | Command (Dispense, Abort, …)  |
| base → all    | `0x100`          | Broadcast command             |
| node → base   | `0x200 + nodeId` | Heartbeat (1 Hz)              |
| node → base   | `0x300 + nodeId` | Event (Loaded/Presented/Taken/Fault/Pong) |
| node → base   | `0x080`          | ANNOUNCE (first boot)         |
| base → node   | `0x081`          | ASSIGN (node ID assignment)   |
| node → base   | `0x082`          | ACK                           |
| node → base   | `0x083`          | REJOIN (returning node)       |

Broadcast command opcodes include `ClearId` (`0x07`) — the GUI **Clear All IDs**
button sends this so every node wipes its NVS ID, drops AEO, and re-enters
discovery for a fresh assign pass.

BNC IN/OUT activity is not a CAN frame — it is logged in the event log with
`frame_type="BNC"` for a unified timeline alongside CAN traffic.
