#include "DispenserService.h"

namespace vfm {

// ---------------------------------------------------------------------------
// AccelStepper HALF4WIRE: (A1, A3, A2, A4) = Orange, Pink, Yellow, Blue
// M2 direction: +speed = UP (forward), -speed = DOWN (reverse)
//
// CRITICAL: use setSpeed()+runSpeed() only. Never AccelStepper::stop()/move()/
// run() — stop() issues move() and corrupts constant-speed mode.
// ---------------------------------------------------------------------------
DispenserService::DispenserService()
    : motor1_(AccelStepper::HALF4WIRE, PIN_M1_A1, PIN_M1_A3, PIN_M1_A2, PIN_M1_A4),
      motor2_(AccelStepper::HALF4WIRE, PIN_M2_A1, PIN_M2_A3, PIN_M2_A2, PIN_M2_A4),
      state_(DispenseState::Idle),
      pendingEvent_(DispenseEvent::None),
      pelletCount_(0),
      lastFault_(ServiceStatus::Ok),
      motionStartMs_(0),
      motor2Target_(0),
      pg3WasOpen_(false),
      pg3BlankUntilMs_(0),
      pg1OnSinceMs_(0),
      raiseStartMs_(0),
      pg3OpenSinceMs_(0),
      domeWarnLatched_(false),
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
    pinMode(PIN_PG1, INPUT_PULLUP);
    pinMode(PIN_PG2, INPUT_PULLUP);
    pinMode(PIN_PG3, INPUT_PULLDOWN);

    motor1_.setMaxSpeed(motorSpeed_ * 2.0f);
    motor2_.setMaxSpeed(motorSpeed_ * 2.0f);
    haltMotors();

    uint32_t now = millis();
    pg1Raw_ = (digitalRead(PIN_PG1) == LOW);
    pg2Raw_ = (digitalRead(PIN_PG2) == LOW);
    pg3Raw_ = (digitalRead(PIN_PG3) == HIGH);
    pg1State_ = pg1Raw_;
    pg2State_ = pg2Raw_;
    pg3State_ = pg3Raw_;
    pg1LastChangeMs_ = pg2LastChangeMs_ = pg3LastChangeMs_ = now;
    pg3WasOpen_ = pg3State_;
    pg1OnSinceMs_ = pg1State_ ? now : 0;
    pg3OpenSinceMs_ = pg3State_ ? now : 0;
    domeWarnLatched_ = false;
    lastFault_ = ServiceStatus::Ok;

    return ServiceStatus::Ok;
}

void DispenserService::update() {
    updatePhotogates();

    // Jam / warning monitors run in all non-Fault states
    if (state_ != DispenseState::Fault) {
        checkPg1Jam();
        if (state_ == DispenseState::Fault) return;
        checkDomeOpenWarning();
    }

    switch (state_) {
        case DispenseState::Idle:
        case DispenseState::Fault:
            break;

        case DispenseState::SeekingAway:
            if (phaseTimedOut(lowerTimeoutMs_) ||
                (motor2_.currentPosition() >= lowerSteps_)) {
                faultNow(ServiceStatus::Timeout);
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
                faultNow(ServiceStatus::Timeout);
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
                faultNow(ServiceStatus::Timeout);
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
            // Jam: PG2 (home) must clear within 5 s of raise start
            if (pg2State_ &&
                (millis() - raiseStartMs_) >= kPg2ClearOnRaiseMs) {
                faultNow(ServiceStatus::Jam);
                break;
            }
            if (phaseTimedOut(raiseTimeoutMs_)) {
                faultNow(ServiceStatus::Timeout);
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
            // B2: rising edge of dome-open → AccessAttempt; stay Presented.
            if (pg3EventBlanked()) {
                pg3WasOpen_ = pg3State_;
                break;
            }
            if (pg3State_ && !pg3WasOpen_) {
                setEvent(DispenseEvent::AccessAttempt);
                blankPg3Events();
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
    lastFault_ = ServiceStatus::Ok;
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
    motor2_.enableOutputs();
    motor2_.setCurrentPosition(0);
    motionStartMs_ = millis();
    motor2_.setSpeed(motorSpeed_);
}

void DispenserService::startApproachPg2() {
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
    raiseStartMs_ = millis();
    motor2Target_ = raiseSteps_;
    motor2_.setSpeed(motorSpeed_); // UP
}

void DispenserService::updatePhotogates() {
    uint32_t now = millis();

    bool raw1 = (digitalRead(PIN_PG1) == LOW);
    if (raw1 != pg1Raw_) { pg1Raw_ = raw1; pg1LastChangeMs_ = now; }
    if ((now - pg1LastChangeMs_) >= kPGDebounceMs) {
        bool prev = pg1State_;
        pg1State_ = pg1Raw_;
        if (pg1State_ && !prev) {
            pg1OnSinceMs_ = now;
        } else if (!pg1State_) {
            pg1OnSinceMs_ = 0;
        }
    }

    bool raw2 = (digitalRead(PIN_PG2) == LOW);
    if (raw2 != pg2Raw_) { pg2Raw_ = raw2; pg2LastChangeMs_ = now; }
    if ((now - pg2LastChangeMs_) >= kPGDebounceMs) pg2State_ = pg2Raw_;

    bool raw3 = (digitalRead(PIN_PG3) == HIGH);
    if (raw3 != pg3Raw_) { pg3Raw_ = raw3; pg3LastChangeMs_ = now; }
    if ((now - pg3LastChangeMs_) >= kPGDebounceMs) {
        bool prev = pg3State_;
        pg3State_ = pg3Raw_;
        if (pg3State_ && !prev) {
            pg3OpenSinceMs_ = now;
            domeWarnLatched_ = false;
        } else if (!pg3State_) {
            pg3OpenSinceMs_ = 0;
            domeWarnLatched_ = false;
        }
    }
}

void DispenserService::checkPg1Jam() {
    // Drop detector should be a brief pulse; held >1 s ⇒ pellet jam
    if (!pg1State_ || pg1OnSinceMs_ == 0) return;
    if ((millis() - pg1OnSinceMs_) >= kPg1JamMs) {
        faultNow(ServiceStatus::Jam);
    }
}

void DispenserService::checkDomeOpenWarning() {
    if (!pg3State_ || pg3OpenSinceMs_ == 0 || domeWarnLatched_) return;
    if ((millis() - pg3OpenSinceMs_) < kDomeOpenWarnMs) return;
    // Wait until the event slot is free so we do not drop the warning
    if (pendingEvent_ != DispenseEvent::None) return;
    setEvent(DispenseEvent::DomeOpenWarning);
    domeWarnLatched_ = true;
}

void DispenserService::setState(DispenseState next) { state_ = next; }

void DispenserService::setEvent(DispenseEvent ev) { pendingEvent_ = ev; }

void DispenserService::haltMotors() {
    motor1_.setSpeed(0);
    motor2_.setSpeed(0);
    motor1_.disableOutputs();
    motor2_.disableOutputs();
}

void DispenserService::faultNow(ServiceStatus code) {
    haltMotors();
    lastFault_ = code;
    setEvent(DispenseEvent::Fault);
    setState(DispenseState::Fault);
}

bool DispenserService::phaseTimedOut(uint32_t timeoutMs) const {
    return (millis() - motionStartMs_) >= timeoutMs;
}

} // namespace vfm
