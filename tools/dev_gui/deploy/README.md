# Base Station Deployment — CAN HAT bring-up

One-time system configuration to bring up the MCP2515 CAN controller on the
custom base station HAT as a standard SocketCAN `can0` interface. Do this
once per Raspberry Pi before running the VFM Developer GUI (`tools/dev_gui`)
against real hardware.

## Hardware reference

| Signal        | BCM GPIO | Notes                                    |
|---------------|----------|-------------------------------------------|
| SPI CE0       | 8        | MCP2515 chip select                       |
| SPI MISO      | 9        | MCP2515 data in                           |
| SPI MOSI      | 10       | MCP2515 data out                          |
| SPI SCK       | 11       | MCP2515 clock                             |
| MCP2515 INT   | 5        | Active-low interrupt                      |
| Crystal       | —        | 12 MHz (Y1 on schematic)                  |

## 1. Enable SPI + the MCP2515 device tree overlay

Append [boot-config-can.append.txt](boot-config-can.append.txt) to
`/boot/firmware/config.txt`:

```bash
sudo tee -a /boot/firmware/config.txt < boot-config-can.append.txt
sudo reboot
```

After reboot, confirm the kernel driver probed the chip:

```bash
dmesg | grep -i mcp251x
ls /dev/spidev0.0        # SPI bus present
ip link show can0        # can0 interface exists
```

## 2. Bring up `can0` persistently

Install the systemd-networkd unit [80-can.network](80-can.network):

```bash
sudo cp 80-can.network /etc/systemd/network/80-can.network
sudo systemctl enable --now systemd-networkd
sudo systemctl restart systemd-networkd
ip -details link show can0   # should report state UP, bitrate 250000
```

If you are not using systemd-networkd, bring the interface up manually
instead (not persistent across reboots):

```bash
sudo ip link set can0 up type can bitrate 250000
```

## 3. Verify with `candump` / `cansend`

```bash
sudo apt install can-utils   # if not already installed

# Terminal 1
candump can0

# Terminal 2 (loopback test, no nodes required)
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 250000 loopback on
sudo ip link set can0 up
cansend can0 123#DEADBEEF   # should be echoed back in Terminal 1

# Restore normal (non-loopback) operation
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 250000 loopback off
sudo ip link set can0 up
```

With at least one VFM node powered and connected, `candump can0` should show
`080` (ANNOUNCE) or `083` (REJOIN) discovery frames, followed by `2xx`
heartbeat frames at 1 Hz once the node is assigned an ID.

## 4. Run the GUI

Once `can0` is up, the VFM Developer GUI works exactly as documented in
[tools/dev_gui/README.md](../README.md) — no additional configuration is
needed; `CanManager` opens `can0` directly via SocketCAN.

## 5. Interactive hardware validation

For a full pin-by-pin checklist (CAN, AEO, BNC IN/OUT, button), run:

```bash
cd tools/dev_gui
python tests/test_hat.py
```

See [tests/test_hat.py](../tests/test_hat.py) for details.
