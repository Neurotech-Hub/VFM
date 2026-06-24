#pragma once

#include <Arduino.h>
#include <Preferences.h>
#include "ServiceTypes.h"
#include "CanService.h"
#include "../hardware/VFMPins.h"

namespace vfm {

// NVS namespace / key used to persist the node ID
constexpr char kNvsNamespace[] = "vfm";
constexpr char kNvsKeyNodeId[] = "nodeId";

// Timeouts for the discovery FSM
constexpr uint32_t kAnnounceRetryMs    = 500;  // retry ANNOUNCE after this
constexpr uint32_t kDiscoveryTimeoutMs = 5000; // give up waiting for ASSIGN

// ---------------------------------------------------------------------------
// NodeIdentity
//
// Manages the node's persistent ID (stored in NVS) and drives the AEO/AEI
// daisy-chain discovery protocol at boot.
//
// AEI (GPIO14): INPUT_PULLDOWN – driven HIGH by upstream AEO / base station.
// AEO (GPIO47): OUTPUT – held LOW until identity is resolved, then set HIGH
//               to enable the next downstream node.
//
// Discovery flow (first boot, NVS empty):
//   WaitAEI -> CheckNVS -> Announce -> WaitAssign -> (Save) -> Enabled
//
// Normal boot (NVS has id):
//   WaitAEI -> CheckNVS -> Rejoin -> Enabled
// ---------------------------------------------------------------------------
class NodeIdentity {
public:
    explicit NodeIdentity(CanService &can);

    // begin(): configure AEI/AEO pins, read MAC, restore NVS id if present.
    // Does NOT start the discovery FSM yet; call startDiscovery() after
    // CanService::begin() so the TWAI bus is ready.
    ServiceStatus begin();

    // Start the boot-time discovery FSM. Must be called once after begin().
    void startDiscovery();

    // Non-blocking update – advance the discovery FSM, check AEI/AEO timers.
    void update();

    // --------------- Identity accessors -----------------------------------
    uint8_t nodeId() const  { return nodeId_; }
    bool    hasId()  const  { return nodeId_ > 0; }

    // 6-byte MAC address (UUID)
    const uint8_t *mac() const { return mac_; }

    // true once discovery has completed (Enabled state)
    bool isEnabled() const { return discoveryState_ == DiscoveryState::Enabled; }

    DiscoveryState discoveryState() const { return discoveryState_; }

    // Directly assign a node ID (e.g. from serial or CanCmd::AssignId).
    // Persists to NVS and drives AEO HIGH if not already Enabled.
    void assignId(uint8_t id);

    // Clear the NVS id (forces first-boot discovery on next reset).
    void clearId();

private:
    CanService   &can_;
    Preferences   prefs_;

    DiscoveryState discoveryState_;
    uint8_t        nodeId_;
    uint8_t        mac_[6];

    uint32_t       lastAnnounceMs_;
    uint32_t       discoveryStartMs_;

    // Pending ASSIGN frame received via CanService discovery callback
    bool     assignPending_;
    uint8_t  assignedId_;

    // Internal helpers
    void     readMac();
    void     saveIdToNvs(uint8_t id);
    uint8_t  loadIdFromNvs();

    void     sendAnnounce();
    void     sendAck();
    void     sendRejoin();
    void     enableNode();

    // Called by CanService::onDiscovery callback (from CanService::update())
    void     onDiscoveryFrame(uint32_t frameId, const uint8_t *payload, uint8_t len);
};

} // namespace vfm
