"""Precision sleep for synchronized multi-STA test starts.

## Problem

The orchestrator broadcasts a `start_unix_ms` timestamp to all agents.
Each agent must start iperf3 as close to that timestamp as possible.
The sync_offset_ms = actual_start_ms - target_start_ms must be < 100ms
(Phase 1 requirement), ideally < 5ms to minimize measurement noise.

A naïve `time.sleep(delta)` has two problems:

  1. OS scheduler granularity: Linux/macOS typically wakes up 1–15ms late
     depending on HZ setting and system load. Under load, outliers can reach
     50ms+.

  2. time.time() syscall cost: calling time.time() in a tight loop has
     overhead (~100–500ns per call), which accumulates.

## Solution: Coarse sleep + busy-wait hybrid

                 now        coarse_target      target
                  |               |               |
                  |<── time.sleep ────────────────>|
                  |               |<─ busy-wait ──>|
                                  |                |
                              target-20ms       target

Phase 1 — Coarse sleep:
    time.sleep() from now until (target - BUSY_WAIT_MS).
    Burns zero CPU. May overshoot slightly but that is acceptable
    because we still have the busy-wait window as a safety margin.

Phase 2 — Busy-wait:
    Spin on time.perf_counter() for the final BUSY_WAIT_MS window.
    perf_counter() uses the CPU's hardware timer (HPET/TSC), which has
    nanosecond resolution and much lower syscall overhead than time.time().
    This guarantees we wake up within ~1ms of the target.

## Clock model

    time.time()       — wall clock (epoch), synced to NTP, ~1ms resolution
    time.perf_counter() — monotonic, high-res (ns), NOT synced to NTP

The hybrid uses time.time() for the coarse phase (needs epoch alignment)
and perf_counter() for the busy-wait phase (needs precision, not epoch).
We anchor perf_counter() to the remaining delta computed from time.time()
at the transition point.

## Accuracy expectations

    Condition                      Typical sync_offset
    ─────────────────────────────────────────────────
    NTP synced, idle system        0–2 ms
    NTP synced, moderate load      0–5 ms
    No NTP (clock drift > 1s)      Undefined (test is invalid)

BUSY_WAIT_MS = 20 is chosen to:
  - Cover worst-case time.sleep() overshoot on a loaded RPi (~15ms)
  - Keep CPU spin short enough to not impact iperf3 throughput measurement
    (iperf3 starts ~1ms after busy-wait ends)
"""

import time

BUSY_WAIT_MS = 20


def sleep_until(target_unix_ms: int) -> int:
    """Sleep until target_unix_ms with sub-millisecond precision.

    Args:
        target_unix_ms: Target wakeup time as Unix epoch milliseconds.
                        Must be at least BUSY_WAIT_MS in the future.

    Returns:
        Actual wakeup time as Unix epoch milliseconds.
        sync_offset = return_value - target_unix_ms  (positive = late)
    """
    # Phase 1: coarse sleep (saves CPU, no precision needed here)
    coarse_target_ms = target_unix_ms - BUSY_WAIT_MS
    now_ms = int(time.time() * 1000)
    if coarse_target_ms > now_ms:
        time.sleep((coarse_target_ms - now_ms) / 1000)

    # Phase 2: busy-wait anchored to perf_counter for precision
    # Recompute remaining delta from time.time() at the transition boundary
    remaining_sec = (target_unix_ms - int(time.time() * 1000)) / 1000
    deadline_perf = time.perf_counter() + remaining_sec
    while time.perf_counter() < deadline_perf:
        pass

    return int(time.time() * 1000)
