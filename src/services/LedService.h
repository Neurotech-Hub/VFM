#pragma once

#include "../hardware/VFMPins.h"
#include "ServiceTypes.h"
#include <Arduino.h>

namespace vfm {

// LED service for the status LED and the two user IO LEDs.
//
// Blink channels:
//   1. Status LED  (PIN_STATUS_LED) – fault / discovery indication
//   2. LED 9       (PIN_LED_IO_9)   – boot / discovery / button-hold warning
//   3. LED 10      (PIN_LED_IO_10)  – optional blink; used for clear confirm flash
//
// All LEDs are digital (on/off). RGB or PWM extensions can be added later.
class LedService {
public:
  LedService() = default;

  ServiceStatus begin() {
    pinMode(PIN_STATUS_LED, OUTPUT);
    pinMode(PIN_LED_IO_9, OUTPUT);
    pinMode(PIN_LED_IO_10, OUTPUT);
    setStatusLed(false);
    setLed9(false);
    setLed10(false);
    return ServiceStatus::Ok;
  }

  // --- Direct on/off controls ---
  void setStatusLed(bool on) { digitalWrite(PIN_STATUS_LED, on ? HIGH : LOW); }
  void setLed9(bool on)      { digitalWrite(PIN_LED_IO_9,   on ? HIGH : LOW); }
  void setLed10(bool on)     { digitalWrite(PIN_LED_IO_10,  on ? HIGH : LOW); }

  // --- Blink channels (pass 0 to stop) ---
  void setStatusLedBlinkMs(uint32_t ms) {
    statusBlinkMs_    = ms;
    statusBlinkStart_ = millis();
    statusOn_         = false;
  }

  void setLed9BlinkMs(uint32_t ms) {
    led9BlinkMs_    = ms;
    led9BlinkStart_ = millis();
    led9On_         = false;
  }

  void setLed10BlinkMs(uint32_t ms) {
    led10BlinkMs_    = ms;
    led10BlinkStart_ = millis();
    led10On_         = false;
  }

  // Tick all blink channels; call from loop().
  void update() {
    if (statusBlinkMs_ > 0) {
      if ((millis() - statusBlinkStart_) >= statusBlinkMs_) {
        statusBlinkStart_ = millis();
        statusOn_ = !statusOn_;
        setStatusLed(statusOn_);
      }
    }

    if (led9BlinkMs_ > 0) {
      if ((millis() - led9BlinkStart_) >= led9BlinkMs_) {
        led9BlinkStart_ = millis();
        led9On_ = !led9On_;
        setLed9(led9On_);
      }
    }

    if (led10BlinkMs_ > 0) {
      if ((millis() - led10BlinkStart_) >= led10BlinkMs_) {
        led10BlinkStart_ = millis();
        led10On_ = !led10On_;
        setLed10(led10On_);
      }
    }
  }

private:
  uint32_t statusBlinkMs_    = 0;
  uint32_t statusBlinkStart_ = 0;
  bool     statusOn_         = false;

  uint32_t led9BlinkMs_    = 0;
  uint32_t led9BlinkStart_ = 0;
  bool     led9On_         = false;

  uint32_t led10BlinkMs_    = 0;
  uint32_t led10BlinkStart_ = 0;
  bool     led10On_         = false;
};

} // namespace vfm
