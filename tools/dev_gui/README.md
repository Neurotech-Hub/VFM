# VFM Developer GUI

Python desktop application (DearPyGui) for controlling and monitoring VFM foraging modules over the CAN bus from the Raspberry Pi 5 base station.

## Requirements

- Raspberry Pi 5 with CAN HAT (`can0`)
- Python 3.9+
- Monitor attached (or Raspberry Pi Remote for headless access)

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

## File structure

```
tools/dev_gui/
├── requirements.txt
├── run.py                    # Entry point
├── node_simulator.py         # Fake VFM nodes for vcan0 testing
└── vfm_gui/
    ├── protocol.py           # CAN protocol constants + parsers
    ├── can_manager.py        # SocketCAN wrapper (threaded RX)
    ├── discovery_manager.py  # AEO GPIO + ANNOUNCE/ASSIGN/REJOIN
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
