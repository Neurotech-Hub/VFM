# Hardcoded / tunable values

Living note of firmware constants that may need changing after bench or field tweaks.
**Update this file when you change a default.** Source of truth remains the code; this is the checklist.

Pins (`VFMPins.h`) and CAN ID opcodes (`ServiceTypes.h`) are omitted unless they carry timing or motion meaning.

---

## High-priority (likely to change)

These are the ones called out most often during bring-up.


| Value         | Constant             | Location             | Notes                                                         |
| ------------- | -------------------- | -------------------- | ------------------------------------------------------------- |
| **30 s**      | `kDomeOpenWarnMs`    | `DispenserService.h` | PG3 open continuously → `DomeOpenWarning` (non-sticky)        |
| **5 s**       | `kPg2ClearOnRaiseMs` | `DispenserService.h` | After raise starts, PG2 must clear within this or Fault/`Jam` |
| **1 s**       | `kPg1JamMs`          | `DispenserService.h` | PG1 held (drop detector not a brief pulse) → Jam              |
| **3 s**       | `kPg3EventBlankMs`   | `DispenserService.h` | After PG3 high→low cycle, suppress further PG3 event-log edges |
| **700 steps** | `kDefaultRaiseSteps` | `DispenserService.h` | M2 raise travel from PG2 home; bench default for 28BYJ-48     |


---



## Dispenser — motion defaults

Defined in `src/services/DispenserService.h`. Overridable before `begin()` via setters (`setRaiseSteps`, `setFeedTimeoutMs`, etc.).


| Value       | Constant                 | Meaning                                |
| ----------- | ------------------------ | -------------------------------------- |
| 500 steps/s | `kDefaultMotorSpeed`     | AccelStepper commanded speed (M1/M2)   |
| 2048 steps  | `kDefaultLowerSteps`     | Max seek-away / approach budget for M2 |
| 700 steps   | `kDefaultRaiseSteps`     | M2 up travel from PG2 home             |
| 4096 steps  | `kDefaultFeedMaxSteps`   | M1 max steps before feed timeout path  |
| 8 s         | `kDefaultLowerTimeoutMs` | M2 lower / seek-away phase timeout     |
| 30 s        | `kDefaultFeedTimeoutMs`  | M1 pellet load timeout                 |
| 8 s         | `kDefaultRaiseTimeoutMs` | M2 raise phase timeout                 |
| 20 ms       | `kPGDebounceMs`          | Photogate debounce (PG1/PG2/PG3)       |


Not overrideable via SetConfig CAN yet — only compile-time / setter before begin.

---



## Dispenser — jam / warning / blanking

Same header; **not** runtime-configurable via CAN today.


| Value | Constant             | Trigger                                          |
| ----- | -------------------- | ------------------------------------------------ |
| 1 s   | `kPg1JamMs`          | PG1 stuck HIGH → Jam                             |
| 5 s   | `kPg2ClearOnRaiseMs` | PG2 still blocked after raise start → Jam        |
| 30 s  | `kDomeOpenWarnMs`    | PG3 held open → DomeOpenWarning                  |
| 3 s   | `kPg3EventBlankMs`   | After PG3 high→low, blank next `AccessAttempt` / `InputChanged` |


---



## CAN / discovery


| Value  | Constant                      | Location         | Notes                                                                       |
| ------ | ----------------------------- | ---------------- | --------------------------------------------------------------------------- |
| 5 s    | `kDefaultHeartbeatIntervalMs` | `CanService.h`   | Default status heartbeat; **runtime** via `SetConfig` / `HeartbeatInterval` |
| 500 ms | `kAnnounceRetryMs`            | `NodeIdentity.h` | Retry ANNOUNCE while awaiting ASSIGN                                        |
| 5 s    | `kDiscoveryTimeoutMs`         | `NodeIdentity.h` | Give up waiting for ASSIGN                                                  |


---



## UI / LED / button (`VFM`)


| Value          | Where                                 | Notes                                                                         |
| -------------- | ------------------------------------- | ----------------------------------------------------------------------------- |
| 3 s            | `VFM` ctor → `btnHoldMs_(3000)`       | Hold to arm NVS clear (`VFM.h` in-class default `1000` is overridden by ctor) |
| 100 ms         | `VFM.cpp` LED9 blink while hold armed | Rapid blink warning                                                           |
| 1.5 s / 150 ms | `kPingBlinkMs` / `kPingBlinkPeriodMs` | Status LED “which node” blink on Ping                                         |
| 500 ms         | LED9 blink at boot                    | Fast blink = booting                                                          |
| 1 s            | LED9 / status blink                   | Slow = waiting for discovery                                                  |
| 40             | `touchThreshold_`                     | Capacitive presence threshold                                                 |
| 100 ms         | `flashLedsClear()` delays             | Visual confirm of NVS clear                                                   |


---



## How to change

1. Edit the `constexpr` (or ctor default) in the file listed above.
2. Rebuild / flash the node firmware.
3. Update the corresponding row in this document.
4. If the value becomes experiment- or site-specific, prefer a setter / `SetConfig` path so nodes do not need a reflash.

