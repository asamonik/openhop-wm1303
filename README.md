# OpenHop WM1303

A MeshCore bridge and repeater for SX1302/SX1303 concentrators. It connects up to
six LoRa channels with configurable forwarding, duplicate suppression, and a web
interface for radio settings, packet traces, and spectrum monitoring.

Based on [Hans van Meer's WM1303 integration](https://github.com/HansvanMeer/pyMC_WM1303),
[OpenHop core](https://github.com/openhop-dev/openhop_core), and
[OpenHop Repeater](https://github.com/openhop-dev/openhop_repeater).
The installer uses the revisions in [config/dependencies.conf](config/dependencies.conf).
This repository adds WM1303 hardware support to the current stable OpenHop
releases. Existing pyMC installation paths migrate automatically; see the
[migration details](docs/installation.md#migration-from-pymc-installation-paths).

## Hardware

The reference platform is a **SenseCAP M1** (Raspberry Pi 4 with WM1302/WM1303 HAT),
running Raspberry Pi OS Lite, Bookworm or newer. Installation requires SSH access
and an internet connection.

An onboard companion radio is required for channel activity detection; the tested
setup uses an **SX1261**. An SX1302/SX1303 concentrator alone is not enough.
RAK2287-based hotspots are incompatible. Other boards may need SPI and GPIO
adaptation; see the [hardware guide](docs/hardware.md).

## Install or upgrade

The same command handles a fresh installation or an existing deployment:

```bash
curl -fsSL https://raw.githubusercontent.com/asamonik/openhop-wm1303/main/bootstrap.sh | sudo bash
```

A fresh install prompts for region and channel preset. Upgrades preserve existing
settings. To select a region without the interactive wizard:

```bash
curl -fsSL https://raw.githubusercontent.com/asamonik/openhop-wm1303/main/bootstrap.sh | sudo env WM1303_REGION=EU868 bash
```

Open the WM1303 Manager at `http://<pi-ip>:8000/wm1303.html` (default port).
Hard-refresh the browser after upgrading.

See [installation and upgrades](docs/installation.md) for manual setup, region
options, existing `pyMC_WM1303` checkouts, and the GUI updater.

## Operation

- Channels A–D use the concentrator's multi-SF receivers; E uses the companion
  radio; F uses the concentrator's single-SF receiver.
- Bridge rules choose which packets are forwarded between channels.
- Channels share the transmit path. More target channels and slower LoRa settings
  increase airtime and reduce receive availability.
- The web interface provides channel settings, bridge rules, metrics, and
  diagnostics. Start with only the channels you need.

For radio details, see the [channel model](docs/radio.md) and
[transmit queues](docs/tx_queue.md). UI examples are in [screenshots/](screenshots/).

Service control and logs:

```bash
sudo systemctl status openhop-repeater
sudo systemctl restart openhop-repeater
journalctl -u openhop-repeater -f
```

## Documentation

Start with the [documentation index](docs/README.md), or go directly to:

- [Configuration](docs/configuration.md) and [Manager UI](docs/ui.md)
- [SPI and GPIO troubleshooting](docs/spi-troubleshooting.md)
- [Architecture and dependencies](docs/repositories.md)
- [Development and testing](docs/testing.md)
- [Release notes](release_notes/)

## License

Code is licensed under **PolyForm Noncommercial 1.0.0**; documentation and assets
under **CC BY-NC 4.0**. See [LICENSE](LICENSE) for terms and
[COMMERCIAL.md](COMMERCIAL.md) for commercial licensing.

Use at your own risk. Incorrect hardware configuration can damage radio equipment;
no responsibility is taken for resulting hardware damage.
