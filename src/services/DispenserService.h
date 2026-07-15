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
constexpr long     kDefaultFeedMaxSteps    = 4096;   // M1 max steps before timeout
constexpr uint32_t kDefaultLowerTimeoutMs  = 8000;   // M2 lower / seek-away
constexpr uint32_t kDefaultFeedTimeoutMs   = 30000;  // M1 pellet load (30 s)
constexpr uint32_t kDefaultRaiseTimeoutMs  = 8000;   // M2 raise (step target)
constexpr uint32_t kPGDebounceMs           = 20;
// After a PG3 trigger edge, suppress further PG3 event-log edges for this long.
constexpr uint32_t kPg3EventBlankMs        = 3000;
// Jam / warning timers
constexpr uint32_t kPg1JamMs               = 1000;   // PG1 held → Jam
constexpr uint32_t kPg2ClearOnRaiseMs      = 5000;   // PG2 must clear after raise start
constexpr uint32_t kDomeOpenWarnMs         = 30000;  // PG3 open → DomeOpenWarning

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

    // Sticky fault code while in Fault state (Timeout / Jam); Ok otherwise.
    ServiceStatus faultCode() const { return lastFault_; }

    // Photogate logical state (true = triggered for that gate's role)
    // PG1/PG2: beam break = pin LOW. PG3: dome open = pin HIGH (idle LOW).
    bool pg1() const { return pg1State_; } // pellet in cup / drop detect
    bool pg2() const { return pg2State_; } // actuator at home / down
    bool pg3() const { return pg3State_; } // dome open

    // True while PG3 event edges are suppressed (AccessAttempt / InputChanged).
    // Blank starts after a completed high→low cycle. Edge latches are frozen
    // during blank so a high that begins late in the window still fires once after.
    bool pg3EventBlanked() const {
        return pg3BlankUntilMs_ != 0 && (int32_t)(millis() - pg3BlankUntilMs_) < 0;
    }

    void blankPg3Events() {
        pg3BlankUntilMs_ = millis() + kPg3EventBlankMs;
    }

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
    ServiceStatus lastFault_;

    uint32_t motionStartMs_;
    long     motor2Target_;
    bool     pg3WasOpen_;       // edge detect for AccessAttempt / blanking
    uint32_t pg3BlankUntilMs_;  // millis() deadline; 0 = not blanking

    // Jam / warning timers
    uint32_t pg1OnSinceMs_;     // when PG1 became true; 0 = currently clear
    uint32_t raiseStartMs_;     // when Raising began (for PG2 clear check)
    uint32_t pg3OpenSinceMs_;   // when PG3 became true; 0 = currently closed
    bool     domeWarnLatched_;  // one-shot DomeOpenWarning per open bout

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
    void checkPg1Jam();
    void checkDomeOpenWarning();
    void setState(DispenseState next);
    void setEvent(DispenseEvent ev);
    void haltMotors();              // setSpeed(0) + disableOutputs — never stop()
    void faultNow(ServiceStatus code);
    bool phaseTimedOut(uint32_t timeoutMs) const;

    // M2 direction: forward (positive speed) = UP, reverse (negative) = DOWN
    void startSeekAwayFromPg2();  // up until PG2 clears
    void startApproachPg2();      // down until PG2 triggers
    void startRaise();
    void startFeed();
    void beginLoweringPhase();    // enter SeekingAway or Lowering from dispense()
};

} // namespace vfm
