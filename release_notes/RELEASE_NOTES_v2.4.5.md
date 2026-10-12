# Release Notes v2.4.5

## Memory Optimization & Data Retention Overhaul

Major memory usage reduction (-61%), SQLite connection leak fix, tiered data retention system, and several bug fixes. Optimized for deployment on devices with as little as 512 MB RAM.

---

### Memory Optimization

#### SQLite Connection Leak Fix
- **Root cause**: Python's `with sqlite3.connect(path) as conn:` commits but does NOT close connections. Repeated calls leaked ~110 open file descriptors to `repeater.db`.
- **Fix**: New `_db_conn()` context manager that explicitly commits AND closes connections.
- **Impact**: Unbounded memory growth eliminated.

#### Shared Connection Architecture (`_SharedConn`)
- Introduced module-level `_SharedConn` registry: one persistent connection per database path, protected by a reentrant lock.
- Replaces per-call `sqlite3.connect()` in high-frequency code paths (TX events, periodic recorders, API requests).
- Thread-safe via `threading.RLock()` per connection.

#### SQLite PRAGMA Tuning
- `PRAGMA cache_size=-512` (512 KB per persistent connection, vs default 2 MB)
- `PRAGMA cache_size=-256` for transient `_db_conn()` connections
- `PRAGMA mmap_size=0` — disabled memory-mapped I/O
- `PRAGMA temp_store=MEMORY` — in-memory temp tables

#### Python 3.13 Compatibility
- Replaced `@classmethod` pattern on `_SharedConn.get()` with module-level `_get_shared_conn()` function.
- Python 3.13 changed descriptor protocol behavior for classmethods, causing `'classmethod' object is not callable` errors.

#### Results

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| RSS (steady state) | 224 MB (growing) | **~90 MB** (stable) | **-61%** |
| File descriptors | 110+ (growing) | **~41** (stable) | **-63%** |
| WAL file size | 134 MB (unchecked) | **~4 MB** (auto-truncated) | **-97%** |

---

### Tiered Data Retention System

New 4-tier downsampling architecture preserves historical data at decreasing resolution while keeping database size bounded:

| Tier | Age | Resolution | Tables |
|------|-----|------------|--------|
| Hot | 0–7 hours | Full (raw) | Source tables |
| Warm | 7–24 hours | 1-minute aggregation | `*_1m` |
| Cool | 1–3 days | 10-minute aggregation | `*_10m` |
| Cold | 3–8 days | 15-minute aggregation | `*_15m` |
| Delete | > 8 days | Purged | — |

#### Summary Tables Created
- `packet_metrics_1m`, `packet_metrics_10m`, `packet_metrics_15m`
- `dedup_events_1m`, `dedup_events_10m`, `dedup_events_15m`
- `noise_floor_history_1m`, `noise_floor_history_10m`, `noise_floor_history_15m`
- `cad_events_1m`, `cad_events_10m`, `cad_events_15m`
- `channel_stats_history_1m`, `channel_stats_history_10m`, `channel_stats_history_15m`

#### Aggregation Features
- Weighted averages via `sample_count` for cascading re-aggregation
- MIN/MAX preservation across all tiers
- Fallback direct-from-source aggregation for data that bypasses intermediate tiers
- Automatic `PRAGMA wal_checkpoint(TRUNCATE)` after each cleanup cycle
- Periodic `VACUUM` to reclaim disk space

---

### CAD Random Retry Delay

- **Was**: Fixed 5 ms delay between each CAD retry
- **Now**: Random 1–10 ms jitter per retry (`1 + rand() % 10`)
- **Why**: Prevents synchronized retries when multiple gateways attempt TX simultaneously
- `srand(time(NULL))` seeded at pkt_fwd startup
- Applied to both immediate TX and JIT TX paths

---

### LBT Double-Counting Bug Fix

- **Symptom**: UI showed LBT passed count equal to total TX across ALL channels instead of per-channel values
- **Root cause**: LBT counters were incremented twice per TX event — once in `record_lbt_result()` (correct, per-channel) and again in a duplicate block within `GlobalTXScheduler` dispatch
- **Fix**: Removed duplicate LBT statistics block from `tx_queue.py` (lines 672–697)

---

### Files Changed

| File | Changes |
|------|------|
| `overlay/pymc_core/src/pymc_core/hardware/wm1303_backend.py` | Connection leak fix, `_SharedConn`, Python 3.13 classmethod fix |
| `overlay/pymc_core/src/pymc_core/hardware/tx_queue.py` | LBT double-counting removed |
| `overlay/pymc_repeater/repeater/web/wm1303_api.py` | Connection leak fix, `_SharedConn` moved to module-level, `import threading` added, Python 3.13 fix |
| `overlay/pymc_repeater/repeater/metrics_retention.py` | Connection leak fix, `_SharedConn`, tiered retention system, WAL truncate, Python 3.13 fix |
| `overlay/pymc_repeater/repeater/data_acquisition/storage_collector.py` | Connection leak fix, `_SharedConn`, Python 3.13 fix |
| `overlay/hal/packet_forwarder/src/lora_pkt_fwd.c` | CAD random delay (1–10 ms jitter) |

---

### Compatibility

- Tested on Raspberry Pi 3B+ (1 GB) and Raspberry Pi 4 (4 GB)
- Suitable for devices with **512 MB RAM** (estimated ~182 MB total usage)
- Python 3.13 compatible
- Summary tables created automatically on first retention cycle (no manual migration needed)

---

### Deployment

Standard upgrade via bootstrap one-liner:
```bash
curl -fsSL https://raw.githubusercontent.com/asamonik/openhop-wm1303/main/bootstrap.sh | sudo bash
```

After upgrade, the first retention cycle will create summary tables and perform initial aggregation. Database size will decrease significantly after the first run.
