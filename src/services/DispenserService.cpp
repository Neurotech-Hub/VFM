#include "DispenserService.h"

namespace vfm {

// ---------------------------------------------------------------------------
// AccelStepper HALF4WIRE: (A1, A3, A2, A4) = Orange, Pink, Yellow, Blue
// M2 direction convention: +speed = UP (forward), -speed = DOWN (reverse)
// ---------------------------------------------------------------------------
DispenserService::DispenserService()
    : motor1_(AccelStepper::HALF4WIRE, PIN_M1_A1, PIN_M1_A3, PIN_M1_A2, PIN_M1_A4),
      motor2_(AccelStepper::HALF4WIRE, PIN_M2_A1, PIN_M2_A3, PIN_M2_A2, PIN_M2_A4),
      state_(DispenseState::Idle),
      pendingEvent_(DispenseEvent::None),
      pelletCount_(0),
      motionStartMs_(0),
      motor2Target_(0),
      pg3WasOpen_(false),
      motorSpeed_(kDefaultMotorSpeed),
      lowerSteps_(kDefaultLowerSteps),
      raiseSteps_(kDefaultRaiseSteps),
      feedMaxSteps_(kDefaultFeedMaxSteps),
      lowerTimeoutMs_(kDefaultLowerTimeoutMs),
      feedTimeoutMs_(kDefaultFeedTimeoutMs),
      raiseTimeoutMs_(kDefaultRaiseTimeoutMs),
      pg1State_(false), pg2State_(false), pg3State_(false),
      pg1Raw_(false),   pg2Raw_(false),   pg3Raw_(false),
      pg1LastChangeMs_(0), pg2LastChangeMs_(0), pg3LastChangeMs_(0)
{}

ServiceStatus DispenserService::begin() {
    // PG1/PG2: beam break = LOW = triggered (INPUT_PULLUP)
    // PG3: idle LOW, dome open = HIGH (INPUT_PULLDOWN)
    pinMode(PIN_PG1, INPUT_PULLUP);
    pinMode(PIN_PG2, INPUT_PULLUP);
    pinMode(PIN_PG3, INPUT_PULLDOWN);

    // Max must be >= any |setSpeed| we use
    motor1_.setMaxSpeed(motorSpeed_ * 2.0f);
    motor2_.setMaxSpeed(motorSpeed_ * 2.0f);
    haltMotors();

    uint32_t now = millis();
    pg1Raw_ = (digitalRead(PIN_PG1) == LOW);   // break = trigger
    pg2Raw_ = (digitalRead(PIN_PG2) == LOW);
    pg3Raw_ = (digitalRead(PIN_PG3) == HIGH);  // open = trigger
    pg1State_ = pg1Raw_;
    pg2State_ = pg2Raw_;
    pg3State_ = pg3Raw_;
    pg1LastChangeMs_ = pg2LastChangeMs_ = pg3LastChangeMs_ = now;
    pg3WasOpen_ = pg3State_;

    return ServiceStatus::Ok;
}

void DispenserService::update() {
    updatePhotogates();

    switch (state_) {
        case DispenseState::Idle:
        case DispenseState::Fault:
            break;

        case DispenseState::SeekingAway:
            if (phaseTimedOut(lowerTimeoutMs_) ||
                (motor2_.currentPosition() >= lowerSteps_)) {
                faultNow();
                break;
            }
            if (!pg2State_) {
                startApproachPg2();
                setState(DispenseState::Lowering);
            } else {
                motor2_.runSpeed();
            }
            break;

        case DispenseState::Lowering:
            if (phaseTimedOut(lowerTimeoutMs_) ||
                (labs(motor2_.currentPosition()) >= lowerSteps_)) {
                faultNow();
                break;
            }
            if (pg2State_) {
                haltMotors();
                startFeed();
                setState(DispenseState::Feeding);
            } else {
                motor2_.runSpeed();
            }
            break;

        case DispenseState::Feeding:
            if (phaseTimedOut(feedTimeoutMs_) ||
                (labs(motor1_.currentPosition()) >= feedMaxSteps_)) {
                faultNow();
                break;
            }
            if (pg1State_) {
                haltMotors();
                setEvent(DispenseEvent::PelletLoaded);
                startRaise();
                setState(DispenseState::Raising);
            } else {
                motor1_.runSpeed();
            }
            break;

        case DispenseState::Raising:
            if (phaseTimedOut(raiseTimeoutMs_)) {
                faultNow();
                break;
            }
            if (motor2_.currentPosition() >= motor2Target_) {
                haltMotors();
                setEvent(DispenseEvent::PelletPresented);
                pelletCount_++;
                pg3WasOpen_ = pg3State_;
                setState(DispenseState::Presented);
            } else {
                motor2_.runSpeed();
            }
            break;

        case DispenseState::Presented:
            if (pg3State_ && !pg3WasOpen_) {
                setEvent(DispenseEvent::AccessAttempt);
            }
            pg3WasOpen_ = pg3State_;
            break;
    }
}

bool DispenserService::dispense() {
    if (state_ != DispenseState::Idle && state_ != DispenseState::Presented) {
        return false;
    }

    haltMotors();
    beginLoweringPhase();
    return true;
}

void DispenserService::abort() {
    haltMotors();
    setState(DispenseState::Idle);
}

DispenseEvent DispenserService::takeEvent() {
    DispenseEvent ev = pendingEvent_;
    pendingEvent_ = DispenseEvent::None;
    return ev;
}

// ---------------------------------------------------------------------------
void DispenserService::beginLoweringPhase() {
    motionStartMs_ = millis();

    if (pg2State_) {
        startSeekAwayFromPg2();
        setState(DispenseState::SeekingAway);
    } else {
        startApproachPg2();
        setState(DispenseState::Lowering);
    }
}

void DispenserService::startSeekAwayFromPg2() {
    // UP until PG2 clears
    motor2_.enableOutputs();
    motor2_.setCurrentPosition(0);
    motionStartMs_ = millis();
    motor2_.setSpeed(motorSpeed_);
}

void DispenserService::startApproachPg2() {
    // DOWN until PG2 triggers
    motor2_.enableOutputs();
    motor2_.setCurrentPosition(0);
    motionStartMs_ = millis();
    motor2_.setSpeed(-motorSpeed_);
}

void DispenserService::startFeed() {
    motor1_.enableOutputs();
    motor1_.setCurrentPosition(0);
    motionStartMs_ = millis();
    motor1_.setSpeed(motorSpeed_);
}

void DispenserService::startRaise() {
    motor2_.enableOutputs();
    motor2_.setCurrentPosition(0);
    motionStartMs_ = millis();
    motor2Target_ = raiseSteps_;
    motor2_.setSpeed(motorSpeed_); // UP
}

void DispenserService::updatePhotogates() {
    uint32_t now = millis();

    // PG1/PG2: triggered on beam break (pin LOW)
    bool raw1 = (digitalRead(PIN_PG1) == LOW);
    if (raw1 != pg1Raw_) { pg1Raw_ = raw1; pg1LastChangeMs_ = now; }
    if ((now - pg1LastChangeMs_) >= kPGDebounceMs) pg1State_ = pg1Raw_;

    bool raw2 = (digitalRead(PIN_PG2) == LOW);
    if (raw2 != pg2Raw_) { pg2Raw_ = raw2; pg2LastChangeMs_ = now; }
    if ((now - pg2LastChangeMs_) >= kPGDebounceMs) pg2State_ = pg2Raw_;

    // PG3: idle LOW, dome open = HIGH
    bool raw3 = (digitalRead(PIN_PG3) == HIGH);
    if (raw3 != pg3Raw_) { pg3Raw_ = raw3; pg3LastChangeMs_ = now; }
    if ((now - pg3LastChangeMs_) >= kPGDebounceMs) pg3State_ = pg3Raw_;
}

void DispenserService::setState(DispenseState next) { state_ = next; }

void DispenserService::setEvent(DispenseEvent ev) { pendingEvent_ = ev; }

void DispenserService::haltMotors() {
    // Do NOT call AccelStepper::stop() — it uses move() and breaks runSpeed().
    motor1_.setSpeed(0);
    motor2_.setSpeed(0);
    motor1_.disableOutputs();
    motor2_.disableOutputs();
}

void DispenserService::faultNow() {
    haltMotors();
    setEvent(DispenseEvent::Fault);
    setState(DispenseState::Fault);
}

bool DispenserService::phaseTimedOut(uint32_t timeoutMs) const {
    return (millis() - motionStartMs_) >= timeoutMs;
}

} // namespace vfm
