// CanBusTest – two-device CAN bus communication test.
//
// Flash this same sketch onto both VFM modules, then assign different IDs.
//
// Two-device wiring (daisy chain via RJ45):
//   Device-A OUT -> Device-B IN
//
// Auto-termination is handled entirely in hardware:
//   Device-A (OUT port connected) -> 120 Ω resistor disengaged automatically
//   Device-B (OUT port open)      -> 120 Ω resistor engaged automatically
//
// Daisy-chain enable:
//   Device-A: tie AEI (GPIO14) HIGH (to 3.3 V) to start the chain.
//   Device-B: AEI is driven HIGH by Device-A's AEO (GPIO47) automatically
//             once Device-A drives AEO HIGH (done here via 'id' command).
//
// CAN bus: 250 kbps, GPIO33 (TX) / GPIO13 (RX)
//
// Open Serial Monitor at 115200 baud on each device.
//
// Commands:
//   id <n>   assign local node ID (1 on Device-A, 2 on Device-B)
//   ping     send one Ping to the peer node
//   a        toggle auto-ping every 2 s (enabled after 'id')
//   s        print TWAI bus statistics (state, error counters, ping counts)
//   c        print AEI / AEO chain pin levels
//   h        help

#include <VFM.h>
#include <driver/twai.h>

static constexpr uint32_t kAutoPingMs = 2000;
static constexpr uint32_t kStatsMs    = 5000;

vfm::CanService can;

uint8_t  nodeId      = 0;
uint8_t  peerId      = 0;
bool     autoPing    = false;
uint32_t txPingCount = 0;
uint32_t rxPingCount = 0;
uint32_t lastPingMs  = 0;
uint32_t lastStatsMs = 0;

static char    lineBuf[16];
static uint8_t lineIdx = 0;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

uint8_t defaultPeerId() {
    if (nodeId == 1) return 2;
    if (nodeId == 2) return 1;
    return 0;
}

void printHelp() {
    Serial.println(F("Commands:"));
    Serial.println(F("  id <n>  assign node ID (1=Device-A, 2=Device-B)"));
    Serial.println(F("  ping    send Ping to peer"));
    Serial.println(F("  a       toggle auto-ping (2 s interval)"));
    Serial.println(F("  s       TWAI bus statistics"));
    Serial.println(F("  c       AEI / AEO chain pin levels"));
    Serial.println(F("  h       help"));
}

void printChain() {
    Serial.print(F("[CHAIN] AEI (GPIO14)="));
    Serial.print(digitalRead(vfm::PIN_AEI) == HIGH ? F("HIGH") : F("LOW"));
    Serial.print(F("  AEO (GPIO47)="));
    Serial.println(digitalRead(vfm::PIN_AEO) == HIGH ? F("HIGH") : F("LOW"));
}

void printBusStats() {
    if (!can.isStarted()) {
        Serial.println(F("[CAN] Driver not started"));
        return;
    }

    twai_status_info_t info;
    if (twai_get_status_info(&info) != ESP_OK) {
        Serial.println(F("[CAN] Could not read TWAI status"));
        return;
    }

    Serial.print(F("[CAN] state="));
    switch (info.state) {
        case TWAI_STATE_STOPPED:    Serial.print(F("STOPPED"));    break;
        case TWAI_STATE_RUNNING:    Serial.print(F("RUNNING"));    break;
        case TWAI_STATE_BUS_OFF:    Serial.print(F("BUS_OFF"));    break;
        case TWAI_STATE_RECOVERING: Serial.print(F("RECOVERING")); break;
        default:                    Serial.print(info.state);      break;
    }
    Serial.print(F("  tx_err="));  Serial.print(info.tx_error_counter);
    Serial.print(F("  rx_err="));  Serial.print(info.rx_error_counter);
    Serial.print(F("  tx_fail=")); Serial.print(info.tx_failed_count);
    Serial.print(F("  rx_miss=")); Serial.print(info.rx_missed_count);
    Serial.print(F("  ping_tx=")); Serial.print(txPingCount);
    Serial.print(F("  ping_rx=")); Serial.println(rxPingCount);
}

void sendPing(uint8_t target) {
    if (!can.isStarted() || target == 0) {
        Serial.println(F("[CAN] Assign an id first"));
        return;
    }
    twai_message_t msg = {};
    msg.identifier       = vfm::CAN_CMD_BASE + target;
    msg.data_length_code = 1;
    msg.data[0]          = static_cast<uint8_t>(vfm::CanCmd::Ping);

    if (twai_transmit(&msg, pdMS_TO_TICKS(100)) == ESP_OK) {
        txPingCount++;
        Serial.print(F("[CAN] TX Ping -> node ")); Serial.println(target);
    } else {
        Serial.println(F("[CAN] TX failed – check wiring and termination"));
    }
}

void assignNodeId(uint8_t id) {
    if (id == 0 || id > 254) {
        Serial.println(F("Invalid id (use 1-254)"));
        return;
    }
    nodeId = id;
    peerId = defaultPeerId();
    can.setNodeId(id);

    // Drive AEO HIGH to enable the downstream node's AEI.
    // (Has no effect if this device is the last in the chain.)
    digitalWrite(vfm::PIN_AEO, HIGH);

    autoPing = (peerId > 0);

    Serial.print(F("Node ID=")); Serial.print(id);
    Serial.print(F("  peer=")); Serial.print(peerId);
    Serial.print(F("  auto-ping=")); Serial.println(autoPing ? F("ON") : F("OFF"));
    printChain();
}

void handleSerialLine(const char *line) {
    if (strncmp(line, "id ", 3) == 0) {
        assignNodeId((uint8_t)atoi(line + 3));
    } else if (strcmp(line, "ping") == 0) {
        sendPing(peerId > 0 ? peerId : defaultPeerId());
    } else if (strcmp(line, "a") == 0) {
        autoPing = !autoPing;
        Serial.print(F("Auto-ping ")); Serial.println(autoPing ? F("ON") : F("OFF"));
    } else if (strcmp(line, "s") == 0) {
        printBusStats();
    } else if (strcmp(line, "c") == 0) {
        printChain();
    } else if (strcmp(line, "h") == 0 || strcmp(line, "help") == 0) {
        printHelp();
    } else if (line[0] != '\0') {
        Serial.print(F("Unknown: ")); Serial.println(line);
    }
}

// ---------------------------------------------------------------------------
void setup() {
    Serial.begin(115200);
    while (!Serial && millis() < 3000) {}

    Serial.println(F("\n===== VFM CanBusTest ====="));
    Serial.println(F("250 kbps TWAI  |  TX=GPIO33  RX=GPIO13"));
    Serial.println(F("Auto-termination: hardware-controlled via OUT RJ45 port"));
    Serial.println(F("  OUT connected -> 120 ohm OFF  |  OUT open -> 120 ohm ON"));
    printHelp();

    pinMode(vfm::PIN_AEI, INPUT_PULLDOWN);
    pinMode(vfm::PIN_AEO, OUTPUT);
    digitalWrite(vfm::PIN_AEO, LOW);

    can.onCommand([](vfm::CanCmd cmd, const uint8_t *, uint8_t) {
        if (cmd != vfm::CanCmd::Ping) return;
        rxPingCount++;
        Serial.println(F("[CAN] RX Ping -> TX Pong"));
        can.sendEvent(vfm::CanEvent::Pong);
    });

    if (can.begin(0) != vfm::ServiceStatus::Ok) {
        Serial.println(F("ERROR: CAN driver failed to start"));
        Serial.println(F("  Check TX/RX wiring and transceiver power"));
    } else {
        Serial.println(F("CAN driver OK. Type: id 1  (Device-A)  or  id 2  (Device-B)"));
    }

    lastStatsMs = millis();
}

void loop() {
    can.update();

    const uint32_t now = millis();

    if (autoPing && nodeId > 0 && peerId > 0 && (now - lastPingMs) >= kAutoPingMs) {
        lastPingMs = now;
        sendPing(peerId);
    }

    if (nodeId > 0 && (now - lastStatsMs) >= kStatsMs) {
        lastStatsMs = now;
        printBusStats();
    }

    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\r') continue;
        if (c == '\n') {
            lineBuf[lineIdx] = '\0';
            if (lineIdx > 0) handleSerialLine(lineBuf);
            lineIdx = 0;
        } else if (lineIdx < (sizeof(lineBuf) - 1)) {
            lineBuf[lineIdx++] = c;
        }
    }
}
