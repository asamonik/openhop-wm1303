# SPI Troubleshooting Guide

This guide helps diagnose and resolve SPI communication issues between the Raspberry Pi and the WM1303/SX1302 LoRa concentrator HAT.

## Overview

The WM1303 HAT communicates with the Raspberry Pi via SPI (Serial Peripheral Interface). There are two SPI devices:

| Device | Default Path | Purpose |
|--------|-------------|----------|
| SX1302 concentrator | `/dev/spidev0.0` | Main concentrator chip (SX1302 + SX1250 radios) |
| SX1261 radio | `/dev/spidev0.1` | Auxiliary radio for LBT (Listen-Before-Talk) and Channel E RX |

## Common Issues

### 1. "Failed to set SX1250_0 in STANDBY_RC mode"

This is the most common error and means the SX1302 chip can communicate via SPI, but the SX1250 radio (connected through the SX1302) fails to respond.

**Possible causes:**
- GPIO reset sequence not completing correctly
- Power supply issue (insufficient current for the concentrator)
- SPI bus speed too high for the hardware
- Hardware connection issue (loose HAT, damaged pins)

**Diagnostic steps:**
```bash
# 1. Check if SPI devices exist
ls -la /dev/spidev*

# 2. Check if SPI is enabled
lsmod | grep spi

# 3. Verify GPIO export status
ls /sys/class/gpio/gpio*/

# 4. Try a manual power cycle
sudo ~/wm1303_pf/reset_lgw.sh stop
sleep 5
sudo ~/wm1303_pf/reset_lgw.sh start

# 5. Try a deep reset (60-second hardware drain)
sudo ~/wm1303_pf/reset_lgw.sh deep_reset
```

### 2. "Opening SPI communication interface" fails

The SPI device file doesn't exist or is inaccessible.

**Diagnostic steps:**
```bash
# Check available SPI devices
ls -la /dev/spidev*

# If no spidev devices exist, SPI is not enabled
# Enable it (Raspberry Pi OS):
sudo raspi-config nonint do_spi 0

# Enable it (Armbian / other distros):
# Check and edit /boot/armbianEnv.txt or /boot/config.txt
# Ensure dtparam=spi=on or equivalent overlay is active

# Verify after reboot:
ls -la /dev/spidev*
lsmod | grep spi
```

### 3. SX1302 chip not detected (no "chip version" in logs)

The SX1302 chip itself isn't responding via SPI.

**Diagnostic steps:**
```bash
# Check SPI device permissions
ls -la /dev/spidev0.*

# Ensure user is in the spi group
groups $(whoami)

# Add user to spi group if missing
sudo usermod -aG spi $(whoami)

# Check if another process is using the SPI bus
sudo fuser /dev/spidev0.0
```

## Finding Your SPI Device Numbers

Different HATs and boards may use different SPI bus and chip-select numbers.

```bash
# List all SPI devices
ls /dev/spidev*

# Common configurations:
# /dev/spidev0.0 + /dev/spidev0.1  → Most Pi HATs (SenseCAP M1, Pisces P100, etc.)
# /dev/spidev1.0 + /dev/spidev1.1  → Some alternative boards
# /dev/spidev32766.0               → Some Armbian/DT configurations

# Check which SPI controllers are available
ls /sys/class/spi_master/

# See detailed SPI device info
for d in /sys/class/spi_master/spi*/; do
    echo "=== $(basename $d) ==="
    cat "$d/of_node/compatible" 2>/dev/null || echo "(no compatible string)"
    ls "$d" 2>/dev/null
done

# Check device tree for SPI configuration
ls /proc/device-tree/soc/spi*/
```

If your SPI devices have different numbers, update `global_conf.json`:
```json
{
    "SX130x_conf": {
        "com_path": "/dev/spidev0.0",
        "sx1261_conf": {
            "spi_path": "/dev/spidev0.1"
        }
    }
}
```

## GPIO Pin Configuration

The WM1303 HAT requires four GPIO pins for hardware control:

| Function | Default BCM Pin | Description |
|----------|----------------|-------------|
| SX1302 Reset | BCM 17 | Resets the SX1302 concentrator chip |
| SX1302 Power Enable | BCM 18 | Controls power to the concentrator |
| SX1261 Reset | BCM 5 | Resets the SX1261 auxiliary radio |
| AD5338R Reset | BCM 13 | Resets the DAC (used for TX power control) |

### GPIO Base Offset

The GPIO sysfs numbers are calculated as: `sysfs_number = BCM_pin + base_offset`

| Platform / Kernel | Base Offset | Example (BCM17 → sysfs) |
|-------------------|-------------|-------------------------|
| RPi4/5, kernel 6.x+ | 512 | 512 + 17 = 529 |
| RPi4/5, kernel 5.x | 0 | 0 + 17 = 17 |
| Some Armbian builds | 0 or 512 | Varies by kernel |

**How to find your GPIO base offset:**
```bash
# Method 1: Check gpiochip base numbers
ls /sys/class/gpio/gpiochip*/
cat /sys/class/gpio/gpiochip*/base

# Method 2: Use gpiodetect (if installed)
sudo apt-get install -y gpiod
gpiodetect

# Method 3: Check device tree
cat /proc/device-tree/soc/gpio*/compatible 2>/dev/null

# Method 4: Use the pinctrl tool (Raspberry Pi)
pinctrl get | head -20
```

**Important:** GPIO pin numbers (BCM) are **hardware-dependent** (tied to the SoC, e.g., BCM2711 on RPi4), NOT operating system dependent. If you're using the same Raspberry Pi model, the BCM pin numbers are the same regardless of whether you run Raspberry Pi OS, Armbian, Ubuntu, or DietPi. Only the GPIO base offset in sysfs may differ between kernel versions.

### Configuring GPIO Pins in WM1303

You can configure GPIO pins through the WM1303 Manager UI:

1. Open the WM1303 Manager web interface
2. Go to the **Adv. Config** tab
3. Scroll to **GPIO Pin Configuration**
4. Set the correct **GPIO Base Offset** and **BCM pin numbers** for your board
5. Click **Save GPIO & Restart**

The UI will show a real-time preview of the calculated sysfs GPIO numbers.

## SPI Bus Speed

The WM1303 installation configures the SPI bus at 2 MHz (via `spi-stability.conf`), which provides sufficient bandwidth for the current implementation while maintaining signal integrity.

```bash
# Check current SPI speed configuration
cat /etc/modprobe.d/spi-stability.conf

# Check actual SPI clock during operation
# (requires logic analyzer or oscilloscope for precise measurement)

# If you experience SPI errors, you can try reducing the speed:
echo 'options spi_bcm2835 speed_hz=1000000' | sudo tee /etc/modprobe.d/spi-stability.conf
sudo reboot
```

## Board Compatibility

### Tested Boards

| Board | Status | Notes |
|-------|--------|-------|
| SenseCAP M1 (WM1302C) | ✅ Fully tested | Reference platform |
| Pisces P100 (GreenPalm GPML9932) | ⚠️ Community reported | Same SX1302+SX1250+SX1261 chipset; requires matching GPIO config |

### Requirements

For a board to work with WM1303, it needs:
- **SX1302** concentrator chip (verified by `chip version is 0x10`)
- **SX1250** radio(s) connected via the SX1302
- **SX1261** auxiliary radio (optional, but required for LBT and Channel E)
- **SPI interface** connected to the Raspberry Pi GPIO header
- **GPIO control lines** for power enable, reset, and auxiliary chip resets

### How to Check Your Board's Chipset

1. **Check the datasheet** for your HAT/board model
2. **Look at the chip markings** on the PCB (SX1302, SX1250, SX1261 printed on the ICs)
3. **Run the packet forwarder** with verbose logging — it will report detected chips:
   ```
   Note: chip version is 0x10 (v1.0)     ← SX1302 detected
   INFO: radio 0 enabled (type SX1250)    ← SX1250 detected
   INFO: Listen-Before-Talk with SX1261   ← SX1261 detected
   ```

## Quick Diagnostic Script

Run this one-liner to gather all SPI/GPIO diagnostic information:

```bash
echo "=== SPI Devices ===" && ls -la /dev/spidev* 2>/dev/null || echo "No SPI devices found" && \
echo "\n=== SPI Module ===" && lsmod | grep spi && \
echo "\n=== GPIO Chips ===" && ls /sys/class/gpio/gpiochip*/ 2>/dev/null && \
for chip in /sys/class/gpio/gpiochip*/; do echo "$chip base=$(cat ${chip}base) ngpio=$(cat ${chip}ngpio)"; done && \
echo "\n=== Exported GPIOs ===" && ls -d /sys/class/gpio/gpio[0-9]* 2>/dev/null || echo "None exported" && \
echo "\n=== SPI Speed Config ===" && cat /etc/modprobe.d/spi-stability.conf 2>/dev/null || echo "No custom SPI config" && \
echo "\n=== User Groups ===" && groups $(whoami)
```

## Getting Help

If you're still experiencing issues after following this guide:

1. Run the diagnostic script above and save the output
2. Check the packet forwarder logs: `sudo journalctl -u openhop-repeater -n 100`
3. Generate a debug bundle via the WM1303 Manager UI (Status tab → Debug Bundle)
4. Open an issue on GitHub with the diagnostic output and your board model/configuration
