"""
io_manager.py — Base station GPIO management (BNC I/O, button, AEO).

Owns every base-station GPIO pin *except* CAN, which is handled entirely by
the kernel SocketCAN driver (see ``deploy/`` for the MCP2515 device tree
overlay and ``can_manager.py`` for the SocketCAN wrapper). Responsibilities:

  - BNC IN 1 / BNC IN 2 : edge-triggered digital inputs, transparently
    de-inverted in software. The Schmitt-trigger inverters on the HAT
    (SN74AHCT1G14) invert the true BNC TTL level before it reaches the Pi
    GPIO, so callers of this module always see true BNC-level logic
    (``True`` == BNC signal is HIGH).
  - BNC OUT             : digital output with a hardware-precision one-shot
    pulse generator, used to mirror CAN/system events out to external
    recording systems (e.g. electrophysiology DAQs) for synchronization.
  - User button (GPIO3) : simple debounced digital input.
  - AEO (GPIO27)        : Address Enable Out — drives the daisy-chain
    discovery signal to the first node. Used by DiscoveryManager instead of
    it touching GPIO directly.

All GPIO access degrades gracefully when no GPIO backend is available (e.g.
running the GUI on a dev machine against vcan0, or on a Pi without the
optional ``gpiod``/``RPi.GPIO`` packages installed) — every public method
becomes a harmless no-op / simulated value rather than raising.

Trigger/action configuration (``BNCInputConfig`` / ``BNCOutputConfig``) is
intentionally free-form: this module does not hard-code a fixed list of
"actions" or "triggers". The GUI layer owns interpreting those strings and
deciding what to actually do (send a CAN command, log something, etc.). This
keeps the base station flexible for use cases we haven't anticipated yet.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

# ---------------------------------------------------------------------------
# Pin definitions (BCM numbering) — fixed by the custom CAN HAT schematic.
# ---------------------------------------------------------------------------
PIN_BNC_IN1 = 12   # BNC Input 1  — inverted Schmitt trigger
PIN_BNC_IN2 = 13   # BNC Input 2  — inverted Schmitt trigger
PIN_BNC_OUT = 6    # BNC Output   — non-inverting buffer + LED
PIN_BTN     = 3    # User button
PIN_AEO     = 27   # Address Enable Out -> first node in the daisy chain
                    # (CAN_IO_27 on the pin table; NOT GPIO17, which is a
                    # spare IO routed onto the CAN bus cable for future use)

# Debounce window for the user button (seconds).
BUTTON_DEBOUNCE_S = 0.05

# BNC OUT pulse: sleep for the bulk of the duration, then spin the final
# stretch on a monotonic clock for tight timing accuracy.
_PULSE_SPIN_MARGIN_S = 0.0002

EdgeCallback = Callable[[], None]


# ---------------------------------------------------------------------------
# Configurable BNC mapping — deliberately free-form placeholders.
# ---------------------------------------------------------------------------

@dataclass
class BNCInputConfig:
    """
    Describes what should happen when a BNC input edge is detected.

    ``action`` / ``action_params`` are free-form placeholders, not a fixed
    enum — the GUI is responsible for offering/interpreting whatever action
    the user types (e.g. "dispense_all") and for dispatching it when an edge
    fires. Unrecognized values are simply logged, ready to be wired up later.
    """
    label: str = ""
    enabled: bool = False
    edge: str = "rising"                 # "rising" | "falling" | "both"
    action: str = ""                     # placeholder — GUI-defined/free text
    action_params: dict = field(default_factory=dict)


@dataclass
class BNCOutputConfig:
    """
    Describes what should cause the BNC output to pulse.

    Like ``BNCInputConfig``, ``trigger`` / ``trigger_params`` are free-form
    placeholders — the GUI owns matching CAN/system events against the
    configured trigger and calling ``IOManager.pulse_bnc_out()``.
    """
    label: str = ""
    enabled: bool = False
    pulse_width_us: int = 100
    trigger: str = ""                    # placeholder — GUI-defined/free text
    trigger_params: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# IOManager
# ---------------------------------------------------------------------------

class IOManager:
    """
    Owns every base-station GPIO pin except CAN.

    Usage::

        io = IOManager()
        io.begin()
        io.on_bnc_in1_edge(lambda: print("BNC IN1 edge!"))
        io.pulse_bnc_out(100)     # 100 microsecond pulse
        io.drive_aeo(True)
        ...
        io.shutdown()
    """

    def __init__(self) -> None:
        self._gpio_backend: Optional[str] = None   # "gpiod" | "RPi.GPIO" | None
        self._gpio = None                           # RPi.GPIO module reference
        self._request = None                        # gpiod LineRequest handle

        self._bnc_in1_cb: List[EdgeCallback] = []
        self._bnc_in2_cb: List[EdgeCallback] = []
        self._button_cb: List[EdgeCallback] = []

        self._last_button_time = 0.0
        self._bnc_out_state = False
        self._edge_thread_stop = threading.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def begin(self) -> None:
        """
        Configure all pins. Tries backends in order of precision:

          1. libgpiod (v2)     — preferred: native edge events + fast writes
          2. RPi.GPIO          — fallback: widely available on Raspberry Pi OS
          3. None (simulation) — no hardware present (dev machine / CI)
        """
        if self._try_setup_gpiod():
            self._gpio_backend = "gpiod"
        elif self._try_setup_rpi_gpio():
            self._gpio_backend = "RPi.GPIO"
        else:
            self._gpio_backend = None

        # Always leave BNC OUT low at startup so the onboard LED does not
        # light spuriously before anything has been explicitly configured.
        self.set_bnc_out(False)

    def shutdown(self) -> None:
        """Release GPIO resources cleanly."""
        self.set_bnc_out(False)
        self._edge_thread_stop.set()

        if self._gpio_backend == "RPi.GPIO" and self._gpio is not None:
            try:
                self._gpio.cleanup([PIN_BNC_IN1, PIN_BNC_IN2, PIN_BNC_OUT, PIN_BTN, PIN_AEO])
            except Exception:
                pass
        elif self._gpio_backend == "gpiod" and self._request is not None:
            try:
                self._request.release()
            except Exception:
                pass

        self._gpio_backend = None
        self._gpio = None
        self._request = None

    @property
    def backend(self) -> Optional[str]:
        """Active GPIO backend: "gpiod", "RPi.GPIO", or None (simulation)."""
        return self._gpio_backend

    @property
    def is_available(self) -> bool:
        return self._gpio_backend is not None

    # ------------------------------------------------------------------
    # BNC Inputs — transparent de-inversion + edge callbacks
    # ------------------------------------------------------------------

    def read_bnc_in1(self) -> bool:
        """True == BNC signal is logically HIGH (Schmitt inversion already undone)."""
        return self._read_input_inverted(PIN_BNC_IN1)

    def read_bnc_in2(self) -> bool:
        """True == BNC signal is logically HIGH (Schmitt inversion already undone)."""
        return self._read_input_inverted(PIN_BNC_IN2)

    def on_bnc_in1_edge(self, callback: EdgeCallback) -> None:
        """Register a callback fired (from a background thread) on every BNC IN1 edge."""
        self._bnc_in1_cb.append(callback)

    def on_bnc_in2_edge(self, callback: EdgeCallback) -> None:
        """Register a callback fired (from a background thread) on every BNC IN2 edge."""
        self._bnc_in2_cb.append(callback)

    # ------------------------------------------------------------------
    # BNC Output — hardware-precision one-shot pulse
    # ------------------------------------------------------------------

    def set_bnc_out(self, high: bool) -> None:
        """Drive the BNC output (and its onboard LED indicator) level directly."""
        self._bnc_out_state = high
        self._write_output(PIN_BNC_OUT, high)

    def read_bnc_out(self) -> bool:
        """Last commanded level of the BNC output (not read back from hardware)."""
        return self._bnc_out_state

    def pulse_bnc_out(self, duration_us: int = 100) -> None:
        """
        Fire a single HIGH pulse of ``duration_us`` microseconds on the BNC
        output, then return it LOW. Runs on a dedicated daemon thread using a
        monotonic-clock spin-wait for the final stretch of the pulse width,
        which keeps jitter far tighter than a plain ``time.sleep()`` call —
        important for synchronizing with external recording systems.
        """
        def _run() -> None:
            self._write_output(PIN_BNC_OUT, True)
            self._bnc_out_state = True
            self._precise_wait_us(duration_us)
            self._write_output(PIN_BNC_OUT, False)
            self._bnc_out_state = False

        threading.Thread(target=_run, daemon=True, name="vfm-bnc-pulse").start()

    # ------------------------------------------------------------------
    # User button
    # ------------------------------------------------------------------

    def read_button(self) -> bool:
        return self._read_input_raw(PIN_BTN)

    def on_button_press(self, callback: EdgeCallback) -> None:
        """Register a debounced callback fired on every button edge."""
        self._button_cb.append(callback)

    # ------------------------------------------------------------------
    # AEO — Address Enable Out (daisy-chain discovery signal)
    # ------------------------------------------------------------------

    def drive_aeo(self, high: bool) -> None:
        """Drive AEO (GPIO27) HIGH/LOW to enable the next node in the daisy chain."""
        self._write_output(PIN_AEO, high)

    # ------------------------------------------------------------------
    # Backend setup
    # ------------------------------------------------------------------

    def _try_setup_gpiod(self) -> bool:
        """
        Attempt to configure pins via libgpiod v2. Provides native edge-event
        support (no polling) for BNC IN1/2 and the button.
        """
        try:
            import gpiod
            from gpiod.line import Bias, Direction, Edge, Value
        except Exception:
            return False

        chip_path = self._find_gpiochip()
        if chip_path is None:
            return False

        try:
            request = gpiod.request_lines(
                chip_path,
                consumer="vfm-io-manager",
                config={
                    PIN_BNC_IN1: gpiod.LineSettings(
                        direction=Direction.INPUT, edge_detection=Edge.BOTH, bias=Bias.PULL_DOWN
                    ),
                    PIN_BNC_IN2: gpiod.LineSettings(
                        direction=Direction.INPUT, edge_detection=Edge.BOTH, bias=Bias.PULL_DOWN
                    ),
                    PIN_BTN: gpiod.LineSettings(
                        direction=Direction.INPUT, edge_detection=Edge.BOTH, bias=Bias.PULL_UP
                    ),
                    PIN_BNC_OUT: gpiod.LineSettings(
                        direction=Direction.OUTPUT, output_value=Value.INACTIVE
                    ),
                    PIN_AEO: gpiod.LineSettings(
                        direction=Direction.OUTPUT, output_value=Value.INACTIVE
                    ),
                },
            )
        except Exception:
            return False

        self._request = request
        self._edge_thread_stop.clear()
        threading.Thread(
            target=self._gpiod_edge_loop, args=(request,), daemon=True, name="vfm-gpiod-edges"
        ).start()
        return True

    def _try_setup_rpi_gpio(self) -> bool:
        """Fallback backend using RPi.GPIO (polling-based edge detection via its own thread)."""
        try:
            import RPi.GPIO as GPIO  # type: ignore
        except Exception:
            return False

        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(PIN_BNC_IN1, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            GPIO.setup(PIN_BNC_IN2, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            GPIO.setup(PIN_BTN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.setup(PIN_BNC_OUT, GPIO.OUT, initial=GPIO.LOW)
            GPIO.setup(PIN_AEO, GPIO.OUT, initial=GPIO.LOW)

            GPIO.add_event_detect(
                PIN_BNC_IN1, GPIO.BOTH, callback=lambda ch: self._dispatch(self._bnc_in1_cb)
            )
            GPIO.add_event_detect(
                PIN_BNC_IN2, GPIO.BOTH, callback=lambda ch: self._dispatch(self._bnc_in2_cb)
            )
            GPIO.add_event_detect(PIN_BTN, GPIO.BOTH, callback=self._on_button_raw_edge)
        except Exception:
            return False

        self._gpio = GPIO
        return True

    @staticmethod
    def _find_gpiochip() -> Optional[str]:
        """Locate the Pi's main GPIO chip device node (varies by Pi model/kernel)."""
        for candidate in ("/dev/gpiochip0", "/dev/gpiochip4"):
            if os.path.exists(candidate):
                return candidate
        return None

    def _gpiod_edge_loop(self, request) -> None:
        """Background thread: block on libgpiod edge events and dispatch callbacks."""
        while not self._edge_thread_stop.is_set():
            try:
                if request.wait_edge_events(timeout=0.2):
                    for event in request.read_edge_events():
                        offset = event.line_offset
                        if offset == PIN_BNC_IN1:
                            self._dispatch(self._bnc_in1_cb)
                        elif offset == PIN_BNC_IN2:
                            self._dispatch(self._bnc_in2_cb)
                        elif offset == PIN_BTN:
                            self._on_button_raw_edge(None)
            except Exception:
                time.sleep(0.2)

    # ------------------------------------------------------------------
    # IO primitives (backend dispatch)
    # ------------------------------------------------------------------

    def _read_input_raw(self, pin: int) -> bool:
        """
        Raw GPIO level, no inversion applied.

        In simulation mode (no GPIO backend available), BNC IN pins default
        to raw HIGH — mirroring the Schmitt inverter's resting output when no
        BNC signal is present — so ``read_bnc_in1()``/``read_bnc_in2()`` report
        the same idle (no-signal) state as real hardware.
        """
        if self._gpio_backend == "RPi.GPIO" and self._gpio is not None:
            try:
                return bool(self._gpio.input(pin))
            except Exception:
                return False
        if self._gpio_backend == "gpiod" and self._request is not None:
            try:
                from gpiod.line import Value
                return self._request.get_value(pin) == Value.ACTIVE
            except Exception:
                return False
        return pin in (PIN_BNC_IN1, PIN_BNC_IN2)

    def _read_input_inverted(self, pin: int) -> bool:
        """
        Read a BNC input pin and undo the hardware Schmitt-trigger inversion,
        so callers always see true BNC-level logic (True == signal present).
        """
        return not self._read_input_raw(pin)

    def _write_output(self, pin: int, high: bool) -> None:
        if self._gpio_backend == "RPi.GPIO" and self._gpio is not None:
            try:
                self._gpio.output(pin, self._gpio.HIGH if high else self._gpio.LOW)
            except Exception:
                pass
        elif self._gpio_backend == "gpiod" and self._request is not None:
            try:
                from gpiod.line import Value
                self._request.set_value(pin, Value.ACTIVE if high else Value.INACTIVE)
            except Exception:
                pass
        # else: simulation mode (no hardware) — no-op, state still tracked in memory.

    @staticmethod
    def _dispatch(callbacks: List[EdgeCallback]) -> None:
        for cb in callbacks:
            try:
                cb()
            except Exception:
                pass

    def _on_button_raw_edge(self, _channel) -> None:
        now = time.monotonic()
        if (now - self._last_button_time) < BUTTON_DEBOUNCE_S:
            return
        self._last_button_time = now
        self._dispatch(self._button_cb)

    # ------------------------------------------------------------------
    # BNC OUT pulse timing
    # ------------------------------------------------------------------

    @staticmethod
    def _precise_wait_us(duration_us: int) -> None:
        """
        Busy-wait for ``duration_us`` microseconds using a monotonic clock.
        Sleeps for the bulk of the duration (yielding the CPU) then spins for
        the final ~200 us for tight timing accuracy — this is the best
        precision achievable from pure Python/userspace without a dedicated
        real-time kernel timer or hardware PWM one-shot.
        """
        target = time.perf_counter() + (duration_us / 1_000_000)
        remaining = target - time.perf_counter()
        if remaining > _PULSE_SPIN_MARGIN_S:
            time.sleep(remaining - _PULSE_SPIN_MARGIN_S)
        while time.perf_counter() < target:
            pass
