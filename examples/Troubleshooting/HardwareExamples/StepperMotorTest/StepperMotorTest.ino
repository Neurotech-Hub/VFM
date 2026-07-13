// StepperMotorTest – hardware bring-up test for VFM 28BYJ-48 steppers.
//
// Motor 1 (M1) – Pellet feeder   : GPIO 35(Orange) 36(Yellow) 37(Pink) 38(Blue)
// Motor 2 (M2) – Actuator        : GPIO 40(Orange) 41(Yellow) 42(Pink) 43(Blue)
// Red wire on both motors → +5 V (common).
//
// Open Serial Monitor at 115200 baud.
// Each command jogs one motor a fixed step count at the current speed.
//
// IMPORTANT: uses setSpeed() + runSpeed() exclusively (no move()/run()).
// Mixing move() with setSpeed() corrupts AccelStepper's internal speed and
// causes the motor to lock – drawing current but not rotating.
//
// Commands:
//   1  – M1 forward  |  q  – M1 reverse
//   2  – M2 forward  |  w  – M2 reverse
//   x  – stop and de-energise both motors
//   +  – increase speed by 50 steps/s
//   -  – decrease speed by 50 steps/s
//   p  – print step counters and speed
//   h  – help

#include <VFM.h>
#include <AccelStepper.h>

using namespace vfm;

static constexpr long  kJogSteps    = 1024;
static constexpr float kDefaultSpeed = 400.0f; // 28BYJ-48 reliable range: 200–600 half-steps/s
static constexpr float kMinSpeed     = 100.0f;
static constexpr float kMaxSpeed     = 800.0f;

// AccelStepper HALF4WIRE arg order: (p1, p2, p3, p4)
//   Half-step sequence: p1 -> p1+p2 -> p2 -> p2+p3 -> p3 -> p3+p4 -> p4 -> p4+p1
// Mapping: p1=Orange, p2=Pink, p3=Yellow, p4=Blue  →  correct 28BYJ-48 sequence
AccelStepper motor1(AccelStepper::HALF4WIRE,
                    PIN_M1_A1, PIN_M1_A3, PIN_M1_A2, PIN_M1_A4);
AccelStepper motor2(AccelStepper::HALF4WIRE,
                    PIN_M2_A1, PIN_M2_A3, PIN_M2_A2, PIN_M2_A4);

float  speed     = kDefaultSpeed;
long   m1Steps   = 0;   // remaining half-steps for active jog
long   m2Steps   = 0;
long   m1Total   = 0;   // cumulative signed position (for display)
long   m2Total   = 0;

void deenergiseAll() {
    motor1.setSpeed(0);
    motor2.setSpeed(0);
    motor1.disableOutputs();
    motor2.disableOutputs();
    m1Steps = 0;
    m2Steps = 0;
}

void startJog(AccelStepper &motor, long &remaining, float jogSpeed) {
    motor.enableOutputs();
    motor.setCurrentPosition(0);
    motor.setSpeed(jogSpeed);
    remaining = abs((long)kJogSteps);
}

void runMotors() {
    if (m1Steps > 0) {
        if (motor1.runSpeed()) {
            m1Steps--;
            if (m1Steps == 0) motor1.disableOutputs();
        }
    }
    if (m2Steps > 0) {
        if (motor2.runSpeed()) {
            m2Steps--;
            if (m2Steps == 0) motor2.disableOutputs();
        }
    }
}

void printStatus() {
    Serial.print(F("[Motor] speed="));  Serial.print(speed, 0);
    Serial.print(F(" steps/s  jog="));  Serial.print(kJogSteps);
    Serial.print(F("  M1 total="));     Serial.print(m1Total);
    Serial.print(F("  M2 total="));     Serial.println(m2Total);
}

void printHelp() {
    Serial.println(F("Commands:"));
    Serial.println(F("  1 / q   M1 forward / reverse (feeder)"));
    Serial.println(F("  2 / w   M2 forward / reverse (actuator)"));
    Serial.println(F("  x       stop + de-energise both"));
    Serial.println(F("  + / -   speed ±50 steps/s"));
    Serial.println(F("  p       status    h  help"));
}

void setup() {
    Serial.begin(115200);
    while (!Serial && millis() < 3000) {}

    Serial.println(F("\n===== VFM StepperMotorTest ====="));
    Serial.println(F("M1 = feeder (GPIO35-38)  |  M2 = actuator (GPIO40-43)"));
    Serial.println(F("Red wire -> +5 V on both motors"));
    Serial.print(F("Default speed: ")); Serial.print(kDefaultSpeed, 0);
    Serial.println(F(" half-steps/s"));
    printHelp();

    // setMaxSpeed is required by AccelStepper even in constant-speed mode
    motor1.setMaxSpeed(kMaxSpeed);
    motor2.setMaxSpeed(kMaxSpeed);

    deenergiseAll();
}

void loop() {
    runMotors();

    if (!Serial.available()) return;

    char cmd = (char)Serial.read();
    switch (cmd) {
        case '1':
            startJog(motor1, m1Steps, speed);
            m1Total += kJogSteps;
            Serial.print(F("[M1] Forward ")); Serial.print(kJogSteps);
            Serial.print(F(" steps  total=")); Serial.println(m1Total);
            break;
        case 'q':
            startJog(motor1, m1Steps, -speed);
            m1Total -= kJogSteps;
            Serial.print(F("[M1] Reverse ")); Serial.print(kJogSteps);
            Serial.print(F(" steps  total=")); Serial.println(m1Total);
            break;
        case '2':
            startJog(motor2, m2Steps, speed);
            m2Total += kJogSteps;
            Serial.print(F("[M2] Forward ")); Serial.print(kJogSteps);
            Serial.print(F(" steps  total=")); Serial.println(m2Total);
            break;
        case 'w':
            startJog(motor2, m2Steps, -speed);
            m2Total -= kJogSteps;
            Serial.print(F("[M2] Reverse ")); Serial.print(kJogSteps);
            Serial.print(F(" steps  total=")); Serial.println(m2Total);
            break;
        case 'x':
            deenergiseAll();
            m1Total = 0;
            m2Total = 0;
            Serial.println(F("[Motor] Stopped, coils de-energised"));
            break;
        case '+':
            speed = min(kMaxSpeed, speed + 50.0f);
            printStatus();
            break;
        case '-':
            speed = max(kMinSpeed, speed - 50.0f);
            printStatus();
            break;
        case 'p':
            printStatus();
            break;
        case 'h':
            printHelp();
            break;
        default:
            break;
    }
}
