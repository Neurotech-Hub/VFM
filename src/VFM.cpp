#include "VFM.h"

namespace vfm {

VFM::VFM()
    : can_(),
      identity_(can_),
      presence_(false),
      touchThreshold_(40)
{}

// ---------------------------------------------------------------------------
bool VFM::begin() {
    bool ok = true;

    // 1. LEDs first – visual feedback during boot
    leds_.begin();
    leds_.setIoBlinkIntervalMs(500); // IO LED fast blink = booting

    // 2. NodeIdentity: configure pins, read MAC, restore NVS id
    if (identity_.begin() != ServiceStatus::Ok) {
        ok = false;
    }

    // 3. CAN bus: start TWAI driver
    uint8_t savedId = identity_.nodeId(); // may be 0 if NVS empty
    if (can_.begin(savedId) != ServiceStatus::Ok) {
        ok = false;
    }

    // 4. Register CanService command handler
    can_.onCommand([this](CanCmd cmd, const uint8_t *payload, uint8_t len) {
        switch (cmd) {
            case CanCmd::Dispense:
                dispenser_.dispense();
                break;
            case CanCmd::Abort:
                dispenser_.abort();
                break;
            case CanCmd::AssignId:
                if (len >= 1 && payload[0] > 0) {
                    identity_.assignId(payload[0]);
                    can_.setNodeId(payload[0]);
                }
                break;
            case CanCmd::Ping: {
                CanEvent pong = CanEvent::Pong;
                can_.sendEvent(pong);
                break;
            }
            case CanCmd::ReqStatus:
                sendHeartbeatNow();
                break;
            default:
                break;
        }
    });

    // 5. Dispenser hardware
    if (dispenser_.begin() != ServiceStatus::Ok) {
        ok = false;
    }

    // 6. Touch pin (analog read)
    pinMode(PIN_TOUCH, INPUT);

    // 7. Start discovery FSM (requires CAN to be up)
    identity_.startDiscovery();

    if (ok) {
        leds_.setIoBlinkIntervalMs(1000);  // IO LED slow blink = waiting for discovery
        leds_.setBlinkIntervalMs(1000);    // status LED slow blink = waiting for discovery
    }
    return ok;
}

// ---------------------------------------------------------------------------
void VFM::update() {
    can_.update();       // pump RX first so callbacks (discovery, commands) fire
    identity_.update();  // then act on any received discovery frames
    dispenser_.update();
    leds_.update();
    updateTouch();
    handleDispenserEvents();
    sendHeartbeatIfDue();

    // Once discovery completes, turn both LEDs off
    if (identity_.isEnabled()) {
        leds_.setBlinkIntervalMs(0);       // stop status LED blinking
        leds_.setStatus(false);            // status LED OFF
        leds_.setIoBlinkIntervalMs(0);     // stop IO LED blinking
        leds_.setIoLed(false);             // IO LED OFF
    }

    // Status LED solid ON while in Fault state
    if (dispenser_.state() == DispenseState::Fault) {
        leds_.setBlinkIntervalMs(0);       // ensure not blinking
        leds_.setStatus(true);             // solid ON = fault
    }
}

// ---------------------------------------------------------------------------
// Private
// ---------------------------------------------------------------------------

void VFM::handleDispenserEvents() {
    DispenseEvent ev = dispenser_.takeEvent();
    if (ev == DispenseEvent::None) return;

    CanEvent canEv;
    switch (ev) {
        case DispenseEvent::PelletLoaded:    canEv = CanEvent::PelletLoaded;    break;
        case DispenseEvent::PelletPresented: canEv = CanEvent::PelletPresented; break;
        case DispenseEvent::PelletTaken:     canEv = CanEvent::PelletTaken;     break;
        case DispenseEvent::Fault:
            canEv = CanEvent::Fault;
            leds_.setBlinkIntervalMs(0);    // stop any blink pattern
            leds_.setStatus(true);          // status LED solid ON = fault
            break;
        default: return;
    }

    // Clear status LED when returning to normal operation after a fault
    if (ev == DispenseEvent::PelletLoaded || ev == DispenseEvent::PelletPresented ||
        ev == DispenseEvent::PelletTaken) {
        leds_.setStatus(false);             // status LED OFF = no fault
    }

    // Attach pellet count as one extra byte in the event payload
    uint8_t extra[2];
    uint32_t count = dispenser_.pelletCount();
    extra[0] = static_cast<uint8_t>(count & 0xFF);
    extra[1] = static_cast<uint8_t>((count >> 8) & 0xFF);
    can_.sendEvent(canEv, extra, 2);
}

static HeartbeatPayload buildHeartbeat(const DispenserService &d, bool presence) {
    HeartbeatPayload p = {};
    p.dispenseState  = static_cast<uint8_t>(d.state());
    uint32_t count   = d.pelletCount();
    p.pelletCountLo  = static_cast<uint8_t>(count & 0xFF);
    p.pelletCountHi  = static_cast<uint8_t>((count >> 8) & 0xFF);
    p.presence       = presence ? 1 : 0;
    p.pgBits         = (d.pg1() ? 0x01 : 0) |
                       (d.pg2() ? 0x02 : 0) |
                       (d.pg3() ? 0x04 : 0);
    p.faultCode      = (d.state() == DispenseState::Fault)
                           ? static_cast<uint8_t>(ServiceStatus::Jam)
                           : static_cast<uint8_t>(ServiceStatus::Ok);
    return p;
}

void VFM::sendHeartbeatIfDue() {
    if (!can_.heartbeatDue()) return;
    can_.sendHeartbeat(buildHeartbeat(dispenser_, presence_));
}

void VFM::sendHeartbeatNow() {
    can_.sendHeartbeat(buildHeartbeat(dispenser_, presence_));
}

void VFM::updateTouch() {
    // ESP32-S3 touch sensor is read via touchRead() which returns a raw value;
    // lower values typically indicate a touch. Threshold must be bench-tuned.
    uint32_t val = touchRead(PIN_TOUCH);
    presence_ = (val < touchThreshold_);
}

} // namespace vfm
