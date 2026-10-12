# Release Notes — pyMC_WM1303 v2.4.11

**Release date**: 2026-05-24
**Type**: Patch release — critical bug fixes + companion app compatibility
**Upgrade**: Recommended for all v2.4.10 users

---

## 🚨 Critical fix — TRACE echo-filter bug

v2.4.10 introduced a regression where **all pings were silently discarded** as `unknown_echo`. This affected both companion pings and dashboard-initiated pings, on **all regions**.

### Root cause

The self-echo guard in `wm1303_backend.py` compared the wrong bits of the MeshCore packet header. Byte 0 of a MeshCore packet encodes `[VER(2)|TYPE(4)|ROUTE(2)]`, so TYPE lives in **bits 5–2**, not the entire byte. For a TRACE packet (TYPE=9, ROUTE=2), the raw byte value is `0x26`, which never matched the literal `0x09` that the guard was checking. The guard never triggered, all TRACE TX hashes were cached, and returning TRACE_RESP packets matched those hashes — causing them to be discarded as self-echo before they could reach the companion.

### Fix

The echo-cache guard now correctly extracts the TYPE field using a bit-shift before comparing against the TRACE type value. Applied at both echo-cache insertion sites (`_send_for_enqueue()` and `_send_for_scheduler()`).

Credit: **@fahimshariff-au** (issue #7, commit reference in his fork).

---

## 🚨 Critical fix — TRACE dispatch missing in WM1303 bridge flow

After applying the echo-filter fix, a **second independent issue** was found: incoming TRACE pings from companion nodes were never dispatched to the trace processing helper. The repeater silently re-broadcast the original TRACE without appending its own SNR, so companions never received trace data and pings appeared to time out.

### Root cause

The upstream pymc_repeater router dispatches TRACE packets to the trace helper before forwarding. However, the WM1303 bridge path (channel E/F → BridgeEngine → handler) bypasses that router entirely. There was no TRACE-specific dispatch in the bridge handler, so TRACE packets fell through to the generic forwarding path.

### Fix

A TRACE branch was added to the bridge handler in `main.py`. When a TRACE packet arrives via the bridge path, it is now dispatched to the existing `TraceHelper` instance with a bridge-aware injector so the SNR-annotated response goes out via the correct channel (E or F) instead of the non-existent classic radio path.

This fix lives in the WM1303 overlay because the TRACE dispatch gap only exists in the WM1303 bridge flow, not in upstream pymc_repeater.

---

## ⚠️ Config schema migration — region as plain string

v2.4.10 expects the `region` key in `wm1303_ui.json` to be a nested dict with `code`, `tx_freq_min`, and `tx_freq_max` fields. Upgrading from an older install where `region` was a plain string (e.g. `"EU868"`) caused a startup error.

The fix is now automatic: the config loader detects legacy formats (plain string or missing key) and migrates them to the canonical nested-dict schema on first read, then persists the corrected version back to disk.

Credit: **@fahimshariff-au** (issue #7).

---

## 📝 Non-interactive bootstrap now documented in README

The `WM1303_REGION` environment variable was supported by `bootstrap.sh` since v2.4.9 but only documented in the script header itself. v2.4.11 adds an **Advanced installation** section to the README:

```bash
curl -sSL https://raw.githubusercontent.com/asamonik/openhop-wm1303/main/bootstrap.sh | sudo WM1303_REGION=AU915 bash
```

Supported codes: `EU868`, `US915`, `AU915`, `AS923`, `IN865`, `JP920`, `KR920`, `CUSTOM`. When `WM1303_REGION` is set, the bootstrap selects the matching preset automatically and skips the interactive wizard — ideal for scripted / headless deployments.

---

## 🆕 Companion App Compatibility

Field testing with a MeshCore-family companion app revealed several gaps in how the WM1303 bridge flow served companion requests. The following fixes ensure that companion features (login, CLI commands, telemetry, owner info) work correctly in a WM1303 deployment where only channel E and channel F are active.

### Bridge-aware response routing

Upstream helper classes (`LoginHelper`, `TextHelper`, `ProtocolRequestHelper`) route their responses via the classic radio TX path (`radios[0]/[1]`), which is **not active** in a WM1303 setup. As a result, all helper-generated responses (login confirmations, CLI replies, status/telemetry packets) silently never reached the companion.

A permanent bridge-aware response injector was added that routes helper output through the BridgeEngine so responses go out via channel E or F. All three helpers now use this injector. A per-request override pattern was rejected because it would race against async delayed responses in `LoginHelper`.

### Telemetry support (REQ 0x03)

A new WM1303-specific telemetry helper provides real-time device telemetry to companion apps via the standard MeshCore telemetry request protocol:

- **CPU temperature** (°C) — from the Raspberry Pi thermal zone
- **WM1303 concentrator temperature** (°C) — from the SX1302 HAL packet forwarder
- **CPU / memory / disk usage** (%) — encoded as Humidity-type CayenneLPP values so the companion displays them as readable percentages rather than confusing raw values

The helper also provides an extended binary OWNER_INFO response for any future companion that uses the binary REQ 0x07 path.

### Firmware version level fix

The MeshCore companion gates the Owner Info menu on `firmware_ver_level ≥ 2`. Upstream pymc_repeater reports level `1`, causing the companion to display a firmware-update warning instead of the Owner Info page. A compatibility patch raises the reported level to `11`, unlocking Owner Info, telemetry refresh, and other gated features without modifying upstream code.

### Dynamic `owner.info` via CLI

The MeshCore companion does **not** use the binary REQ 0x07 (GET_OWNER_INFO) to fetch owner information. Instead, it sends a text-based CLI command `get owner.info` over the encrypted admin channel. Upstream did not recognize this key and returned the literal error string `??: owner.info`, which the companion displayed verbatim.

A handler was added that builds the owner info string **dynamically at request time** from the actual device:

- Software version (from `/etc/pymc_repeater/version`)
- Hardware model (from the device tree)
- Total RAM
- Total disk capacity

The fields are joined with `|` (the companion's line separator) and **hard-capped at 115 characters** with graceful degradation — fields that would exceed the limit are skipped entirely rather than truncated mid-token. The `set owner.info` command returns a read-only error since the value is derived live from the device.

This approach ensures that **every installation** automatically reports its own correct hardware and version info to the companion without requiring any manual configuration.

---

## 📋 Known issues / not in this release

- **Spectrum scan regio-aware UI** (issue #7 comment #5, AU-specific): The scan range and heading still show EU868 defaults on AU915/US915 installs. The backend scan itself works; only the UI labels are off. **Targeted for v2.4.12.**
- **NF display spike-rejection + closest-scan-point** (issue #7): Quality improvement, not a regression. **Targeted for v2.4.12.**
- **VirtualLoRaRadio plain instance attributes** (issue #10): Separate tracking issue.

---

## Upgrade instructions

Standard upgrade — no manual steps required, migration runs automatically:

```bash
curl -sSL https://raw.githubusercontent.com/asamonik/openhop-wm1303/main/bootstrap.sh | sudo bash
```

After upgrade, hard-refresh the UI (Ctrl+Shift+R) to clear any cached assets.

---

## Testing performed

### Code review
- TRACE bit-shift fix verified against MeshCore packet header spec
- Config migration helper verified for legacy-string and missing-key cases
- TRACE dispatch flow compared against upstream packet router
- Syntax and import checks on all modified and new overlay files

### Field tests on a Pi 4 / 4 GB test unit (EU868, Channel E @ 869.618 MHz BW62.5 SF7 CR5)
- Service start, UI reachability, region API endpoint verified
- TraceHelper and telemetry helper initialization confirmed in startup logs
- Companion login handled with admin ACL granted
- GET_STATUS round-trip via the bridge-aware response injector confirmed
- GET_TELEMETRY_DATA round-trip confirmed; CayenneLPP fields display correctly in the companion
- `get owner.info` CLI command returns dynamic device info (79 of 115 chars, four lines)
- `set owner.info` correctly returns read-only error
- Firmware version level patch verified in the running process

### End-to-end validation with a MeshCore-family companion
- TRACE ping completes and returns SNR
- Telemetry refresh populates device metrics in the companion
- Owner Info displays four lines (version / hardware / RAM / disk) sourced live from the device
