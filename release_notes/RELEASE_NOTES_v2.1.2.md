# Release Notes — v2.1.2

**Release date:** 2026-04-21

## Summary

Logging and startup quality-of-life release. Eliminates all spurious warnings and errors during service startup and shutdown, and fixes the pkt_fwd stats throttle that was inadvertently bypassed.

## Bug Fixes

### 1. pkt_fwd Stats Throttle Not Working

| | Detail |
|---|---|
| **Problem** | Stats summary lines (`# TX errors: 0`, `# BEACON queued: 0`, etc.) were logged at INFO every 30 seconds instead of being throttled to every 5 minutes. The `TX ` and `BEACON` substrings in these lines matched the activity keywords check, which had higher priority than the stats throttle. |
| **Fix** | Added a `# ` (hash-space) prefix check before the activity keywords check. Lines starting with `# ` are now correctly routed to the stats throttle (logged at INFO every 5 minutes, DEBUG otherwise). |
| **Impact** | Reduces ~480 unnecessary INFO log lines per hour to ~12. |
| **File** | `overlay/pymc_core/src/pymc_core/hardware/wm1303_backend.py` |

### 2. "Radio does not support CAD configuration" Warning

| | Detail |
|---|---|
| **Problem** | pymc_core's RepeaterDaemon attempted to configure CAD on VirtualLoRaRadio at startup, which doesn't support it. This produced a misleading WARNING because CAD is handled by the C-level `lora_pkt_fwd` on WM1303. |
| **Fix** | Suppressed the warning when `radio_type` is `wm1303`. |
| **File** | `overlay/pymc_repeater/repeater/main.py` |

### 3. "alias miss for channel_e" Warning (2× at Startup)

| | Detail |
|---|---|
| **Problem** | BridgeEngine logged a WARNING for every bridge rule referencing `channel_e` because it wasn't found in `CHANNEL_ALIASES` or `radio_map`. Channel E is managed directly by the HAL (SPI conflict prevents independent access) and handled by `channel_e_bridge.py`. |
| **Fix** | Added `channel_e` to `NON_RADIO_ENDPOINTS` set in BridgeEngine, recognizing it as a valid HAL-managed special channel. |
| **File** | `overlay/pymc_repeater/repeater/bridge_engine.py` |

### 4. "Failed to stop lora_pkt_fwd.service" at Startup

| | Detail |
|---|---|
| **Problem** | The systemd service `ExecStartPre` command tried to stop `lora_pkt_fwd.service`, which doesn't exist (pkt_fwd is started as a subprocess by WM1303Backend). This produced a noisy error at every service start. |
| **Fix** | Added `2>/dev/null` to the `systemctl stop lora_pkt_fwd.service` command in the service file. |
| **File** | `config/pymc-repeater.service` |

### 5. "Event loop stopped before Future completed" on Shutdown

| | Detail |
|---|---|
| **Problem** | When SIGTERM was received, Python 3.13's asyncio cleanup raised `RuntimeError` because tasks were still running when the event loop was stopped. This made the service exit with status=1 (FAILURE) even though shutdown was intentional. |
| **Fix** | Replaced `loop.stop()` with `asyncio.Event`-based cooperative shutdown. The signal handler sets `_stop_event`, `run()` awaits it, and cleanup proceeds naturally. Service now exits cleanly with code 0. |
| **File** | `overlay/pymc_repeater/repeater/main.py` |

## Log Quality Improvements

| Metric | Before v2.1.2 | After v2.1.2 |
|---|---|---|
| Startup warnings | 5 (CAD, alias miss ×2, lora_pkt_fwd.service, event loop) | **0** |
| Shutdown errors | 1 (RuntimeError + traceback) | **0** |
| pkt_fwd stats lines/hour | ~480 (every 30s) | **~12** (every 5 min) |
| Service exit code on stop | 1 (FAILURE) | **0** (clean) |

## Files Changed

| File | Changes |
|---|---|
| `overlay/pymc_core/src/pymc_core/hardware/wm1303_backend.py` | Stats throttle `# ` prefix priority fix |
| `overlay/pymc_core/src/pymc_core/hardware/virtual_radio.py` | WAITING log level: INFO → DEBUG |
| `overlay/pymc_repeater/repeater/main.py` | CAD warning suppress + cooperative shutdown |
| `overlay/pymc_repeater/repeater/bridge_engine.py` | `NON_RADIO_ENDPOINTS` set with channel_e |
| `overlay/pymc_repeater/repeater/channel_e_bridge.py` | origin_channel parameter on inject |
| `config/pymc-repeater.service` | ExecStartPre stderr redirect |
| `overlay/hal/packet_forwarder/src/lora_pkt_fwd.c` | Spectral scan status comment correction |

## Upgrade Instructions

```bash
curl -sSL https://raw.githubusercontent.com/asamonik/openhop-wm1303/main/bootstrap.sh | sudo bash
```

The upgrade script will automatically apply all overlay changes and restart the service.
