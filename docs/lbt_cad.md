# LBT and CAD

> Listen Before Talk and Channel Activity Detection in the WM1303 system

## Overview

The WM1303 system implements two channel-sensing mechanisms to reduce collisions before transmitting:

- **CAD (Channel Activity Detection)** — LoRa preamble detection on the SX1261, attempted by both native TX paths for supported bandwidths
- **LBT (Listen Before Talk)** — **Optional** per-channel RSSI-based check that runs after CAD when enabled

Pre-TX CAD has no user toggle. LBT can be enabled per channel. The Python pre-TX
RSSI filter has been removed; the native HAL performs the RSSI decision.

## CAD — Channel Activity Detection

### Mandatory Hardware CAD

Both the immediate and JIT paths in `lora_pkt_fwd.c` attempt CAD before sending
LoRa packets with a supported bandwidth. Supported CAD settings are SF7–SF12
with 62.5, 125, 250 or 500 kHz bandwidth. A scan error records `scan_error` and
allows TX to continue; an unsupported bandwidth records `unsupported_bw` and
skips the scan. A successful transmission therefore does not establish that a
scan found the channel clear.

### How It Works

1. **Inhibit SX1261 LoRa RX** — the auxiliary receiver is paused for the CAD scan
2. **CAD Scan** — the SX1261 performs a LoRa preamble detection scan on the exact TX frequency, spreading factor, and bandwidth
3. **Result** —
   - **Clear**: TX proceeds immediately (IMMEDIATE mode)
   - **Detected**: retry up to 15 times, choosing a random 1–10 ms wait each time
   - **After 15 retries**: continue to TX with `forced_after_retries`; enabled HAL LBT still applies
   - **Scan error**: continue to TX with `scan_error`
4. **Resume RX** — SX1261 RX is restarted between busy-channel waits and after the TX guard

### SX1261 / SX1302 Interference Resolution

During development, a critical hardware interaction was discovered: configuring the SX1261 for LoRa CAD mode interferes with the SX1302's TX state machine, particularly on narrow-bandwidth channels (BW 62.5 kHz). This was resolved through several techniques:

| Issue | Solution |
|-------|----------|
| Race condition between `spectral_scan_abort()` and `SET_STANDBY` | **Abort + 5 ms delay + Standby** sequence prevents SX1261 state corruption |
| SX1261 state not fully restored after CAD | **GPIO hardware reset with PRAM reload** after each CAD scan |
| PRAM reload taking ~460 ms | **Bulk PRAM write** — single SPI transfer instead of 386 individual writes, reducing to ~42 ms |
| `uint8_t` overflow bug | Fixed firmware upload that truncated 1546-byte transfer to 11 bytes |
| Stale timestamps after CAD | **IMMEDIATE TX mode** instead of TIMESTAMPED, preventing SX1302 TX FSM stuck at `TX_SCHEDULED (0x91)` |
| Stuck TX FSM | **TX abort** (`lgw_abort_tx()`) when `TX_SCHEDULED` state detected before sending |

### CAD Timing Breakdown

The following are historical hardware measurements recorded during earlier
development. They have not been remeasured during the hardware-independent
maintenance campaign and do not establish current per-packet latency:

| Phase | Channel A (SF7 BW125) | Channel E (SF8 BW62.5) |
|-------|----------------------|------------------------|
| Abort (spectral scan) | 4.3 ms | 4.2 ms |
| Setup (frequency, modulation) | 6.5 ms | 6.5 ms |
| CAD scan (preamble detection) | 7.5–14 ms | 18–27 ms |
| Reinit (GPIO reset + PRAM) | 18.1 ms | 18.1 ms |
| **Total** | **~37–43 ms** | **~47–56 ms** |

Channel E takes longer due to its narrower bandwidth (BW 62.5 kHz) requiring more symbols for preamble detection.

### Current CAD Retry Delays

Both native TX paths allow an initial scan plus 15 retries. Each retry wait is
`1 + rand() % 10` milliseconds, so exhausting all retries adds 15–150 ms of
requested sleep. That excludes scan duration, mutex contention and scheduling
overhead. Earlier fixed 50/100/200/300/400 ms retry tables no longer describe
the code. CAD force-through does not bypass an enabled HAL LBT decision.


### Historical CAD Timing Optimizations

| Optimization | Before | After | Savings |
|-------------|--------|-------|---------|
| Bulk PRAM write | 460 ms | 42 ms | 418 ms |
| Abort delay reduction | 5 ms | 2 ms | 3 ms |
| Skip full calibrate in reinit | 10 ms | 0 ms | 10 ms |
| Calibrate wait reduction | 10 ms | 4 ms | 6 ms |
| Skip `sx1261_calibrate()` | 7 ms | 0 ms | 7 ms |
| TCXO wait reduction | 5 ms | 2 ms | 3 ms |
| Image calibrate wait reduction | 5 ms | 2 ms | 3 ms |
| **Total overhead reduction** | **~500+ ms** | **~37–56 ms** | **~90% faster** |

### CAD Event Tracking

CAD events are recorded per channel with two outcomes:

- **Clear** — no LoRa preamble detected, TX proceeds
- **Detected** — LoRa activity remained after CAD retries; the CAD gate allowed TX to proceed

Events are stored in `repeater.db` (`cad_events` table) and displayed in the Spectrum tab's **CAD Activity** chart. The chart shows Clear (green) and Detected (red) counts per channel.

Use the final TX result and CAD reason in packet traces alongside these counts.
CAD detection alone does not confirm RF transmission, and `scan_error` does not
mean a clear channel was observed.

## LBT — Listen Before Talk

### HAL Per-Channel RSSI Check

The RSSI gate runs inside `lgw_send()` through the HAL's SX1261 LBT functions.
The former custom software RSSI check is disabled. Hardware effectiveness and
timing still require measurements on the target radio.

### How It Works

1. HAL LBT is globally enabled when at least one active TX channel requests it.
2. Channel selection prefers matching bandwidth within the HAL's ±10 kHz frequency tolerance; 62.5 kHz TX can fall back to a 125 kHz scan entry.
3. The selected channel's RSSI threshold is supplied to the SX1261 LBT scan and AGC handshake.
4. A busy result rejects that send and produces a blocked TX acknowledgement.
5. If another channel has LBT disabled while global LBT is enabled, its handshake uses a permit-all threshold. When no channel enables LBT, HAL LBT is skipped globally.

The generated configuration uses a 128 µs scan setting and a 4,000 ms transmit
time limit. These settings are not measurements of total LBT latency. Enabling
LBT alongside an active 500 kHz TX channel is rejected by configuration validation.

### Configuration (per channel)

| Parameter | Description | Default |
|-----------|-------------|---------|
| `lbt_enabled` | Enable/disable LBT for this channel | `false` |
| `lbt_threshold` | RSSI threshold in dBm | `-80` |

Both parameters are configurable per channel via the **Channels** tab in the WM1303 Manager UI.

### Timing Impact

Measure end-to-end latency on hardware. The previous custom-check estimate of
47 ms does not describe the current HAL path; a disabled logical channel can
still participate in the handshake when another channel enables global LBT.

### LBT Event Tracking

LBT events are recorded per channel with frequency and RSSI values, stored in `repeater.db` and displayed in the Spectrum tab's **LBT History** chart.

## TX Delay Elimination

The default Python and per-rule TX delays were set to zero in v2.1.0. Users can
configure Python jitter; native CAD retries also have their own random waits:

| Parameter | Before | After |
|-----------|--------|-------|
| `tx_delay_factor` | 1.0 | **0.0** |
| `direct_tx_delay_factor` | 0.5 | **0.0** |
| Per-rule `tx_delay_ms` | variable | **0 ms** |
| Python airtime guard | duplicated check | **removed** |

## TX Hold Behavior

| Hold Type | Status | Duration | Purpose |
|-----------|--------|----------|---------|
| CAD scan | Attempted for supported LoRa settings | Hardware-dependent | Preamble detection before sending |
| CAD retry wait | Active on detection | 1–10 ms per retry, at most 15 retries | Wait for channel to clear |
| HAL LBT check | Optional per channel | Hardware-dependent | RSSI-based channel assessment |
| **Python pre-TX check** | ❌ Removed (v2.1.1) | — | Was: software LBT/CAD check before PULL_RESP (redundant with C-level CAD) |
| Queue depth hold after RX | Active | 50 ms with one pending bulk packet; otherwise zero | Brief dedup window |
| Noise floor hold | ❌ Removed | — | Was: pause TX for noise measurement |

### Design Principles

The TX pipeline follows the project's core design principles:

- **RX availability is the #1 priority** — CAD + TX duration is minimized to restore RX as quickly as possible
- **TX should proceed promptly** — default Python random delays are zero; native CAD retries have bounded random waits
- **TX duration must be as short as possible** — IMMEDIATE TX mode, optimized CAD cycle

## SPI Considerations

- The SX1261 operates on a **separate SPI bus** (`/dev/spidev0.1`) from the SX1302 concentrator
- SPI clock runs at optimized speed for minimal transfer overhead
- Bulk PRAM write uses a single SPI transaction for the entire 1546-byte firmware image
- The separate SPI paths mean CAD/LBT operations on the SX1261 do not block SX1302 RX data transfer

## UI Behavior

### CAD Display

CAD is always active and has no user toggle. The **Spectrum** tab shows:

- **CAD Activity chart** — per-channel Clear (green) and Detected (red) counts
- Explanatory text below the chart describes what Clear and Detected mean

### LBT Controls

Available on all channels (A–F) in the **Channels** tab:

- Toggle to enable/disable LBT per channel
- RSSI threshold slider (when enabled)
- Save the settings and restart the repeater to apply the native HAL configuration

### Charts

| Chart | Data Source | Database | Shows |
|-------|------------|----------|-------|
| CAD Activity | `_packet_activity_recorder` (Python stats) | `repeater.db` → `cad_events` | Per-channel Clear / Detected counts |
| LBT History | `_packet_activity_recorder` (Python stats) | `repeater.db` | Per-channel LBT events with frequency and RSSI |
| Noise Floor | `_packet_activity_recorder` | `repeater.db` → `noise_floor` | Per-channel noise floor values over time |

Channel E is shown in **orange** in all charts.

> **Data Architecture Note:** CAD and LBT chart data is sourced exclusively from Python-level statistics in `repeater.db`. The `spectrum_collector.py` log parsers for CAD/LBT have been removed. Spectral scan data remains in `spectrum_history.db`.

## Noise Floor Interaction

Noise floor values provide context for LBT threshold decisions:

- Per-channel noise floor tracking shows the baseline signal level
- LBT threshold should be set above the noise floor to avoid false busy detections
- Noise floor is measured by the NoiseFloorMonitor without pausing TX (uses TX-free window detection with retry logic)

### Noise Floor Sources (Fallback Chain)

1. **Spectral scan data** — Primary, from SX1261 continuous scan
2. **SX1261 RSSI point measurement** — Secondary
3. **RX packet-based estimation** — Last resort

All values are persisted to `repeater.db` and exposed through API and WebSocket.

## Metrics Retention

| Database | Retention | Relevant Tables |
|----------|-----------|----------------|
| `repeater.db` | 8 days | `cad_events`, `noise_floor`, `noise_floor_history`, `packet_activity` |
| `spectrum_history.db` | 7 days | `spectrum_scans` |

## Troubleshooting

### CAD always showing "Detected"
- This may indicate persistent LoRa activity on the channel
- Check the Noise Floor chart for elevated signal levels
- Verify there are no nearby transmitters on the same frequency
- Inspect the CAD reason and final TX result: after 15 retries CAD permits sending, but HAL LBT or another send failure can still prevent it

### LBT blocking all TX
- Check noise floor values — if stuck at `-120 dBm`, scan data may not be updating
- Verify SX1261 is operational (check system logs for SPI errors)
- LBT threshold may be set too low — raise it above the noise floor
- Inspect the HAL LBT result and threshold in packet traces; an unavailable RSSI sample is not a measured clear channel

### High CAD retry rate
- Channel may be genuinely busy with LoRa traffic
- Check if other devices are transmitting on the same frequency/SF/BW
- Monitor the CAD Activity chart for patterns

### TX latency seems high
- Check whether HAL LBT reports blocked sends or missing observations
- Check CAD retry counts — retries add scan time plus random 1–10 ms waits
- Review the packet forwarder logs for timing details

## Related Documents

- [`tx_queue.md`](./tx_queue.md) — TX queue system
- [`radio.md`](./radio.md) — Radio architecture and RF chains
- [`channel_e_sx1261.md`](./channel_e_sx1261.md) — Channel E / SX1261 specifics
- [`configuration.md`](./configuration.md) — Configuration files
- [`ui.md`](./ui.md) — WM1303 Manager UI
- [`architecture.md`](./architecture.md) — System architecture
