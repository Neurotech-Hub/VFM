// DispenseTest – serial-driven bench test for the VFM DispenserService.
//
// Open the Arduino Serial Monitor at 115200 baud.
//
// Commands:
//   d          – start a dispense cycle (also ends Presented wait and starts next)
//   a          – abort current motion / clear Fault / leave Presented
//   s          – print current dispenser state + photogate readings
//   +          – increase motor speed by 100 steps/s
//   -          – decrease motor speed by 100 steps/s
//   r          – print current raiseSteps
//   r <n>      – set raiseSteps (e.g. "r 700" or "r 1200")
//
// Defaults match library: raise=700, feed timeout=30 s.

#include <VFM.h>

static constexpr float    kMotorSpeed    = 500.0f;
static constexpr long     kLowerSteps    = 2048;
static constexpr long     kRaiseSteps    = 700;
static constexpr long     kFeedMaxSteps  = 4096;
static constexpr uint32_t kFeedTimeoutMs = 30000;

vfm::DispenserService dispenser;
float currentSpeed     = kMotorSpeed;
long  currentRaiseSteps = kRaiseSteps;

static char     lineBuf[48];
static uint8_t  lineIdx = 0;

static const char *stateStr(vfm::DispenseState s) {
    switch (s) {
        case vfm::DispenseState::Idle:        return "Idle";
        case vfm::DispenseState::SeekingAway: return "SeekingAway";
        case vfm::DispenseState::Lowering:    return "Lowering";
        case vfm::DispenseState::Feeding:     return "Feeding";
        case vfm::DispenseState::Raising:     return "Raising";
        case vfm::DispenseState::Presented:   return "Presented";
        case vfm::DispenseState::Fault:       return "Fault";
    }
    return "?";
}

void printStatus() {
    Serial.print(F("[State] "));
    Serial.print(stateStr(dispenser.state()));
    Serial.print(F("  PG1="));   Serial.print(dispenser.pg1());
    Serial.print(F(" PG2="));    Serial.print(dispenser.pg2());
    Serial.print(F(" PG3="));    Serial.print(dispenser.pg3());
    Serial.print(F("  Pellets=")); Serial.print(dispenser.pelletCount());
    Serial.print(F("  raiseSteps=")); Serial.print(currentRaiseSteps);
    Serial.print(F("  speed=")); Serial.println(currentSpeed);
}

void handleLine(const char *line) {
    if (line[0] == '\0') return;

    // "r" or "r <n>" — set / query raiseSteps
    if (line[0] == 'r' || line[0] == 'R') {
        if (line[1] == '\0') {
            Serial.print(F("raiseSteps = "));
            Serial.println(currentRaiseSteps);
            return;
        }
        // Skip optional whitespace after 'r'
        const char *p = line + 1;
        while (*p == ' ' || *p == '\t') p++;
        if (*p == '\0') {
            Serial.print(F("raiseSteps = "));
            Serial.println(currentRaiseSteps);
            return;
        }
        long steps = atol(p);
        if (steps < 1) {
            Serial.println(F("raiseSteps must be >= 1"));
            return;
        }
        currentRaiseSteps = steps;
        dispenser.setRaiseSteps(currentRaiseSteps);
        Serial.print(F("raiseSteps set to "));
        Serial.println(currentRaiseSteps);
        return;
    }

    // Single-char commands (first character of the line)
    switch (line[0]) {
        case 'd':
        case 'D':
            if (dispenser.dispense()) {
                Serial.println(F("Dispense started."));
            } else {
                Serial.print(F("Cannot dispense – state: "));
                Serial.println(stateStr(dispenser.state()));
            }
            break;
        case 'a':
        case 'A':
            dispenser.abort();
            Serial.println(F("Aborted."));
            break;
        case 's':
        case 'S':
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
        case 'h':
        case 'H':
            Serial.println(F("Commands: d a s + -  |  r  |  r <n>"));
            break;
        default:
            Serial.print(F("Unknown: "));
            Serial.println(line);
            break;
    }
}

void setup() {
    Serial.begin(115200);
    while (!Serial) {}
    Serial.println(F("VFM DispenseTest"));
    Serial.println(F("Commands: d=dispense  a=abort  s=status  +=faster  -=slower"));
    Serial.println(F("          r         = show raiseSteps"));
    Serial.println(F("          r <n>     = set raiseSteps (e.g. r 700)"));
    Serial.println(F("PG3 AccessAttempt keeps Presented until Abort/Dispense"));

    dispenser.setMotorSpeed(kMotorSpeed);
    dispenser.setLowerSteps(kLowerSteps);
    dispenser.setRaiseSteps(kRaiseSteps);
    dispenser.setFeedMaxSteps(kFeedMaxSteps);
    dispenser.setFeedTimeoutMs(kFeedTimeoutMs);

    if (dispenser.begin() != vfm::ServiceStatus::Ok) {
        Serial.println(F("ERROR: dispenser.begin() failed"));
    } else {
        Serial.println(F("Dispenser ready."));
        Serial.print(F("raiseSteps = ")); Serial.println(currentRaiseSteps);
    }
}

void loop() {
    dispenser.update();

    switch (dispenser.takeEvent()) {
        case vfm::DispenseEvent::PelletLoaded:
            Serial.println(F("[Event] PelletLoaded"));
            break;
        case vfm::DispenseEvent::PelletPresented:
            Serial.print(F("[Event] PelletPresented  total="));
            Serial.println(dispenser.pelletCount());
            break;
        case vfm::DispenseEvent::AccessAttempt:
            Serial.println(F("[Event] AccessAttempt (still Presented)"));
            break;
        case vfm::DispenseEvent::DomeOpenWarning:
            Serial.println(F("[Event] DomeOpenWarning (>30s open)"));
            break;
        case vfm::DispenseEvent::Fault:
            Serial.print(F("[Event] FAULT – "));
            Serial.println(
                dispenser.faultCode() == vfm::ServiceStatus::Timeout ? F("Timeout") :
                dispenser.faultCode() == vfm::ServiceStatus::Jam     ? F("Jam") : F("?"));
            break;
        default:
            break;
    }

    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\r') continue;
        if (c == '\n') {
            lineBuf[lineIdx] = '\0';
            if (lineIdx > 0) handleLine(lineBuf);
            lineIdx = 0;
        } else if (lineIdx < sizeof(lineBuf) - 1) {
            lineBuf[lineIdx++] = c;
        }
    }
}
