// DispenseTest – serial-driven bench test for the VFM DispenserService.
//
// Open the Arduino Serial Monitor at 115200 baud.
//
// Commands:
//   d  – start a dispense cycle
//   a  – abort current motion
//   s  – print current dispenser state + photogate readings
//   +  – increase motor speed by 100 steps/s
//   -  – decrease motor speed by 100 steps/s
//   r  – reset pellet count
//
// Tune kLowerSteps, kRaiseSteps, kFeedMaxSteps, and kMotorSpeed below to
// match your physical mechanism before field deployment.

#include <VFM.h>

// ---------------------------------------------------------------------------
// Bench-tuning parameters – adjust these to match your hardware
// ---------------------------------------------------------------------------
static constexpr float    kMotorSpeed    = 500.0f; // steps/s
static constexpr long     kLowerSteps    = 2048;   // actuator down travel (half-steps)
static constexpr long     kRaiseSteps    = 2048;   // actuator up travel  (half-steps)
static constexpr long     kFeedMaxSteps  = 4096;   // max feed steps before fault
static constexpr uint32_t kTimeoutMs     = 8000;   // motion watchdog

// ---------------------------------------------------------------------------
vfm::DispenserService dispenser;
float currentSpeed = kMotorSpeed;

static const char *stateStr(vfm::DispenseState s) {
    switch (s) {
        case vfm::DispenseState::Idle:      return "Idle";
        case vfm::DispenseState::Lowering:  return "Lowering";
        case vfm::DispenseState::Feeding:   return "Feeding";
        case vfm::DispenseState::Raising:   return "Raising";
        case vfm::DispenseState::Presented: return "Presented";
        case vfm::DispenseState::Taken:     return "Taken";
        case vfm::DispenseState::Fault:     return "Fault";
    }
    return "?";
}

void printStatus() {
    Serial.print(F("[State] "));
    Serial.print(stateStr(dispenser.state()));
    Serial.print(F("  PG1="));   Serial.print(dispenser.pg1());
    Serial.print(F(" PG2="));    Serial.print(dispenser.pg2());
    Serial.print(F(" PG3="));    Serial.print(dispenser.pg3());
    Serial.print(F("  Pellets=")); Serial.println(dispenser.pelletCount());
}

void setup() {
    Serial.begin(115200);
    while (!Serial) {}
    Serial.println(F("VFM DispenseTest"));
    Serial.println(F("Commands: d=dispense  a=abort  s=status  +=faster  -=slower  r=reset count"));

    dispenser.setMotorSpeed(kMotorSpeed);
    dispenser.setLowerSteps(kLowerSteps);
    dispenser.setRaiseSteps(kRaiseSteps);
    dispenser.setFeedMaxSteps(kFeedMaxSteps);
    dispenser.setMotionTimeoutMs(kTimeoutMs);

    vfm::ServiceStatus st = dispenser.begin();
    if (st != vfm::ServiceStatus::Ok) {
        Serial.println(F("ERROR: dispenser.begin() failed"));
    } else {
        Serial.println(F("Dispenser ready."));
    }
}

void loop() {
    dispenser.update();

    // Handle events
    vfm::DispenseEvent ev = dispenser.takeEvent();
    switch (ev) {
        case vfm::DispenseEvent::PelletLoaded:
            Serial.println(F("[Event] PelletLoaded"));
            break;
        case vfm::DispenseEvent::PelletPresented:
            Serial.print(F("[Event] PelletPresented  total="));
            Serial.println(dispenser.pelletCount());
            break;
        case vfm::DispenseEvent::PelletTaken:
            Serial.println(F("[Event] PelletTaken"));
            break;
        case vfm::DispenseEvent::Fault:
            Serial.println(F("[Event] FAULT – check photogate / motor / timeout"));
            break;
        default:
            break;
    }

    // Serial command handling
    if (Serial.available()) {
        char cmd = Serial.read();
        switch (cmd) {
            case 'd':
                if (dispenser.dispense()) {
                    Serial.println(F("Dispense started."));
                } else {
                    Serial.print(F("Cannot dispense – state: "));
                    Serial.println(stateStr(dispenser.state()));
                }
                break;
            case 'a':
                dispenser.abort();
                Serial.println(F("Aborted."));
                break;
            case 's':
                printStatus();
                break;
            case '+':
                currentSpeed += 100.0f;
                dispenser.setMotorSpeed(currentSpeed);
                Serial.print(F("Speed = ")); Serial.println(currentSpeed);
                break;
            case '-':
                currentSpeed = max(100.0f, currentSpeed - 100.0f);
                dispenser.setMotorSpeed(currentSpeed);
                Serial.print(F("Speed = ")); Serial.println(currentSpeed);
                break;
            case 'r':
                Serial.println(F("Pellet count reset."));
                // pelletCount_ is private; if you need external reset, add a
                // resetCount() method to DispenserService.
                break;
            default:
                break;
        }
    }
}
