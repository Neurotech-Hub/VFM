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

## Experiment engine (headless task/session API)

The GUI is for live monitoring and discovery. Behavioral tasks live in a
separate **event-driven experiment engine** under
[`vfm_gui/experiment/`](vfm_gui/experiment/). Nodes stay dumb (commands in,
events out); your script decides what to do next.

### Quick start — free feeding against the simulator

```bash
# Terminal 1 — fake nodes (Linux / Pi; needs vcan0 — see above)
python node_simulator.py --interface vcan0 --nodes 3 --skip-discovery

# Terminal 2 — run the built-in free-feeding template for 60 s
python run_experiment.py free_feeding --interface vcan0 --nodes 1,2,3 \
    --seconds 60 --reload-delay 2 --no-io --log-dir ~/vfm_logs
```

On a non-Pi host use `--no-io` so GPIO/BNC setup is skipped.

### Write your own experiment

```python
from vfm_gui.experiment import Experiment, EventKind

exp = Experiment(nodes=[1, 2, 3], name="my_task")

@exp.on_start
def start(ctx):
    for n in ctx.nodes:
        ctx.dispense(n)

@exp.on_access_attempt
def attempted(ctx, ev):
    ctx.log("retrieval_attempt", node=ev.node_id)

@exp.on_dome_closed
def reload(ctx, ev):
    ctx.after(2.0, lambda: ctx.dispense(ev.node_id))

exp.end_after(hours=12)
# exp.run(interface="vcan0")   # or: save as my_task.py and use the CLI
```

```bash
python run_experiment.py my_task.py --interface vcan0 --no-io
```

A script may expose either `exp = Experiment(...)` or
`def build(**kwargs) -> Experiment`.

### Built-in templates

| Name | Module | Behavior |
|------|--------|----------|
| `free_feeding` | `vfm_gui.experiment.templates.free_feeding` | Dispense on all nodes at start; on dome close, wait `reload_delay` and re-dispense; end on duration and/or pellet cap |

### API surface

- **Events** (`EventKind`): `PELLET_LOADED`, `PELLET_PRESENTED`, `ACCESS_ATTEMPT`,
  `FAULT`, phase events, `PRESENCE_CHANGED`, `PG_CHANGED`, plus derived
  `DOME_OPENED` / `DOME_CLOSED`, `NODE_ONLINE` / `NODE_OFFLINE`, and
  base-station `BNC_IN`, `SESSION_START`, `SESSION_END`.
- **Context actions**: `dispense`, `abort`, `broadcast_dispense`,
  `bnc_pulse`, `set_heartbeat_interval`, `after` / `every` timers,
  named `counter` / `incr`, `log`.
- **Lifecycle**: `start_when(condition)`, `end_after(hours=…, pellets=…)`,
  `end_when(condition)`.
- **Hosting**: `exp.run(interface=…)` (blocking) or
  `runner = exp.make_runner(…); runner.step(now)` for GUI integration later.

Experiment-level CSV logs go to `--log-dir` as
`experiment_<name>_YYYYMMDD_HHMMSS.csv` (separate from the raw-CAN session log).

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
├── run.py                    # GUI entry point
├── run_experiment.py         # Headless experiment CLI
├── node_simulator.py         # Fake VFM nodes for vcan0 testing
├── deploy/                   # One-time Pi setup: MCP2515 device tree overlay,
│                              # systemd-networkd unit — see deploy/README.md
├── tests/
│   ├── test_hat.py           # Interactive hardware validation (not pytest)
│   └── test_*.py             # Automated unit tests (pytest)
├── vfm_gui/
    ├── protocol.py           # CAN protocol constants + parsers
    ├── can_manager.py        # SocketCAN wrapper (threaded RX)
    ├── io_manager.py         # BNC I/O, button, AEO GPIO (non-CAN)
    ├── discovery_manager.py  # ANNOUNCE/ASSIGN/REJOIN via IOManager.drive_aeo()
    ├── mac_id_registry.py    # Persistent MAC ↔ Node ID dictionary (~/.vfm/…)
    ├── node_registry.py      # Per-node live state (session)
    ├── log_manager.py        # Ring buffer + CSV auto-save
    ├── app.py                # DearPyGui screens + render loop
    └── experiment/           # Headless event-driven task engine
        ├── events.py         # EventKind + CAN → NodeEvent normalizer
        ├── context.py        # Actions, timers, counters, experiment CSV
        ├── runner.py         # Experiment API + tick loop
        └── templates/
            └── free_feeding.py
```

## Persistent MAC ↔ Node ID map

The base station keeps a dictionary of discovered modules in
`~/.vfm/mac_id_registry.json` (created automatically):

```json
{
  "version": 1,
  "mappings": {
    "AA:BB:CC:DD:EE:01": 1,
    "AA:BB:CC:DD:EE:02": 2
  }
}
```

- On **ANNOUNCE**, if the MAC is already in the file the same ID is re-assigned;
  otherwise the next free ID is used and written to the file after **ACK**.
- On **REJOIN**, if the node's NVS ID disagrees with the file, the base station
  sends **ASSIGN** with the historical ID so the mapping stays stable.
- **Clear All IDs** wipes this file (and broadcasts `ClearId` to node NVS), then
  rediscovers and rebuilds the dictionary from scratch.

## CAN frame reference

| Direction      | CAN ID           | Content                        |
|---------------|------------------|-------------------------------|
| base → node   | `0x100 + nodeId` | Command (Dispense, Abort, …)  |
| base → all    | `0x100`          | Broadcast command             |
| node → base   | `0x200 + nodeId` | Periodic heartbeat/status snapshot |
| node → base   | `0x300 + nodeId` | Immediate event (dispense, fault, Pong, input change) |
| node → base   | `0x080`          | ANNOUNCE (first boot)         |
| base → node   | `0x081`          | ASSIGN (node ID assignment)   |
| node → base   | `0x082`          | ACK                           |
| node → base   | `0x083`          | REJOIN (returning node)       |

Broadcast command opcodes include `ClearId` (`0x07`) — the GUI **Clear All IDs**
button clears `~/.vfm/mac_id_registry.json`, broadcasts ClearId so every node
wipes its NVS ID, then rediscovers and rebuilds the MAC↔ID dictionary.

`InputChanged` event payloads are `[0x06, inputId, active]`, where input IDs
are PG1=`1`, PG2=`2`, PG3=`3`, and presence=`4`. These events update the GUI
indicators and log immediately; heartbeats remain the periodic recovery
snapshot.

BNC IN/OUT activity is not a CAN frame — it is logged in the event log with
`frame_type="BNC"` for a unified timeline alongside CAN traffic.
