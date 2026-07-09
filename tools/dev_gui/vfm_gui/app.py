"""
app.py — VFM Developer GUI main application.

Two screens:
  1. Setup  — configure CAN interface, node count, mode, logging.
  2. Main   — node grid + broadcast bar + event log panel.

The DearPyGui render callback fires every frame (~60 fps).  Each frame:
  1. Drain the CanManager RX queue.
  2. Classify and dispatch each received frame.
  3. Tick the DiscoveryManager for timeout detection.
  4. Run staleness checks every second.
  5. Refresh tile widgets and the log table.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Dict, Optional

import dearpygui.dearpygui as dpg

from .can_manager import CanManager
from .discovery_manager import DiscoveryManager, DiscoveryPhase
from .log_manager import LogEntry, LogManager
from .node_registry import NodeRegistry
from .protocol import (
    CanCmd,
    CanEvent,
    classify_frame,
    format_mac,
    node_id_from_event_id,
    node_id_from_hb_id,
    parse_event,
    parse_heartbeat,
    parse_discovery,
    CAN_ID_ANNOUNCE,
    CAN_ID_ACK,
    CAN_ID_REJOIN,
    DISCOVERY_IDS,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WINDOW_W = 1280
WINDOW_H = 800
TILE_W   = 280
TILE_H   = 290
LOG_ROWS = 18        # visible rows in the log table before scroll
STALE_CHECK_INTERVAL = 1.0  # seconds between staleness sweeps

# Status dot color tags (registered once at startup)
_COLOR_GREEN  = (60,  200, 80,  255)
_COLOR_BLUE   = (60,  130, 220, 255)
_COLOR_CYAN   = (50,  200, 220, 255)
_COLOR_YELLOW = (220, 200, 50,  255)
_COLOR_RED    = (220, 50,  50,  255)
_COLOR_GREY   = (120, 120, 120, 255)
_COLOR_AMBER  = (230, 140, 30,  255)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class VFMApp:
    """Top-level application controller."""

    def __init__(self, args: argparse.Namespace) -> None:
        self._args = args
        self._can: Optional[CanManager] = None
        self._registry: Optional[NodeRegistry] = None
        self._discovery: Optional[DiscoveryManager] = None
        self._log: Optional[LogManager] = None
        self._last_stale_check = 0.0

        # GUI state
        self._screen = "setup"
        self._node_tiles: Dict[int, dict] = {}   # node_id → {tag dict}
        self._log_filter_node = 0                # 0 = all
        self._log_filter_type = "All"
        self._show_heartbeats = False

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        dpg.create_context()
        self._setup_theme()
        self._setup_fonts()
        self._build_setup_screen()
        dpg.create_viewport(
            title="VFM Developer GUI",
            width=WINDOW_W,
            height=WINDOW_H,
            resizable=True,
        )
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_render_callback(self._on_render)
        dpg.start_dearpygui()
        self._shutdown()
        dpg.destroy_context()

    # ------------------------------------------------------------------
    # Theme + Fonts
    # ------------------------------------------------------------------

    def _setup_theme(self) -> None:
        with dpg.theme() as global_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg,       (18,  20,  24,  255))
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg,        (26,  28,  35,  255))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg,        (38,  42,  52,  255))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (50,  56,  70,  255))
                dpg.add_theme_color(dpg.mvThemeCol_Button,         (45,  105, 195, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,  (60,  130, 220, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,   (30,  80,  170, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Header,         (45,  105, 195, 100))
                dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered,  (60,  130, 220, 150))
                dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive,  (30,  60,  120, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Text,           (220, 225, 235, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Border,         (60,  65,  80,  255))
                dpg.add_theme_style(dpg.mvStyleVar_WindowRounding,  6)
                dpg.add_theme_style(dpg.mvStyleVar_ChildRounding,   6)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding,   4)
                dpg.add_theme_style(dpg.mvStyleVar_GrabRounding,    4)
                dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing,     8, 6)
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding,   12, 12)
        dpg.bind_theme(global_theme)

    def _setup_fonts(self) -> None:
        with dpg.font_registry():
            # Default font (DearPyGui built-in)
            default = dpg.add_font_range_hint(dpg.mvFontRangeHint_Default)
        # Monospace font for the log — use default if no system font available
        self._mono_font = None

    # ------------------------------------------------------------------
    # Setup Screen
    # ------------------------------------------------------------------

    def _build_setup_screen(self) -> None:
        vp_w, vp_h = WINDOW_W, WINDOW_H
        win_w, win_h = 480, 520

        with dpg.window(
            tag="setup_window",
            label="VFM Developer GUI",
            width=win_w,
            height=win_h,
            pos=((vp_w - win_w) // 2, (vp_h - win_h) // 2),
            no_close=True,
            no_collapse=True,
            no_move=True,
            no_resize=True,
        ):
            dpg.add_spacer(height=8)
            dpg.add_text("VFM Developer GUI", color=(100, 180, 255, 255))
            dpg.add_text("Base Station Control & Monitoring", color=(160, 165, 175, 255))
            dpg.add_separator()
            dpg.add_spacer(height=6)

            # -- CAN Interface --
            with dpg.collapsing_header(label="CAN Interface", default_open=True):
                dpg.add_input_text(
                    tag="setup_interface",
                    label="Interface",
                    default_value=self._args.interface,
                    width=160,
                )
                dpg.add_input_int(
                    tag="setup_bitrate",
                    label="Bitrate (bps)",
                    default_value=self._args.bitrate,
                    width=160,
                    min_value=10_000,
                    max_value=1_000_000,
                )

            dpg.add_spacer(height=6)

            # -- Node Configuration --
            with dpg.collapsing_header(label="Node Configuration", default_open=True):
                dpg.add_input_int(
                    tag="setup_num_nodes",
                    label="Number of nodes",
                    default_value=self._args.nodes,
                    width=120,
                    min_value=1,
                    max_value=254,
                    min_clamped=True,
                    max_clamped=True,
                )
                dpg.add_spacer(height=4)
                dpg.add_text("Mode:")
                dpg.add_radio_button(
                    tag="setup_mode",
                    items=["Multi-node (discovery via AEO/AEI)", "Single-node (direct, no discovery)"],
                    default_value="Multi-node (discovery via AEO/AEI)",
                    horizontal=False,
                )

            dpg.add_spacer(height=6)

            # -- Logging --
            with dpg.collapsing_header(label="Logging", default_open=True):
                dpg.add_input_text(
                    tag="setup_log_dir",
                    label="Log directory",
                    default_value=str(Path(self._args.log_dir).expanduser()),
                    width=260,
                )
                dpg.add_checkbox(tag="setup_auto_save", label="Auto-save to CSV", default_value=True)

            dpg.add_spacer(height=12)
            dpg.add_separator()
            dpg.add_spacer(height=8)

            # Error text (hidden until needed)
            dpg.add_text("", tag="setup_error", color=(220, 80, 80, 255))

            dpg.add_button(
                tag="setup_start_btn",
                label="   Start Session   ",
                width=200,
                callback=self._on_start_session,
            )

    def _on_start_session(self) -> None:
        interface  = dpg.get_value("setup_interface").strip()
        bitrate    = dpg.get_value("setup_bitrate")
        num_nodes  = dpg.get_value("setup_num_nodes")
        mode       = dpg.get_value("setup_mode")
        log_dir    = dpg.get_value("setup_log_dir").strip()
        auto_save  = dpg.get_value("setup_auto_save")

        if not interface:
            dpg.set_value("setup_error", "Interface name cannot be empty.")
            return

        # Open CAN
        try:
            self._can = CanManager(interface=interface, bitrate=bitrate)
            self._can.start()
        except Exception as exc:
            dpg.set_value("setup_error", f"CAN error: {exc}")
            return

        # Create subsystems
        self._registry = NodeRegistry(num_nodes)
        self._log = LogManager(log_dir=log_dir, auto_save=auto_save)
        self._discovery = DiscoveryManager(self._can)
        self._discovery.on_node_discovered(self._on_node_discovered)
        self._discovery.on_complete(self._on_discovery_complete)

        # Start discovery if multi-node
        single_node = "Single" in mode
        if single_node:
            # Pre-register node 1 without discovery
            self._registry.register_node(1, b"\x00" * 6, source="MANUAL")
        else:
            self._discovery.start(start_id=1)

        # Transition to main screen
        dpg.delete_item("setup_window")
        self._build_main_screen(num_nodes)
        self._screen = "main"

    # ------------------------------------------------------------------
    # Main Screen
    # ------------------------------------------------------------------

    def _build_main_screen(self, num_nodes: int) -> None:
        with dpg.window(
            tag="main_window",
            label="VFM Developer GUI — Main",
            width=WINDOW_W,
            height=WINDOW_H,
            pos=(0, 0),
            no_close=True,
            no_collapse=True,
            no_move=True,
            no_resize=True,
            no_title_bar=True,
        ):
            # -- Broadcast bar --
            self._build_broadcast_bar()
            dpg.add_separator()
            dpg.add_spacer(height=4)

            # -- Node grid --
            self._build_node_grid(num_nodes)
            dpg.add_spacer(height=6)
            dpg.add_separator()

            # -- Event log --
            self._build_log_panel()

    def _build_broadcast_bar(self) -> None:
        with dpg.group(horizontal=True):
            dpg.add_text("Broadcast:", color=(160, 165, 175, 255))
            dpg.add_button(label="Dispense All",  width=110,
                           callback=lambda: self._broadcast(CanCmd.Dispense))
            dpg.add_button(label="Abort All",     width=90,
                           callback=lambda: self._broadcast(CanCmd.Abort))
            dpg.add_button(label="Ping All",      width=80,
                           callback=lambda: self._broadcast(CanCmd.Ping))
            dpg.add_button(label="ReqStatus All", width=110,
                           callback=lambda: self._broadcast(CanCmd.ReqStatus))
            dpg.add_spacer(width=20)
            dpg.add_button(
                tag="discovery_btn",
                label="⟳ Start Discovery",
                width=140,
                callback=self._on_start_discovery,
            )
            dpg.add_text("", tag="discovery_status_text", color=(100, 200, 100, 255))

    def _build_node_grid(self, num_nodes: int) -> None:
        cols = min(num_nodes, 3)
        with dpg.table(
            tag="node_grid_table",
            header_row=False,
            borders_innerH=False,
            borders_innerV=False,
            borders_outerH=False,
            borders_outerV=False,
        ):
            for _ in range(cols):
                dpg.add_table_column(width_fixed=True, init_width=TILE_W + 12)

            row_tag = None
            for i, node_id in enumerate(range(1, num_nodes + 1)):
                if i % cols == 0:
                    row_tag = dpg.add_table_row(parent="node_grid_table")
                with dpg.table_cell(parent=row_tag):
                    self._build_node_tile(node_id)

    def _build_node_tile(self, node_id: int) -> None:
        tags: dict = {}
        with dpg.child_window(width=TILE_W, height=TILE_H, border=True):

            # -- Header row: label + status dot --
            with dpg.group(horizontal=True):
                tags["label_input"] = dpg.add_input_text(
                    default_value=f"Node {node_id}",
                    width=TILE_W - 90,
                    on_enter=True,
                    callback=lambda s, a, u=node_id: self._on_label_change(u, a),
                )
                tags["status_dot"] = dpg.add_text("●", color=_COLOR_GREY)
                tags["status_text"] = dpg.add_text("OFFLINE", color=_COLOR_GREY)

            # -- Identity --
            dpg.add_separator()
            with dpg.group():
                tags["can_id_text"]  = dpg.add_text(f"CAN ID : {node_id}")
                tags["mac_text"]     = dpg.add_text("MAC    : —")
                tags["disc_text"]    = dpg.add_text("Disc   : Pending")

            dpg.add_separator()

            # -- Sensor state --
            with dpg.group(horizontal=True):
                dpg.add_text("HB:", color=(160,165,175,255))
                tags["hb_text"] = dpg.add_text("—")
                dpg.add_spacer(width=12)
                dpg.add_text("Presence:", color=(160,165,175,255))
                tags["presence_text"] = dpg.add_text("—")

            with dpg.group(horizontal=True):
                dpg.add_text("PG:", color=(160,165,175,255))
                tags["pg1_text"] = dpg.add_text("1:○")
                dpg.add_spacer(width=6)
                tags["pg2_text"] = dpg.add_text("2:○")
                dpg.add_spacer(width=6)
                tags["pg3_text"] = dpg.add_text("3:○")

            with dpg.group(horizontal=True):
                dpg.add_text("Fault:", color=(160,165,175,255))
                tags["fault_text"] = dpg.add_text("—")

            dpg.add_separator()

            # -- Command buttons --
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Dispense", width=85,
                    callback=lambda s, a, u=node_id: self._send_cmd(u, CanCmd.Dispense),
                )
                dpg.add_button(
                    label="Abort", width=70,
                    callback=lambda s, a, u=node_id: self._send_cmd(u, CanCmd.Abort),
                )
                dpg.add_button(
                    label="Ping", width=55,
                    callback=lambda s, a, u=node_id: self._send_cmd(u, CanCmd.Ping),
                )
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="ReqStatus", width=90,
                    callback=lambda s, a, u=node_id: self._send_cmd(u, CanCmd.ReqStatus),
                )
                dpg.add_button(
                    label="SetConfig", width=90, enabled=False,
                )  # placeholder — protocol TBD

            # -- AssignId override --
            with dpg.group(horizontal=True):
                tags["assign_input"] = dpg.add_input_int(
                    default_value=node_id, width=80,
                    min_value=1, max_value=254,
                    min_clamped=True, max_clamped=True,
                )
                dpg.add_button(
                    label="AssignId",
                    callback=lambda s, a, u=(node_id, tags): self._on_assign_id(u[0], u[1]),
                )

        self._node_tiles[node_id] = tags

    def _build_log_panel(self) -> None:
        dpg.add_text("Event Log", color=(100, 180, 255, 255))
        with dpg.group(horizontal=True):
            dpg.add_text("Node:", color=(160,165,175,255))
            dpg.add_combo(
                tag="log_filter_node",
                items=["All"] + [str(i) for i in range(1, (self._registry.num_nodes() if self._registry else 10) + 1)],
                default_value="All",
                width=80,
                callback=self._refresh_log_table,
            )
            dpg.add_text("Type:", color=(160,165,175,255))
            dpg.add_combo(
                tag="log_filter_type",
                items=["All", "EVENT", "COMMAND", "HEARTBEAT", "DISCOVERY"],
                default_value="All",
                width=120,
                callback=self._refresh_log_table,
            )
            dpg.add_checkbox(
                tag="log_show_hb",
                label="Show Heartbeats",
                default_value=False,
                callback=self._refresh_log_table,
            )
            dpg.add_button(label="Clear",  callback=self._on_log_clear)
            dpg.add_button(label="Export", callback=self._on_log_export)
            dpg.add_text("", tag="log_count_text", color=(160,165,175,255))

        with dpg.table(
            tag="log_table",
            header_row=True,
            borders_innerH=True,
            borders_innerV=True,
            borders_outerH=True,
            borders_outerV=True,
            scrollY=True,
            freeze_rows=1,
            height=220,
            policy=dpg.mvTable_SizingFixedFit,
        ):
            dpg.add_table_column(label="Time",      width_fixed=True, init_width=95)
            dpg.add_table_column(label="Node",      width_fixed=True, init_width=60)
            dpg.add_table_column(label="Dir",       width_fixed=True, init_width=35)
            dpg.add_table_column(label="Type",      width_fixed=True, init_width=90)
            dpg.add_table_column(label="Event",     width_fixed=True, init_width=150)
            dpg.add_table_column(label="ID",        width_fixed=True, init_width=55)
            dpg.add_table_column(label="Data",      width_stretch=True)
            dpg.add_table_column(label="Details",   width_stretch=True)

    # ------------------------------------------------------------------
    # Render callback (called every frame)
    # ------------------------------------------------------------------

    def _on_render(self) -> None:
        if self._screen != "main" or self._can is None:
            return

        # 1. Drain RX queue
        messages = self._can.poll_rx()
        for msg in messages:
            self._dispatch_rx(msg)

        # 2. Tick discovery timeout
        if self._discovery:
            self._discovery.tick()
            self._update_discovery_status_text()

        # 3. Staleness check (once per second)
        now = time.time()
        if now - self._last_stale_check >= STALE_CHECK_INTERVAL:
            self._last_stale_check = now
            if self._registry:
                self._registry.check_staleness()
            self._refresh_all_tiles()

        # 4. Refresh log table if new entries
        if messages:
            self._refresh_log_table()

    # ------------------------------------------------------------------
    # Frame dispatch
    # ------------------------------------------------------------------

    def _dispatch_rx(self, msg) -> None:
        arb_id = msg.arbitration_id
        data   = bytes(msg.data)
        ftype  = classify_frame(arb_id)

        entry_name = ""
        details    = ""

        if ftype == "HEARTBEAT":
            node_id = node_id_from_hb_id(arb_id)
            if node_id and self._registry:
                hb = parse_heartbeat(data)
                if hb:
                    self._registry.update_from_heartbeat(node_id, hb)
                    self._refresh_tile(node_id)
                    details = (f"state={hb.dispense_state.name} "
                               f"presence={int(hb.presence)} "
                               f"pg={''.join(str(int(b)) for b in [hb.pg1,hb.pg2,hb.pg3])} "
                               f"fault={hb.fault_code.name}")

        elif ftype == "EVENT":
            node_id = node_id_from_event_id(arb_id)
            if node_id and self._registry:
                ev = parse_event(data)
                if ev:
                    self._registry.update_from_event(node_id, ev.event)
                    self._refresh_tile(node_id)
                    entry_name = ev.event.name

        elif ftype == "DISCOVERY":
            node_id = 0
            info = parse_discovery(arb_id, data)
            if info:
                entry_name = {
                    0x080: "ANNOUNCE",
                    0x081: "ASSIGN",
                    0x082: "ACK",
                    0x083: "REJOIN",
                }.get(arb_id, "?")
                if info["mac"]:
                    details = f"MAC={format_mac(info['mac'])}"
                if info["node_id"] is not None:
                    details += f" id={info['node_id']}"
                # Route to discovery manager
                if self._discovery:
                    self._discovery.handle_frame(arb_id, data)
            node_id = 0

        else:
            node_id = 0

        if self._log:
            self._log.add(LogEntry(
                timestamp=time.time(),
                direction="RX",
                node_id=node_id or 0,
                frame_type=ftype,
                event_name=entry_name,
                raw_id=arb_id,
                raw_data=data,
                details=details,
            ))

    # ------------------------------------------------------------------
    # Tile refresh
    # ------------------------------------------------------------------

    def _refresh_tile(self, node_id: int) -> None:
        if not self._registry:
            return
        node = self._registry.get(node_id)
        if node is None:
            return
        tags = self._node_tiles.get(node_id)
        if tags is None:
            return

        color = node.status_color

        dpg.configure_item(tags["status_dot"],  color=color)
        dpg.configure_item(tags["status_text"], default_value=node.status_label, color=color)
        dpg.set_value(tags["label_input"],      node.label)
        dpg.configure_item(tags["mac_text"],    default_value=f"MAC    : {node.mac_str}")
        dpg.configure_item(tags["disc_text"],   default_value=f"Disc   : {node.discovery_state}")

        # Heartbeat age
        age = node.heartbeat_age_s
        if age is None:
            hb_str = "—"
            hb_color = _COLOR_GREY
        elif age > 5:
            hb_str = f"{age:.1f}s ago"
            hb_color = _COLOR_RED
        elif age > 2:
            hb_str = f"{age:.1f}s ago"
            hb_color = _COLOR_AMBER
        else:
            hb_str = f"{age:.1f}s ago"
            hb_color = (200, 210, 220, 255)
        dpg.configure_item(tags["hb_text"], default_value=hb_str, color=hb_color)

        # Presence
        dpg.configure_item(
            tags["presence_text"],
            default_value="Yes" if node.presence else "No",
            color=(100, 220, 120, 255) if node.presence else (160, 165, 175, 255),
        )

        # Photogates
        for pg_tag, val, label in [
            (tags["pg1_text"], node.pg1, "1"),
            (tags["pg2_text"], node.pg2, "2"),
            (tags["pg3_text"], node.pg3, "3"),
        ]:
            sym = "●" if val else "○"
            col = (100, 220, 120, 255) if val else (160, 165, 175, 255)
            dpg.configure_item(pg_tag, default_value=f"{label}:{sym}", color=col)

        # Fault
        fault_str = node.fault_code.name
        fault_col = _COLOR_RED if node.fault_code.value != 0 else (160, 165, 175, 255)
        dpg.configure_item(tags["fault_text"], default_value=fault_str, color=fault_col)

    def _refresh_all_tiles(self) -> None:
        if not self._registry:
            return
        for node in self._registry.all_nodes():
            self._refresh_tile(node.node_id)

    # ------------------------------------------------------------------
    # Log table refresh
    # ------------------------------------------------------------------

    def _refresh_log_table(self, *_) -> None:
        if not self._log:
            return

        node_filter = dpg.get_value("log_filter_node")
        type_filter = dpg.get_value("log_filter_type")
        show_hb     = dpg.get_value("log_show_hb")

        node_id = None if node_filter == "All" else int(node_filter)
        ftype   = None if type_filter == "All" else type_filter

        entries = self._log.get_filtered(
            node_id=node_id,
            frame_type=ftype,
            show_heartbeats=show_hb,
        )

        # Rebuild table rows
        dpg.delete_item("log_table", children_only=True, slot=1)
        for entry in entries:
            with dpg.table_row(parent="log_table"):
                dpg.add_text(entry.timestamp_str)
                dpg.add_text(str(entry.node_id) if entry.node_id else "—")
                dir_color = (100, 220, 120, 255) if entry.direction == "RX" else (120, 160, 255, 255)
                dpg.add_text(entry.direction, color=dir_color)
                dpg.add_text(entry.frame_type)
                dpg.add_text(entry.event_name)
                dpg.add_text(entry.raw_id_hex)
                dpg.add_text(entry.raw_data_hex)
                dpg.add_text(entry.details)

        total = self._log.total_count
        shown = len(entries)
        dpg.set_value("log_count_text", f"  {shown} / {total} entries")

    # ------------------------------------------------------------------
    # Discovery UI
    # ------------------------------------------------------------------

    def _on_start_discovery(self) -> None:
        if self._discovery:
            self._discovery.reset()
        dpg.configure_item("discovery_btn", label="⟳ Re-discover")

    def _on_node_discovered(self, node) -> None:
        if self._registry:
            self._registry.register_node(node.node_id, node.mac, source=node.source)
        self._refresh_tile(node.node_id)
        if self._log:
            self._log.add(LogEntry(
                timestamp=time.time(),
                direction="RX",
                node_id=node.node_id,
                frame_type="DISCOVERY",
                event_name=node.source,
                raw_id=0,
                raw_data=node.mac,
                details=f"MAC={format_mac(node.mac)} id={node.node_id}",
            ))

    def _on_discovery_complete(self) -> None:
        dpg.set_value("discovery_status_text", "  Discovery complete")

    def _update_discovery_status_text(self) -> None:
        if not self._discovery:
            return
        if self._discovery.is_running:
            dpg.set_value("discovery_status_text", "  Discovering…")
        elif self._discovery.is_complete:
            n = len(self._discovery.discovered_nodes)
            dpg.set_value("discovery_status_text", f"  {n} node(s) found")

    # ------------------------------------------------------------------
    # Command helpers
    # ------------------------------------------------------------------

    def _send_cmd(self, node_id: int, cmd: CanCmd, payload: bytes = b"") -> None:
        if not self._can:
            return
        ok = self._can.send_command(node_id, cmd, payload)
        if self._log:
            self._log.add(LogEntry(
                timestamp=time.time(),
                direction="TX",
                node_id=node_id,
                frame_type="COMMAND",
                event_name=cmd.name,
                raw_id=0x100 + node_id,
                raw_data=bytes([cmd.value]) + payload,
            ))

    def _broadcast(self, cmd: CanCmd) -> None:
        if not self._can:
            return
        self._can.send_broadcast(cmd)
        if self._log:
            self._log.add(LogEntry(
                timestamp=time.time(),
                direction="TX",
                node_id=0,
                frame_type="COMMAND",
                event_name=f"{cmd.name} (broadcast)",
                raw_id=0x100,
                raw_data=bytes([cmd.value]),
            ))

    def _on_assign_id(self, node_id: int, tags: dict) -> None:
        new_id = dpg.get_value(tags["assign_input"])
        self._send_cmd(node_id, CanCmd.AssignId, bytes([new_id]))

    # ------------------------------------------------------------------
    # Label change
    # ------------------------------------------------------------------

    def _on_label_change(self, node_id: int, new_label: str) -> None:
        if self._registry:
            self._registry.set_label(node_id, new_label)

    # ------------------------------------------------------------------
    # Log actions
    # ------------------------------------------------------------------

    def _on_log_clear(self) -> None:
        if self._log:
            self._log.clear()
        self._refresh_log_table()

    def _on_log_export(self) -> None:
        if not self._log:
            return
        path = self._log.export(
            f"~/vfm_logs/export_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        )
        dpg.set_value("log_count_text", f"  Exported → {path.name}")

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def _shutdown(self) -> None:
        if self._can:
            self._can.stop()
        if self._log:
            self._log.close()
        if self._discovery:
            self._discovery.stop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args: argparse.Namespace) -> None:
    app = VFMApp(args)
    app.run()
