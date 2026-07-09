#!/usr/bin/env python3
"""
run.py — VFM Developer GUI entry point.

Usage:
    python run.py                          # defaults: can0, 250000 bps, 9 nodes
    python run.py --interface vcan0        # virtual CAN for testing
    python run.py -i vcan0 -n 3            # 3 nodes on vcan0
    python run.py --help

CLI arguments pre-fill the setup screen fields; you can still edit them
before clicking "Start Session".
"""

import argparse
import sys

from vfm_gui.app import main

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="vfm-gui",
        description="VFM Developer GUI — Base Station Control & Monitoring",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--interface", "-i",
        default="can0",
        metavar="IFACE",
        help="SocketCAN interface name (e.g. can0, vcan0)",
    )
    parser.add_argument(
        "--bitrate", "-b",
        type=int,
        default=250_000,
        metavar="BPS",
        help="CAN bus bitrate in bits/sec",
    )
    parser.add_argument(
        "--nodes", "-n",
        type=int,
        default=9,
        metavar="N",
        help="Number of expected nodes (1–254)",
    )
    parser.add_argument(
        "--log-dir",
        default="~/vfm_logs",
        metavar="DIR",
        help="Directory for CSV session logs",
    )
    args = parser.parse_args()

    if not (1 <= args.nodes <= 254):
        print(f"Error: --nodes must be between 1 and 254 (got {args.nodes})", file=sys.stderr)
        sys.exit(1)

    main(args)
