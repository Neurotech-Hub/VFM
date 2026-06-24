#pragma once

// ---------------------------------------------------------------------------
// VFM – Spatial Foraging Platform module library
//
// Facade class that aggregates all services. Typical sketch usage:
//
//   #include <VFM.h>
//   vfm::VFM vfm;
//
//   void setup() {
//       vfm.begin();            // init all services + start discovery
//   }
//   void loop() {
//       vfm.update();           // drive FSMs, pump CAN, heartbeat
//   }
// ---------------------------------------------------------------------------

#include "hardware/VFMPins.h"
#include "services/ServiceTypes.h"
#include "services/DispenserService.h"
#include "services/CanService.h"
#include "services/NodeIdentity.h"
#include "services/LedService.h"

namespace vfm {

class VFM {
public:
    VFM();

    // Initialise all services in the correct order.
    // Returns false if any critical service fails to initialise.
    bool begin();

    // Non-blocking main-loop update.
    // Call every loop iteration; order matters: identity -> CAN -> dispenser -> leds.
    void update();

    // --- Service accessors ---
    DispenserService &dispenser()   { return dispenser_; }
    CanService       &can()         { return can_; }
    NodeIdentity     &identity()    { return identity_; }
    LedService       &leds()        { return leds_; }

    // Capacitive touch reading (raw analog value from PIN_TOUCH).
    // The VFM facade exposes a simple threshold-based presence flag.
    bool presenceDetected() const   { return presence_; }

    // Override the touch threshold (default 40; lower = more sensitive).
    void setTouchThreshold(uint16_t t) { touchThreshold_ = t; }

    // Force an immediate heartbeat regardless of the heartbeat timer.
    void sendHeartbeatNow();

private:
    DispenserService dispenser_;
    CanService       can_;
    NodeIdentity     identity_;
    LedService       leds_;

    bool     presence_;
    uint16_t touchThreshold_;

    // Translates DispenserService events into CanService event frames and
    // feeds the current state into periodic heartbeats.
    void handleDispenserEvents();
    void sendHeartbeatIfDue();
    void updateTouch();
};

} // namespace vfm
