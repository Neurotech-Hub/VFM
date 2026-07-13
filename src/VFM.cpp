#include "VFM.h"



namespace vfm {



VFM::VFM()

    : can_(),

      identity_(can_),

      presence_(false),

      touchThreshold_(40),

      btnHoldMs_(3000),

      btnPressStartMs_(0),

      btnWasPressed_(false),

      btnArmed_(false)

{}



// ---------------------------------------------------------------------------

bool VFM::begin() {

    bool ok = true;



    // 1. LEDs first – visual feedback during boot

    leds_.begin();

    leds_.setLed9BlinkMs(500); // LED 9 fast blink = booting



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

                blinkStatusLedForPing();

                break;

            }

            case CanCmd::ReqStatus:

                sendHeartbeatNow();

                break;

            case CanCmd::SetConfig:

                if (len >= 1 && static_cast<ConfigType>(payload[0]) == ConfigType::HeartbeatInterval) {

                    if (len >= 3) {

                        uint16_t ms = static_cast<uint16_t>(payload[1]) |
                                      (static_cast<uint16_t>(payload[2]) << 8);

                        can_.setHeartbeatIntervalMs(ms);

                    }

                }

                break;

            case CanCmd::ClearId:

                // Wipe NVS ID, drop AEO, and wait for the base to re-drive
                // AEO HIGH so this node ANNOUNCE's for a fresh assignment.
                identity_.clearId();

                break;

            default:

                break;

        }

    });



    // 5. Dispenser hardware

    if (dispenser_.begin() != ServiceStatus::Ok) {

        ok = false;

    }



    // 6. Touch pin (analog read) and BTN (active LOW, long-press clears NVS ID)

    pinMode(PIN_TOUCH, INPUT);

    pinMode(PIN_BTN, INPUT_PULLUP);



    // 7. Start discovery FSM (requires CAN to be up)

    identity_.startDiscovery();



    if (ok) {

        leds_.setLed9BlinkMs(1000);       // LED 9 slow blink = waiting for discovery

        leds_.setStatusLedBlinkMs(1000);  // status LED slow blink = waiting for discovery

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

    updateButton();

    handleDispenserEvents();

    sendHeartbeatIfDue();

    updatePingBlink();



    // Once discovery completes, turn status / LED 9 off — unless a Ping
    // blink is currently active, which takes precedence so the node stays
    // visually identifiable for its full blink duration.

    if (identity_.isEnabled() && !pingBlinkActive_) {

        leds_.setStatusLedBlinkMs(0);

        leds_.setStatusLed(false);

        leds_.setLed9BlinkMs(0);

        leds_.setLed9(false);

    }



    // Status LED solid ON while in Fault state — always wins over a Ping blink.

    if (dispenser_.state() == DispenseState::Fault) {

        pingBlinkActive_  = false;

        pingBlinkUntilMs_ = 0;

        leds_.setStatusLedBlinkMs(0);

        leds_.setStatusLed(true);

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

            leds_.setStatusLedBlinkMs(0);

            leds_.setStatusLed(true);       // status LED solid ON = fault

            break;

        default: return;

    }



    // Clear status LED when returning to normal operation after a fault

    if (ev == DispenseEvent::PelletLoaded || ev == DispenseEvent::PelletPresented ||

        ev == DispenseEvent::PelletTaken) {

        leds_.setStatusLed(false);

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



void VFM::blinkStatusLedForPing() {

    // Don't interrupt a solid fault indication with a blink.
    if (dispenser_.state() == DispenseState::Fault) return;

    pingBlinkActive_  = true;
    pingBlinkUntilMs_ = millis() + kPingBlinkMs;
    leds_.setStatusLedBlinkMs(kPingBlinkPeriodMs);

}



void VFM::updatePingBlink() {

    if (!pingBlinkActive_) return;

    if ((int32_t)(millis() - pingBlinkUntilMs_) >= 0) {
        pingBlinkActive_  = false;
        pingBlinkUntilMs_ = 0;
        leds_.setStatusLedBlinkMs(0);
        leds_.setStatusLed(false);
    }

}



void VFM::updateTouch() {

    // ESP32-S3 touch sensor is read via touchRead() which returns a raw value;

    // lower values typically indicate a touch. Threshold must be bench-tuned.

    uint32_t val = touchRead(PIN_TOUCH);

    presence_ = (val < touchThreshold_);

}



// ---------------------------------------------------------------------------

// Button: PIN_BTN is INPUT_PULLUP; button press drives it LOW.

//

// Behaviour:

//   - Press and hold for btnHoldMs_ → LED 9 blinks rapidly as visual warning.

//   - Release after hold threshold → NVS ID cleared; status/LED9/LED10 flash 3x.

//   - Release before threshold     → no action (accidental press ignored).

// ---------------------------------------------------------------------------

void VFM::updateButton() {

    bool pressed = (digitalRead(PIN_BTN) == LOW);



    if (pressed) {

        if (!btnWasPressed_) {

            // Leading edge: record press start

            btnPressStartMs_ = millis();

            btnWasPressed_   = true;

            btnArmed_        = false;

        }



        uint32_t heldMs = millis() - btnPressStartMs_;



        if (!btnArmed_ && heldMs >= btnHoldMs_) {

            // Hold threshold reached – arm and start rapid blink as warning

            btnArmed_ = true;

            leds_.setLed9BlinkMs(100);

        }

    } else {

        if (btnWasPressed_) {

            // Trailing edge

            if (btnArmed_) {

                // Held long enough – clear NVS ID and confirm visually

                identity_.clearId();

                leds_.setLed9BlinkMs(0);

                flashLedsClear();

            }

            btnWasPressed_ = false;

            btnArmed_      = false;

        }

    }

}



// Three rapid flashes on status, LED 9, and LED 10 to confirm NVS ID was cleared.

// This is the one intentional blocking call in the library; it runs for

// ~600 ms total and only fires on a deliberate 3-second button hold.

void VFM::flashLedsClear() {

    for (int i = 0; i < 3; i++) {

        leds_.setStatusLed(true);

        leds_.setLed9(true);

        leds_.setLed10(true);

        delay(100);

        leds_.setStatusLed(false);

        leds_.setLed9(false);

        leds_.setLed10(false);

        delay(100);

    }

}



} // namespace vfm


