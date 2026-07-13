import argparse

from vfm_gui.app import VFMApp


def test_render_callback_wrapper_reschedules_itself(monkeypatch) -> None:
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
