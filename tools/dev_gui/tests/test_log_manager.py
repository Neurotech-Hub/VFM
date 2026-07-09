"""Tests for vfm_gui.log_manager."""

import sys
import os
import tempfile
import csv
import time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from vfm_gui.log_manager import LogManager, LogEntry


def make_entry(**kwargs) -> LogEntry:
    defaults = dict(
        timestamp=time.time(),
        direction="RX",
        node_id=1,
        frame_type="EVENT",
        event_name="PelletLoaded",
        raw_id=0x301,
        raw_data=bytes([0x01]),
        details="",
    )
    defaults.update(kwargs)
    return LogEntry(**defaults)


class TestLogManager:
    def test_add_and_retrieve(self):
        lm = LogManager(auto_save=False)
        lm.add(make_entry(event_name="PelletLoaded"))
        assert lm.total_count == 1

    def test_ring_buffer_overflow(self):
        lm = LogManager(max_entries=5, auto_save=False)
        for i in range(10):
            lm.add(make_entry(event_name=f"Event{i}"))
        assert lm.total_count == 5
        # Only the last 5 entries remain
        entries = lm.get_filtered(show_heartbeats=True)
        names = [e.event_name for e in entries]
        assert "Event0" not in names
        assert "Event9" in names

    def test_filter_by_node(self):
        lm = LogManager(auto_save=False)
        lm.add(make_entry(node_id=1, event_name="A"))
        lm.add(make_entry(node_id=2, event_name="B"))
        lm.add(make_entry(node_id=1, event_name="C"))
        result = lm.get_filtered(node_id=1)
        assert all(e.node_id == 1 for e in result)
        assert len(result) == 2

    def test_filter_by_type(self):
        lm = LogManager(auto_save=False)
        lm.add(make_entry(frame_type="EVENT"))
        lm.add(make_entry(frame_type="COMMAND"))
        lm.add(make_entry(frame_type="HEARTBEAT"))
        result = lm.get_filtered(frame_type="COMMAND", show_heartbeats=True)
        assert all(e.frame_type == "COMMAND" for e in result)

    def test_heartbeats_hidden_by_default(self):
        lm = LogManager(auto_save=False)
        lm.add(make_entry(frame_type="HEARTBEAT"))
        lm.add(make_entry(frame_type="EVENT"))
        result = lm.get_filtered(show_heartbeats=False)
        assert all(e.frame_type != "HEARTBEAT" for e in result)

    def test_heartbeats_shown_when_requested(self):
        lm = LogManager(auto_save=False)
        lm.add(make_entry(frame_type="HEARTBEAT"))
        result = lm.get_filtered(show_heartbeats=True)
        assert len(result) == 1

    def test_newest_first(self):
        lm = LogManager(auto_save=False)
        t = time.time()
        lm.add(make_entry(timestamp=t,     event_name="First"))
        lm.add(make_entry(timestamp=t+1.0, event_name="Second"))
        entries = lm.get_filtered(show_heartbeats=True)
        assert entries[0].event_name == "Second"

    def test_clear_empties_buffer(self):
        lm = LogManager(auto_save=False)
        for _ in range(5):
            lm.add(make_entry())
        lm.clear()
        assert lm.total_count == 0

    def test_export_csv(self, tmp_path):
        lm = LogManager(auto_save=False)
        lm.add(make_entry(event_name="TestEvent", node_id=3))
        export_path = tmp_path / "export.csv"
        out = lm.export(str(export_path))
        assert out.exists()
        with open(out) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["event_name"] == "TestEvent"
        assert rows[0]["node_id"] == "3"

    def test_autosave_csv(self, tmp_path):
        lm = LogManager(max_entries=100, log_dir=str(tmp_path), auto_save=True)
        lm.add(make_entry(event_name="AutoSaved"))
        lm.close()
        # Find the session file
        csv_files = list(tmp_path.glob("session_*.csv"))
        assert len(csv_files) == 1
        with open(csv_files[0]) as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        assert rows[0]["event_name"] == "AutoSaved"

    def test_timestamp_str_format(self):
        entry = make_entry(timestamp=0.0)  # epoch midnight
        # Just check it's HH:MM:SS.mmm format
        ts = entry.timestamp_str
        assert len(ts) == 12  # "HH:MM:SS.mmm"
        assert ts[2] == ":" and ts[5] == ":"

    def test_raw_id_hex(self):
        entry = make_entry(raw_id=0x301)
        assert entry.raw_id_hex == "0x301"

    def test_raw_data_hex(self):
        entry = make_entry(raw_data=bytes([0x01, 0xAB]))
        assert entry.raw_data_hex == "01 AB"

    def test_combined_filter(self):
        lm = LogManager(auto_save=False)
        lm.add(make_entry(node_id=1, frame_type="EVENT"))
        lm.add(make_entry(node_id=1, frame_type="COMMAND"))
        lm.add(make_entry(node_id=2, frame_type="EVENT"))
        result = lm.get_filtered(node_id=1, frame_type="EVENT")
        assert len(result) == 1
        assert result[0].node_id == 1
        assert result[0].frame_type == "EVENT"
