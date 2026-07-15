#pragma once

#include <Arduino.h>

namespace vfm {

// ---------------------------------------------------------------------------
// CAN (TWAI)
// ---------------------------------------------------------------------------
constexpr uint8_t PIN_CAN_TX = 33; // CAN_Tx  -> TWAI TX
constexpr uint8_t PIN_CAN_RX = 13; // CAN_Rx  -> TWAI RX

// ---------------------------------------------------------------------------
// Daisy-chain addressing (AEO/AEI)
// ---------------------------------------------------------------------------
// AEI: Address Enable In  – driven HIGH by the upstream node's AEO (or base).
// AEO: Address Enable Out – driven HIGH by this node once identity is resolved,
//      enabling the next downstream node to participate in discovery.
constexpr uint8_t PIN_AEI = 14; // CAN_IO_14  INPUT_PULLDOWN
constexpr uint8_t PIN_AEO = 47; // CAN_IO_47  OUTPUT (default LOW)

// Spare CAN-RJ45 GPIOs – reserved for future use
constexpr uint8_t PIN_AUX_CAN_A = 12; // CAN_IO_12
constexpr uint8_t PIN_AUX_CAN_B = 26; // CAN_IO_26

// ---------------------------------------------------------------------------
// Motor 1 – Pellet feeder (28BYJ-48 via ULN2003)
// Wire colours: Orange / Yellow / Pink / Blue
// AccelStepper HALF4WIRE argument order: (A1, A3, A2, A4) = O, P, Y, B
// Energise sequence: Orange -> Pink -> Yellow -> Blue (verify on bench)
// ---------------------------------------------------------------------------
constexpr uint8_t PIN_M1_A1 = 35; // Orange
constexpr uint8_t PIN_M1_A2 = 36; // Yellow
constexpr uint8_t PIN_M1_A3 = 37; // Pink
constexpr uint8_t PIN_M1_A4 = 38; // Blue

// ---------------------------------------------------------------------------
// Motor 2 – Actuator / elevator (28BYJ-48 via ULN2003)
// Same wire colour convention as M1
// ---------------------------------------------------------------------------
constexpr uint8_t PIN_M2_A1 = 40; // Orange
constexpr uint8_t PIN_M2_A2 = 41; // Yellow
constexpr uint8_t PIN_M2_A3 = 42; // Pink
constexpr uint8_t PIN_M2_A4 = 43; // Blue

// ---------------------------------------------------------------------------
// Photogates
// PG1 / PG2: beam break = pin LOW = triggered (INPUT_PULLUP)
// PG3:      idle LOW; dome open = pin HIGH = triggered (INPUT_PULLDOWN)
//
// NOTE: GPIO45 and GPIO46 are ESP32-S3 strapping pins (VDD_SPI / boot mode).
// They are safe to use as GPIO after boot; ensure external pull resistors
// do not force unsafe logic levels at reset.
// ---------------------------------------------------------------------------
constexpr uint8_t PIN_PG1 = 46; // trigger = LOW  – strapping pin, see note above
constexpr uint8_t PIN_PG2 = 45; // trigger = LOW  – strapping pin, see note above
constexpr uint8_t PIN_PG3 = 44; // trigger = HIGH (dome open)

// ---------------------------------------------------------------------------
// Sensing / user IO
// ---------------------------------------------------------------------------
constexpr uint8_t PIN_TOUCH    = 5;  // Capacitive touch (touch sensor input)
constexpr uint8_t PIN_STATUS_LED = 39; // RGB / status LED
constexpr uint8_t PIN_LED_IO_9   = 9;  // LED_IO_09
constexpr uint8_t PIN_LED_IO_10  = 10; // LED_IO_10
constexpr uint8_t PIN_BTN        = 11; // BTN_IO_11
constexpr uint8_t PIN_USER_IO_6  = 6;  // User_IO_6
constexpr uint8_t PIN_PGX        = PIN_USER_IO_6; // alternate PG2 bring-up input
constexpr uint8_t PIN_USER_IO_7  = 7;  // User_IO_7
constexpr uint8_t PIN_POWER_ST   = 15; // Power_ST

} // namespace vfm
