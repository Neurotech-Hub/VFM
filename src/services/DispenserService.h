#pragma once

#include <Arduino.h>
#include <AccelStepper.h>
#include "ServiceTypes.h"
#include "../hardware/VFMPins.h"

namespace vfm {

// ---------------------------------------------------------------------------
// Tunable constants (may be overridden before begin() via setters)
// ---------------------------------------------------------------------------

// Default motor speed (steps/sec) and max steps for each motion phase.
// 28BYJ-48 in half-step mode has 4096 steps/rev; the right number of steps
// per mechanical travel must be tuned on the bench and set via setters.
constexpr float    kDefaultMotorSpeed     = 500.0f; // steps/s
constexpr long     kDefaultLowerSteps     = 2048;   // actuator down travel
constexpr long     kDefaultRaiseSteps     = 2048;   // actuator up travel
constexpr long     kDefaultFeedMaxSteps   = 4096;   // max feed steps before fault
constexpr uint32_t kDefaultMotionTimeout  = 8000;   // ms – global motion watchdog
constexpr uint32_t kPGDebounceMs          = 20;     // photogate debounce

// ---------------------------------------------------------------------------
class DispenserService {
public:
    DispenserService();

    // --- Lifecycle ---
    ServiceStatus begin();

    // Non-blocking update; call every loop iteration.
    void update();

    // --- Commands ---
    // Start a dispense cycle from Idle. Returns false if not Idle.
    bool dispense();

    // Abort any in-progress motion, de-energise coils, return to Idle.
    void abort();

    // --- State / telemetry ---
    DispenseState state() const { return state_; }
    uint32_t      pelletCount() const { return pelletCount_; }

    // Returns the most recent DispenseEvent then clears it to None.
    DispenseEvent takeEvent();

    // Photogate raw reads (after debounce)
    bool pg1() const { return pg1State_; } // true = pellet in cup
    bool pg2() const { return pg2State_; } // true = actuator at home/down
    bool pg3() const { return pg3State_; } // true = dome opened

    // --- Tuning setters ---
    void setMotorSpeed(float stepsPerSec)   { motorSpeed_ = stepsPerSec; }
    void setLowerSteps(long steps)           { lowerSteps_ = steps; }
    void setRaiseSteps(long steps)           { raiseSteps_ = steps; }
    void setFeedMaxSteps(long steps)         { feedMaxSteps_ = steps; }
    void setMotionTimeoutMs(uint32_t ms)     { motionTimeoutMs_ = ms; }

private:
    // AccelStepper instances.
    // HALF4WIRE pin order: (pin1, pin2, pin3, pin4) = (A1, A3, A2, A4)
    // = (Orange, Pink, Yellow, Blue) — energise sequence for 28BYJ-48.
    AccelStepper motor1_; // Pellet feeder
    AccelStepper motor2_; // Actuator / elevator

    // State machine
    DispenseState state_;
    DispenseEvent pendingEvent_;
    uint32_t      pelletCount_;

    // Motion tracking
    uint32_t motionStartMs_; // millis() when current motion phase started
    long     motor2Target_;  // absolute step target for M2 raise/lower

    // Tuning
    float    motorSpeed_;
    long     lowerSteps_;
    long     raiseSteps_;
    long     feedMaxSteps_;
    uint32_t motionTimeoutMs_;

    // Photogate debounced state + debounce tracking
    bool     pg1State_;
    bool     pg2State_;
    bool     pg3State_;
    bool     pg1Raw_;
    bool     pg2Raw_;
    bool     pg3Raw_;
    uint32_t pg1LastChangeMs_;
    uint32_t pg2LastChangeMs_;
    uint32_t pg3LastChangeMs_;

    // Internal helpers
    void     updatePhotogates();
    void     setState(DispenseState next);
    void     setEvent(DispenseEvent ev);
    void     deenergiseAll();
    bool     motionTimedOut() const;
};

} // namespace vfm
