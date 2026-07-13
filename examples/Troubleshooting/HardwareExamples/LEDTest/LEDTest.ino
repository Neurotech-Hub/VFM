// LEDTest – hardware bring-up test for VFM module LEDs.
//
// Pins:
//   GPIO39 – Status LED
//   GPIO 9 – LED_IO_09
//   GPIO10 – LED_IO_10
//
// Open Serial Monitor at 115200 baud.
// On boot, LEDs cycle one at a time (500 ms each) so you can verify wiring.
//
// Commands:
//   a  – all LEDs on
//   o  – all LEDs off
//   1  – status LED only
//   2  – IO9 LED only
//   3  – IO10 LED only
//   r  – resume auto cycle

#include <VFM.h>

static constexpr uint32_t kStepMs = 500;

vfm::LedService leds;

enum class Mode { Auto, Manual };
Mode mode = Mode::Auto;

uint8_t autoStep = 0;
uint32_t lastStepMs = 0;

void allOff() {
    leds.setStatus(false);
    leds.setIoLed(false);
    leds.setLed10(false);
}

void showStep(uint8_t step) {
    allOff();
    switch (step % 3) {
        case 0:
            leds.setStatus(true);
            Serial.println(F("[LED] Status (GPIO39)"));
            break;
        case 1:
            leds.setIoLed(true);
            Serial.println(F("[LED] IO9 (GPIO9)"));
            break;
        case 2:
            leds.setLed10(true);
            Serial.println(F("[LED] IO10 (GPIO10)"));
            break;
    }
}

void setup() {
    Serial.begin(115200);
    while (!Serial && millis() < 3000) {}

    Serial.println(F("\n===== VFM LEDTest ====="));
    Serial.println(F("GPIO39 = Status  |  GPIO9 = IO9  |  GPIO10 = IO10"));
    Serial.println(F("Auto cycle running. Commands: a=all on  o=all off  1/2/3=single  r=auto"));

    if (leds.begin() != vfm::ServiceStatus::Ok) {
        Serial.println(F("ERROR: LED init failed"));
    }

    lastStepMs = millis();
    showStep(autoStep);
}

void loop() {
    if (mode == Mode::Auto) {
        if ((millis() - lastStepMs) >= kStepMs) {
            lastStepMs = millis();
            autoStep = (autoStep + 1) % 3;
            showStep(autoStep);
        }
    }

    if (!Serial.available()) return;

    char cmd = (char)Serial.read();
    switch (cmd) {
        case 'a':
            mode = Mode::Manual;
            leds.setStatus(true);
            leds.setIoLed(true);
            leds.setLed10(true);
            Serial.println(F("[LED] All ON"));
            break;
        case 'o':
            mode = Mode::Manual;
            allOff();
            Serial.println(F("[LED] All OFF"));
            break;
        case '1':
            mode = Mode::Manual;
            allOff();
            leds.setStatus(true);
            Serial.println(F("[LED] Status only"));
            break;
        case '2':
            mode = Mode::Manual;
            allOff();
            leds.setIoLed(true);
            Serial.println(F("[LED] IO9 only"));
            break;
        case '3':
            mode = Mode::Manual;
            allOff();
            leds.setLed10(true);
            Serial.println(F("[LED] IO10 only"));
            break;
        case 'r':
            mode = Mode::Auto;
            autoStep = 0;
            lastStepMs = millis();
            showStep(autoStep);
            Serial.println(F("[LED] Auto cycle resumed"));
            break;
        default:
            break;
    }
}
