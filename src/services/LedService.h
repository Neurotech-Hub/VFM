#pragma once

#include "../hardware/VFMPins.h"
#include "ServiceTypes.h"
#include <Arduino.h>

namespace vfm {

// LED service for the status LED and the two user IO LEDs.
//
// Two independent blink channels:
//   1. Status LED  (PIN_STATUS_LED) – reserved for fault indication;
//      also slow-blinks during discovery wait.
//   2. IO LED 9    (PIN_LED_IO_9)   – boot / discovery indication.
//
// All LEDs are digital (on/off). RGB or PWM extensions can be added later.
class LedService {
public:
  LedService() = default;

  ServiceStatus begin() {
    pinMode(PIN_STATUS_LED, OUTPUT);
    pinMode(PIN_LED_IO_9, OUTPUT);
    pinMode(PIN_LED_IO_10, OUTPUT);
    setStatus(false);
    setIoLed(false);
    setLed10(false);
    return ServiceStatus::Ok;
  }

  // --- Direct on/off controls ---
  void setStatus(bool on) { digitalWrite(PIN_STATUS_LED, on ? HIGH : LOW); }
  void setIoLed(bool on) { digitalWrite(PIN_LED_IO_9, on ? HIGH : LOW); }
  void setLed10(bool on) { digitalWrite(PIN_LED_IO_10, on ? HIGH : LOW); }

  // --- Status LED blink channel ---
  // Blink status LED at the given interval. Pass 0 to stop blinking.
  void setBlinkIntervalMs(uint32_t ms) {
    blinkMs_ = ms;
    blinkStart_ = millis();
    statusOn_ = false;
  }

  // --- IO LED 9 blink channel ---
  // Blink IO LED 9 at the given interval. Pass 0 to stop blinking.
  void setIoBlinkIntervalMs(uint32_t ms) {
    ioBlinkMs_ = ms;
    ioBlinkStart_ = millis();
    ioOn_ = false;
  }

  // Tick both blink channels; call from loop().
  void update() {
    // Status LED blink channel
    if (blinkMs_ > 0) {
      if ((millis() - blinkStart_) >= blinkMs_) {
        blinkStart_ = millis();
        statusOn_ = !statusOn_;
        setStatus(statusOn_);
      }
    }

    // IO LED 9 blink channel
    if (ioBlinkMs_ > 0) {
      if ((millis() - ioBlinkStart_) >= ioBlinkMs_) {
        ioBlinkStart_ = millis();
        ioOn_ = !ioOn_;
        setIoLed(ioOn_);
      }
    }
  }

private:
  // Status LED blink state
  uint32_t blinkMs_ = 0;
  uint32_t blinkStart_ = 0;
  bool statusOn_ = false;

  // IO LED 9 blink state
  uint32_t ioBlinkMs_ = 0;
  uint32_t ioBlinkStart_ = 0;
  bool ioOn_ = false;
};

} // namespace vfm
