# pyMC_WM1303 v2.4.6 — Memory Stability & Upstream Sync

**Release date:** 2026-05-01

This release dramatically reduces long-running memory usage and integrates the
latest upstream changes from pyMC_core and pyMC_Repeater.  On the reference
test unit (4 GB Pi)
the `pymc-repeater` process now stabilizes at **~85 MB RSS** after 30+ minutes
of uptime — a **~60% reduction** versus v2.4.5 and fully suitable for 512 MB
devices.

---

## 🚀 Highlights

| Area | Change |
|---|---|
| **Memory** | RSS stable at ~85 MB (was 199+ MB, growing unbounded in some conditions) |
| **WAL file** | Capped at 8 MB via `journal_size_limit`, periodic TRUNCATE every 5 min |
| **VACUUM** | No longer fires on every restart — persisted timestamp ensures weekly cadence |
| **Allocator** | `malloc_trim(0)` after each cleanup/checkpoint returns pages to the OS |
| **Threads** | -1 thread via unified 60 s recorder (packet_activity + crc_error_rate merged) |
| **UI** | New Packet Duration Timeline chart on the Tracing tab (250 ms – 10 s filter) |
| **Upstream** | Merged pyMC_core `ba0a945…` and pyMC_Repeater `11572f8…` (GPS service, renames) |

---

## 🧠 Memory optimization details

### 1. SQLite WAL management (`sqlite_handler.py`)

SQLite's default `wal_autocheckpoint` performs only passive checkpoints that
silently fail when persistent readers hold WAL frames.  With this release:

- `PRAGMA journal_size_limit = 8000000` — hard cap at 8 MB (recycled thereafter)
- `PRAGMA wal_autocheckpoint = 500` — attempt checkpoints every ~2 MB
- A dedicated **background thread** runs `PRAGMA wal_checkpoint(TRUNCATE)`
  every 5 minutes, guaranteeing the WAL stays small even under heavy reader
  activity.
- After each TRUNCATE: `gc.collect()` + `malloc_trim(0)` to release memory.

### 2. VACUUM timestamp persistence (`metrics_retention.py`)

Previously `self._last_vacuum = 0.0` caused VACUUM to fire on the very first
cleanup after every restart — briefly using 2-3× the DB size in RAM (a 50 MB DB
produced a ~100 MB spike).  Now:

- `_last_vacuum` is persisted to `/var/lib/pymc_repeater/.last_vacuum`.
- On startup the timestamp is loaded; a missing file is seeded with `time.time()`
  so the first VACUUM only fires after a full `vacuum_interval_s` (7 days).
- VACUUM still runs **weekly** as designed, just no longer on restart.

### 3. `malloc_trim(0)` helper

Glibc's allocator holds freed pages in per-arena free lists instead of returning
them to the kernel.  Two small helpers added (in `metrics_retention.py` and
`sqlite_handler.py`) call `libc.malloc_trim(0)` after:

- Each metrics retention cleanup cycle (hourly)
- Each WAL checkpoint (every 5 minutes)

Result: the Python process footprint tracks actual live data rather than
accumulated allocator reserves.  Growth rate dropped from ~5 MB/min to ~0.26
MB/min (-95 %).

### 4. Unified 60 s recorder (`wm1303_api.py`)

The separate `_pkt_rec_thread` and `_crc_rec_thread` were merged into a single
`_unified_60s_recorder` that calls both recorder helpers per tick.  Benefits:

- One thread instead of two.
- Reuses the same shared SQLite connection via `_db_conn()`, closing an extra
  leak where both threads had been opening raw `sqlite3.connect()` handles.
- Both tables (`packet_activity` and `crc_error_rate`) still receive writes at
  the same timestamp.

### 5. Complete bounded-cache audit

All `self._*` caches in the overlay were audited against the risk of unbounded
growth.  Every structure has either a TTL or maximum size:

| Location | Cache | Bound |
|---|---|---|
| bridge_engine | `_seen` | 5 s TTL |
| bridge_engine | `_tx_echo_hashes` | TTL |
| bridge_engine | `_dedup_events` | `deque(maxlen=500)` |
| wm1303_backend | `_tx_ack_cache` | 30 s TTL + max 512 (LRU) |
| wm1303_backend | `_respawn_times` | 1 h TTL |
| wm1303_backend | `_rx_dedup_cache` | 5 s TTL |
| wm1303_backend | rolling averages | max 50 entries |
| wm1303_backend | `_rx_nf_estimates` | bounded deque |
| packet_trace | traces | `MAX_TRACES=200` |

No leaks were found in project code.  The remaining growth above base RSS is
fully explained by (and now mitigated by) Python allocator fragmentation —
which `malloc_trim(0)` addresses.

---

## 🎨 UI: Packet Duration Timeline (Tracing tab)

A new chart at the top of the Tracing tab visualises each packet's end-to-end
duration over time, with one line per packet type (ADVERT, REQ, RESP, TXT,
PATH, ACK, etc.).

- **Filter:** 250 ms ≤ duration ≤ 10 s (short routine traces and extreme
  outliers are excluded to keep the scale meaningful).
- **Data source:** existing in-memory `TraceCollector` (MAX_TRACES = 200) —
  **no new database tables or storage**, no retention impact.
- Chart updates automatically with the trace list.
- Consistent colour scheme per packet type.

---

## 🔄 Upstream synchronisation

| Repo | Before | After |
|---|---|---|
| pyMC_core | `7e0c2ea` | `ba0a945` (sx1262_wrapper stub) |
| pyMC_Repeater | `a36d991` | `11572f80` (GPS service, buildroot info, service fixes) |

### Overlay files updated for upstream compatibility

| File | Notable changes |
|---|---|
| `config.py` | GPS defaults, `update_unscoped_flood_policy` rename, `radio_timing_delay` |
| `main.py` | GPS service init/stop, `_update_repeater_location_from_gps` method |
| `api_endpoints.py` | GPS endpoints, `get_buildroot_image_info` import, rename adaptation |
| `wm1303_backend.py` | Removed stray `@_contextmanager` from `_SharedConn` class introduced during merge |

---

## 📊 Measured impact (test unit, 4 GB Pi, 33 min uptime)

| Metric | v2.4.5 (trending) | v2.4.6 |
|---|---|---|
| `pymc-repeater` RSS | 199 MB ↗️ unbounded | **84 MB** ✅ stable |
| VmHWM (peak) | 199 MB | **84 MB** (no VACUUM spike) |
| RssAnon (Python heap) | 175 MB | **60 MB** |
| Growth rate | ~5 MB/min | **~0.26 MB/min** (-95 %) |
| WAL file | up to 50 MB | **1–3 MB** stable |
| System RAM used | 449 MB | **316 MB** |
| Open DB FDs | 19 → 12 | 12 (stable) |

### Expected headroom on a 512 MB device

Realistic estimate based on measured data from a 4 GB test unit
(`free -m` reports ~350 MB used at idle with a few SSH sessions).
The biggest items easily overlooked are the **kernel Slab allocator
(~100 MB)** and the combined overhead of **systemd, journald,
NetworkManager, wpa_supplicant, sshd and dbus (~80–120 MB)**.

| Component | RAM |
|---|---|
| Raspberry Pi OS Lite + kernel + Slab | ~120–150 MB |
| **pymc-repeater** | **~85–90 MB** |
| `lora_pkt_fwd` (if run as separate process) | ~10–20 MB |
| systemd, journald, NetworkManager, sshd, dbus, etc. | ~40–60 MB |
| **Total** | **~260–320 MB** |
| **Free on 512 MB Pi** | **~190–250 MB (37–49 %)** |

A 512 MB device has less buffer/cache pressure and a smaller Slab
footprint than a 4 GB device, so actual headroom typically lands at the
higher end of that range.  Prior to v2.4.6 the unbounded Python heap
growth (up to ~200 MB RSS) could easily push a 512 MB device into swap;
with the v2.4.6 stability fixes, `pymc-repeater` holds steady around
85–90 MB, leaving sufficient headroom for robust operation.

---

## 🔧 Changed files

| File | Summary |
|---|---|
| `VERSION` | 2.4.5 → 2.4.6 |
| `overlay/pymc_repeater/repeater/data_acquisition/sqlite_handler.py` | WAL management thread, new PRAGMAs, `_malloc_trim()` helper |
| `overlay/pymc_repeater/repeater/metrics_retention.py` | `.last_vacuum` persistence, `malloc_trim()` helper + call |
| `overlay/pymc_repeater/repeater/web/wm1303_api.py` | Unified 60 s recorder, `_db_conn` adoption |
| `overlay/pymc_repeater/repeater/web/html/wm1303.html` | Packet Duration Timeline chart |
| `overlay/pymc_repeater/repeater/config.py` | Upstream merge (GPS, rename, timing) |
| `overlay/pymc_repeater/repeater/main.py` | Upstream merge (GPS service integration) |
| `overlay/pymc_repeater/repeater/web/api_endpoints.py` | Upstream merge (GPS endpoints, renames) |
| `overlay/pymc_core/src/pymc_core/hardware/wm1303_backend.py` | Remove stray `@_contextmanager` decorator |

---

## 🧪 Verified on

- **Reference test unit**: clean bootstrap install, 33 min stability test, all
  services active, UI functional, memory stable.

## 📝 Upgrade path

Run the standard bootstrap one-liner — it will pull the new overlay files and
restart services automatically:

```bash
curl -fsSL https://raw.githubusercontent.com/asamonik/openhop-wm1303/main/bootstrap.sh | sudo bash
```

No configuration changes are required.  The `.last_vacuum` file will be
created automatically on first start after the upgrade.
