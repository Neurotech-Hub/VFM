"""Tests for ScheduleConfig countdown formatting."""

import time

from sfm_gui.app import ScheduleConfig


def test_countdown_interval_formats_minutes_and_seconds() -> None:
    now = 1_000_000.0
    cfg = ScheduleConfig(
        mode="interval",
        interval_minutes=10.0,
        next_fire_time=now + (4 * 60) + 32.4,
    )
    assert cfg.countdown_str(now) == "4m 33s"
    assert cfg.display_line(now) == "Schedule: Every 10 min · 4m 33s"


def test_countdown_zero_when_due() -> None:
    now = time.time()
    cfg = ScheduleConfig(mode="interval", next_fire_time=now - 1.0)
    assert cfg.countdown_str(now) == "0m 00s"


def test_countdown_chained_waiting() -> None:
    cfg = ScheduleConfig(mode="chained", chained_node_id=2, chained_delay_minutes=5.0)
    assert cfg.countdown_str() == "waiting"
    assert cfg.display_line() == "Schedule: 5 min after Node 2 · waiting"


def test_countdown_chained_armed() -> None:
    now = 1_000_000.0
    cfg = ScheduleConfig(
        mode="chained",
        chained_node_id=2,
        chained_delay_minutes=5.0,
        armed_fire_time=now + 90.0,
    )
    assert cfg.countdown_str(now) == "1m 30s"


def test_countdown_hours_for_long_intervals() -> None:
    now = 1_000_000.0
    cfg = ScheduleConfig(
        mode="interval",
        interval_minutes=120.0,
        next_fire_time=now + (2 * 3600) + (5 * 60) + 7,
    )
    assert cfg.countdown_str(now) == "2h 5m 07s"


def test_off_has_no_countdown() -> None:
    cfg = ScheduleConfig(mode="off")
    assert cfg.countdown_str() == ""
    assert cfg.display_line() == "Schedule: Off"
    assert cfg.due_time is None
