# Release Notes — v2.4.10

**Release date:** 2026-05-20  
**Status:** Pre-release (testing)

> ⚠️ This is a **test release**. It addresses all bugs reported in [issue #7](https://github.com/HansvanMeer/pyMC_WM1303/issues/7) and includes significant architectural changes to the bootstrap, preset system, and channel configuration flow. Thorough testing on multiple regions is recommended before considering this stable.

---

## Summary

v2.4.10 focuses on **multi-region reliability** and a **clean fresh-install experience**. The preset system has been completely restructured (Presets v3), the bootstrap wizard now works interactively even when piped from curl, and the service gracefully handles the scenario where no channels are configured yet (idle mode).

---

## Highlights

### 1. Bootstrap Self-Reexecution for Interactive Installs

The standard one-liner `curl -sSL .../bootstrap.sh | sudo bash` now works **interactively**. When the script detects it's being piped (no TTY on stdin), it:

1. Downloads a fresh copy of itself to `/tmp`
2. Checks if `/dev/tty` is available and readable
3. Re-executes itself with `/dev/tty` as stdin → full interactive wizard
4. If `/dev/tty` is unavailable (CI/CD, headless): falls back to non-interactive mode with a prominent warning box

Environment variable overrides remain supported:
```bash
WM1303_REGION=AU915 curl -sSL .../bootstrap.sh | sudo bash
```

Supported env vars: `WM1303_REGION`, `WM1303_SYNC_WORD`, `--non-interactive`.

### 2. Presets v3 — Region-Only Presets

Presets have been completely restructured. They now contain **only**:
- `region` — regulatory region code (EU868, US915, AU915, AS923, IN865, JP920, KR920, CUSTOM)
- `rf_center_freq_mhz` — default RF0 center frequency for the region

**No channel configurations are included in presets.** All channels start disabled on a fresh install. The user configures channels manually through the WM1303 Manager UI.

This eliminates the mismatch between region and channel frequencies that was reported in issue #7.

| Region | Center Frequency (MHz) |
|--------|----------------------|
| EU868  | 869.525              |
| US915  | 915.000              |
| AU915  | 915.275              |
| AS923  | 923.200              |
| IN865  | 866.100              |
| JP920  | 920.600              |
| KR920  | 921.900              |
| CUSTOM | 869.525 (editable)   |

### 3. Idle Mode — Service Starts Without Radio

When no channels are configured (fresh install), the service now starts in **idle mode**:
- Web UI is available on port 8000
- `lora_pkt_fwd` is **not** started (no radio activity)
- User configures channels via the UI, then clicks "Save & Restart"
- Service restarts with the configured channels and starts the radio

This prevents crash loops on fresh installs where no channels are set up yet.

### 4. Clean Config Template

The `config.yaml.template` has been cleaned of all hardcoded EU868 frequencies:
- `radio.frequency`: `0` (unconfigured — set by region preset)
- `wm1303.channels`: `{}` (empty — configured via UI)
- `chan_FSK.enable`: `False` (was `True`)
- Generic defaults: `admin_password: admin123`, `node_name: mesh-repeater-01`

### 5. Mandatory Channel Names

Every channel now requires a **user-defined name** (Channel Name field):
- The name field is mandatory — saving is blocked if empty
- Custom names appear throughout the UI: bridge rules, status, charts, noise floor
- Channel A-D: identifier dropdown (Channel A/B/C/D) + mandatory name text field
- Channel E/F: mandatory name text field

### 6. LBT Field Name Unification

The LBT threshold field naming inconsistency (`lbt_rssi_target` vs `lbt_threshold`) has been resolved:
- UI writes **both** field names on save
- Backend and API read both with fallback
- No more silent mismatches between UI configuration and backend behavior

---

## Bug Fixes (Issue #7)

| Bug | Description | Priority | Fix |
|-----|-------------|----------|-----|
| **#1** | Bootstrap non-interactive silently defaults to EU868 | High | Self-reexec via `/dev/tty` + warning box fallback |
| **#2** | Region change in UI doesn't update channel frequencies | Medium | Presets v3: no channel configs in presets |
| **#3** | Channel F enabled by default crashes pkt_fwd on AU915 | High | All channels disabled by default + idle mode |

## Additional Fixes

| Fix | Description |
|-----|-------------|
| **chan_FSK** | Disabled by default (was always enabled) |
| **Channel F in bridge rules** | Now appears in dropdown when frequency is set |
| **Channel F friendly_name** | Correctly saved and displayed |
| **saveChE missing friendly_name** | Now sends friendly_name in POST body |
| **Config template** | Removed hardcoded EU868 frequencies |
| **IP addresses** | Redacted from release notes and documentation |
| **chan_multiSF fallback** | Disabled all slots when no UI channels configured (prevents wrong IF offsets) |

---

## UI Changes

- **Channel A-D**: Identifier dropdown restored + mandatory Channel Name text field
- **Channel E/F**: Mandatory Channel Name text field
- **Bridge Rules**: Display user-defined channel names (priority: name → friendly_name → default)
- **All save functions**: Validate that Channel Name is not empty before saving
- **New channel default**: Empty name (placeholder: "Channel name (required)"), TX power 22 dBm

---

## Files Changed

| File | Changes |
|------|--------|
| `bootstrap.sh` | Self-reexec mechanism, non-interactive warning, preset auto-select, early flag parsing |
| `config/presets.json` | Presets v3: region + rf_center_freq_mhz only (8 presets) |
| `config/wm1303_ui.json` | Clean template: all channels disabled, region/sync_word top-level |
| `config/config.yaml.template` | Region-agnostic defaults, empty channels, chan_FSK disabled |
| `overlay/pymc_repeater/repeater/web/html/wm1303.html` | Channel names, bridge rules, LBT unification, validation |
| `overlay/pymc_repeater/repeater/web/wm1303_api.py` | LBT setdefault for both field names |
| `overlay/pymc_repeater/repeater/main.py` | Idle mode: skip pkt_fwd when no channels configured |
| `overlay/pymc_repeater/repeater/bridge_engine.py` | Graceful handling of empty radio list |
| `_BACKEND_HANDOVER.md` | IP addresses redacted |
| `release_notes/RELEASE_NOTES_v2.4.9.md` | IP addresses redacted |

---

## Upgrade Instructions

### Standard upgrade (existing installation)
```bash
curl -sSL https://raw.githubusercontent.com/asamonik/openhop-wm1303/main/bootstrap.sh | sudo bash
```

### Fresh install
```bash
curl -sSL https://raw.githubusercontent.com/asamonik/openhop-wm1303/main/bootstrap.sh | sudo bash
```
The interactive wizard will guide you through region selection.

### After upgrade
- **Hard refresh** your browser: `Ctrl + Shift + R`
- Verify version in the WM1303 Manager header
- Check that your channels and bridge rules are intact

---

## Known Limitations

- Channels A-D (chan_multiSF) support **BW125 only** — this is a hardware limitation of the SX1302 multi-SF demodulators
- Channel E (SX1261) and Channel F (chan_Lora_std) support BW125, BW250, and BW500
- AU sub-region presets (AU-Mid, AU-Narrow, AU-Wide) are not included — region-specific channel configurations are planned as an upstream feature in pymc_repeater
