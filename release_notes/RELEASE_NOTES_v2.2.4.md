# Release Notes — v2.2.4

**Release date:** 2026-04-24

## Summary

This release resolves critical TX reliability issues on both channels and improves SX1261 (Channel E) RX availability. The main changes are:

1. **JIT bypass with direct-send** — Eliminates ~76% TX packet drops caused by JIT timestamp mismatches during SX1302 counter resets
2. **SX1261 TX protection for direct-send** — Restores Channel E RX after each TX, fixing near-complete Channel E RX loss
3. **Pre-TX CAD/LBT checks for direct-send** — Re-enables Channel Activity Detection before every transmission
4. **Stale-ACK token sweep** — Prevents Python-side 3+ second timeouts when packets are dropped by JIT
5. **TX DROPPED handling and retry** — Correctly reports failed TX and automatically retries (up to 2x)
6. **Deferred SX1261 RX restart** — Eliminates ~32/min missed RX windows on Channel E

---

## Changed files

| File | Layer | Changes |
|---|---|---|
| `lora_pkt_fwd.c` | C (packet forwarder) | JIT bypass direct-send, stale-ACK sweep, CAD/LBT pre-TX checks |
| `loragw_sx1261.c` | C (HAL) | Deferred RX restart flag, TX inhibit release handler |
| `wm1303_backend.py` | Python (backend) | TOO_LATE/COLLISION future resolution, DROPPED packet handling |
| `tx_queue.py` | Python (TX queue) | Retry mechanism for DROPPED packets (max 2 retries) |

---

## Detailed changes

### 1. JIT Bypass — Direct Send (`lora_pkt_fwd.c`)

**Problem:** The JIT scheduler converts `IMMEDIATE` mode packets to `TIMESTAMPED` with an ~80 ms offset. When the SX1302 internal counter resets during L1/L1.5 recovery, all pending packets become stale (`TOO_LATE`), causing ~76% TX drop rates on Channel A.

**Solution:** Packets marked `sent_immediate=true` now bypass the JIT queue entirely:
- Wait for `TX_FREE` status (max 500 ms)
- Call `lgw_send()` directly
- Wait for transmission to complete
- Send post-TX ACK with CAD/LBT results

**Impact:** TX drop rate reduced from ~76% to 0%.

### 2. SX1261 TX Protection for Direct-Send (`lora_pkt_fwd.c`)

**Problem:** The direct-send path initially skipped three critical SX1261 protection steps that the JIT path provided, causing the SX1261 to be bombarded with RF energy during TX and never restarting in RX mode. Channel E RX dropped from ~20/min to ~1 per 5 minutes.

**Solution:** The direct-send path now mirrors the JIT path's SX1261 handling:
- `sx1261_set_tx_inhibit_rx(true)` before `lgw_send()`
- Airtime estimation and guard expiry calculation
- Scheduled RX restart after TX completes

**Impact:** Channel E RX restored from ~1/5min to ~20/min (~100x improvement).

### 3. Pre-TX CAD/LBT Checks (`lora_pkt_fwd.c`)

**Problem:** The direct-send path initially had no Channel Activity Detection, transmitting without checking if the channel was clear.

**Solution:** Full CAD/LBT scan integrated into the direct-send path:
- Mandatory CAD scan before every TX (all channels)
- Optional LBT check (per-channel configuration)
- Retry loop: up to 5 CAD retries with increasing backoff (50/100/200/300/400 ms)
- Force-send after max retries to prevent indefinite blocking
- Per-channel configuration lookup from `bridge_conf.json`

**Impact:** Collision avoidance restored for all direct-send transmissions.

### 4. Stale-ACK Token Sweep (`lora_pkt_fwd.c`)

**Problem:** Packets dropped by JIT (when it was still partially active) received no ACK, causing Python to wait 3+ seconds for a timeout, then falling back to conservative 100 ms guard timing.

**Solution:** A periodic sweep (every ~1 second) checks all token slots. Slots older than 2 seconds automatically receive a `TOO_LATE` ACK, immediately notifying the Python layer.

**Impact:** Conservative guard fallbacks reduced from ~10/30s to 0.

### 5. TX DROPPED Handling and Retry (`wm1303_backend.py`, `tx_queue.py`)

**Problem:** When a packet was dropped (TOO_LATE/COLLISION), the Python backend still reported `ok=True` and the TX queue did not retry.

**Solution:**
- `wm1303_backend.py`: TOO_LATE/COLLISION ACKs now resolve the pending future with `ok=False, tx_result='dropped'` and reset `_last_tx_end` to `now` (no airtime to wait for)
- `tx_queue.py`: DROPPED packets are automatically re-enqueued with a fresh timestamp, up to 2 retries (3 total attempts)

**Impact:** Correct TX failure reporting; automatic retry for recoverable drops.

### 6. Deferred SX1261 RX Restart (`loragw_sx1261.c`)

**Problem:** When a spectral scan was aborted during TX inhibit (~32 times/min), the SX1261 RX restart was skipped entirely, leaving Channel E deaf until the next scan cycle.

**Solution:** A `deferred_rx_restart` flag is set when scan abort finds TX inhibit active. When `sx1261_set_tx_inhibit_rx(false)` is called (TX complete), the deferred restart is automatically executed.

**Impact:** SX1261 RX skip events reduced from ~32/min to 0.

---

## Performance comparison

| Metric | v2.2.3 | v2.2.4 | Improvement |
|---|---|---|---|
| Channel A TX drop rate | ~76% | 0% | 100% fix |
| Channel E RX rate | ~1/5min | ~20/min | ~100x |
| Conservative guard fallbacks | ~20/min | 0 | 100% fix |
| SX1261 RX skips | ~32/min | 0 | 100% fix |
| Post-TX ACK timeout | ~100% | 0% | 100% fix |
| CAD pre-TX checks | Not active | Active (all channels) | Restored |

---

## Known issues

- **SX1302 correlator stalls** continue to occur intermittently (L1/L1.5 recovery). The existing recovery chain (L1 → L1.5 → process restart) handles these, but brief RX gaps (~5-30s) may occur during recovery cycles.
- **test Sensecap M1 WM1303 connectivity** was intermittently slow during verification; binary and Python files were confirmed matching test Sensecap M1 WM1303 via earlier checks.

---

## Upgrade

Use the one-liner bootstrap:

```bash
curl -sL https://raw.githubusercontent.com/asamonik/openhop-wm1303/main/bootstrap.sh | sudo bash
```

The upgrade script will:
- Pull the latest repository
- Apply all overlay files (HAL + Python)
- Rebuild HAL and packet forwarder
- Perform a 60-second hardware drain reset
- Restart the pymc-repeater service
