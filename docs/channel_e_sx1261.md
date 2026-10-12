# Channel E & SX1261 Companion Radio

> Dedicated documentation for the 5th WM1303 channel implemented on the SX1261 companion chip

## Overview

Channel E is the most significant architectural extension in this project. Earlier WM1303 revisions used the SX1261 only as a **companion utility radio** for spectral scanning, LBT, and CAD support. In the current design, the SX1261 has been elevated into a **full LoRa RX/TX channel** in addition to its monitoring role.

This means the WM1303 platform now combines:

- **Channels A-D** on the SX1302/SX1303 concentrator IF path
- **Channel E** on the SX1261 companion radio path

The result is a hybrid architecture in which one physical module exposes both:

1. **Concentrator-style multi-channel reception** through SX1302 IF chains
2. **Single-radio LoRa transceiver behavior** through SX1261

Channel E is therefore not just "another channel". It is a separate radio path with its own capabilities, constraints, and implementation details.

## Why Channel E Exists

The main reason for introducing Channel E is that the SX1302 IF chains are effectively optimized around standard concentrator channel widths and do not provide the same flexibility for **sub-125 kHz bandwidths**.

Channel E solves this by using the SX1261 directly for:

- **Bandwidths below 125 kHz**, including **62.5 kHz**
- A separate LoRa RX/TX path outside the concentrator demodulator chain
- Additional bridge options for special-purpose channels
- A flexible "one extra radio" inside the same WM1303 hardware stack

## Architectural Role

## Hardware split

| Channel group | Hardware path | Typical role |
|---|---|---|
| Channel A-D | SX1302/SX1303 + SX1250 RF chain + IF demodulators | Main multi-channel bridge/repeater traffic |
| Channel E | SX1261 | Special channel, narrow bandwidth, utility radio, extra RX/TX path |

## Software split

| Layer | Channel A-D | Channel E |
|---|---|---|
| HAL / pkt_fwd | Concentrator RX/TX flow | SX1261-specific RX/TX flow integrated via overlay |
| Backend | VirtualLoRaRadio over concentrator backend | Dedicated Channel E handling plus bridge integration |
| UI/API | Standard channel cards and controls | Same UI model, but backed by a different hardware path |

## What makes Channel E different

### 1. Separate radio architecture

Channels A-D are virtualized from one concentrator backend. Channel E is not derived from an IF demodulator. It comes from the SX1261 companion radio.

### 2. Narrow-band support

Channel E is the channel used for **62.5 kHz support** and other bandwidths that are not a natural fit for the SX1302 IF-chain implementation.

### 3. Multi-purpose SX1261 usage

The SX1261 serves multiple roles simultaneously:

- **Mandatory CAD role** (since v2.1.0): hardware LoRa preamble detection before every TX on any channel
- **Monitoring role**: spectral scan, noise floor measurement
- **Optional LBT role**: per-channel RSSI-based listen-before-talk (when enabled)
- **Traffic role**: full LoRa RX/TX for Channel E

This makes scheduling, coordination, and documentation more important than for the A-D channels.

> **v2.1.0 optimization:** After each CAD scan, the SX1261 is restored via GPIO hardware reset + bulk PRAM write (single SPI transfer, ~42 ms reload). This resolved SX1261/SX1302 interference discovered during development.

## Capabilities of Channel E

Channel E is integrated into the same high-level management model as Channels A-D:

- Full RX path
- Full TX path
- Bridge participation
- UI configuration
- Metrics and status display
- Signal-quality visibility where available
- LBT/CAD behavior through the C-layer packet forwarder (mandatory CAD + optional LBT)

### Configurable parameters

Channel E is documented and exposed with the same type of channel settings as the other channels:

- Frequency
- Bandwidth
- Spreading Factor
- Coding Rate
- Preamble length
- TX power
- Active state
- LBT enable / threshold (optional, per channel)
- RX Boost (Channel E specific)

> **Note:** CAD is mandatory since v2.1.0 and cannot be disabled. There is no CAD toggle in the UI.

The configuration is sourced from **`/etc/openhop_repeater/wm1303_ui.json`**, keeping it aligned with the SSOT model used throughout the project.

## Bridge integration

Channel E participates in bridge rules just like the other channels.

Typical examples:

- Channel E -> Repeater
- Repeater -> Channel E
- Channel E -> Channel A/B/C/D
- Channel A/B/C/D -> Channel E

This makes Channel E useful as:

- A narrow-band ingress/egress channel
- An experiment channel for different PHY settings
- A compatibility or special-purpose path in mixed deployments

## UI integration

Channel E has become part of the visible management story:

- Included in channel totals and active-channel counts
- Included in Spectrum tab charts where relevant
- Included in packet activity views
- Included in live channel status and configuration handling
- Given distinct chart coloring for readability

From the user's perspective, Channel E should feel like a first-class channel, even though the underlying hardware path is very different.

## Relationship to spectral scan, LBT, and CAD

This is where the SX1261 story becomes especially important.

Historically, the SX1261 was used as the chip that supplied:

- Spectral scan results
- Noise floor inputs
- LBT RSSI data
- CAD support

Since v2.1.0, the SX1261 has an even more central role:

- **Mandatory CAD** — every TX on any channel triggers a hardware CAD scan on the SX1261
- **Optional LBT** — per-channel RSSI check after CAD (when enabled)
- **Spectral scan** — noise floor monitoring during TX-free windows
- **Channel E traffic** — full LoRa RX/TX for the 5th channel

The CAD and LBT logic runs in the **C-layer packet forwarder** (`lora_pkt_fwd.c`), not in Python. The spectral scan runs in a dedicated HAL thread. Channel E RX/TX is coordinated through the backend.

This multi-purpose design requires careful scheduling to avoid SX1261 contention. The project resolves this through:
- GPIO hardware reset + bulk PRAM reload cycle after each CAD scan (~42 ms)
- Abort + 5 ms delay + Standby sequence to prevent SX1261/SX1302 race conditions
- Spectral scan only during TX-free windows

## Important operational considerations

### RX priority still dominates

The project design principle remains that **RX availability is the highest priority**. Any SX1261-based feature must not degrade overall repeater RX performance unnecessarily.

### TX should stay short and timely

TX must still happen as quickly as possible. Scheduling and guard behavior should avoid unnecessary delays.

### Channel E is not "just IF5"

It should not be explained as if it were simply another concentrator demodulator. It is a distinct radio path with its own implementation and constraints.

## Documentation impact

Channel E affects multiple other documents:

| Document | Why it matters |
|---|---|
| `docs/architecture.md` | System is now effectively 4 concentrator channels + 1 SX1261 channel |
| `docs/hardware.md` | SX1261 is no longer only a scan/CAD chip |
| `docs/software.md` | Backend and bridge behavior now include Channel E-specific logic |
| `docs/configuration.md` | Channel model and SSOT behavior must mention Channel E |
| `docs/ui.md` | Charts, status, and controls include Channel E |
| `docs/api.md` | Channel-oriented endpoints may expose Channel E alongside A-D |

## Recommended mental model

Use this model when reading the rest of the repository documentation:

- **Channels A-D** = concentrator channels
- **Channel E** = companion-radio channel
- **Bridge engine** = unifies them into one forwarding domain
- **WM1303 Manager UI** = presents them in one operational model

That unified view is what makes the WM1303 project useful: the user gets one managed system, even though internally the hardware paths are different.

## Related documents

- [Hardware & HAL](hardware.md)
- [Software Components](software.md)
- [System Architecture](architecture.md)
- [LBT & CAD](lbt_cad.md)
- [Configuration Reference](configuration.md)
- [WM1303 API](api.md)
