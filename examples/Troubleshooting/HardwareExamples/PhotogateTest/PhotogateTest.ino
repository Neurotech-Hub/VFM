// PhotogateTest – hardware bring-up test for VFM photogates.
//
// All gates active LOW (INPUT_PULLUP), pin LOW = triggered:
//   PG1 (GPIO46) – pellet seated in actuator cup
//   PG2 (GPIO45) – actuator at home / down (PCB net; may stay clear on MINI-1)
//   PG3 (GPIO44) – dome opened
//   PGx (GPIO6)  – User_IO_6, alternate PG2 input (wire sensor here if GPIO45 stuck)
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

// ---------------------------------------------------------------------------
struct Gate {
    uint8_t    pin;
    const char *name;
    bool       raw;
    bool       state;
    bool       prevState;
    uint32_t   lastChangeMs;
};

Gate gates[4] = {
    { PIN_PG1, "PG1 (GPIO46)" },
    { PIN_PG2, "PG2 (GPIO45)" },
    { PIN_PG3, "PG3 (GPIO44)" },
    { PIN_PGX, "PGx (GPIO6)" },
};

bool     monitoring   = false;
uint32_t lastMonMs    = 0;
uint32_t lastBannerMs = 0;

// ---------------------------------------------------------------------------
void initGates() {
    for (auto &g : gates) {
        pinMode(g.pin, INPUT_PULLUP);
        g.raw          = digitalRead(g.pin) == LOW;
        g.state        = g.raw;
        g.prevState    = g.raw;
        g.lastChangeMs = millis();
    }
}

void updateGates() {
    uint32_t now = millis();
    for (auto &g : gates) {
        bool newRaw = digitalRead(g.pin) == LOW;
        if (newRaw != g.raw) {
            g.raw          = newRaw;
            g.lastChangeMs = now;
        }
        if ((now - g.lastChangeMs) >= kDebounceMs) {
            g.state = g.raw;
        }
    }
}

// ---------------------------------------------------------------------------
void printRaw() {
    Serial.print(F("[RAW] "));
    for (const auto &g : gates) {
        Serial.print(g.name);
        Serial.print(digitalRead(g.pin) == LOW ? F("=LOW(TRIG) ") : F("=HIGH(clr) "));
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
    Serial.println(F("Logic: pin LOW = TRIGGERED  |  pin HIGH = clear"));
}

// ---------------------------------------------------------------------------
void setup() {
    Serial.begin(115200);
    while (!Serial && millis() < 3000) {}

    Serial.println(F("\n===== VFM PhotogateTest ====="));
    Serial.println(F("PG1=GPIO46  PG2=GPIO45  PG3=GPIO44  PGx=GPIO6 (all active LOW)"));
    printHelp();

    initGates();
    printRaw();
    printDebounced();
    Serial.println(F("Monitoring for changes..."));
}

// ---------------------------------------------------------------------------
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
