#include "DispenserService.h"

namespace vfm {

// ---------------------------------------------------------------------------
// Constructor
// AccelStepper HALF4WIRE pin order drives the 28BYJ-48 coils in the sequence:
//   Orange(A1) -> Pink(A3) -> Yellow(A2) -> Blue(A4)
// The AccelStepper(HALF4WIRE, p1, p2, p3, p4) constructor maps phases as:
//   Phase 0: p1        Phase 1: p1+p2    Phase 2: p2      Phase 3: p2+p3
//   Phase 4: p3        Phase 5: p3+p4    Phase 6: p4      Phase 7: p4+p1
// So passing (A1, A3, A2, A4) = (Orange, Pink, Yellow, Blue) gives the
// correct energise sequence. Verify direction on bench; negate setSpeed if
// the motor turns the wrong way.
// ---------------------------------------------------------------------------
DispenserService::DispenserService()
    : motor1_(AccelStepper::HALF4WIRE, PIN_M1_A1, PIN_M1_A3, PIN_M1_A2, PIN_M1_A4),
      motor2_(AccelStepper::HALF4WIRE, PIN_M2_A1, PIN_M2_A3, PIN_M2_A2, PIN_M2_A4),
      state_(DispenseState::Idle),
      pendingEvent_(DispenseEvent::None),
      pelletCount_(0),
      motionStartMs_(0),
      motor2Target_(0),
      motorSpeed_(kDefaultMotorSpeed),
      lowerSteps_(kDefaultLowerSteps),
      raiseSteps_(kDefaultRaiseSteps),
      feedMaxSteps_(kDefaultFeedMaxSteps),
      motionTimeoutMs_(kDefaultMotionTimeout),
      pg1State_(false), pg2State_(false), pg3State_(false),
      pg1Raw_(false),   pg2Raw_(false),   pg3Raw_(false),
      pg1LastChangeMs_(0), pg2LastChangeMs_(0), pg3LastChangeMs_(0)
{}

// ---------------------------------------------------------------------------
ServiceStatus DispenserService::begin() {
    // Photogate inputs
    // PG1/PG2 active LOW (normally HIGH): use internal pull-up
    // PG3 active HIGH (normally LOW): use internal pull-down
    pinMode(PIN_PG1, INPUT_PULLUP);
    pinMode(PIN_PG2, INPUT_PULLUP);
    pinMode(PIN_PG3, INPUT_PULLDOWN);

    // Motor max speed (AccelStepper requires a positive max speed)
    motor1_.setMaxSpeed(motorSpeed_);
    motor2_.setMaxSpeed(motorSpeed_);

    // De-energise coils – AccelStepper keeps coils energised by default
    deenergiseAll();

    // Initialise debounce state from actual pin readings
    uint32_t now = millis();
    pg1Raw_ = !digitalRead(PIN_PG1); // active LOW inverted to logic-level
    pg2Raw_ = !digitalRead(PIN_PG2);
    pg3Raw_ =  digitalRead(PIN_PG3); // active HIGH
    pg1State_ = pg1Raw_;
    pg2State_ = pg2Raw_;
    pg3State_ = pg3Raw_;
    pg1LastChangeMs_ = pg2LastChangeMs_ = pg3LastChangeMs_ = now;

    return ServiceStatus::Ok;
}

// ---------------------------------------------------------------------------
void DispenserService::update() {
    updatePhotogates();

    switch (state_) {
        case DispenseState::Idle:
        case DispenseState::Fault:
            // Nothing to run; coils are already de-energised
            break;

        // ----- Phase 1: Lower actuator until PG2 fires (active LOW) ------
        case DispenseState::Lowering:
            if (motionTimedOut()) {
                setEvent(DispenseEvent::Fault);
                setState(DispenseState::Fault);
                break;
            }
            if (pg2State_) {
                // PG2 triggered – actuator is at home/down position
                motor2_.stop();
                deenergiseAll();
                setState(DispenseState::Feeding);
                motionStartMs_ = millis();
                motor1_.setCurrentPosition(0);
                motor1_.setSpeed(motorSpeed_);
            } else {
                // Not yet at home – keep running M2 downward
                motor2_.runSpeed();
            }
            break;

        // ----- Phase 2: Feed pellet until PG1 fires (active LOW) ----------
        case DispenseState::Feeding:
            if (motionTimedOut() ||
                (motor1_.currentPosition() >= feedMaxSteps_)) {
                setEvent(DispenseEvent::Fault);
                setState(DispenseState::Fault);
                break;
            }
            if (pg1State_) {
                // PG1 triggered – pellet is seated in actuator cup
                motor1_.stop();
                deenergiseAll();
                setEvent(DispenseEvent::PelletLoaded);
                setState(DispenseState::Raising);
                motionStartMs_ = millis();
                motor2_.setCurrentPosition(0);
                motor2Target_ = raiseSteps_;    // stored as positive count
                motor2_.setSpeed(-motorSpeed_); // negative = upward direction
            } else {
                motor1_.runSpeed();
            }
            break;

        // ----- Phase 3: Raise actuator (step count) ------------------------
        // Motor runs at -speed (upward direction); position decrements from 0.
        // Raise is complete when currentPosition <= -raiseSteps_.
        case DispenseState::Raising:
            if (motionTimedOut()) {
                setEvent(DispenseEvent::Fault);
                setState(DispenseState::Fault);
                break;
            }
            if (motor2_.currentPosition() <= -motor2Target_) {
                motor2_.stop();
                deenergiseAll();
                setEvent(DispenseEvent::PelletPresented);
                pelletCount_++;
                setState(DispenseState::Presented);
            } else {
                motor2_.runSpeed();
            }
            break;

        // ----- Phase 4: Presented – waiting for mouse to take pellet ------
        case DispenseState::Presented:
            if (pg3State_) {
                setState(DispenseState::Taken);
            }
            break;

        // ----- Phase 5: Taken – brief acknowledgement, back to Idle -------
        case DispenseState::Taken:
            setEvent(DispenseEvent::PelletTaken);
            setState(DispenseState::Idle);
            break;
    }
}

// ---------------------------------------------------------------------------
bool DispenserService::dispense() {
    if (state_ != DispenseState::Idle) return false;

    // Start by lowering the actuator to home position
    motor2_.setCurrentPosition(0);
    motor2_.setSpeed(motorSpeed_); // positive = downward direction

    motionStartMs_ = millis();
    setState(DispenseState::Lowering);
    return true;
}

// ---------------------------------------------------------------------------
void DispenserService::abort() {
    motor1_.stop();
    motor2_.stop();
    deenergiseAll();
    setState(DispenseState::Idle);
}

// ---------------------------------------------------------------------------
DispenseEvent DispenserService::takeEvent() {
    DispenseEvent ev = pendingEvent_;
    pendingEvent_ = DispenseEvent::None;
    return ev;
}

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

void DispenserService::updatePhotogates() {
    uint32_t now = millis();

    // PG1 – active LOW sensor: HIGH pin = no pellet, LOW pin = pellet present
    bool raw1 = !digitalRead(PIN_PG1);
    if (raw1 != pg1Raw_) { pg1Raw_ = raw1; pg1LastChangeMs_ = now; }
    if ((now - pg1LastChangeMs_) >= kPGDebounceMs) pg1State_ = pg1Raw_;

    // PG2 – active LOW sensor
    bool raw2 = !digitalRead(PIN_PG2);
    if (raw2 != pg2Raw_) { pg2Raw_ = raw2; pg2LastChangeMs_ = now; }
    if ((now - pg2LastChangeMs_) >= kPGDebounceMs) pg2State_ = pg2Raw_;

    // PG3 – active HIGH sensor
    bool raw3 = digitalRead(PIN_PG3);
    if (raw3 != pg3Raw_) { pg3Raw_ = raw3; pg3LastChangeMs_ = now; }
    if ((now - pg3LastChangeMs_) >= kPGDebounceMs) pg3State_ = pg3Raw_;
}

void DispenserService::setState(DispenseState next) {
    state_ = next;
}

void DispenserService::setEvent(DispenseEvent ev) {
    pendingEvent_ = ev;
}

void DispenserService::deenergiseAll() {
    // AccelStepper disableOutputs() sets all coil pins LOW
    motor1_.disableOutputs();
    motor2_.disableOutputs();
}

bool DispenserService::motionTimedOut() const {
    return (millis() - motionStartMs_) >= motionTimeoutMs_;
}

} // namespace vfm
