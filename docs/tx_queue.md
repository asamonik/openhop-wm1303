# TX Queue System

> Per-channel TX queue architecture, scheduling, and hold behavior

## Overview

The WM1303 system uses **per-channel TX queues** managed by a `GlobalTXScheduler`. Each active TX channel (A–F) has its own queue instance that handles buffering, gating, and fair transmission scheduling.

Python enforces queue limits, deadlines and the shared airtime budget. The native
packet forwarder attempts LoRa CAD and applies configured HAL LBT before sending.
Default Python TX jitter is zero; busy CAD scans can add up to 15 random 1–10 ms
retry waits, plus scan time. There is no fixed two-second TX batch window.

## Architecture

```
Bridge Engine
    → Per-channel TX Queue instances
        ├── Channel A Queue
        ├── Channel B Queue
        ├── Channel C Queue
        ├── Channel D Queue
        ├── Channel E Queue
        └── Channel F Queue
    → GlobalTXScheduler
        → Fair round-robin scheduling
        → Per-queue gating (TTL, overflow)
        → PULL_RESP (UDP :1730) → Packet Forwarder (C)
            → CAD attempt for supported LoRa settings (SX1261)
            → Optional LBT check (if enabled per channel)
            → IMMEDIATE TX → Radio
```

## Queue Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| Queue depth | 15 packets per lane by default | Each channel has a bulk lane and a separately bounded ACK lane |
| TTL per packet | 60 seconds by default | Configurable via `tx_packet_ttl_seconds`; retained through retries |
| Queue depth hold after RX | 50 ms | Applies with exactly one pending bulk packet; otherwise zero |
| TX delay factor | 0.0 (default) | Random pre-TX jitter; set to 0 since v2.1.0 |

While queued, callers wait at most 15 seconds for priority ACKs or the configured
TTL plus 15 seconds for bulk traffic. Once the scheduler dispatches a packet,
its caller gets a separate deadline of the estimated airtime plus 15 seconds.
Long valid transmissions therefore keep their caller without extending the
pre-dispatch queue wait. Cancellation and the original packet TTL still apply
to queued work and retries.

## TX Pipeline

The full TX path from enqueue to air:

1. **Bridge Engine** enqueues packet to the appropriate channel queue (origin channel first since v2.1.1)
2. **GlobalTXScheduler** inspects each channel's head packet, preferring its ACK lane
3. **Python gating checks** enforce TTL, cancellation, shared duty budget and current TX holds
4. **PULL_RESP** sends eligible work to the packet forwarder via UDP (:1730)
5. **Packet forwarder (C)** attempts CAD on SX1261, then applies HAL LBT if enabled
6. **Radio TX** uses the concentrator's TX path for all channels, including E/F

The former Python RSSI check based on cached spectral/noise data is removed.
Python queue and duty controls remain active. CAD retry sleep alone is not an
upper bound on end-to-end latency.

## Origin-Channel-First TX Priority (v2.1.1)

When the Bridge Engine forwards a received packet to multiple target channels via bridge rules, the **originating channel gets TX priority** — it is enqueued and transmitted first.

Example: packet received on Channel E → forwarded to A, B, C, E via bridge rules → Channel E is sent **first**, then A, B, C in their normal order.

This reduces repeat latency for the node that originally sent the packet. The `origin_channel` parameter is tracked through `bridge_engine.py` (`inject_packet()` → `_forward_by_rules()`) and `main.py` (`_bridge_repeater_handler()`). The GlobalTXScheduler's round-robin fairness is unaffected — origin priority only controls the enqueue order within a single bridge forwarding event.

### Python → C Overhead

Earlier development notes recorded a reduction from roughly 62 ms to 8 ms in
Python-to-C queue wait time after these changes:

- JIT thread poll interval reduced from 10 ms to **1 ms**
- Redundant hardware status checks eliminated
- Mutex locks consolidated

These historical figures were not remeasured during software-only maintenance.

## Scheduling

### Fair Round-Robin

The `GlobalTXScheduler` uses a **rotating start index** to ensure fair access across channels:

1. On each scheduling cycle, iterate through all channel queues
2. Start from a different channel each cycle (rotating index)
3. For each queue, check if the head packet can be sent (gating checks)
4. If yes, dequeue and send via PULL_RESP
5. Track TX statistics per channel

This prevents one busy channel from starving others.

The native immediate and JIT workers also share a TX lock. It covers CAD retries,
sending and RX restoration, while the concentrator mutex is released during
waits so receiver work can proceed. A JIT attempt requires confirmed `TX_FREE`;
unknown/off/busy status or unsuccessful abort recovery produces a failed-send
acknowledgement before CAD or transmission starts.

### Gating Checks

Gating is split between the Python TX queue and the C packet forwarder:

#### Python-Level (TX Queue)

| # | Check | Action on Fail |
|---|-------|----------------|
| 1 | **TTL** | Packet expired → discard |
| 2 | **Queue overflow** | Apply the configured `drop_oldest` or `drop_newest` policy |
| 3 | **Shared duty budget** | Keep the packet queued; examine other channels |
| 4 | **Random delay / RF hold / inter-packet delay** | Keep the packet queued while its TX delay or hold remains active |

#### C-Level (Packet Forwarder)

| # | Check | Action on Fail |
|---|-------|----------------|
| 1 | CAD for supported LoRa settings | Activity detected → up to 15 retries with random 1–10 ms waits → permit sending; scan errors also permit sending |
| 2 | HAL LBT (optional, per channel) | Busy result → reject the send and report a blocked TX acknowledgement |

Exhausting CAD retries adds 15–150 ms of requested sleep plus scan and scheduling
time. CAD force-through does not bypass enabled HAL LBT.
See [`lbt_cad.md`](./lbt_cad.md) for full details on CAD and LBT behavior.

### RF-Chain Guard

Python initially reserves the estimated packet airtime plus two seconds from
dispatch. A final `sent` acknowledgement means the native TX completion poll
observed `TX_FREE`; Python then replaces that fallback with a 50 ms margin from
receipt. A confirmed JIT drop clears the reservation immediately, and other
confirmed refusals retain a 50 ms margin. Missing acknowledgements and
`tx_unconfirmed` keep the original conservative reservation.

The native transaction mutex covers CAD waits, sending, completion polling and
SX1261 RX restoration across both immediate and JIT send paths. During waits,
the concentrator mutex remains available for RX work. Completion polling uses
the HAL airtime estimate plus a 100 ms allowance, checking every 2 ms after an
initial 5 ms delay; shutdown interrupts the wait. A timeout or interrupted wait
reports `tx_unconfirmed`, not a successful transmission. Actual recovery when
hardware fails to report completion still needs testing on the target radio.

The native JIT queue stores airtime reservations in 32-bit microseconds. It
rejects zero/invalid estimates and durations beyond that representation;
immediate sends use the wider HAL timing calculation. Timestamped downlinks,
calculated ASAP slots and beacon reservations must fit the existing window of less than 512 seconds
ahead. Collision checks include reservation margins without wrapping their sums.

Optional beacon replenishment makes at most 32 attempts per receive iteration,
returns promptly when the queue is full, and honors shutdown and GPS failures.
After GPS recovery it starts from the current reference instead of iterating
through every missed slot. Beacon timestamps and corrected frequencies are
checked before narrowing conversions; the sent counter advances only after
confirmed TX completion. The generated WM1303 configuration leaves beacons disabled.

A separate short queue-access lock protects enqueue with ACK-token publication,
and selection with dequeue and token claim. It releases before CAD/TX waits.
Pending tokens stay valid while their packet remains queued, including future
scheduled transmissions. The worker owns the token once dequeued; a periodic
sweep reports entries removed by JIT expiration. Token timestamps match exactly,
including zero when the concentrator counter wraps.

Failed concentrator counter reads propagate through the HAL as errors without
changing the caller's output value. New queue admissions reject unavailable
counters; the JIT worker leaves queued packets and ACK tokens untouched until
a valid read permits scheduling or expiration. A valid zero at timer wrap
remains distinguishable from failure.

## TX Delay Elimination

Since v2.1.0, mandatory CAD handles collision avoidance, replacing all random TX delays:

| Parameter | Before | After (v2.1.0) |
|-----------|--------|----------------|
| `tx_delay_factor` | 1.0 | **0.0** |
| `direct_tx_delay_factor` | 0.5 | **0.0** |
| Per-rule `tx_delay_ms` | variable | **0 ms** |
| Extra random delay | configured separately | Default zero; RF busy guard remains active |

Users can still increase `tx_delay_factor` in **Adv. Config → TX Queue Management** for specific use cases.

## RX-triggered TX Hold

The backend may briefly hold TX after receiving a packet. It checks the total
pending bulk queue depth: one pending packet gives a 50 ms hold, while zero or
two or more pending packets clear the hold. This is separate from duty-budget,
radio-busy and configured random-delay checks. There is no fixed two-second
window that every multi-channel forwarding operation must wait through.

## TX Hold Behavior

| Hold Type | Status | Duration | Purpose |
|-----------|--------|----------|---------|
| CAD scan | Attempted for supported LoRa settings | Hardware-dependent | Preamble detection (C layer) |
| CAD retry wait | Active on detection | 1–10 ms per retry, up to 15 retries | Wait for channel to clear |
| Queue depth hold after RX | ✅ Active | 50 ms with one pending bulk packet; otherwise zero | Brief dedup window |
| Noise floor hold | ❌ Removed | — | Was: pause TX for noise measurement |

The noise floor hold was removed because it violated the "TX ASAP" design principle — the SX1261 spectral scan runs on a separate SPI bus and the NoiseFloorMonitor now waits for TX-free windows with retry logic.

## Noise Floor Integration

Each TX queue receives per-channel noise floor values from the NoiseFloorMonitor:

- Values stored in a rolling buffer (20 samples)
- Used for displayed noise-floor context; HAL LBT measures RSSI for its own gate
- Updated every 30 seconds (monitor interval)
- **Does NOT pause the queue** — values are fed asynchronously

## TX Statistics

Per-channel TX statistics tracked:

| Metric | Description |
|--------|-------------|
| `total_sent` | Packets successfully sent |
| `priority_sent` | Successful sends from the ACK lane |
| `dropped_ttl` | Packets expired before send |
| `dropped_overflow` / `dropped_priority_overflow` | Packets dropped due to full bulk / ACK lanes |
| `lbt_blocked` | TX attempts blocked by LBT |
| `cad_detected` | Final CAD scan still busy after retries; inspect the separate TX result |
| `cad_retry_total` | CAD retries across attempts, including attempts that eventually cleared |

### Duty Cycle Calculation

All enabled TX channels, including E/F, share the scheduler's airtime budget.
The displayed aggregate includes each successful send once.

### Duty-cycle enforcement

The repeater binds its live airtime manager to the global TX scheduler before
starting forwarding. All enabled channels, including E/F and room-server
transmissions, share the configured rolling 60-second budget. For example,
10% permits 6,000 ms of airtime in that window. Airtime is calculated from each
destination channel's SF, bandwidth, coding rate and preamble.

When a packet cannot fit the remaining budget, it stays in its bounded queue
within its original TTL. The scheduler inspects its head without removing or
reordering it, so other channels and same-channel ACKs can proceed when eligible.
Held packets remain part of queue-drain accounting. Expired or cancelled requests
never reach the radio. Live changes to the
limit or enforcement switch apply without clearing transmission history.
Successful transmissions update the same counter displayed by the Console;
dispatcher traffic is not counted twice. Missing acknowledgements and native
`tx_unconfirmed` results are charged conservatively, while confirmed LBT/JIT
refusals consume no airtime. The budget
is held in memory and resets with the daemon. It is shared conservatively even
when enabled channels use different sub-bands.

The rolling window uses elapsed monotonic time, so wall-clock corrections do
not erase or extend recent airtime. Recording and statistics snapshots share a
lock. Each recording also prunes old history, including when enforcement is off
and no dashboard is reading statistics; lifetime totals remain cumulative.

This duty budget is separate from the HAL's CAD/LBT checks and RF busy guard.
Scheduler statistics expose `duty_cycle_waits` for packets delayed by the budget.

## Packet Activity Recording

TX events (including CAD results, LBT checks, and packet delivery) are recorded by the `_packet_activity_recorder` in `repeater.db`:

| Table | Data |
|-------|------|
| `packet_activity` | Per-packet TX events with timing and channel info |
| `cad_events` | Per-channel CAD clear/detected counts |
| `dedup_events` | Deduplication statistics |

All data is automatically cleaned up after **8 days** (retention policy).

## Socket Recovery

The TX path includes automatic socket recovery:

- If UDP send fails, the socket is recreated
- The send is retried once after socket recreation; persistent failure returns a failed result
- Socket errors are logged for diagnostics

## Channel E/F TX

E/F use the same queue and concentrator TX path as A–D. Channel E's SX1261
receive path does not make the SX1261 the transmitter:

- Supports sub-125 kHz bandwidths (62.5 kHz)
- TX power configurable per channel
- CAD timing depends on spreading factor and bandwidth and requires hardware measurement
- Same native CAD attempt and optional HAL LBT flow as Channels A–D

## Design Principles

1. **TX ASAP** — Zero default Python jitter; bounded native CAD retry waits
2. **RX priority** — CAD + TX duration minimized to restore RX as quickly as possible
3. **Fairness** — Round-robin scheduling prevents channel starvation
4. **Safety** — TTL and overflow prevent unbounded queue growth
5. **Hardware channel assessment** — CAD and optional HAL LBT report their outcomes separately from TX success

## Related Documents

- [`lbt_cad.md`](./lbt_cad.md) — LBT and CAD behavior
- [`radio.md`](./radio.md) — Radio architecture
- [`architecture.md`](./architecture.md) — System architecture
- [`software.md`](./software.md) — Software components
- [`configuration.md`](./configuration.md) — Configuration reference
