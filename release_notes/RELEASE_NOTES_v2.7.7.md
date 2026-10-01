# Release Notes — v2.7.7

Maintenance release. Four defects that all share one theme: **the system could
not account for its own behaviour.** The journal discarded its evidence on every
reboot, a status endpoint reported false security warnings against itself, a
retired database column kept travelling along on every upgraded device, and a
dropped-packet warning carried no information at all.

None of these affected radio operation. Their combined effect was that when
something did go wrong in the field, there was little left to diagnose it with.

The journal defect (#255) is the significant one: it had been present since the
journal limits were introduced in #14 and silently disabled the single most
useful diagnostic facility on the device.

---

## #255 — `Storage=persistent` never took effect; the journal was volatile on every install

**Symptom.** `config/journald-openhop.conf` declared `Storage=persistent`,
`/var/log/journal` existed, and `journalctl --list-boots` still reported a single
boot. `journalctl --header` pointed at `/run/log/journal`, which is tmpfs, so
every reboot erased the complete log history. After an unexplained outage there
was nothing left to analyse.

**Root cause.** systemd merges journald drop-ins by sorting filenames across
**all** search directories — `/etc`, `/run` and `/usr/lib` — and the **last**
value read for a key wins. Raspberry Pi OS ships two drop-ins that collide with
ours:

- `/usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf` → `Storage=volatile`
- `/usr/lib/systemd/journald.conf.d/syslog.conf` → `ForwardToSyslog=yes`

The drop-in was installed as `10-openhop.conf`, which sorts **before** both.
`Storage=persistent` and `ForwardToSyslog=no` were therefore discarded on every
boot, while every other key in the same file — `SystemMaxUse`, `SystemKeepFree`,
`SystemMaxFileSize`, `MaxRetentionSec`, `MaxFileSec` and the rate limits —
applied normally. That partial success is exactly why the defect went unnoticed:
the journal was correctly capped, just never written to disk.

A common assumption makes this worse. `/etc` takes precedence over `/usr/lib`
only for an **identical** filename; with different names, sorting alone decides.
Placing the file in `/etc` was never enough.

**Fix.** Renamed the drop-in to `zz-openhop.conf`, which sorts after any numeric
prefix and after `syslog.conf`. Both scripts now:

- install the new name and delete the superseded `10-openhop.conf`
  unconditionally, so a device upgraded twice cannot end up with two drop-ins;
- verify the **merged** configuration through
  `systemd-analyze cat-config systemd/journald.conf`, reading only the final
  `Storage=` line, since that is the one in effect;
- run `journalctl --flush` after the restart so existing runtime logs are
  migrated to disk rather than lost.

The verification warns rather than fails, so a future drop-in conflict becomes
visible immediately instead of silently reverting the setting.

**Impact.** Logs now survive a reboot, a watchdog restart and a power loss, and
`ForwardToSyslog=no` takes effect again, removing a duplicate write path to the
SD card.

---

## #256 — TX-queue endpoint called its own authenticated API on every poll

**Symptom.** The journal filled with `Unauthorized access attempt to
/api/status`, in bursts that matched the UI poll interval.

**Root cause.** `_tx_queues_get()` in `wm1303_api.py` opened
`http://127.0.0.1:8000/api/status` with `urllib.request` as the **first**
statement of the handler, before reading TX statistics from the direct backend
reference. That endpoint requires a bearer token; the loopback request sent
none, so every poll produced a 401 and a journal warning — and the result was
discarded regardless, because the backend reference had already supplied the
data.

The HTTP response served only as a fallback for the case where the backend
reference yields nothing. That fallback cannot actually help: both paths read
the state of the same process.

**Fix.** The fallback is now lazy and runs only when `tx_stats` is empty.

**Impact.** No more false security warnings, and the common path no longer pays
for a synchronous loopback request with a 2-second timeout inside a request
handler — a needless latency cost and a deadlock risk against a bounded thread
pool.

---

## #257 — The retired `pkt_count` column survived on already-upgraded devices

**Symptom.** `channel_stats_history` still carried `pkt_count`, permanently
NULL, on devices migrated by a pre-fix script version.

**Root cause.** #248 removed the column from the migration list, which stops new
installs from acquiring it but does nothing for existing ones. On the test device
the column held 0 non-NULL values across 449 rows — the dead column described in
#248, and the schema fingerprint that the stale-DB quarantine from #228
classifies as corrupt.

**Fix.** `upgrade.sh` now also drops the column, guarded so it only does so when
the column holds **no** data whatsoever (`SELECT COUNT(pkt_count)` must be 0).
A database that unexpectedly does contain values is left untouched.
`ALTER TABLE ... DROP COLUMN` requires SQLite 3.35 or newer; on older runtimes
the statement raises and the column simply stays in place.

**Impact.** Existing installations converge on the authoritative schema instead
of carrying the discrepancy forward indefinitely.

---

## #258 — Dropped advert packets logged without diagnostic context

**Symptom.** `Invalid advert packet received, dropping.` repeated with nothing
to distinguish one cause from another.

**Root cause.** The handler reports validity as a boolean without a reason, so
the warning carried no information. The same line was emitted for a frame
corrupted at the edge of receiver sensitivity and for a structurally wrong advert
arriving at a healthy signal level — two problems with entirely different causes
and remedies.

**Fix.** The message now includes `rssi`, `snr` and the payload length, read
defensively so a missing attribute cannot turn a dropped packet into an
exception.

**Impact.** A weak-link symptom can be told apart from a peer or firmware
problem. This is a diagnostics change only: it does not alter which adverts are
accepted, and the underlying validity check still exposes no reason code.

---

## Verification

All measurements were taken on the test device running this release.

**Journal storage (#255)**

| Check | Before | After |
|---|---|---|
| `journalctl --header` path | `/run/log/journal/...` | `/var/log/journal/...` |
| Effective `Storage=` | `volatile` | `persistent` |
| Effective `ForwardToSyslog=` | `yes` | `no` |
| Boot history | wiped on reboot | retained |
| Repeater service | — | `active`, `NRestarts=0`, RX uninterrupted |

**TX-queue endpoint (#256)**

- `GET /api/wm1303/tx_queues` → HTTP 200, five queues returned, live data for
  the active channel (`pending=0`, `total_sent=13`, `freq=869618000`, `sf=7`).
- `Unauthorized access attempt` occurrences in the window where the endpoint was
  demonstrably called: **0**.
- Legacy query-parameter tolerance confirmed: `?range=24h` on both
  `tx_queues` and `noise_floor_history` returns HTTP 200, not 500.

**Schema (#257)**

- `PRAGMA table_info(channel_stats_history)`: `rx_count` present, `pkt_count`
  absent.
- `PRAGMA integrity_check` → `ok`; row data intact.
- The drop was validated against a throwaway database before being applied to
  the live one, and a WAL-consistent `.backup` was taken with the service
  stopped.

**Scripts**

- `bash -n` passes on `install.sh`, `upgrade.sh` and `bootstrap.sh`.
- The embedded migration Python in `upgrade.sh` was extracted and compiled
  separately, since `bash -n` does not validate heredoc contents.
- `py_compile` passes on both modified overlay modules.

**Not verified in this release.** The new installer and upgrade phases were
validated by syntax check, by isolated replay of their logic and by applying the
equivalent steps manually on the test device, but a full end-to-end clean install
and a full upgrade run have not yet been executed against this exact commit.

---

## Files changed vs v2.7.6

| File | Change |
|---|---|
| `config/journald-openhop.conf` | Documents the load-order constraint; deployed under a new name |
| `install.sh` | New drop-in name, legacy removal, merged-config verification, journal flush |
| `upgrade.sh` | Same as above, plus the guarded `pkt_count` drop migration |
| `overlay/pymc_repeater/repeater/web/wm1303_api.py` | Lazy status-API fallback in `_tx_queues_get()` |
| `overlay/pymc_repeater/repeater/handler_helpers/advert.py` | Signal context on the dropped-advert warning |
| `TODO.md` | Items #255–#258 |
| `VERSION` | `2.7.6` → `2.7.7` |
