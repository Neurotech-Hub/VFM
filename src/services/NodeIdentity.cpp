#include "NodeIdentity.h"
#include "esp_mac.h"

namespace vfm {

NodeIdentity::NodeIdentity(CanService &can)
    : can_(can),
      discoveryState_(DiscoveryState::WaitAEI),
      nodeId_(0),
      lastAnnounceMs_(0),
      discoveryStartMs_(0),
      assignPending_(false),
      assignedId_(0)
{
    memset(mac_, 0, sizeof(mac_));
}

// ---------------------------------------------------------------------------
ServiceStatus NodeIdentity::begin() {
    // AEI – input with pull-down: gate-in from upstream AEO / base station
    pinMode(PIN_AEI, INPUT_PULLDOWN);

    // AEO – output, default LOW: will be driven HIGH once identity is resolved
    pinMode(PIN_AEO, OUTPUT);
    digitalWrite(PIN_AEO, LOW);

    // Read this node's WiFi station MAC as its UUID
    readMac();

    // Try to restore a previously assigned ID from NVS
    nodeId_ = loadIdFromNvs();

    // Register the discovery callback with CanService so that TWAI frames
    // with discovery IDs (0x080-0x083) are forwarded to this FSM.
    can_.onDiscovery([this](uint32_t frameId, const uint8_t *payload, uint8_t len) {
        onDiscoveryFrame(frameId, payload, len);
    });

    return ServiceStatus::Ok;
}

// ---------------------------------------------------------------------------
void NodeIdentity::startDiscovery() {
    discoveryStartMs_ = millis();
    discoveryState_   = DiscoveryState::WaitAEI;
}

// ---------------------------------------------------------------------------
void NodeIdentity::update() {
    switch (discoveryState_) {

        // ----- Wait for AEI to go HIGH (upstream node or base drives it) ---
        case DiscoveryState::WaitAEI:
            if (digitalRead(PIN_AEI) == HIGH) {
                discoveryState_ = DiscoveryState::CheckNVS;
            }
            break;

        // ----- Check whether we already have a saved ID --------------------
        case DiscoveryState::CheckNVS:
            if (nodeId_ > 0) {
                discoveryState_ = DiscoveryState::Rejoin;
            } else {
                discoveryState_ = DiscoveryState::Announce;
                lastAnnounceMs_ = 0; // force immediate send
            }
            break;

        // ----- Send ANNOUNCE (or retry) and wait for ASSIGN ----------------
        case DiscoveryState::Announce:
            if ((millis() - lastAnnounceMs_) >= kAnnounceRetryMs) {
                sendAnnounce();
                lastAnnounceMs_ = millis();
                discoveryState_ = DiscoveryState::WaitAssign;
                discoveryStartMs_ = millis();
            }
            break;

        case DiscoveryState::WaitAssign:
            if (assignPending_) {
                assignPending_ = false;
                saveIdToNvs(assignedId_);
                nodeId_ = assignedId_;
                can_.setNodeId(nodeId_);
                sendAck();
                enableNode();
            } else if ((millis() - discoveryStartMs_) >= kDiscoveryTimeoutMs) {
                // Retry: go back to Announce state
                discoveryState_ = DiscoveryState::Announce;
            }
            break;

        // ----- Already have ID – send REJOIN and enable immediately --------
        case DiscoveryState::Rejoin:
            sendRejoin();
            enableNode(); // Raise AEO right away (fast propagate)
            break;

        // ----- Discovery complete; update CanService with resolved id ------
        case DiscoveryState::Enabled:
            // Nothing to do: AEO is HIGH, CanService is running normally
            break;
    }
}

// ---------------------------------------------------------------------------
void NodeIdentity::assignId(uint8_t id) {
    saveIdToNvs(id);
    nodeId_ = id;
    can_.setNodeId(id);
    if (discoveryState_ != DiscoveryState::Enabled) {
        enableNode();
    }
}

// ---------------------------------------------------------------------------
void NodeIdentity::clearId() {
    prefs_.begin(kNvsNamespace, false);
    prefs_.remove(kNvsKeyNodeId);
    prefs_.end();
    nodeId_ = 0;
    can_.setNodeId(0);
    // Drop AEO so the daisy chain re-sequences from the base on next discovery.
    digitalWrite(PIN_AEO, LOW);
    assignPending_ = false;
    discoveryState_ = DiscoveryState::WaitAEI;
}

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

void NodeIdentity::readMac() {
    esp_read_mac(mac_, ESP_MAC_WIFI_STA);
}

void NodeIdentity::saveIdToNvs(uint8_t id) {
    prefs_.begin(kNvsNamespace, false);
    prefs_.putUChar(kNvsKeyNodeId, id);
    prefs_.end();
}

uint8_t NodeIdentity::loadIdFromNvs() {
    prefs_.begin(kNvsNamespace, true); // read-only
    uint8_t id = prefs_.getUChar(kNvsKeyNodeId, 0);
    prefs_.end();
    return id;
}

// ---------------------------------------------------------------------------
// Discovery frame builders
// Payload layout for ANNOUNCE / ACK / REJOIN: MAC[0..5] (6 bytes)
// Payload layout for ASSIGN received:         MAC[0..5] + id[6] (7 bytes)
// ---------------------------------------------------------------------------

void NodeIdentity::sendAnnounce() {
    // ANNOUNCE: this node has no ID and requests one from the base station.
    // Payload: MAC address (6 bytes)
    can_.sendDiscovery(CAN_ID_ANNOUNCE, mac_, 6);
}

void NodeIdentity::sendAck() {
    // ACK: confirm receipt of the ASSIGN frame.
    // Payload: MAC(6) + assigned id(1)
    uint8_t payload[7];
    memcpy(payload, mac_, 6);
    payload[6] = nodeId_;
    can_.sendDiscovery(CAN_ID_ACK, payload, 7);
}

void NodeIdentity::sendRejoin() {
    // REJOIN: inform the base station of our existing ID on normal boot.
    // Payload: MAC(6) + saved id(1)
    uint8_t payload[7];
    memcpy(payload, mac_, 6);
    payload[6] = nodeId_;
    can_.sendDiscovery(CAN_ID_REJOIN, payload, 7);
}

void NodeIdentity::enableNode() {
    // Drive AEO HIGH so the next downstream node can begin its discovery step.
    digitalWrite(PIN_AEO, HIGH);
    discoveryState_ = DiscoveryState::Enabled;
}

// ---------------------------------------------------------------------------
// Called from CanService::onDiscovery callback (runs in CanService::update())
// ---------------------------------------------------------------------------
void NodeIdentity::onDiscoveryFrame(uint32_t frameId, const uint8_t *payload, uint8_t len) {
    if (frameId == CAN_ID_ASSIGN && len >= 7) {
        // ASSIGN frame: base says "node with MAC X, you are ID Y"
        // Only accept if the MAC matches ours
        if (memcmp(payload, mac_, 6) == 0) {
            assignedId_   = payload[6];
            assignPending_ = true;
        }
    }
    // ANNOUNCE, ACK, REJOIN originate from nodes, not the base; ignore them.
}

} // namespace vfm
