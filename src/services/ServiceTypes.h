#pragma once

#include <Arduino.h>

namespace vfm {

// ---------------------------------------------------------------------------
// General service status (returned from begin() and error paths)
// ---------------------------------------------------------------------------
enum class ServiceStatus : uint8_t {
    Ok = 0,
    NotInitialized,
    Timeout,
    Jam,
    InvalidData,
};

// ---------------------------------------------------------------------------
// Dispenser state machine states
// ---------------------------------------------------------------------------
enum class DispenseState : uint8_t {
    Idle = 0,
    Lowering,   // M2 driving actuator down until PG2 LOW
    Feeding,    // M1 feeding pellet until PG1 LOW
    Raising,    // M2 driving actuator up to top (step-count target)
    Presented,  // Pellet at top, waiting for mouse
    Taken,      // PG3 fired (dome opened) – brief acknowledgement state
    Fault,      // Timeout / jam; cleared by abort()
};

// ---------------------------------------------------------------------------
// Dispenser events – one event is latched per transition.
// Read with DispenserService::takeEvent(); returns None if no new event.
// ---------------------------------------------------------------------------
enum class DispenseEvent : uint8_t {
    None = 0,
    PelletLoaded,    // PG1 fired: pellet seated in actuator cup
    PelletPresented, // Actuator reached top
    PelletTaken,     // PG3 fired: mouse opened dome
    Fault,           // Timeout or jam detected
};

// ---------------------------------------------------------------------------
// CAN command codes  (byte[0] of every command frame)
// Base -> Node on ID: 0x100 + nodeId   (0x100 = broadcast to all nodes)
// ---------------------------------------------------------------------------
enum class CanCmd : uint8_t {
    Ping      = 0x01,
    Dispense  = 0x02,
    Abort     = 0x03,
    AssignId  = 0x04, // payload byte[1] = new nodeId
    SetConfig = 0x05, // payload TBD
    ReqStatus = 0x06,
    ClearId   = 0x07, // clear NVS id; re-enter discovery (broadcast-friendly)
};

// ---------------------------------------------------------------------------
// CAN event codes  (byte[0] of every event frame)
// Node -> Base on ID: 0x300 + nodeId
// ---------------------------------------------------------------------------
enum class CanEvent : uint8_t {
    PelletLoaded    = 0x01,
    PelletPresented = 0x02,
    PelletTaken     = 0x03,
    Fault           = 0x04,
    Pong            = 0x05,
};

// ---------------------------------------------------------------------------
// SetConfig sub-types (byte[0] of the SetConfig command payload)
// ---------------------------------------------------------------------------
enum class ConfigType : uint8_t {
    HeartbeatInterval = 0x01, // value = uint16 LE, heartbeat interval in ms
};

// ---------------------------------------------------------------------------
// Discovery frame IDs  (used by NodeIdentity during boot)
// ---------------------------------------------------------------------------
constexpr uint32_t CAN_ID_ANNOUNCE = 0x080; // node -> base: MAC(6)
constexpr uint32_t CAN_ID_ASSIGN   = 0x081; // base -> node: MAC(6) + id(1)
constexpr uint32_t CAN_ID_ACK      = 0x082; // node -> base: MAC(6) + id(1)
constexpr uint32_t CAN_ID_REJOIN   = 0x083; // node -> base: MAC(6) + id(1)

// ---------------------------------------------------------------------------
// CAN ID layout
// ---------------------------------------------------------------------------
constexpr uint32_t CAN_CMD_BASE       = 0x100; // 0x100 + nodeId
constexpr uint32_t CAN_CMD_BROADCAST  = 0x100; // nodeId == 0 -> all nodes
constexpr uint32_t CAN_STATUS_BASE    = 0x200; // 0x200 + nodeId
constexpr uint32_t CAN_EVENT_BASE     = 0x300; // 0x300 + nodeId

// ---------------------------------------------------------------------------
// NodeIdentity discovery states
// ---------------------------------------------------------------------------
enum class DiscoveryState : uint8_t {
    WaitAEI,    // Waiting for AEI pin to go HIGH
    CheckNVS,   // AEI is HIGH – check NVS for saved id
    Announce,   // No saved id – sending ANNOUNCE and waiting for ASSIGN
    WaitAssign, // ANNOUNCE sent, waiting for base ASSIGN frame
    Rejoin,     // Saved id found – sending REJOIN
    Enabled,    // Identity resolved – AEO driven HIGH, normal operation
};

} // namespace vfm
