import argparse

import pytest

from vfm_gui.protocol import CanCmd


def test_command_purpose_covers_all_cmds() -> None:
    # Import COMMAND_PURPOSE without requiring a full DearPyGui session beyond
    # the module import (dearpygui must be installed for app.py).
    try:
        from vfm_gui.app import COMMAND_PURPOSE
    except ModuleNotFoundError as exc:
        if "dearpygui" in str(exc).lower():
            pytest.skip("dearpygui not installed")
        raise
    for cmd in CanCmd:
        assert cmd in COMMAND_PURPOSE, f"missing COMMAND_PURPOSE for {cmd.name}"
        assert isinstance(COMMAND_PURPOSE[cmd], str) and COMMAND_PURPOSE[cmd]


def test_render_callback_wrapper_reschedules_itself(monkeypatch) -> None:
    try:
        from vfm_gui.app import VFMApp
    except ModuleNotFoundError as exc:
        if "dearpygui" in str(exc).lower():
            pytest.skip("dearpygui not installed")
        raise

    app = VFMApp(
        argparse.Namespace(interface="can0", bitrate=250000, nodes=3, log_dir="~/vfm_logs")
    )

    calls = []
    monkeypatch.setattr(app, "_on_render", lambda: calls.append("render"))
    monkeypatch.setattr("vfm_gui.app.dpg.get_frame_count", lambda: 7)
    scheduled = []
    monkeypatch.setattr("vfm_gui.app.dpg.set_frame_callback", lambda frame, cb: scheduled.append((frame, cb)))

    callback = app._make_render_callback()
    callback()

    assert calls == ["render"]
    assert scheduled == [(8, callback)]
