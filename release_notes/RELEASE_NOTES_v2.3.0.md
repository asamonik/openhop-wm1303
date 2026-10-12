# Release Notes v2.3.0

**Release Date:** 2026-04-24

## Summary

Major stability release. Root cause of persistent SX1302 correlator stalls identified and resolved: the periodic AGC reload and proactive post-TX correlator reinit (added in earlier versions) were themselves causing the hardware instability they attempted to fix. All custom HAL recovery mechanisms have been removed, returning the SX1302 to its original Semtech HAL behavior. Additional improvements include precise CAD duration tracking in the tracing UI, optimized CAD retry timing, and chart visibility enhancements.

## Breaking Changes

- **Clean-slate HAL**: All custom SX1302 recovery code removed from `loragw_hal.c`. The periodic AGC reload (every 30s), proactive post-TX correlator reinit, multi-tier L1/L1.5/L2 stall recovery, and all associated forensic instrumentation have been eliminated. The SX1302 now operates as designed by Semtech.

## Key Changes

### 1. Clean-Slate HAL — Root Cause Resolution

**What changed:** Removed all custom SX1302 recovery mechanisms from `loragw_hal.c`:
- Periodic AGC reload timer (was 30s/60s/300s interval)
- Proactive post-TX correlator reinit
- Multi-tier L1 (L1a/L1b/L1c) stall detection and recovery
- L2 full HAL restart logic
- All forensic timestamp globals and instrumentation
- Dynamic channel mask computation
- Per-SF correlator parameter restoration

**Why:** Investigation revealed that our own additions were causing the SX1302 correlator stalls. The periodic AGC reload restarted the AGC MCU every 30 seconds, disrupting the correlator state. Each disruption triggered stall detection, which attempted recovery (L1 reinit), which sometimes failed and escalated to a full process restart (L2). This created a cycle of instability:

```
AGC reload → correlator disruption → stall detected → L1 reinit →
sometimes OK, sometimes fails → L2 process restart → AGC reload → ...
```

**Result:**
- Zero process crashes (was ~7/hour)
- Zero stall detections (was ~40/hour)
- Zero L1/L2 recovery events
- Continuous pkt_fwd uptime (same PID for hours)
- Both channels (SX1302 + SX1261) fully operational

### 2. Precise CAD Duration Tracking

**What changed:** Added `cad_duration_ms` measurement in the C code (`lora_pkt_fwd.c`) and a new `cad_start` trace event in the Python backend. The tracing UI now shows the exact CAD scan duration for every TX, including zero-retry scans.

**Files:** `lora_pkt_fwd.c`, `bridge_engine.py`, `packet_trace.py`, `wm1303.html`

### 3. Optimized CAD Retry Timing

**What changed:** CAD retry delays updated from `{50, 100, 200, 300, 400}` ms to `{75, 50, 75, 50, 75}` ms.

**Result:** Worst-case CAD duration reduced from ~1284ms to ~559ms (-56%), while maintaining effective channel sensing with alternating delay pattern.

### 4. RF Guard Display Correction

**What changed:** The tracing UI "rf transmission" waiting row now correctly shows the actual 50ms RF guard margin (was displaying 150ms).

### 5. RX Hops Chart Visibility

**What changed:** Added `grace: '10%'` Y-axis padding to the RX Hops per Channel chart, preventing low-hop-count channels from being invisible at the bottom of the chart.

### 6. Python Watchdog Speedup

**What changed:** Watchdog poll interval reduced from 5s to 1s in `wm1303_backend.py`. This is a purely beneficial change that accelerates detection of any future pkt_fwd process issues without affecting radio operation.

## Changed Files

| File | Change |
|---|---|
| `overlay/hal/libloragw/src/loragw_hal.c` | Removed all custom recovery code (clean-slate) |
| `overlay/hal/libloragw/src/loragw_sx1302.c` | Synced with test Sensecap M1 WM1303 reference |
| `overlay/hal/libloragw/src/loragw_sx1261.c` | Synced with test Sensecap M1 WM1303 reference |
| `overlay/hal/libloragw/src/loragw_spi.c` | SPI speed 4 MHz |
| `overlay/hal/libloragw/src/loragw_aux.c` | Synced with test Sensecap M1 WM1303 reference |
| `overlay/hal/libloragw/src/loragw_lbt.c` | Synced with test Sensecap M1 WM1303 reference |
| `overlay/hal/libloragw/src/sx1261_spi.c` | Synced with test Sensecap M1 WM1303 reference |
| `overlay/hal/libloragw/inc/*.h` | All headers synced with test Sensecap M1 WM1303 |
| `overlay/hal/packet_forwarder/src/lora_pkt_fwd.c` | CAD duration tracking, optimized retry delays |
| `overlay/hal/packet_forwarder/src/capture_thread.c` | Synced with test Sensecap M1 WM1303 reference |
| `overlay/hal/packet_forwarder/inc/capture_thread.h` | Synced with test Sensecap M1 WM1303 reference |
| `overlay/pymc_repeater/repeater/bridge_engine.py` | `cad_start` trace event emission |
| `overlay/pymc_repeater/repeater/web/packet_trace.py` | `ts_offset_ms` backdate support |
| `overlay/pymc_repeater/repeater/web/wm1303_backend.py` | Watchdog 1s poll interval |
| `overlay/pymc_repeater/repeater/web/html/wm1303.html` | RF guard 50ms, chart grace, cad_start step |
| `config/config.yaml.template` | Synced with test Sensecap M1 WM1303 |
| `config/global_conf.json` | Synced with test Sensecap M1 WM1303 |
| `config/reset_lgw.sh` | Synced with test Sensecap M1 WM1303 |

## Lessons Learned

This release documents an important finding for SX1302-based LoRa gateways:

1. **Do not periodically reload the AGC firmware** — the SX1302 AGC MCU is designed to be loaded once at startup. Periodic reloads disrupt the correlator state and cause RX stalls.
2. **Do not reinitialize the correlator after TX** — the SX1302 handles post-TX recovery internally. External correlator reinit can leave incomplete state.
3. **Software recovery mechanisms can create the problems they aim to solve** — aggressive monitoring and automated recovery introduced instability that exceeded the original issue.
4. **When multiple devices show the same issue, look at the software, not the hardware** — three different WM1303 modules exhibited identical stall behavior because they all ran the same modified HAL.

## Upgrade Instructions

Use the bootstrap script:
```bash
curl -fsSL https://raw.githubusercontent.com/asamonik/openhop-wm1303/main/bootstrap.sh | sudo bash
```

The upgrade will rebuild the HAL and packet forwarder with the clean-slate code, deploy updated Python files, and restart the service.

## Verification

After upgrade, verify stability:
```bash
# Check service is running
sudo systemctl status pymc-repeater

# Monitor for 5 minutes — should see ZERO stall/recovery messages
sudo journalctl -u pymc-repeater -f | grep -E 'stall|L1|L2|PROCESS_EXIT|correlator'

# Verify both channels active
sudo journalctl -u pymc-repeater --since '2 min ago' | grep -c 'channel_e_rx'
sudo journalctl -u pymc-repeater --since '2 min ago' | grep -c 'CRC_OK'
```
