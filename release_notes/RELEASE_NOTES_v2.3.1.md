# Release Notes — v2.3.1

**Release date:** 2026-04-24

## Summary

This patch release fixes a critical SX1261 RX starvation issue where Channel E stopped receiving packets after ~1 minute under heavy TX load. The root cause was the non-blocking deferred RX restart mechanism, which was replaced with a reliable blocking restart.

## Changes

### SX1261 Blocking RX Restart (`lora_pkt_fwd.c`)

- **Replaced non-blocking deferred RX restart with blocking restart** on both TX paths (direct-send and JIT)
- After each TX, the packet forwarder now **sleeps for airtime + 20ms** then immediately calls `sx1261_lora_rx_restart_light()` to restore RX mode
- This guarantees the SX1261 is back in RX mode before the next TX cycle, eliminating the race condition that caused RX starvation
- Added a **safety fallback** in the main loop that triggers if a blocking restart is somehow missed (logs a WARNING)
- **Impact:** adds ~20ms delay per TX cycle (negligible vs airtime), but guarantees Channel E stays alive

### Layer 2 Watchdog Escalation (`wm1303_backend.py`)

- Added **consecutive crash counter** for pkt_fwd process exits
- After 2 consecutive crashes, the watchdog escalates to a **15-second deep_reset** power cycle via `reset_lgw.sh deep_reset 15`
- Counter resets to 0 on successful RX, preventing false escalation
- This provides a safety net for rare SX1261 hardware latch states that survive normal GPIO resets

### CAD Retry Configuration (`lora_pkt_fwd.c`)

- **Increased CAD_MAX_RETRIES from 5 to 15** for more persistent channel-clear detection
- **Reduced retry delays to alternating 10/15ms** (from original 50-400ms) for faster CAD cycling
- Delay pattern: `{10, 15, 10, 15, 10, 15, 10, 15, 10, 15, 10, 15, 10, 15, 10}` ms
- Worst-case CAD duration: ~809ms (16 scans × 39ms + 15 delays × ~12.5ms avg)
- Applied to both direct-send and JIT TX paths

## Problem Description

When Channel A was configured with SF10 (longer airtime ~567ms vs ~100ms at SF7), bridge traffic generated rapid TX bursts on Channel E. The non-blocking RX restart scheduled a deferred restart timestamp, but under heavy TX load, each new TX would extend the timestamp, causing the SX1261 to never return to RX mode. Channel E would die after ~60 seconds.

## Verification

- Tested on test Sensecap M1 WM1303 with Channel A at SF10 (worst case for TX burst density)
- Channel E maintained stable operation: 10+ RX/min and 20+ TX/min for 3+ minutes (previously died after ~1 minute)
- No fallback warnings triggered (blocking restart always completes)

## Files Changed

| File | Lines Changed | Description |
|------|--------------|-------------|
| `overlay/hal/packet_forwarder/src/lora_pkt_fwd.c` | +16 -22 | Blocking RX restart on both TX paths + safety fallback |
| `overlay/pymc_core/src/pymc_core/hardware/wm1303_backend.py` | +75 -7 | Layer 2 watchdog escalation (consecutive crash → deep_reset) |
| `VERSION` | 2.3.0 → 2.3.1 | Version bump |

## Known Issues

### LBT (Listen Before Talk) Causes Operational Problems

**LBT is currently unstable and should be disabled on all channels.** When LBT is enabled, it can cause TX failures, excessive retries, and degraded overall system performance. The SX1261-based LBT implementation has timing conflicts with the TX/RX pipeline that have not yet been resolved.

**Workaround:** Disable LBT on all channels via the WM1303 Manager UI (Channels tab → set LBT to "off" for each channel). CAD (Channel Activity Detection) remains functional and is the recommended collision avoidance mechanism.


## Upgrade

Use the one-liner bootstrap:
```bash
curl -fsSL https://raw.githubusercontent.com/asamonik/openhop-wm1303/main/bootstrap.sh | sudo bash
```

The HAL will be rebuilt automatically during upgrade.
