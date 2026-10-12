# WM1303 REST API

> API reference for the WM1303 Manager backend endpoints

## Overview

The WM1303 API provides REST endpoints for managing the concentrator, channels, bridge rules, spectral data, and system status. All endpoints are mounted under `/api/wm1303/` on the OpenHop Repeater HTTP server (port 8000).

## Authentication

All API endpoints require a valid **JWT token** from the OpenHop Repeater authentication system. The token is obtained during the initial OpenHop Repeater setup and stored in the repeater configuration.

## Base URL

```
http://<pi-ip>:8000/api/wm1303/
```

## Status Endpoints

### GET `/api/wm1303/status`

Returns the current system status including all channel statistics.

**Response:**
```json
{
  "status": "running",
  "version": "2.1.1",
  "uptime": 86400,
  "pkt_fwd_status": "running",
  "channels": {
    "channel_a": {
      "active": true,
      "frequency": 869525000,
      "bandwidth": 125000,
      "spreading_factor": 12,
      "rx_packets": 1523,
      "tx_packets": 487,
      "rx_crc_errors": 12,
      "rssi_last": -78.5,
      "snr_last": 8.2,
      "noise_floor": -115.3,
      "tx_duty_cycle": 0.42
    },
    "channel_b": { ... },
    "channel_c": { ... },
    "channel_d": { ... },
    "channel_e": {
      "active": true,
      "frequency": 869525000,
      "bandwidth": 62500,
      "spreading_factor": 12,
      "rx_packets": 23,
      "tx_packets": 8,
      "noise_floor": -118.7,
      "rx_boost": true
    }
  },
  "active_channels": 5,
  "system": {
    "cpu_temp": 48.2,
    "memory_used_pct": 34.5,
    "disk_used_pct": 12.8,
    "uptime_seconds": 86400
  }
}
```

### GET `/api/wm1303/health`

Simple health check endpoint.

**Response:**
```json
{ "ok": true, "version": "2.1.1" }
```

## Channel Configuration Endpoints

### GET `/api/wm1303/channels`

Returns configuration of all channels (reads from `wm1303_ui.json` SSOT).

### PUT `/api/wm1303/channels/<channel_id>`

Update a channel's configuration.

**Path parameters:**
- `channel_id` — `channel_a` through `channel_e`

**Request body:**
```json
{
  "frequency": 869525000,
  "bandwidth": 125000,
  "spreading_factor": 12,
  "coding_rate": 8,
  "preamble": 32,
  "tx_power": 20,
  "lbt_enabled": false,
  "lbt_threshold": -75,
  "cad_enabled": true,
  "active": true,
  "name": "SF12-868"
}
```

Channel E additionally supports:
```json
{
  "rx_boost": true,
  "bandwidth": 62500
}
```

**Behavior:**
1. Writes to `wm1303_ui.json` (SSOT)
2. Syncs active channels to `config.yaml`
3. Changes take effect within 5 seconds (cache TTL auto-reload)

### POST `/api/wm1303/channels/<channel_id>/toggle`

Toggle a channel active/inactive.

## Bridge Rule Endpoints

### GET `/api/wm1303/bridge/rules`

Returns all bridge rules from the SSOT.

**Response:**
```json
{
  "rules": [
    {
      "id": "rule_1",
      "source": "channel_a",
      "target": "channel_b",
      "types": ["all"],
      "enabled": true
    }
  ]
}
```

### POST `/api/wm1303/bridge/rules`

Create a new bridge rule.

### PUT `/api/wm1303/bridge/rules/<rule_id>`

Update an existing bridge rule.

### DELETE `/api/wm1303/bridge/rules/<rule_id>`

Delete a bridge rule.

### POST `/api/wm1303/bridge/rules/<rule_id>/toggle`

Toggle a bridge rule enabled/disabled.

## Spectrum / Noise Floor Endpoints

### GET `/api/wm1303/duty_cycle_history?hours=1`

Accepts `hours=1|6|12|24|72|168`. Returns `data` for total TX duty and `channels`
for each historical channel. Points include `t`, `bucket_seconds`,
`tx_airtime_ms`, and `tx_duty_pct` (airtime divided by bucket duration).
`interval_tx_duty_pct` is recorded airtime divided by the selected interval;
it is null when no measurements are available. Cumulative snapshots include
every TX consumer and retain counter resets. Missing snapshots are gaps.

`GET /api/stats` exposes `tx_duty_pct`, WM1303 `rx_duty_pct`,
`rx_availability_pct`, and `duty_window_seconds=60`. Existing
`utilization_percent` retains its meaning as percentage of the TX allowance
used. `GET /api/wm1303/channels/live` exposes the same global metrics under
`duty_cycle`, alongside per-channel RX/TX duty.

`GET /api/packet_by_id` and `/api/packet_by_hash` include `scope` and
`scope_known`. An unresolved or ambiguous scope is `unknown scope`; transport
keys are never returned with packet details.

`GET /api/repeater_info` returns only public `node_name`, `owner_info`,
`latitude`, `longitude`, and `max_owner_info_bytes=115`. Save these fields
through `POST /api/update_radio_config`. `owner_info` accepts an empty string
to remove the saved text. This scoped save preserves unrelated pending settings
and reports whether live application succeeded or a restart is required.

### GET `/api/wm1303/spectrum`

Returns current spectral scan data for all channels.

The response also includes `spectral_scan_requested`, `scan_pace_seconds`, and
`scan_channel_count`. Enable/disable scans with
`POST /api/wm1303/spectrum {"action":"toggle","enable":true|false}`. This
saves the SSOT setting and restarts the service. A failed restart is reported
with `restart_required` and `restart_error` while preserving the saved setting.
`{"action":"scan"}` refreshes available HAL readings without initiating an
immediate hardware sweep. Zero-sample and invalid RSSI readings are excluded.

**Response includes:**
- Per-channel RSSI values
- Per-channel noise floor estimates
- Scan timestamp
- Number of scan points

### GET `/api/wm1303/spectrum/history`

Returns noise floor history for charting.

**Query parameters:**
- `period` — Time window (`1h`, `6h`, `24h`, `7d`)
- `channel` — Optional channel filter (`channel_a` through `channel_e`)

### GET `/api/wm1303/spectrum/cad`

Returns CAD event history.

**Response includes:**
- Per-channel CAD events with timestamps
- **Clear** and **Detected** counts per channel
- Detected means activity was found on all 5 CAD retries and the packet was force-sent

> **v2.1.0 change:** The previous HW/SW source distinction has been removed. CAD now reports only Clear and Detected outcomes. The orphaned `/api/wm1303/cad_history` endpoint (which read from `spectrum_history.db`) has been removed.

### GET `/api/wm1303/spectrum/lbt`

Returns LBT event history.

## Origin Channel Metrics (v2.1.1)

### GET `/api/wm1303/origin_stats`

Returns per-channel origin statistics — how many packets each channel sources for the repeater.

**Query parameters:**
- `hours` — Time window in hours (default: 24)

**Response:**
```json
{
  "period_hours": 24,
  "channels": {
    "channel_a": [
      { "timestamp": "2026-04-20T10:00:00", "count": 42 },
      { "timestamp": "2026-04-20T10:10:00", "count": 38 }
    ],
    "channel_e": [
      { "timestamp": "2026-04-20T10:00:00", "count": 15 },
      { "timestamp": "2026-04-20T10:10:00", "count": 12 }
    ]
  }
}
```

**Details:**
- Data is stored in 10-minute buckets in the `origin_channel_stats` SQLite table
- Counters are flushed from in-memory to SQLite every 60 seconds
- Automatic retention cleanup removes data older than 8 days
- Used by the **Channel Activity** chart in the Spectrum tab (RX Origin bars)

## Deduplication Endpoints

### GET `/api/wm1303/dedup/stats`

Returns deduplication statistics.

**Response:**
```json
{
  "total_seen": 15234,
  "total_dropped_duplicate": 892,
  "total_dropped_echo": 67,
  "total_forwarded": 14275,
  "per_channel": {
    "channel_a": { "rx": 5234, "dedup": 312, "forwarded": 4922 },
    "channel_b": { ... }
  }
}
```

### GET `/api/wm1303/dedup/events`

Returns recent dedup events for the dedup chart.

**Query parameters:**
- `period` — Time window
- `limit` — Max events

## System Endpoints

### GET `/api/wm1303/system/info`

Returns Raspberry Pi system information.

**Response:**
```json
{
  "hostname": "raspberrypi",
  "cpu_temp": 48.2,
  "memory_total_mb": 3904,
  "memory_used_pct": 34.5,
  "disk_total_gb": 29.7,
  "disk_used_pct": 12.8,
  "uptime_seconds": 86400,
  "kernel": "6.6.20+rpt-rpi-v8",
  "python_version": "3.11.2"
}
```

### POST `/api/wm1303/system/restart`

Restart the openhop-repeater service.

### POST `/api/wm1303/system/restart-pktfwd`

Restart only the lora_pkt_fwd process.

### POST `/api/wm1303/system/reset-hardware`

Trigger a hardware reset (GPIO power cycle of the SX1302).

## Configuration Endpoints

### GET `/api/wm1303/config`

Returns the full `wm1303_ui.json` configuration.

### PUT `/api/wm1303/config`

Replace the full `wm1303_ui.json` configuration.

> **Caution:** This replaces the entire configuration. Use channel-specific endpoints for partial updates.

### GET `/api/wm1303/config/rf-chains`

Returns RF chain configuration.

### GET `/api/wm1303/config/if-chains`

Returns IF chain configuration.

### GET `/api/wm1303/config/advanced`

Returns advanced settings (GPIO, SPI, paths).

## WebSocket

The system pushes real-time updates via WebSocket:

| Event | Data | Frequency |
|-------|------|-----------|
| `channel_stats` | Per-channel RX/TX/RSSI/SNR | ~1 second |
| `noise_floor` | Per-channel noise floor values | ~30 seconds |
| `dedup_event` | Individual dedup/echo events | Real-time |
| `cad_event` | CAD detection events | Real-time |
| `system_status` | pkt_fwd status, warnings | On change |

## Error Responses

```json
{
  "error": "Channel not found",
  "code": 404
}
```

| Code | Meaning |
|------|---------|
| 200 | Success |
| 400 | Invalid request / validation error |
| 401 | Missing or invalid JWT token |
| 404 | Resource not found |
| 500 | Internal server error |

## Rate Limiting

No rate limiting is applied. The API is intended for local network access only.

## Related Documents

- [`configuration.md`](./configuration.md) — Configuration files and SSOT model
- [`ui.md`](./ui.md) — WM1303 Manager UI (consumes this API)
- [`architecture.md`](./architecture.md) — System architecture
