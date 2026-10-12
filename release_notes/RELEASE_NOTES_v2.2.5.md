# Release Notes — v2.2.5

**Release date:** 2026-04-24

## Summary

This release fixes two critical SX1302 recovery bugs and a logging visibility issue. The dynamic channel mask fix ensures all enabled multi-SF channels remain active after AGC reloads, L1, and L1.5 recovery events. Previously, only channel 0 was re-enabled after any recovery, silently disabling additional channels. The per-SF correlator parameter restore ensures that deep (L1.5) recovery fully restores the demodulator configuration. Finally, a Python log filter fix makes AGC periodic and recovery messages visible in the system journal.

## Changes

### 1. Dynamic channel mask for all recovery paths (loragw_hal.c)

**Problem:** All recovery code paths (periodic AGC reload, L1 correlator reinit, L1.5 deep modem reinit) used a hardcoded channel mask of `0x01`, re-enabling only multi-SF channel 0. Any additional enabled channels (channel 1, 2, etc.) were silently disabled after the first recovery event.

**Fix:** The channel mask is now dynamically computed from `CONTEXT_IF_CHAIN[]` at recovery time, matching the initial startup behaviour:

```c
uint8_t recovery_ch_mask = 0x00;
for (i = 0; i < LGW_MULTI_NB; i++)
    recovery_ch_mask |= (CONTEXT_IF_CHAIN[i].enable << i);
```

This mask is used in all three recovery paths: periodic AGC reload, L1 recovery, and L1.5 recovery.

### 2. Dynamic channel mask in sx1302_agc_reload (loragw_sx1302.c)

**Problem:** The post-TX AGC reload (the most frequently called reload path, ~650 times per hour) also used the hardcoded `0x01` mask via `sx1302_correlator_reinit()`.

**Fix:** `sx1302_agc_reload()` now accepts `ch_mask` and `sf_mask` parameters, using them for the correlator reinit call. All callers pass the dynamically computed masks.

### 3. Per-SF correlator parameter restore in L1.5 recovery (loragw_hal.c)

**Problem:** The initial SX1302 startup writes ~32 per-SF correlator registers (ACC_PNR, MSP_PNR, MSP_PEAK_NB, MSP2_PEAK_NB for SF5–SF12). These were never restored during L1 or L1.5 recovery. If these registers became corrupted, no recovery could fix them — only a full process restart.

**Fix:** L1.5 recovery now calls `sx1302_lora_correlator_configure()` before the modem configure and correlator reinit, fully restoring all per-SF demodulator parameters.

### 4. Python log filter fix for AGC/recovery messages (wm1303_backend.py)

**Problem:** The Python stdout reader classified all lines containing the substring `INFO` as low-priority debug messages (Priority 4), hiding them from the system journal. This affected all AGC periodic, AGC reload, L1/L1.5 recovery, and correlator reinit messages.

**Fix:** Added `agc_periodic`, `agc_reload`, `correlator`, and `L1` to the Priority 2 activity keywords list, ensuring these messages are logged at INFO level and visible in `journalctl`.

## Files changed

| File | Changes |
|---|---|
| `overlay/hal/libloragw/src/loragw_hal.c` | Dynamic ch_mask/sf_mask in periodic AGC, L1, L1.5; per-SF correlator restore in L1.5; removed debug printf |
| `overlay/hal/libloragw/src/loragw_sx1302.c` | `sx1302_agc_reload()` accepts ch_mask/sf_mask parameters |
| `overlay/pymc_core/src/pymc_core/hardware/wm1303_backend.py` | Added AGC/recovery keywords to Priority 2 log filter |

## Upgrade

Use the bootstrap upgrade:

```bash
curl -sL https://raw.githubusercontent.com/asamonik/openhop-wm1303/main/bootstrap.sh | sudo bash
```

The HAL binary will be rebuilt automatically. A service restart is included.

## Verification

After upgrade, check the journal for AGC periodic messages:

```bash
sudo journalctl -u pymc-repeater --since "2min ago" | grep agc_periodic
```

Expected output (every 30 seconds):
```
INFO: [agc_periodic] 30s since last reload (interval=30s) — refreshing AGC (ch_mask=0x01, sf_mask=0xFF)
INFO: [agc_periodic] done — next in 30s
```
