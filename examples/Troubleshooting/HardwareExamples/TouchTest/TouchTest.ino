// TouchTest – capacitive touch bring-up test for VFM module (GPIO5).
//
// Pin:
//   GPIO5 – PIN_TOUCH (ESP32-S3 touch sensor)
//
// Observed bench behaviour (ESP32-S3 touchRead):
//   Idle  ~ 30 000 – 35 000
//   Touch ~ 100 000 – 2 500 000   (raw INCREASES when touched)
//   presence = (raw > threshold)
//
// Open Serial Monitor at 115200 baud (any line ending works).
//
// Commands:
//   s       print current raw value + touch state
//   t <n>   set threshold (no upper limit; e.g. t 50000)
//   + / -   nudge threshold by 5000 (immediate)
//   m       toggle continuous raw monitor (default ON)
//   c       capture idle baseline and suggest threshold
//   h       help

#include <VFM.h>

using namespace vfm;

static constexpr uint32_t kSampleMs   = 200;
static constexpr uint32_t kDefaultThr = 50000;  // between idle (~33k) and touch (~100k+)
static constexpr uint32_t kNudge      = 5000;
static constexpr uint8_t  kBaselineN  = 20;

uint32_t threshold   = kDefaultThr;
bool     touched     = false;
bool     prevTouched = false;
bool     monitoring  = true;
uint32_t lastSampleMs = 0;
uint32_t lastRaw      = 0;

static char    lineBuf[24];
static uint8_t lineIdx = 0;

static bool isTouched(uint32_t raw, uint32_t thr) {
    return raw > thr;
}

void printHelp() {
    Serial.println(F("Commands:"));
    Serial.println(F("  s       current raw + state"));
    Serial.println(F("  t <n>   set threshold (e.g. t 50000)"));
    Serial.println(F("  + / -   nudge threshold by 5000 (immediate)"));
    Serial.println(F("  m       toggle continuous monitor"));
    Serial.println(F("  c       capture idle baseline + suggest threshold"));
    Serial.println(F("  h       help"));
    Serial.println(F("Logic: raw > threshold => TOUCHED"));
}

void applyThreshold(uint32_t thr) {
    threshold = thr;

    touched = isTouched(lastRaw, threshold);
    prevTouched = touched;

    Serial.print(F("[TOUCH] thr=")); Serial.print(threshold);
    Serial.print(F("  raw=")); Serial.print(lastRaw);
    Serial.print(F("  -> "));
    Serial.println(touched ? F("TOUCHED") : F("clear"));
}

void printStatus() {
    Serial.print(F("[TOUCH] raw="));
    Serial.print(lastRaw);
    Serial.print(F("  thr="));
    Serial.print(threshold);
    Serial.print(F("  -> "));
    Serial.println(touched ? F("TOUCHED") : F("clear"));
}

void sampleOnce() {
    lastRaw = touchRead(PIN_TOUCH);
    touched = isTouched(lastRaw, threshold);
}

void captureBaseline() {
    Serial.println(F("[TOUCH] Capturing idle baseline – keep finger OFF for ~1 s..."));
    delay(300);

    uint64_t sum = 0;
    uint32_t minV = UINT32_MAX;
    uint32_t maxV = 0;
    for (uint8_t i = 0; i < kBaselineN; i++) {
        uint32_t v = touchRead(PIN_TOUCH);
        sum += v;
        if (v < minV) minV = v;
        if (v > maxV) maxV = v;
        delay(40);
    }

    uint32_t avg = (uint32_t)(sum / kBaselineN);
    // Place threshold above idle max with ~50% headroom toward typical touch
    uint32_t suggest = maxV + (maxV / 2);
    if (suggest <= maxV) suggest = maxV + 1;

    Serial.print(F("[TOUCH] idle avg=")); Serial.print(avg);
    Serial.print(F("  min=")); Serial.print(minV);
    Serial.print(F("  max=")); Serial.println(maxV);
    Serial.print(F("[TOUCH] Suggested threshold: ")); Serial.println(suggest);
    Serial.println(F("  Applying now. Use +/- or 't <n>' to fine-tune."));
    applyThreshold(suggest);
}

void handleSerialLine(const char *line) {
    if (line[0] == 't' || line[0] == 'T') {
        const char *p = line + 1;
        while (*p == ' ') p++;
        if (*p == '\0') {
            Serial.println(F("Usage: t <n>"));
            return;
        }
        // No upper/lower clamp – accept full uint32 range
        char *end = nullptr;
        unsigned long n = strtoul(p, &end, 10);
        if (end == p) {
            Serial.println(F("Invalid threshold"));
            return;
        }
        applyThreshold((uint32_t)n);
    } else if (strcmp(line, "s") == 0) {
        sampleOnce();
        printStatus();
    } else if (strcmp(line, "m") == 0) {
        monitoring = !monitoring;
        Serial.print(F("Monitor ")); Serial.println(monitoring ? F("ON") : F("OFF"));
    } else if (strcmp(line, "c") == 0) {
        captureBaseline();
    } else if (strcmp(line, "h") == 0 || strcmp(line, "help") == 0) {
        printHelp();
    } else if (line[0] != '\0') {
        Serial.print(F("Unknown: ")); Serial.println(line);
    }
}

void setup() {
    Serial.begin(115200);
    while (!Serial && millis() < 3000) {}

    Serial.println(F("\n===== VFM TouchTest ====="));
    Serial.println(F("PIN_TOUCH = GPIO5  |  touchRead()  |  presence = raw > thr"));
    Serial.println(F("Idle ~30k-35k, touch raises raw (often 100k+)"));
    Serial.print(F("Default threshold: ")); Serial.println(kDefaultThr);
    printHelp();
    Serial.println(F("Tip: run 'c' with pad idle, then fine-tune with +/-"));

    sampleOnce();
    prevTouched = touched;
    printStatus();
    lastSampleMs = millis();
}

void loop() {
    const uint32_t now = millis();

    if ((now - lastSampleMs) >= kSampleMs) {
        lastSampleMs = now;
        sampleOnce();

        if (touched != prevTouched) {
            Serial.print(F("[TOUCH] "));
            Serial.print(touched ? F("TOUCHED") : F("released"));
            Serial.print(F("  raw=")); Serial.print(lastRaw);
            Serial.print(F("  thr=")); Serial.println(threshold);
            prevTouched = touched;
        } else if (monitoring) {
            printStatus();
        }
    }

    while (Serial.available()) {
        char c = (char)Serial.read();

        if (c == '+' || c == '=') {
            applyThreshold(threshold + kNudge);
            lineIdx = 0;
            continue;
        }
        if (c == '-' || c == '_') {
            applyThreshold((threshold > kNudge) ? (threshold - kNudge) : 0);
            lineIdx = 0;
            continue;
        }

        if (c == '\r' || c == '\n') {
            if (lineIdx > 0) {
                lineBuf[lineIdx] = '\0';
                handleSerialLine(lineBuf);
                lineIdx = 0;
            }
            continue;
        }

        if (lineIdx < (sizeof(lineBuf) - 1)) {
            lineBuf[lineIdx++] = c;
        }
    }
}
