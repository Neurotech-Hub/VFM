// PhotogateTest – hardware bring-up test for VFM photogates.
//
// Polarity (matches DispenserService):
//   PG1 (GPIO46) – pellet in cup:        TRIGGERED = pin LOW  (beam break)
//   PG2 (GPIO45) – actuator home/down:   TRIGGERED = pin LOW  (beam break)
//   PG3 (GPIO44) – dome OPEN:            TRIGGERED = pin HIGH (idle = LOW)
//
// Open Serial Monitor at 115200 baud.
//
// Commands:
//   s  – debounced state of all gates
//   r  – raw (instant) pin reads
//   m  – toggle continuous raw monitor (200 ms)
//   h  – help

#include <VFM.h>

using namespace vfm;

static constexpr uint32_t kDebounceMs = 20;
static constexpr uint32_t kBannerMs   = 5000;
static constexpr uint32_t kMonitorMs  = 200;

struct Gate {
    uint8_t     pin;
    const char *name;
    bool        activeHigh;   // true → HIGH = triggered; false → LOW = triggered
    uint8_t     pinModeCfg;
    bool        raw;
    bool        state;
    bool        prevState;
    uint32_t    lastChangeMs;
};

Gate gates[4] = {
    { PIN_PG1, "PG1 (GPIO46)", false, INPUT_PULLUP,   false, false, false, 0 },
    { PIN_PG2, "PG2 (GPIO45)", false, INPUT_PULLUP,   false, false, false, 0 },
    { PIN_PG3, "PG3 (GPIO44)", true,  INPUT_PULLDOWN, false, false, false, 0 },
};

bool     monitoring   = false;
uint32_t lastMonMs    = 0;
uint32_t lastBannerMs = 0;

static bool pinTriggered(const Gate &g) {
    const int level = digitalRead(g.pin);
    return g.activeHigh ? (level == HIGH) : (level == LOW);
}

void initGates() {
    for (auto &g : gates) {
        pinMode(g.pin, g.pinModeCfg);
        g.raw          = pinTriggered(g);
        g.state        = g.raw;
        g.prevState    = g.raw;
        g.lastChangeMs = millis();
    }
}

void updateGates() {
    uint32_t now = millis();
    for (auto &g : gates) {
        bool newRaw = pinTriggered(g);
        if (newRaw != g.raw) {
            g.raw          = newRaw;
            g.lastChangeMs = now;
        }
        if ((now - g.lastChangeMs) >= kDebounceMs) {
            g.state = g.raw;
        }
    }
}

void printRaw() {
    Serial.print(F("[RAW] "));
    for (const auto &g : gates) {
        Serial.print(g.name);
        const int level = digitalRead(g.pin);
        Serial.print(level == HIGH ? F("=HIGH") : F("=LOW"));
        Serial.print(pinTriggered(g) ? F("(TRIG) ") : F("(clr) "));
    }
    Serial.println();
}

void printDebounced() {
    Serial.println(F("[PG] Debounced state:"));
    for (const auto &g : gates) {
        Serial.print(F("  ")); Serial.print(g.name);
        Serial.print(F(" -> "));
        Serial.println(g.state ? F("TRIGGERED") : F("clear"));
    }
}

void printHelp() {
    Serial.println(F("Commands:"));
    Serial.println(F("  s  debounced state"));
    Serial.println(F("  r  raw digital reads"));
    Serial.println(F("  m  toggle 200 ms raw monitor"));
    Serial.println(F("  h  help"));
    Serial.println(F("PG1/PG2: LOW=TRIG  |  PG3: HIGH=TRIG (dome open)"));
}

void setup() {
    Serial.begin(115200);
    while (!Serial && millis() < 3000) {}

    Serial.println(F("\n===== VFM PhotogateTest ====="));
    Serial.println(F("PG1=GPIO46  PG2=GPIO45  PG3=GPIO44"));
    Serial.println(F("PG3 idle=LOW, open=HIGH (active HIGH)"));
    printHelp();

    initGates();
    printRaw();
    printDebounced();
    Serial.println(F("Monitoring for changes..."));
}

void loop() {
    updateGates();

    for (auto &g : gates) {
        if (g.state != g.prevState) {
            Serial.print(F("[PG] "));
            Serial.print(g.name);
            Serial.print(F(" -> "));
            Serial.println(g.state ? F("TRIGGERED") : F("clear"));
            g.prevState = g.state;
        }
    }

    if ((millis() - lastBannerMs) >= kBannerMs) {
        lastBannerMs = millis();
        printDebounced();
    }

    if (monitoring && (millis() - lastMonMs) >= kMonitorMs) {
        lastMonMs = millis();
        printRaw();
    }

    if (Serial.available()) {
        char cmd = (char)Serial.read();
        switch (cmd) {
            case 's': printDebounced(); break;
            case 'r': printRaw();       break;
            case 'm':
                monitoring = !monitoring;
                Serial.print(F("Raw monitor ")); Serial.println(monitoring ? F("ON") : F("OFF"));
                break;
            case 'h': printHelp(); break;
            default: break;
        }
    }
}
