# Release Notes — v2.1.0

**Release Date:** 2026-04-20  
**Previous Version:** v2.0.6  
**Upgrade:** Use the bootstrap one-liner — HAL recompilation is handled automatically

---

## Highlights

This is a major feature release that introduces **mandatory hardware-level Channel Activity Detection (CAD)** before every transmission, replacing the previous software-based approach. The entire TX pipeline has been redesigned around CAD-based collision avoidance, eliminating the need for random TX delays. A new **per-channel Custom LBT (Listen Before Talk)** framework provides an additional RSSI-based check. Extensive optimizations reduce the pre-TX overhead to **37–55 ms** per transmission.

---

## ⚡ Mandatory Hardware CAD Before Every TX

The most significant change in this release. Every transmission now passes through a hardware-level LoRa CAD (Channel Activity Detection) scan on the SX1261 radio **before** the packet is sent via the SX1302.

### How It Works

1. **Stop RX** — the concentrator's RX is briefly paused
2. **CAD Scan** — the SX1261 performs a LoRa preamble detection scan on the exact TX frequency, spreading factor, and bandwidth
3. **Result** —
   - **Clear**: TX proceeds immediately
   - **Detected**: retry with exponential back-off (100 → 200 → 400 → 800 → 1600 ms), up to 5 retries
   - **After 5 retries**: force-send the packet
4. **Resume RX** — RX is restored after TX completes

### SX1261 / SX1302 Interference Resolution

During development, a critical hardware interaction was discovered: configuring the SX1261 for LoRa CAD mode interferes with the SX1302's TX state machine, particularly on narrow-bandwidth channels (BW 62.5 kHz). This was resolved through:

- **Abort + 5 ms delay + Standby** sequence — prevents a race condition between `spectral_scan_abort()` and `SET_STANDBY` that corrupts the SX1261 state
- **GPIO hardware reset with PRAM reload** — fully restores the SX1261 after each CAD scan
- **Bulk PRAM write** — rewrote the firmware upload to use a single SPI transfer instead of 386 individual writes, reducing PRAM reload time from **~460 ms to ~42 ms** (also fixed a `uint8_t` overflow bug that truncated the 1546-byte transfer to 11 bytes)
- **IMMEDIATE TX mode** — switched from TIMESTAMPED to IMMEDIATE mode after CAD to prevent stale timestamp issues that left the SX1302 TX FSM stuck at `TX_SCHEDULED (0x91)`
- **TX abort on stuck FSM** — added `lgw_abort_tx()` call when `TX_SCHEDULED` state is detected before sending a new packet

### CAD Timing Breakdown

| Phase | Channel A (SF7 BW125) | Channel E (SF8 BW62.5) |
|---|---|---|
| Abort (spectral scan) | 4.3 ms | 4.2 ms |
| Setup (frequency, modulation) | 6.5 ms | 6.5 ms |
| CAD scan (preamble detection) | 7.5–14 ms | 18–27 ms |
| Reinit (GPIO reset + PRAM) | 18.1 ms | 18.1 ms |
| **Total** | **~37–43 ms** | **~47–56 ms** |

### CAD Timing Optimizations Applied

| Optimization | Before | After | Savings |
|---|---|---|---|
| Bulk PRAM write | 460 ms | 42 ms | 418 ms |
| Abort delay reduction | 5 ms | 2 ms | 3 ms |
| Skip full calibrate in reinit | 10 ms | 0 ms | 10 ms |
| Calibrate wait reduction | 10 ms | 4 ms | 6 ms |
| Skip `sx1261_calibrate()` | 7 ms | 0 ms | 7 ms |
| TCXO wait reduction | 5 ms | 2 ms | 3 ms |
| Image calibrate wait reduction | 5 ms | 2 ms | 3 ms |
| **Total overhead reduction** | **~500+ ms** | **~37–56 ms** | **~90% faster** |

---

## 📡 Custom LBT (Listen Before Talk) — Per-Channel RSSI Check

A new RSSI-based LBT mechanism runs **after** the CAD scan (when enabled per channel).

### Features

- **Per-channel enable/disable** — configurable via the Channels tab in the UI
- **Per-channel RSSI threshold** — default: -80 dBm, adjustable per channel
- **Real RSSI measurement** — reads actual signal strength in continuous RX mode (not placeholder values)
- **Conditional execution** — completely skipped when LBT is disabled, saving ~47 ms per TX
- **Settle time optimized** — reduced from 5 ms to 2 ms

### Timing Impact

| Scenario | Channel A | Channel E |
|---|---|---|
| CAD only (LBT off) | ~37–43 ms | ~47–56 ms |
| CAD + LBT (LBT on) | ~84–90 ms | ~95–104 ms |

---

## 🚀 TX Delay Elimination

With mandatory CAD handling collision avoidance, **all random TX delays have been set to zero**:

| Parameter | Before | After |
|---|---|---|
| `tx_delay_factor` | 1.0 | **0.0** |
| `direct_tx_delay_factor` | 0.5 | **0.0** |
| Per-rule `tx_delay_ms` | variable | **0 ms** |
| Python airtime guard | duplicated | **removed** |

Users can still increase `tx_delay_factor` in Adv. Config if they need randomized pre-TX jitter for specific use cases.

---

## ⚙️ Packet Forwarder Optimizations

| Change | Before | After |
|---|---|---|
| JIT thread poll interval | 10 ms | **1 ms** |
| Shared RF-chain guard | static 50 ms | **dynamic (airtime + 250 ms)** |
| Python airtime guard | duplicated check | **removed** |
| Mutex locks | per-operation | **consolidated** |
| Redundant HW status checks | present | **eliminated** |
| Python→C overhead | ~62 ms | **~8 ms queue wait** |

---

## 📊 Spectrum & Charts Improvements

### CAD Activity Chart
- **Fixed detection parsing** — `detected=0` was incorrectly counted as "detected" in the old parser; now correctly parsed as "clear"
- **Simplified display** — removed SW/HW/Skipped distinction; chart now shows only **Clear** (green) and **Detected** (red) per channel
- **Added explanatory text** — note below chart explains that "Detected" means TX was force-sent after all CAD retries

### LBT History Chart
- **Fixed data capture** — old parser lost frequency and RSSI data (stored as freq=0, rssi=None); new parser correctly captures both from the `Custom LBT RSSI` log format
- **Eliminated duplicate entries** — `CAD+LBT` summary log lines no longer trigger a second (empty) LBT record

### Data Architecture Cleanup
- **Removed orphaned parsers** — `spectrum_collector.py` no longer parses CAD/LBT events from logs (this data now comes exclusively from Python-level stats in `repeater.db`)
- **Removed orphaned API endpoint** — `/api/wm1303/cad_history` (read from `spectrum_history.db`) was unused by the UI and has been removed
- **Database cleanup** — 2,800+ orphaned LBT entries with freq=0 and rssi=None removed

### Clean Data Architecture

| Data | Source | Database | Chart |
|---|---|---|---|
| Spectral Scan | spectrum_collector (log parsing) | spectrum_history.db | Spectral Scan |
| CAD Events | _packet_activity_recorder (Python stats) | repeater.db | CAD Activity |
| LBT Events | _packet_activity_recorder (Python stats) | repeater.db | LBT History |
| Noise Floor | _packet_activity_recorder | repeater.db | Noise Floor |
| Packet Activity | _packet_activity_recorder | repeater.db | TX Activity |

---

## 🖥️ UI Changes

### Config Tab Removed
- The **Config** tab has been removed entirely
- **TX Delay Factor** has been moved to **Adv. Config → TX Queue Management**
- The tab button, HTML, and all associated JavaScript have been cleaned up

### Spectrum Tab Cleanup
- Removed 6 stat cards (SPI Path, SX1261 Role, SX1261 Status, Active Channels, Spectral Scan Status, Noise Floor)
- Removed "📊 Channel E Spectrum & Signal Quality" header and subtitle

### Channels Tab Cleanup
- Removed RF0/RF1 information block from the right side panel

---

## 🔧 Upgrade Script

- **Added `spectrum_history.db` cleanup** — removes orphaned rows from `lbt_events` and `cad_events` tables during upgrade (these tables are no longer populated; data now resides in `repeater.db`)

---

## 📋 Metrics Retention

All chart data is automatically cleaned up:

| Database | Retention | Tables |
|---|---|---|
| `repeater.db` | 8 days | packet_activity, dedup_events, noise_floor, noise_floor_history, cad_events |
| `spectrum_history.db` | 7 days | spectrum_scans |

---

## ⚠️ Breaking Changes

- **HAL recompilation required** — the packet forwarder and libloragw have significant changes; `upgrade.sh` handles this automatically
- **Config tab removed** — TX Delay Factor is now in Adv. Config → TX Queue Management
- **Default TX delays changed to 0** — CAD handles collision avoidance; increase `tx_delay_factor` only if needed

---

## 🔗 Dependencies

| Component | Version / Branch |
|---|---|
| [sx1302_hal](https://github.com/HansvanMeer/sx1302_hal) | HAL v2.10 (with WM1303 overlay) |
| [pyMC_core](https://github.com/HansvanMeer/pyMC_core) | dev branch |
| [pyMC_Repeater](https://github.com/HansvanMeer/pyMC_Repeater) | dev branch |

---

## Upgrade Instructions

Use the bootstrap one-liner (automatically detects install vs upgrade):
```bash
curl -sL https://raw.githubusercontent.com/asamonik/openhop-wm1303/main/bootstrap.sh | sudo bash
```

Or manually from an existing installation:
```bash
cd ~/pyMC_WM1303 && git pull && sudo bash upgrade.sh
```

After upgrade, perform a **hard refresh** (Ctrl+F5) in the browser to load the updated UI.
