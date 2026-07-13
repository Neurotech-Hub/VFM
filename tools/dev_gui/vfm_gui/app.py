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
import queue
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import dearpygui.dearpygui as dpg

from .can_manager import CanManager
from .discovery_manager import DiscoveryManager, DiscoveryPhase
from .io_manager import BNCInputConfig, BNCOutputConfig, IOManager
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
    build_setconfig_heartbeat,
    CAN_ID_ANNOUNCE,
    CAN_ID_ASSIGN,
    CAN_ID_ACK,
    CAN_ID_REJOIN,
    DISCOVERY_IDS,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WINDOW_W = 1280
WINDOW_H = 920        # 1.15x taller viewport (was 800)
TILE_W   = 280
TILE_H   = 320        # ~1.1x taller node tiles on the control & monitoring screen (was 290)
LOG_ROWS = 18        # visible rows in the log table before scroll
LOG_TABLE_HEIGHT = 240  # ~1.1x taller log table (was 220)
STALE_CHECK_INTERVAL = 1.0  # seconds between staleness sweeps
DEFAULT_HEARTBEAT_INTERVAL_S = 5.0  # default node heartbeat interval

# Status dot color tags (registered once at startup)
_COLOR_GREEN  = (60,  200, 80,  255)
_COLOR_BLUE   = (60,  130, 220, 255)
_COLOR_CYAN   = (50,  200, 220, 255)
_COLOR_YELLOW = (220, 200, 50,  255)
_COLOR_RED    = (220, 50,  50,  255)
_COLOR_GREY   = (120, 120, 120, 255)
_COLOR_AMBER  = (230, 140, 30,  255)


# ---------------------------------------------------------------------------
# Base-station-side dispense scheduler
# ---------------------------------------------------------------------------
# "SetConfig" on a node tile configures a base-station-driven dispense
# schedule for that node — either a fixed interval, or "chained" to fire a
# set delay after another node dispenses. This does NOT touch the node's
# NVS/firmware config; it purely drives CanCmd.Dispense from the GUI.

@dataclass
class ScheduleConfig:
    mode: str = "off"                  # "off", "interval", "chained"
    interval_minutes: float = 10.0
    chained_node_id: int = 1
    chained_delay_minutes: float = 5.0
    next_fire_time: Optional[float] = None   # absolute time.time(), "interval" mode
    armed_fire_time: Optional[float] = None  # absolute time.time(), "chained" mode

    @property
    def summary(self) -> str:
        if self.mode == "interval":
            return f"Every {self.interval_minutes:g} min"
        if self.mode == "chained":
            return f"{self.chained_delay_minutes:g} min after Node {self.chained_node_id}"
        return "Off"


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

        # IOManager owns all base-station GPIO except CAN (BNC I/O).
        # Created up front (not tied to CAN session) since it degrades to a
        # harmless no-op when no GPIO hardware is present.
        self._io = IOManager()

        # BNC configuration — deliberately free-form placeholders; see
        # io_manager.BNCInputConfig / BNCOutputConfig docstrings.
        self._bnc_in1_cfg = BNCInputConfig(label="BNC IN 1")
        self._bnc_in2_cfg = BNCInputConfig(label="BNC IN 2")
        self._bnc_out_cfg = BNCOutputConfig(label="BNC OUT")
        self._bnc_tiles: Dict[str, dict] = {}
        self._bnc_edge_queue: "queue.Queue[tuple[str, float]]" = queue.Queue(maxsize=256)

        # GUI state
        self._screen = "setup"
        self._node_tiles: Dict[int, dict] = {}   # node_id → {tag dict}
        self._log_filter_node = 0                # 0 = all
        self._log_filter_type = "All"
        self._show_heartbeats = False
        self._hb_interval_s = DEFAULT_HEARTBEAT_INTERVAL_S

        # Base-station-side dispense scheduler (per node_id)
        self._schedules: Dict[int, ScheduleConfig] = {}

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
        if hasattr(dpg, "set_render_callback"):
            dpg.set_render_callback(self._on_render)
        else:
            dpg.set_frame_callback(1, self._make_render_callback())
        dpg.start_dearpygui()
        self._shutdown()
        dpg.destroy_context()

    def _make_render_callback(self):
        """Return a frame callback that reschedules itself for the next frame."""
        def _frame_callback() -> None:
            self._on_render()
            if hasattr(dpg, "get_frame_count"):
                dpg.set_frame_callback(dpg.get_frame_count() + 1, _frame_callback)

        return _frame_callback

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
        # Character ranges are automatic in current DearPyGui; keep default font.
        # Monospace font for the log — use default if no system font available.
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
                    on_enter=True,
                    callback=self._on_check_can_status,
                )
                dpg.add_input_int(
                    tag="setup_bitrate",
                    label="Bitrate (bps)",
                    default_value=self._args.bitrate,
                    width=160,
                    min_value=10_000,
                    max_value=1_000_000,
                )
                with dpg.group(horizontal=True):
                    dpg.add_text("Driver status:", color=(160, 165, 175, 255))
                    dpg.add_text("", tag="setup_can_status", color=_COLOR_GREY)
                    dpg.add_button(label="Check", width=60, callback=self._on_check_can_status)

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

        self._on_check_can_status()

    def _on_check_can_status(self, *_args) -> None:
        """Check whether the configured SocketCAN interface exists and is UP."""
        interface = dpg.get_value("setup_interface").strip() if dpg.does_item_exist("setup_interface") else self._args.interface
        up = self._is_can_interface_up(interface)
        if up:
            dpg.configure_item("setup_can_status", default_value=f"● {interface} online", color=_COLOR_GREEN)
        else:
            dpg.configure_item("setup_can_status", default_value=f"● {interface} not found", color=_COLOR_RED)

    @staticmethod
    def _is_can_interface_up(interface: str) -> bool:
        """Best-effort check via `ip link show <interface>` — never raises."""
        if not interface:
            return False
        try:
            result = subprocess.run(
                ["ip", "link", "show", interface],
                capture_output=True, text=True, timeout=1.0,
            )
            return result.returncode == 0 and "UP" in result.stdout
        except Exception:
            return False

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

        # Bring up base-station GPIO (BNC I/O, button, AEO). Degrades to a
        # no-op automatically when no GPIO hardware is present.
        self._io.begin()
        self._io.on_bnc_in1_edge(lambda: self._bnc_edge_queue.put(("IN1", time.time())))
        self._io.on_bnc_in2_edge(lambda: self._bnc_edge_queue.put(("IN2", time.time())))

        # Create subsystems
        self._registry = NodeRegistry(num_nodes)
        self._log = LogManager(log_dir=log_dir, auto_save=auto_save)
        self._discovery = DiscoveryManager(self._can, self._io)
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

            # -- BNC / Sync I/O --
            self._build_bnc_panel()
            dpg.add_spacer(height=6)
            dpg.add_separator()

            # -- Event log --
            self._build_log_panel()

    def _build_broadcast_bar(self) -> None:
        with dpg.group(horizontal=True):
            dpg.add_button(
                label="Clear All IDs",
                width=110,
                callback=self._on_clear_all_ids,
            )
            dpg.add_spacer(width=12)
            dpg.add_text("Broadcast:", color=(160, 165, 175, 255))
            dpg.add_button(label="Dispense All",  width=110,
                           callback=lambda: self._broadcast(CanCmd.Dispense))
            dpg.add_button(label="Abort All",     width=90,
                           callback=lambda: self._broadcast(CanCmd.Abort))
            dpg.add_button(label="Ping All",      width=80,
                           callback=lambda: self._broadcast(CanCmd.Ping))
            dpg.add_button(label="ReqStatus All", width=110,
                           callback=lambda: self._broadcast(CanCmd.ReqStatus))
            dpg.add_spacer(width=12)
            dpg.add_button(
                tag="discovery_btn",
                label="Re-discover",
                width=110,
                callback=self._on_start_discovery,
            )
            dpg.add_spacer(width=12)
            dpg.add_text("Heartbeat (s):", color=(160, 165, 175, 255))
            dpg.add_input_float(
                tag="hb_interval_input",
                default_value=DEFAULT_HEARTBEAT_INTERVAL_S,
                width=70,
                min_value=0.1,
                max_value=120.0,
                min_clamped=True,
                max_clamped=True,
                step=0.5,
            )
            dpg.add_button(
                label="Apply HB",
                width=80,
                callback=self._on_apply_heartbeat_interval,
            )

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
                dpg.add_table_column(width_fixed=True, init_width_or_weight=TILE_W + 12)

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
            # DearPyGui always passes (sender, app_data, user_data); defaults on
            # lambda params are overridden by None unless user_data= is set.
            with dpg.group(horizontal=True):
                tags["label_input"] = dpg.add_input_text(
                    default_value=f"Node {node_id}",
                    width=TILE_W - 90,
                    on_enter=True,
                    user_data=node_id,
                    callback=lambda s, a, u: self._on_label_change(u, a),
                )
                tags["status_dot"] = dpg.add_text("●", color=_COLOR_GREY)
                tags["status_text"] = dpg.add_text("OFFLINE", color=_COLOR_GREY)

            # -- Identity --
            dpg.add_separator()
            with dpg.group():
                tags["can_id_text"]  = dpg.add_text(f"ID  : {node_id}")
                tags["mac_text"]     = dpg.add_text("MAC : —")

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
                    label="Dispense", width=85, user_data=node_id,
                    callback=lambda s, a, u: self._send_cmd(u, CanCmd.Dispense),
                )
                dpg.add_button(
                    label="Abort", width=70, user_data=node_id,
                    callback=lambda s, a, u: self._send_cmd(u, CanCmd.Abort),
                )
                dpg.add_button(
                    label="Ping", width=55, user_data=node_id,
                    callback=lambda s, a, u: self._send_cmd(u, CanCmd.Ping),
                )
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="ReqStatus", width=90, user_data=node_id,
                    callback=lambda s, a, u: self._send_cmd(u, CanCmd.ReqStatus),
                )
                dpg.add_button(
                    label="SetConfig", width=90, user_data=node_id,
                    callback=lambda s, a, u: self._on_open_schedule_dialog(u),
                )

            tags["schedule_text"] = dpg.add_text("Schedule: Off", color=(160, 165, 175, 255))

            # -- AssignId override --
            with dpg.group(horizontal=True):
                tags["assign_input"] = dpg.add_input_int(
                    default_value=node_id, width=80,
                    min_value=1, max_value=254,
                    min_clamped=True, max_clamped=True,
                )
                dpg.add_button(
                    label="AssignId",
                    user_data=node_id,
                    callback=lambda s, a, u: self._on_assign_id(u),
                )

        self._node_tiles[node_id] = tags

    # ------------------------------------------------------------------
    # BNC / Sync I/O panel
    # ------------------------------------------------------------------

    def _build_bnc_panel(self) -> None:
        dpg.add_text("BNC / Sync I/O", color=(100, 180, 255, 255))
        dpg.add_text(
            "Action / Trigger fields are free-text placeholders — not tied to a fixed "
            "list. Recognized convenience keywords (dispense_all, abort_all, ping_all, "
            "reqstatus_all for inputs; any_event, fault, or an event name for output) "
            "are dispatched automatically. Anything else is just logged for now.",
            color=(140, 145, 155, 255), wrap=WINDOW_W - 40,
        )
        with dpg.group(horizontal=True):
            self._build_bnc_input_box(1, self._bnc_in1_cfg)
            dpg.add_spacer(width=16)
            self._build_bnc_input_box(2, self._bnc_in2_cfg)
            dpg.add_spacer(width=16)
            self._build_bnc_output_box()

    def _build_bnc_input_box(self, idx: int, cfg: BNCInputConfig) -> None:
        key = f"bnc_in{idx}"
        tags: dict = {"last_edge_ts": 0.0}
        with dpg.child_window(width=300, height=180, border=True):
            with dpg.group(horizontal=True):
                tags["dot"] = dpg.add_text("●", color=_COLOR_GREY)
                dpg.add_text(f"BNC IN {idx}")
            dpg.add_separator()
            dpg.add_input_text(
                label="Label", default_value=cfg.label, width=150,
                user_data=cfg,
                callback=lambda s, a, u: setattr(u, "label", a),
            )
            dpg.add_combo(
                label="Edge", items=["rising", "falling", "both"],
                default_value=cfg.edge, width=100,
                user_data=cfg,
                callback=lambda s, a, u: setattr(u, "edge", a),
            )
            dpg.add_input_text(
                label="Action (placeholder)", default_value=cfg.action, width=180,
                hint="e.g. dispense_all, log_event, ...",
                user_data=cfg,
                callback=lambda s, a, u: setattr(u, "action", a),
            )
            dpg.add_checkbox(
                label="Enabled", default_value=cfg.enabled,
                user_data=cfg,
                callback=lambda s, a, u: setattr(u, "enabled", a),
            )
            tags["last_text"] = dpg.add_text("Last: —", color=(160, 165, 175, 255))
        self._bnc_tiles[key] = tags

    def _build_bnc_output_box(self) -> None:
        cfg = self._bnc_out_cfg
        tags: dict = {"last_pulse_ts": 0.0}
        with dpg.child_window(width=300, height=180, border=True):
            with dpg.group(horizontal=True):
                tags["dot"] = dpg.add_text("●", color=_COLOR_GREY)
                dpg.add_text("BNC OUT")
            dpg.add_separator()
            dpg.add_input_text(
                label="Label", default_value=cfg.label, width=150,
                user_data=cfg,
                callback=lambda s, a, u: setattr(u, "label", a),
            )
            dpg.add_input_int(
                label="Pulse (us)", default_value=cfg.pulse_width_us, width=100,
                min_value=1, max_value=1_000_000, min_clamped=True, max_clamped=True,
                user_data=cfg,
                callback=lambda s, a, u: setattr(u, "pulse_width_us", a),
            )
            dpg.add_input_text(
                label="Trigger (placeholder)", default_value=cfg.trigger, width=180,
                hint="e.g. pellet_taken, any_event, fault, ...",
                user_data=cfg,
                callback=lambda s, a, u: setattr(u, "trigger", a),
            )
            dpg.add_checkbox(
                label="Enabled", default_value=cfg.enabled,
                user_data=cfg,
                callback=lambda s, a, u: setattr(u, "enabled", a),
            )
            dpg.add_button(label="Manual Pulse", callback=lambda s, a, u: self._on_bnc_manual_pulse())
        self._bnc_tiles["bnc_out"] = tags

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
                items=["All", "EVENT", "COMMAND", "HEARTBEAT", "DISCOVERY", "BNC"],
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
            height=LOG_TABLE_HEIGHT,
            policy=dpg.mvTable_SizingFixedFit,
        ):
            dpg.add_table_column(label="Time",      width_fixed=True, init_width_or_weight=95)
            dpg.add_table_column(label="Node",      width_fixed=True, init_width_or_weight=60)
            dpg.add_table_column(label="Dir",       width_fixed=True, init_width_or_weight=35)
            dpg.add_table_column(label="Type",      width_fixed=True, init_width_or_weight=90)
            dpg.add_table_column(label="Event",     width_fixed=True, init_width_or_weight=150)
            dpg.add_table_column(label="ID",        width_fixed=True, init_width_or_weight=55)
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

        # 1b. Drain BNC edge queue (populated by IOManager's GPIO callback threads)
        bnc_events = []
        while True:
            try:
                bnc_events.append(self._bnc_edge_queue.get_nowait())
            except queue.Empty:
                break
        for which, ts in bnc_events:
            self._handle_bnc_edge(which, ts)
        self._refresh_bnc_dots()

        # 2. Tick discovery timeout
        if self._discovery:
            self._discovery.tick()

        # 3. Staleness check (once per second)
        now = time.time()
        if now - self._last_stale_check >= STALE_CHECK_INTERVAL:
            self._last_stale_check = now
            if self._registry:
                self._registry.check_staleness()
            self._refresh_all_tiles()

        # 3b. Base-station dispense scheduler (interval + chained modes)
        self._tick_schedulers(now)

        # 4. Refresh log table if new entries
        if messages or bnc_events:
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
                    self._maybe_fire_bnc_out(entry_name)
                    if ev.event == CanEvent.PelletPresented:
                        self._arm_chained_schedules(node_id)

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
                    handled = self._discovery.handle_frame(arb_id, data)
                    # Log the ASSIGN we just sent in response to ANNOUNCE
                    if handled and arb_id == CAN_ID_ANNOUNCE and self._log:
                        pending_mac = self._discovery.pending_mac
                        pending_id = self._discovery.pending_id
                        if pending_mac is not None and pending_id is not None:
                            self._log.add(LogEntry(
                                timestamp=time.time(),
                                direction="TX",
                                node_id=pending_id,
                                frame_type="DISCOVERY",
                                event_name="ASSIGN",
                                raw_id=CAN_ID_ASSIGN,
                                raw_data=pending_mac + bytes([pending_id]),
                                details=f"MAC={format_mac(pending_mac)} id={pending_id}",
                            ))
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
        dpg.configure_item(tags["mac_text"],    default_value=f"MAC : {node.mac_str}")

        # Heartbeat age — thresholds scale with the configured heartbeat
        # interval so a healthy beat at any interval reads as normal.
        age = node.heartbeat_age_s
        expected = max(self._hb_interval_s, 0.1)
        if age is None:
            hb_str = "—"
            hb_color = _COLOR_GREY
        elif age > expected * 3:
            hb_str = f"{age:.1f}s ago"
            hb_color = _COLOR_RED
        elif age > expected * 1.5:
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

    def _on_start_discovery(self, sender=None, app_data=None, user_data=None) -> None:
        """
        'Re-discover' — re-opens the discovery window (pulses AEO) so any
        newly-connected nodes ANNOUNCE and get assigned, and previously
        assigned nodes REJOIN. Does NOT clear any node's saved NVS ID and
        does NOT wipe existing registry state — use 'Clear All IDs' for that.
        """
        if self._discovery:
            self._discovery.rediscover()

    def _on_clear_all_ids(self, sender=None, app_data=None, user_data=None) -> None:
        """
        Broadcasts ClearId so all nodes wipe their saved NVS ID and re-enter
        WaitAEI, then pulses AEO to trigger fresh ANNOUNCE from every node.
        """
        if self._registry:
            for node in self._registry.all_nodes():
                node.mac = None
                node.discovery_state = "Pending"
                node.online = False
                node.last_heartbeat_time = None
            self._refresh_all_tiles()
        if self._discovery:
            self._discovery.reset()

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
        pass

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
                details="" if ok else "send failed",
            ))

    def _broadcast(self, cmd: CanCmd, payload: bytes = b"") -> None:
        if not self._can:
            return
        self._can.send_broadcast(cmd, payload)
        if self._log:
            self._log.add(LogEntry(
                timestamp=time.time(),
                direction="TX",
                node_id=0,
                frame_type="COMMAND",
                event_name=f"{cmd.name} (broadcast)",
                raw_id=0x100,
                raw_data=bytes([cmd.value]) + payload,
            ))

    def _on_apply_heartbeat_interval(self, sender=None, app_data=None, user_data=None) -> None:
        """Broadcast a SetConfig frame so every node adopts the new heartbeat interval."""
        seconds = dpg.get_value("hb_interval_input") if dpg.does_item_exist("hb_interval_input") else DEFAULT_HEARTBEAT_INTERVAL_S
        seconds = max(0.1, float(seconds))
        self._hb_interval_s = seconds
        payload = build_setconfig_heartbeat(int(seconds * 1000))
        self._broadcast(CanCmd.SetConfig, payload)

    def _on_assign_id(self, node_id: int) -> None:
        """
        Assign / reassign a node ID via discovery ASSIGN (0x081).

        Only works when the node's MAC is known from a previous ANNOUNCE or
        REJOIN — that MAC-addressed frame ensures only the targeted node
        accepts the assignment. Does NOT broadcast.
        """
        tags = self._node_tiles.get(node_id)
        if tags is None:
            return
        new_id = int(dpg.get_value(tags["assign_input"]))
        if not (1 <= new_id <= 254):
            return

        node = self._registry.get(node_id) if self._registry else None
        mac = node.mac if node else None

        if not mac or mac == bytes(6):
            if self._log:
                self._log.add(LogEntry(
                    timestamp=time.time(),
                    direction="TX",
                    node_id=node_id,
                    frame_type="ERROR",
                    event_name="AssignId",
                    raw_id=0,
                    raw_data=b"",
                    details="MAC unknown — run discovery first",
                ))
            return

        ok = self._can.send_assign(mac, new_id)
        if self._log:
            self._log.add(LogEntry(
                timestamp=time.time(),
                direction="TX",
                node_id=new_id,
                frame_type="DISCOVERY",
                event_name="ASSIGN",
                raw_id=CAN_ID_ASSIGN,
                raw_data=mac + bytes([new_id]),
                details=f"MAC={format_mac(mac)} → id={new_id}"
                        + ("" if ok else " (send failed)"),
            ))

    # ------------------------------------------------------------------
    # Dispense scheduler (SetConfig button)
    # ------------------------------------------------------------------

    def _on_open_schedule_dialog(self, node_id: int) -> None:
        """Open a modal letting the user set a flexible dispense schedule for one node."""
        if dpg.does_item_exist("schedule_modal"):
            dpg.delete_item("schedule_modal")

        cfg = self._schedules.get(node_id, ScheduleConfig())
        mode_labels = {
            "off": "Off",
            "interval": "Every X minutes",
            "chained": "X minutes after node Y dispenses",
        }
        num_nodes = self._registry.num_nodes() if self._registry else 9
        other_nodes = [str(i) for i in range(1, num_nodes + 1) if i != node_id]
        default_y = str(cfg.chained_node_id) if str(cfg.chained_node_id) in other_nodes else (other_nodes[0] if other_nodes else "")

        with dpg.window(
            tag="schedule_modal",
            label=f"SetConfig — Node {node_id} Dispense Schedule",
            modal=True,
            no_resize=True,
            width=360,
            height=260,
            pos=((WINDOW_W - 360) // 2, (WINDOW_H - 260) // 2),
        ):
            dpg.add_text(f"Dispense schedule for Node {node_id}", color=(100, 180, 255, 255))
            dpg.add_separator()
            dpg.add_radio_button(
                tag="schedule_mode_radio",
                items=list(mode_labels.values()),
                default_value=mode_labels[cfg.mode],
            )
            dpg.add_spacer(height=6)
            dpg.add_input_float(
                tag="schedule_minutes_input",
                label="Minutes",
                default_value=(cfg.interval_minutes if cfg.mode == "interval" else cfg.chained_delay_minutes),
                width=120,
                min_value=0.1,
                max_value=1440.0,
                min_clamped=True,
                max_clamped=True,
            )
            dpg.add_combo(
                tag="schedule_nodeY_combo",
                label="After node",
                items=other_nodes,
                default_value=default_y,
                width=120,
            )
            dpg.add_spacer(height=10)
            dpg.add_separator()
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Apply", width=90, user_data=node_id,
                    callback=lambda s, a, u: self._on_apply_schedule(u),
                )
                dpg.add_button(
                    label="Cancel", width=90,
                    callback=lambda: dpg.delete_item("schedule_modal"),
                )

    def _on_apply_schedule(self, node_id: int) -> None:
        if not dpg.does_item_exist("schedule_mode_radio"):
            return
        mode_label = dpg.get_value("schedule_mode_radio")
        minutes = float(dpg.get_value("schedule_minutes_input"))
        node_y_str = dpg.get_value("schedule_nodeY_combo")

        cfg = self._schedules.get(node_id, ScheduleConfig())
        if mode_label == "Every X minutes":
            cfg.mode = "interval"
            cfg.interval_minutes = max(0.1, minutes)
            cfg.next_fire_time = time.time() + cfg.interval_minutes * 60.0
            cfg.armed_fire_time = None
        elif mode_label == "X minutes after node Y dispenses":
            cfg.mode = "chained"
            cfg.chained_delay_minutes = max(0.1, minutes)
            cfg.chained_node_id = int(node_y_str) if node_y_str else cfg.chained_node_id
            cfg.next_fire_time = None
            cfg.armed_fire_time = None
        else:
            cfg.mode = "off"
            cfg.next_fire_time = None
            cfg.armed_fire_time = None

        self._schedules[node_id] = cfg
        self._refresh_schedule_text(node_id)

        if self._log:
            self._log.add(LogEntry(
                timestamp=time.time(),
                direction="TX",
                node_id=node_id,
                frame_type="COMMAND",
                event_name="SetConfig (schedule)",
                raw_id=0,
                raw_data=b"",
                details=cfg.summary,
            ))

        dpg.delete_item("schedule_modal")

    def _refresh_schedule_text(self, node_id: int) -> None:
        tags = self._node_tiles.get(node_id)
        cfg = self._schedules.get(node_id)
        if tags is None or "schedule_text" not in tags:
            return
        summary = cfg.summary if cfg else "Off"
        dpg.configure_item(
            tags["schedule_text"],
            default_value=f"Schedule: {summary}",
            color=(160, 165, 175, 255) if (cfg is None or cfg.mode == "off") else (100, 200, 100, 255),
        )

    def _arm_chained_schedules(self, source_node_id: int) -> None:
        """
        Called when `source_node_id` fires a PelletPresented event. Any node
        whose schedule is chained to this node gets its one-shot dispense
        timer (re-)armed for `chained_delay_minutes` from now.
        """
        now = time.time()
        for node_id, cfg in self._schedules.items():
            if cfg.mode == "chained" and cfg.chained_node_id == source_node_id:
                cfg.armed_fire_time = now + cfg.chained_delay_minutes * 60.0

    def _tick_schedulers(self, now: float) -> None:
        """Fire Dispense commands for nodes whose schedule is due. Call once per frame."""
        for node_id, cfg in self._schedules.items():
            if cfg.mode == "interval" and cfg.next_fire_time is not None:
                if now >= cfg.next_fire_time:
                    self._send_cmd(node_id, CanCmd.Dispense)
                    cfg.next_fire_time = now + cfg.interval_minutes * 60.0
            elif cfg.mode == "chained" and cfg.armed_fire_time is not None:
                if now >= cfg.armed_fire_time:
                    self._send_cmd(node_id, CanCmd.Dispense)
                    cfg.armed_fire_time = None

    # ------------------------------------------------------------------
    # BNC / Sync I/O
    # ------------------------------------------------------------------

    def _handle_bnc_edge(self, which: str, ts: float) -> None:
        """Handle a BNC IN1/IN2 edge event drained from the IOManager callback queue."""
        cfg = self._bnc_in1_cfg if which == "IN1" else self._bnc_in2_cfg
        tile = self._bnc_tiles.get(f"bnc_{which.lower()}")
        if tile is not None:
            tile["last_edge_ts"] = ts
            dpg.configure_item(tile["dot"], color=_COLOR_GREEN)
            dpg.set_value(tile["last_text"], f"Last: {time.strftime('%H:%M:%S', time.localtime(ts))}")

        if self._log:
            self._log.add(LogEntry(
                timestamp=ts, direction="RX", node_id=0, frame_type="BNC",
                event_name=f"{which} edge", raw_id=0, raw_data=b"",
                details=f"label={cfg.label or '—'} action={cfg.action or '(none)'}",
            ))

        if cfg.enabled and cfg.action:
            self._dispatch_bnc_action(cfg.action)

    def _dispatch_bnc_action(self, action: str) -> None:
        """
        Best-effort dispatch for a handful of convenience keywords. Anything
        else is a free-form placeholder — it was already logged in
        _handle_bnc_edge, ready to be wired to real behaviour later.
        """
        keyword = action.strip().lower().replace("-", "_")
        if keyword == "dispense_all":
            self._broadcast(CanCmd.Dispense)
        elif keyword == "abort_all":
            self._broadcast(CanCmd.Abort)
        elif keyword == "ping_all":
            self._broadcast(CanCmd.Ping)
        elif keyword == "reqstatus_all":
            self._broadcast(CanCmd.ReqStatus)
        # else: placeholder only — no built-in behaviour yet.

    def _maybe_fire_bnc_out(self, event_name: str) -> None:
        """
        Called for every incoming CAN EVENT frame. If BNC OUT is enabled and
        its (free-form) trigger matches this event, fire a pulse.
        """
        cfg = self._bnc_out_cfg
        if not cfg.enabled or not cfg.trigger:
            return
        trigger = cfg.trigger.strip().lower().replace("-", "_")
        event_key = event_name.strip().lower()
        matched = trigger in ("any", "any_event", "all") or trigger == event_key
        if not matched:
            return

        self._io.pulse_bnc_out(cfg.pulse_width_us)
        tile = self._bnc_tiles.get("bnc_out")
        if tile is not None:
            tile["last_pulse_ts"] = time.time()
            dpg.configure_item(tile["dot"], color=_COLOR_GREEN)

        if self._log:
            self._log.add(LogEntry(
                timestamp=time.time(), direction="TX", node_id=0, frame_type="BNC",
                event_name="BNC OUT pulse", raw_id=0, raw_data=b"",
                details=f"trigger={cfg.trigger} width={cfg.pulse_width_us}us matched={event_name}",
            ))

    def _on_bnc_manual_pulse(self) -> None:
        """'Manual Pulse' button — fires BNC OUT once for bench testing, regardless of Enabled."""
        width = self._bnc_out_cfg.pulse_width_us
        self._io.pulse_bnc_out(width)
        tile = self._bnc_tiles.get("bnc_out")
        if tile is not None:
            tile["last_pulse_ts"] = time.time()
            dpg.configure_item(tile["dot"], color=_COLOR_GREEN)
        if self._log:
            self._log.add(LogEntry(
                timestamp=time.time(), direction="TX", node_id=0, frame_type="BNC",
                event_name="BNC OUT manual pulse", raw_id=0, raw_data=b"",
                details=f"width={width}us",
            ))

    def _refresh_bnc_dots(self) -> None:
        """Decay the BNC IN/OUT indicator dots back to grey shortly after the last edge/pulse."""
        now = time.time()
        for key in ("bnc_in1", "bnc_in2", "bnc_out"):
            tile = self._bnc_tiles.get(key)
            if tile is None:
                continue
            last = tile.get("last_edge_ts", tile.get("last_pulse_ts", 0.0))
            if (now - last) > 0.3:
                dpg.configure_item(tile["dot"], color=_COLOR_GREY)

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
        self._io.shutdown()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args: argparse.Namespace) -> None:
    app = VFMApp(args)
    app.run()
