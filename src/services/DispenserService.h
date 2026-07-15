#pragma once

#include <Arduino.h>
#include <AccelStepper.h>
#include "ServiceTypes.h"
#include "../hardware/VFMPins.h"

namespace vfm {

// ---------------------------------------------------------------------------
// Tunable defaults (override before begin() via setters)
// ---------------------------------------------------------------------------
// 28BYJ-48 half-step ≈ 4096 steps/rev. Raise ~700 is the current bench default.
constexpr float    kDefaultMotorSpeed      = 500.0f; // steps/s
constexpr long     kDefaultLowerSteps      = 2048;   // max seek-away / approach budget
constexpr long     kDefaultRaiseSteps      = 700;    // M2 up travel from PG2 home
constexpr long     kDefaultFeedMaxSteps    = 4096;   // M1 max steps before jam fault
constexpr uint32_t kDefaultLowerTimeoutMs  = 8000;   // M2 lower / seek-away
constexpr uint32_t kDefaultFeedTimeoutMs   = 30000;  // M1 pellet load (30 s)
constexpr uint32_t kDefaultRaiseTimeoutMs  = 8000;   // M2 raise
constexpr uint32_t kPGDebounceMs           = 20;

// ---------------------------------------------------------------------------
class DispenserService {
public:
    DispenserService();

    ServiceStatus begin();
    void update();

    // Start a dispense cycle from Idle or Presented (B2: new Dispense ends
    // the presented wait). Returns false if busy in motion / Fault.
    bool dispense();

    // Abort any phase (including Presented), de-energise, return to Idle.
    // Also clears sticky Fault.
    void abort();

    DispenseState state() const { return state_; }
    uint32_t      pelletCount() const { return pelletCount_; }
    DispenseEvent takeEvent();

    // Photogate logical state (true = triggered for that gate's role)
    // PG1/PG2: beam break = pin LOW. PG3: dome open = pin HIGH (idle LOW).
    bool pg1() const { return pg1State_; } // pellet in cup
    bool pg2() const { return pg2State_; } // actuator at home / down
    bool pg3() const { return pg3State_; } // dome open

    void setMotorSpeed(float stepsPerSec) {
        motorSpeed_ = stepsPerSec;
        // Keep maxSpeed above commanded speed for AccelStepper
        motor1_.setMaxSpeed(motorSpeed_ * 2.0f);
        motor2_.setMaxSpeed(motorSpeed_ * 2.0f);
    }
    void setLowerSteps(long steps)            { lowerSteps_ = steps; }
    void setRaiseSteps(long steps)            { raiseSteps_ = steps; }
    void setFeedMaxSteps(long steps)          { feedMaxSteps_ = steps; }
    void setLowerTimeoutMs(uint32_t ms)       { lowerTimeoutMs_ = ms; }
    void setFeedTimeoutMs(uint32_t ms)        { feedTimeoutMs_ = ms; }
    void setRaiseTimeoutMs(uint32_t ms)       { raiseTimeoutMs_ = ms; }

    // Deprecated alias: sets all three phase timeouts to the same value.
    void setMotionTimeoutMs(uint32_t ms) {
        lowerTimeoutMs_ = feedTimeoutMs_ = raiseTimeoutMs_ = ms;
    }

private:
    AccelStepper motor1_; // Pellet feeder (M1)
    AccelStepper motor2_; // Actuator / elevator (M2)

    // State machine
    DispenseState state_;
    DispenseEvent pendingEvent_;
    uint32_t      pelletCount_;

    uint32_t motionStartMs_;
    long     motor2Target_;
    bool     pg3WasOpen_;     // edge detect for AccessAttempt (B2)

    // Tuning
    float    motorSpeed_;
    long     lowerSteps_;
    long     raiseSteps_;
    long     feedMaxSteps_;
    uint32_t lowerTimeoutMs_;
    uint32_t feedTimeoutMs_;
    uint32_t raiseTimeoutMs_;

    bool     pg1State_, pg2State_, pg3State_;
    bool     pg1Raw_, pg2Raw_, pg3Raw_;
    uint32_t pg1LastChangeMs_, pg2LastChangeMs_, pg3LastChangeMs_;

    void updatePhotogates();
    void setState(DispenseState next);
    void setEvent(DispenseEvent ev);
    void haltMotors();              // setSpeed(0) + disableOutputs — never stop()
    void faultNow();
    bool phaseTimedOut(uint32_t timeoutMs) const;

    // M2 direction: forward (positive speed) = UP, reverse (negative) = DOWN
    void startSeekAwayFromPg2();  // up until PG2 clears
    void startApproachPg2();      // down until PG2 triggers
    void startRaise();
    void startFeed();
    void beginLoweringPhase();    // enter SeekingAway or Lowering from dispense()
};

} // namespace vfm
