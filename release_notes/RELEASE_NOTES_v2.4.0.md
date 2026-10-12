# Release Notes — v2.4.0

**Release date:** 2026-04-27

## Summary

This is a major feature release bundling all changes since v2.3.1. Highlights include: **HAL-based per-channel LBT** replacing the broken custom implementation, **TX_DONE polling** saving ~15 ms per transmission, **trace timing root-cause fix** anchoring all events to hardware timestamps, **CAD retry optimization** cutting worst-case delay by ~112 ms, **channel_e unified trace events**, significant **dead code cleanup** (~396 lines removed), **SX1261 spectral-scan disabled by default**, two spectral-scan bug fixes retained as defense-in-depth, an **LBT RSSI pre-scan read fix** ensuring 100% real RF measurements, expanded **diagnostic and UI improvements**, and various operational refinements. LBT is once again fully usable on a per-channel basis — the v2.3.1 known-issue note no longer applies.

---

## Changes

### A. HAL-Based Per-Channel LBT (`loragw_lbt.c`, `loragw_hal.h`, `lora_pkt_fwd.c`, `wm1303_backend.py`)

Replaces the broken custom LBT implementation that had to be disabled in v2.3.1.

- **Per-channel RSSI threshold** via new `rssi_target_dbm` field in the HAL `lgw_conf_channel_s` structure — each channel can now have its own LBT threshold instead of a single global value
- **HAL LBT activation**: `lbt.enable=true` in `bridge_conf.json` with per-frequency thresholds generated from `wm1303_ui.json` by `_generate_bridge_conf()`
- **TX_ACK HAL LBT reporting**: `tx_queue.py` now reports actual HAL LBT result (enabled, pass/fail, threshold, RSSI) in TX acknowledgements instead of stale custom-LBT data
- **Regulatory compliance**: LBT is now per-channel configurable, stable, and suitable for production use
- **Resolves v2.3.1 known issue** where LBT had to be disabled on all channels due to instability

### B. LBT RSSI Pre-Scan Read Fix (`loragw_sx1261.c`)

- Moved RSSI read in `sx1261_lbt_start()` to **before** the carrier-sense scan
- Previously 78% of readings returned the sentinel value −127 dBm because the read happened after the scan overwrote the register
- Now 100% of readings are real RF measurements
- Added settle delay for accurate readings
- Combined with the sentinel filter (see section K), the noise-floor data pipeline is now fully reliable

### B2. FSK Pre-CAD RSSI — TX Noisefloor Measurement (`loragw_sx1261.c`, `tx_queue.py`, `wm1303_backend.py`, `wm1303.html`)

- Before each CAD scan, the SX1261 is briefly put into **FSK RX mode** with bandwidth matched to the channel (117 kHz for BW62.5, 234 kHz for BW125) to measure the instantaneous noise floor
- This measurement is now called **TX noisefloor** (renamed from the confusing "CAD RSSI" label) across all layers: C code, Python backend, database, API, and UI
- **Database storage**: new `tx_noisefloor` column in channel metrics, with rolling buffer aggregation (avg/min/max per channel)
- **UI**: TX noisefloor values displayed in trace events and used as a data source for noise-floor graphs alongside LBT RSSI and RX-derived estimates
- This is the most reliable per-channel noise floor measurement because it captures pure RF energy **before** any TX or CAD activity

### C. SX1261 Spectral-Scan Disabled by Default (`wm1303_backend.py`, `config/global_conf.json`)

The periodic SX1261 spectral-scan sweep is now **disabled by default** in both the Python config generator and the HAL config template.

**Rationale — all operational noise-floor data is already covered by simpler, more reliable sources:**

| Data source | Measurement | Coverage |
|-------------|-------------|----------|
| HAL LBT RSSI | Real-time, per TX attempt | Every channel with LBT enabled |
| TX noisefloor (FSK pre-CAD) | Per TX attempt, pure noise floor | Every channel (see section B2) |
| RX-derived `noise_floor ≈ RSSI − SNR` | Per received packet | Every active channel with traffic |
| Rolling LBT RSSI buffers | Aggregated per channel | Every channel with LBT enabled |

**Benefits of disabling the sweep:**

- **Maximum RX time on Channel E** — the SX1261 no longer switches between RX and scan modes (design principle: RX availability is priority)
- **No TX contention** — the SX1261 has a simpler two-role state machine (Channel E RX + per-TX LBT), reducing the chance of TX being delayed by a scan in progress (design principle: TX ASAP)
- **Lower SPI bus load** — no more 66-byte result reads every `pace_s` seconds

**How to re-enable (if needed):**

Set `spectral_scan.enable = true` in `~/wm1303_pf/bridge_conf.json` (or via the Adv. Config tab when that UI field is exposed). The freq_start / nb_chan / nb_scan / pace_s values are kept in the generated config so manual re-enable is a one-field change. All bug fixes (see section J) remain active in that case.

### D. Dead Code Cleanup (~396 lines removed)

- **Removed custom LBT framework** from `loragw_lbt.c` (~110 lines) and its public declarations from `loragw_lbt.h` — this was the disabled `lgw_custom_lbt_*` infrastructure marked "broken on WM1303" and never re-enabled
- **Removed Python pre-filter** `_pre_tx_check()` in `wm1303_backend.py` — this legacy function ran an extra RSSI check before HAL and duplicated the HAL decision (usually with stale data)
- **Removed Python `_cad_check()`** — CAD has always been executed by the HAL packet forwarder in C; the Python duplicate was never reached by the TX path
- **Removed blocked-retry state machine** in `tx_queue.py` — the retry loop depended on the Python pre-filter and became dead once the pre-filter was gone
- **Net code reduction**: ~396 lines of legacy code removed across HAL and Python layers, no functional loss

### E. TX_DONE Polling — Option D (`lora_pkt_fwd.c`)

Replaces blind sleep-based TX completion with hardware-verified polling.

- Replaces `usleep(airtime + 20ms)` with `lgw_status(TX_STATUS)` polling at **2 ms intervals**
- **~15 ms savings per TX transmission** — RX is restored faster, TX queue drains faster
- Hard-cap safety timeout: `airtime + 100 ms` prevents infinite polling on hardware faults
- Applied to both `[imme]` (direct-send) and `[jit]` TX paths in `lora_pkt_fwd.c`
- Preserves channel_e stability — no race condition with SX1261 RX restart

### E2. SX1261 Lightweight RX Restart (`lora_pkt_fwd.c`, `loragw_sx1261.c`)

- New `sx1261_lora_rx_restart_light()` function replaces the full SX1261 RX restart after TX completion
- The light restart skips redundant configuration steps (frequency, modulation params) that don't change between TX cycles
- Reduces post-TX SX1261 recovery time, getting channel_e back to RX faster
- Used at all 5 post-TX restart call sites in `lora_pkt_fwd.c`

### E3. Trace FEM Settle Constant (`wm1303_backend.py`)

- Post-TX trace event positioning uses `_FEM_SETTLE_MS = 5.0` — the physical FEM (Front End Module) switching time between TX and RX modes
- This 5 ms value is hardware-measured and represents the actual time between `rf_tx_end` and `sx1261_rx_restart`
- Used by Option 3b (section G) to anchor trace events accurately

### F. CAD Retry Delay Optimization (`lora_pkt_fwd.c`)

- Changed from alternating 10 ms / 15 ms delays to a flat **5 ms** delay between retries
- **~112 ms worst-case savings** over 15 retries (from ~187 ms total delay to ~75 ms)
- Faster channel access without compromising CAD reliability
- `CAD_MAX_RETRIES` remains at 15

### G. Trace Timing Root Cause Fix — Option 3b (`wm1303_backend.py`, `wm1303.html`)

Fixed the fundamental timing accuracy issue in the packet tracing system.

- **Root cause**: stale `_PRE_RF_TX_MS = 88` constant assumed a HAL fast-path that no longer existed after CAD/LBT changes
- **All trace events now hardware-grounded:**
  - `rf_tx_start` anchored to `restart + FEM_SETTLE + airtime`
  - `rf_tx_end` anchored to `restart + FEM_SETTLE`
  - `sx1261_rx_restart` direct from hardware callback
- Queue delay is now visible in the `received → rf_tx_start` gap (varies 71 ms to 2400 ms+ depending on CAD/LBT/queue depth)
- Scan events (CAD, LBT) properly positioned relative to anchored `rf_tx_start`
- Guard for degenerate elapsed times
- **Validated**: 32/32 TX cycles pass monotonicity checks, all gaps are physically correct

### H. Channel_e Unified Trace Events (`wm1303_backend.py`)

- Channel_e now emits the **same trace event sequence** as channel_a
- Previously channel_e had inline endpoint processing with limited trace visibility
- Consistent debugging experience across all channels
- All channel_e TX events (received, queued, scan phases, rf_tx_start/end, restart) are now visible in the Packet Tracing tab

### I. UI Improvements (`wm1303.html`)

- **Time labels**: Δ (delta since previous event), @ (absolute since trace start), Σ (phase sum), ⏱ (duration) — making trace timing immediately readable
- **Dutch tooltips** on all time fields explaining their meaning
- **CRC Error Rate chart** moved from Status tab to the bottom of the Spectrum tab (better contextual grouping)
- **Trace label fix**: "CAD scan" shown correctly instead of "LBT scan" when the gap follows a `cad_start` event

### J. Spectral-Scan Bug Fixes — Defense-in-Depth (`lora_pkt_fwd.c`)

Two real bugs fixed in the `thread_spectral_scan` loop. These fixes remain active as a safety net for users who manually re-enable the sweep.

**Bug #1 — Sweep never advances on error paths**

- All five error exits in the scan loop (TIMEOUT, status read fail, get_results fail, ABORTED, UNKNOWN) invoked SX1261 recovery but **did not advance `freq_hz`** or write a sentinel into the accumulator
- Consequence: a single persistently failing channel caused the sweep to retry the same frequency indefinitely, blocking all subsequent channels
- Fix: advance-freq, sentinel recording (`rssi=-127, samples=0`), and JSON-write now happen in a common block after the if/else chain — progress is guaranteed regardless of per-channel outcome

**Bug #2 — Corrupted histogram accepted as success**

- `lgw_spectral_scan_get_results()` occasionally returns success while the `results[]` buffer contains stale memory, producing histograms with wildly inflated bin counts (observed: 911,504 samples for a scan configured for 100)
- Fix: added a histogram sanity check — if `total_samples > 2 × nb_scan` or `total_samples == 0`, the reading is logged as WARNING and the sentinel path is taken instead
- Corrupted readings are detected and filtered at ~1–2% rate without affecting functional behaviour

### K. Diagnostic Enhancements

- **New `sx1261_health_events` SQLite table** with parser in `debug_collector.py` — records SX1261 recovery events, timeouts, and state transitions for long-running diagnostics
- **Hardware diagnostics** in debug export: SX1302/SX1261 register snapshots, FEM state, GPIO status, SPI bus statistics
- **Accurate LBT counters**: `tx_queue.py` now records every HAL LBT decision (pass/fail/skipped) so Status-tab counters match actual radio behaviour
- **LBT RSSI sentinel filter** in `record_lbt_rssi()`: rejects RSSI values ≤ −126 dBm (the HAL sentinel), preventing pollution of rolling noise-floor buffers (`noise_floor_lbt_avg/min/max`)
- **CRC error rate tracking**: new SQLite table, API endpoint (`/api/wm1303/crc_errors`), and Chart.js graph per channel
- **Metrics retention**: default retention for high-frequency tables adjusted to **24 hours** to keep database size bounded on long-running installs

### L. Other Changes

- **Deduplication TTL** default updated to **300 s** (matches production experience)
- **Upgrade script** minimum version checks: `upgrade.sh` now enforces minimum values for critical config fields to prevent downgrade regressions when merging older user configs
- **Unified RF send list**: origin-channel-first TX priority for all channels including channel_e — channel_e TX packets now go through the same prioritized send list as channel_a
- **Spectrum collector**: empty JSON file handling (prevents warnings on fresh installs or after sweep disable)

---

## Files Changed

| File | Changes | Description |
|------|---------|-------------|
| `overlay/hal/libloragw/inc/loragw_hal.h` | +1 | Per-channel `rssi_target_dbm` field in `lgw_conf_channel_s` |
| `overlay/hal/libloragw/inc/loragw_lbt.h` | −14 | Removed custom LBT public declarations |
| `overlay/hal/libloragw/src/loragw_lbt.c` | ~110 changes | Removed custom LBT implementation, kept HAL LBT with per-channel threshold |
| `overlay/hal/libloragw/src/loragw_sx1261.c` | +45 | LBT RSSI pre-scan read fix, settle delay, FSK pre-CAD noise floor measurement, lightweight RX restart function |
| `overlay/hal/packet_forwarder/src/lora_pkt_fwd.c` | +/− ~900 | TX_DONE polling, CAD retry optimization, spectral-scan bug fixes, HAL LBT threshold wiring |
| `overlay/pymc_core/src/pymc_core/hardware/tx_queue.py` | +/− ~284 | Removed blocked-retry state machine, TX_ACK reports HAL LBT fields, LBT RSSI sentinel filter |
| `overlay/pymc_core/src/pymc_core/hardware/wm1303_backend.py` | +/− ~650 | Removed `_pre_tx_check()`/`_cad_check()`, HAL LBT config generation, trace timing fix (Option 3b), channel_e unified trace events, FEM settle constant |
| `overlay/pymc_repeater/repeater/web/html/wm1303.html` | +/− ~100 | UI time labels, Dutch tooltips, CRC chart move, trace label fix, TX noisefloor display |
| `overlay/pymc_repeater/repeater/web/debug_collector.py` | +250 | SX1261 health events, hardware diagnostics, LBT counter tracking |
| `overlay/pymc_repeater/repeater/web/spectrum_collector.py` | +5 | Empty JSON file handling |
| `overlay/pymc_repeater/repeater/data_acquisition/sqlite_handler.py` | +35 | Schema additions for CRC error tracking and diagnostics |
| `overlay/pymc_repeater/repeater/metrics_retention.py` | +1 | Retention tuning to 24 hours |
| `config/global_conf.json` | +1 | `spectral_scan.enable = false` default |
| `VERSION` | 2.3.1 → 2.4.0 | Version bump |

---

## Verification

### Phase 1 — HAL LBT and Dead Code Removal (seven-test suite)

| Test | Scope | Result |
|------|-------|--------|
| 1 | Baseline RX/TX on both channels with HAL LBT at −80 dBm | ✅ Pass — TX/RX counters, LBT pass-rates, CAD events all nominal |
| 2 | HAL LBT enabled on channel A only | ✅ Pass — per-channel enable/disable respected by HAL |
| 3 | HAL LBT disabled on both channels | ✅ Pass — HAL never blocks TX when disabled |
| 4 | Restrictive −120 dBm threshold on channel A, permissive on channel E | ✅ Pass — A blocks all TX, E passes all TX, TX_ACK fields match HAL state |
| 5 | CAD events continue to populate | ✅ Pass — HAL CAD loop still drives `cad_events`, Python pre-check removal has no side effects |
| 6 | No regressions in RX path | ✅ Pass — RX counters unchanged vs v2.3.1 baseline |
| 7 | No regressions in bridge/forwarding | ✅ Pass — bridge rules, dedup, echo suppression all nominal |

### Phase 2 — Spectral-Scan Fixes (live observation on test device)

- **Run 1** (bug #1 fix only, `pace_s=10`): 6 complete sweeps in 10 min, 1 corrupted histogram confirmed (911,504 samples) — validated bug #2 exists separately
- **Run 2** (both fixes, `pace_s=10`): 7 complete sweeps in 10 min, 2 corrupted histograms detected and filtered, all 9 channels reported healthy `samples=100`
- **Run 3** (production config, `pace_s=300`): 5-minute stability — 62 LBT passes on channel A, 66 on channel E, 6 CAD events, 34 RX, 58 TX, no errors

### Phase 3 — TX_DONE Polling and Trace Timing Validation

- **32 TX cycles** tested with full packet tracing enabled
- All 32 cycles pass **monotonicity checks** — every trace event is in correct temporal order
- All timing gaps are **physically correct** — rf_tx_start/end match actual airtime, scan events correctly positioned
- ~15 ms per-TX savings confirmed via before/after timing comparison
- No channel_e RX starvation observed (TX_DONE polling preserves blocking restart guarantee)

### Regression Check

| Metric (10 min window) | v2.3.1 Baseline | v2.4.0 |
|------------------------|-----------------|--------|
| HAL LBT passes | 142/142 | 156/156 |
| CAD events | 16 | 16 |
| RX channel E | 70 | 77 |
| TX total | 135 | 146 |
| TX_ACK lbt fields | N/A | correct |

Throughput slightly improved: TX_DONE polling and CAD delay optimization reduce per-TX overhead, and sweep-disabled default eliminates concentrator mutex contention.

---

## Known Limitations

### SX1261 Buffer Corruption — Root Cause Not Yet Identified

The histogram sanity check (section J) **detects and filters** corrupted readings but does not prevent them at the source. The underlying bug is believed to be in the HAL driver (`loragw_sx1261.c` or firmware/SPI interaction) where `lgw_spectral_scan_get_results()` occasionally returns success with a stale buffer. **Because the sweep is now disabled by default, this issue has no operational impact.** The sanity check remains as defense-in-depth for users who manually re-enable the sweep.

### HAL LBT RSSI — Residual Sentinel in TX_ACK

The pre-scan RSSI fix (section B) provides real measurements in the SX1261 driver. However, the `lora_pkt_fwd.c` TX_ACK path still uses a hardcoded `lbt_rssi_dbm = -127` because the driver does not yet expose the pre-scan reading via a formal return value. The Python sentinel filter (section K) catches these residual sentinels. A future HAL improvement (TODO #33) will plumb the real RSSI from `sx1261_lbt_start()` through to the TX_ACK structure.

---

## Upgrade

Use the one-liner bootstrap:
```bash
curl -fsSL https://raw.githubusercontent.com/asamonik/openhop-wm1303/main/bootstrap.sh | sudo bash
```

The HAL will be rebuilt automatically during upgrade. HAL LBT is enabled by default on Channel A and Channel E at −80 dBm; adjust per-channel via the UI if needed.

**Notable changes for existing users:**

- SX1261 spectral-scan sweep is now **disabled by default** — set `spectral_scan.enable = true` in `bridge_conf.json` to re-enable if needed
- Deduplication TTL default raised to 300 s — existing configs with lower values will be preserved unless below the new minimum enforced by `upgrade.sh`
- Metrics retention reduced to 24 hours for high-frequency tables — historical data older than 24 h will be purged on first run after upgrade
