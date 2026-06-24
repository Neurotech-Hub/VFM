#pragma once

#include <Arduino.h>
#include "ServiceTypes.h"
#include "../hardware/VFMPins.h"

namespace vfm {

// Simple LED service for the status LED and the two user IO LEDs.
// All LEDs are digital (on/off). RGB or PWM extensions can be added later.
class LedService {
public:
    LedService() = default;

    ServiceStatus begin() {
        pinMode(PIN_STATUS_LED, OUTPUT);
        pinMode(PIN_LED_IO_9,   OUTPUT);
        pinMode(PIN_LED_IO_10,  OUTPUT);
        setStatus(false);
        setLed9(false);
        setLed10(false);
        return ServiceStatus::Ok;
    }

    void setStatus(bool on) { digitalWrite(PIN_STATUS_LED, on ? HIGH : LOW); }
    void setLed9(bool on)   { digitalWrite(PIN_LED_IO_9,   on ? HIGH : LOW); }
    void setLed10(bool on)  { digitalWrite(PIN_LED_IO_10,  on ? HIGH : LOW); }

    // Blink status LED: call from loop at any rate; actually toggles once
    // per blinkIntervalMs. Pass 0 to stop blinking.
    void setBlinkIntervalMs(uint32_t ms) { blinkMs_ = ms; blinkStart_ = millis(); }

    void update() {
        if (blinkMs_ == 0) return;
        if ((millis() - blinkStart_) >= blinkMs_) {
            blinkStart_ = millis();
            statusOn_ = !statusOn_;
            setStatus(statusOn_);
        }
    }

private:
    uint32_t blinkMs_    = 0;
    uint32_t blinkStart_ = 0;
    bool     statusOn_   = false;
};

} // namespace vfm
