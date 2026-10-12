# =============================================================================
# openhop-wm1303 Installation Script
# =============================================================================
# Installs and configures WM1303 (SX1302/SX1303) LoRa concentrator module
# with MeshCore (openhop_core & openhop_repeater) on SenseCAP M1 / Raspberry Pi.
#
# Usage: sudo bash install.sh [--skip-update] [--skip-build]
#
# Prerequisites:
#   - Raspberry Pi OS Lite (Bookworm or newer)
#   - SPI enabled in /boot/firmware/config.txt
#   - Internet connectivity for package installation
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Colors and formatting
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

phase_num=0
step_count=0

# Log file for verbose output
LOG_FILE="/tmp/wm1303_install.log"


phase() {
    phase_num=$((phase_num + 1))
    step_count=0
    echo -e "\n${BOLD}${BLUE}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}${BLUE}  Phase ${phase_num}: $1${NC}"
    echo -e "${BOLD}${BLUE}═══════════════════════════════════════════════════════════════${NC}"
}

step() {
    step_count=$((step_count + 1))
    echo -ne "  ${CYAN}[${phase_num}.${step_count}]${NC} $1 ... "
}

ok() {
    echo -e "${GREEN}✓${NC} $1"
}

warn() {
    echo -e "${YELLOW}⚠${NC} $1"
}

fail() {
    echo -e "${RED}✗${NC} $1"
    echo -e "  ${RED}See ${LOG_FILE} for details${NC}"
    exit 1
}

info() {
    echo -e "  ${CYAN}ℹ${NC} $1"
}

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/config/deploy_overlay.sh"
LEGACY_INSTALL_BASE="/opt/pymc_repeater"
INSTALL_BASE="/opt/openhop_repeater"
REPO_DIR="${INSTALL_BASE}/repos"
VENV_DIR="${INSTALL_BASE}/venv"
CONFIG_DIR="/etc/openhop_repeater"
LOG_DIR="/var/log/openhop_repeater"
DATA_DIR="/var/lib/openhop_repeater"
# PKTFWD_DIR and HAL_DIR are set after user detection (see below)

# Canonical sources and immutable, jointly verified dependency revisions.
source "${SCRIPT_DIR}/config/dependencies.conf"

# Parse arguments
SKIP_UPDATE=false
SKIP_BUILD=false
FORCE_USER=""
for arg in "$@"; do
    case "$arg" in
        --skip-update) SKIP_UPDATE=true ;;
        --skip-build)  SKIP_BUILD=true ;;
        --user=*)      FORCE_USER="${arg#--user=}" ;;
        --help|-h)
            echo "Usage: sudo bash install.sh [--skip-update] [--skip-build] [--user=<username>]"
            echo "  --skip-update    Skip apt update/upgrade"
            echo "  --skip-build     Skip HAL/packet forwarder build"
            echo "  --user=<name>    Force install for specific user (default: auto-detect)"
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            exit 2
            ;;
    esac
done

# Installation state tracking
REBOOT_REQUIRED=false
INSTALL_SUCCESS=false

# Trap to handle installation failures
cleanup_on_failure() {
    if [ "$INSTALL_SUCCESS" = false ]; then
        echo ""
        echo -e "  ${BOLD}${RED}╔══════════════════════════════════════════════════════════╗${NC}"
        echo -e "  ${BOLD}${RED}║     Installation FAILED!                                 ║${NC}"
        echo -e "  ${BOLD}${RED}╚══════════════════════════════════════════════════════════╝${NC}"
        echo ""
        echo -e "  ${RED}The installation encountered an error and could not complete.${NC}"
        echo -e "  ${RED}Check ${LOG_FILE} for detailed output.${NC}"
        echo ""
        echo -e "  ${BOLD}To retry:${NC}  sudo bash install.sh"
        echo -e "  ${BOLD}For help:${NC}  Check the documentation in docs/installation.md"
        echo ""
    fi
}
trap cleanup_on_failure EXIT

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
echo -e "${BOLD}${GREEN}"
echo "  ╔══════════════════════════════════════════════════════════╗"
echo "  ║     openhop-wm1303 Installation                          ║"
echo "  ║     WM1303 LoRa Concentrator + MeshCore Repeater         ║"
echo "  ╚══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

if [ "$(id -u)" -ne 0 ]; then
    fail "This script must be run as root (sudo bash install.sh)"
fi
LOG_FILE=$(mktemp /tmp/wm1303_install.XXXXXX.log)
# Bootstrap runs this script without stdin; dependency tools must not prompt.
export DEBIAN_FRONTEND=noninteractive

# ---------------------------------------------------------------------------
# Detect target user (the non-root user who will own the installation)
# Priority: --user=<name> > SUDO_USER > first non-root user with a home dir
# ---------------------------------------------------------------------------
detect_user() {
    # 1. Explicit --user=<name> argument
    if [ -n "$FORCE_USER" ]; then
        if id "$FORCE_USER" &>/dev/null; then
            echo "$FORCE_USER"
            return
        else
            fail "Specified user '$FORCE_USER' does not exist."
        fi
    fi

    # 2. SUDO_USER (set by sudo when a regular user runs 'sudo bash install.sh')
    if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ] && id "$SUDO_USER" &>/dev/null; then
        echo "$SUDO_USER"
        return
    fi

    # 3. Check for common default users
    for candidate in pi orangepi radxa rock dietpi; do
        if id "$candidate" &>/dev/null; then
            echo "$candidate"
            return
        fi
    done

    # 4. First non-root user with UID >= 1000 and a valid home directory
    local found
    found=$(awk -F: '$3 >= 1000 && $3 < 65534 && $6 != "/" && $7 !~ /nologin|false/ {print $1; exit}' /etc/passwd)
    if [ -n "$found" ] && id "$found" &>/dev/null; then
        echo "$found"
        return
    fi

    # 5. No suitable user found
    fail "Could not detect a non-root user. Please specify one with --user=<username>\n  Example: sudo bash install.sh --user=myuser"
}

PI_USER=$(detect_user)
if [ "$(id -u "${PI_USER}")" -eq 0 ]; then
    fail "The service must run as a non-root user."
fi
PI_GROUP=$(id -gn "${PI_USER}")

# Resolve the account database directly; do not evaluate the account name.
PI_HOME=$(getent passwd "${PI_USER}" 2>/dev/null | cut -d: -f6)

if [ -z "${PI_HOME}" ] || [ "${PI_HOME}" = "~${PI_USER}" ]; then
    fail "Could not determine home directory for user '${PI_USER}'."
fi

if [ ! -d "${PI_HOME}" ]; then
    fail "Home directory '${PI_HOME}' for user '${PI_USER}' does not exist."
fi

info "Detected target user: ${PI_USER} (home: ${PI_HOME})"

# Set user-relative paths
PKTFWD_DIR="${PI_HOME}/wm1303_pf"
HAL_DIR="${PI_HOME}/sx1302_hal"

# Serialize manual install/upgrade runs as well as detached GUI updates.
exec 8>/run/lock/openhop-wm1303-install.lock
flock -n 8 || fail "Another OpenHop install or upgrade is already running"

# Check migration conflicts while the existing daemon is still running.
python3 "${SCRIPT_DIR}/config/migrate_openhop.py" check --home "${PI_HOME}" >> "${LOG_FILE}" 2>&1 || fail "OpenHop path preflight failed; see ${LOG_FILE}"
if [ "$SKIP_BUILD" = true ] && ! packet_forwarder_uses_openhop_paths "${PKTFWD_DIR}/lora_pkt_fwd"; then
    fail "--skip-build requires a packet forwarder using OpenHop runtime paths; rerun without --skip-build"
fi
EXISTING_CORE_DIR=$(existing_dependency_checkout "$INSTALL_BASE" openhop_core pyMC_core)
EXISTING_REPEATER_DIR=$(existing_dependency_checkout "$INSTALL_BASE" openhop_repeater pyMC_Repeater)

# Refuse unexpected sources before package changes or service interruption.
[ ! -d "${HAL_DIR}/.git" ] || require_dependency_origin "${HAL_DIR}" "${HAL_REPO}" || fail "HAL origin check failed"
[ ! -d "${EXISTING_CORE_DIR}/.git" ] || require_dependency_origin "${EXISTING_CORE_DIR}" "${CORE_REPO}" || fail "Core origin check failed"
[ ! -d "${EXISTING_REPEATER_DIR}/.git" ] || require_dependency_origin "${EXISTING_REPEATER_DIR}" "${REPEATER_REPO}" || fail "Repeater origin check failed"

INSTALL_VERSION="unknown"
if [ -f "${SCRIPT_DIR}/VERSION" ]; then
    INSTALL_VERSION="v$(cat "${SCRIPT_DIR}/VERSION")"
fi
info "Installing version: ${INSTALL_VERSION}"
info "Installation directory: ${INSTALL_BASE}"
info "Configuration directory: ${CONFIG_DIR}"
info "Log file: ${LOG_FILE}"

# =============================================================================
# Phase 1: System Prerequisites
# =============================================================================
phase "System Prerequisites"

if [ "$SKIP_UPDATE" = false ]; then
    step "Updating package lists"
    if ! apt-get update -y >> "${LOG_FILE}" 2>&1; then
        fail "Package list update failed"
    fi
    ok "Done"

    step "Upgrading installed packages"
    if ! apt-get -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold upgrade -y >> "${LOG_FILE}" 2>&1; then
        fail "Package upgrade failed"
    fi
    ok "Done"
else
    step "Skipping system update (--skip-update)"
    ok "Skipped"
fi

step "Installing build tools and dependencies"
if ! apt-get install -y \
    build-essential \
    gcc \
    make \
    git \
    rsync \
    util-linux \
    python3 \
    python3-dev \
    python3-pip \
    python3-venv \
    python3-setuptools \
    libffi-dev \
    libssl-dev \
    jq \
    i2c-tools \
    rrdtool \
    librrd-dev \
    python3-rrdtool \
    python3-systemd \
    >> "${LOG_FILE}" 2>&1; then
    fail "Dependency installation failed"
fi
ok "Done"

# NTP packages (optional - Debian 13+ uses systemd-timesyncd)
step "Installing NTP client"
if apt-get install -y ntpdate ntp >> "${LOG_FILE}" 2>&1; then
    ok "NTP packages installed"
else
    ok "Using systemd-timesyncd"
fi

step "Verifying Python 3 version"
PYTHON_VERSION=$(python3 --version 2>&1)
ok "${PYTHON_VERSION}"

step "Configuring passwordless sudo for ${PI_USER}"
# A pre-existing rule for pi says nothing about another selected service user.
# Use a UID-based filename (sudo ignores include filenames containing dots).
SUDOERS_FILE="/etc/sudoers.d/090_wm1303-$(id -u "${PI_USER}")"
SUDOERS_TMP=$(mktemp /etc/sudoers.d/.wm1303.XXXXXX)
printf '%s ALL=(ALL) NOPASSWD: ALL\n' "${PI_USER}" > "${SUDOERS_TMP}"
chown root:root "${SUDOERS_TMP}"
chmod 440 "${SUDOERS_TMP}"
if ! visudo -cf "${SUDOERS_TMP}" >> "${LOG_FILE}" 2>&1; then
    rm -f -- "${SUDOERS_TMP}"
    fail "Invalid sudo configuration for ${PI_USER}"
fi
mv -f -- "${SUDOERS_TMP}" "${SUDOERS_FILE}"
ok "Configured for ${PI_USER}"

step "Adding ${PI_USER} to hardware access groups"
usermod -aG spi,i2c,gpio,dialout ${PI_USER} 2>/dev/null || true
ok "Done"


# =============================================================================
# Phase 2: SPI & I2C Configuration
# =============================================================================
phase "SPI & I2C Configuration Check"

step "Checking SPI kernel module"
if lsmod | grep -q spi_bcm2835 || lsmod | grep -q spidev; then
    ok "SPI kernel module loaded"
else
    modprobe spidev 2>/dev/null || true
    warn "SPI kernel module not detected (loaded spidev)"
fi

step "Checking SPI device nodes"
if [ -e /dev/spidev0.0 ] && [ -e /dev/spidev0.1 ]; then
    ok "SPI devices found"
else
    BOOT_CONFIG="/boot/firmware/config.txt"
    [ ! -f "$BOOT_CONFIG" ] && BOOT_CONFIG="/boot/config.txt"

    if [ -f "$BOOT_CONFIG" ]; then
        if grep -q "^dtparam=spi=on" "$BOOT_CONFIG"; then
            warn "SPI enabled in config.txt but devices not present. Reboot required."
            REBOOT_REQUIRED=true
        else
            sed -i '/^#.*dtparam=spi/d' "$BOOT_CONFIG"
            if grep -q '^\[' "$BOOT_CONFIG"; then
                sed -i '0,/^\[/{s/^\[/dtparam=spi=on\n\n[/}' "$BOOT_CONFIG"
            else
                echo "dtparam=spi=on" >> "$BOOT_CONFIG"
            fi
            ok "SPI enabled in config.txt"
            warn "Reboot required for SPI!"
            REBOOT_REQUIRED=true
        fi
    else
        fail "Cannot find boot config file. Please enable SPI manually."
    fi
fi

step "Checking SPI overlay for SenseCAP M1 Pi HAT"
BOOT_CONFIG="/boot/firmware/config.txt"
[ ! -f "$BOOT_CONFIG" ] && BOOT_CONFIG="/boot/config.txt"
if [ -f "$BOOT_CONFIG" ]; then
    if ! grep -q "dtoverlay=spi0-1cs" "$BOOT_CONFIG" && ! grep -q "dtoverlay=spi0-2cs" "$BOOT_CONFIG"; then
        ok "Default SPI configuration"
    else
        ok "SPI overlay configured"
    fi
fi

step "Configuring SPI buffer size (spidev bufsiz=32768)"
SPIDEV_CONF="/etc/modprobe.d/spidev.conf"
CMDLINE_FILE="/boot/firmware/cmdline.txt"
SPIDEV_PARAM="spidev.bufsiz=32768"

# Method 1: modprobe.d (works on older kernels)
if [ -f "$SPIDEV_CONF" ] && grep -q "bufsiz=32768" "$SPIDEV_CONF"; then
    ok "modprobe.d spidev bufsiz already configured"
else
    echo "options spidev bufsiz=32768" > "$SPIDEV_CONF"
    ok "modprobe.d spidev bufsiz set to 32768"
fi

# Method 2: kernel cmdline (required for Debian Trixie+ where spidev loads before modprobe.d)
if [ -f "$CMDLINE_FILE" ]; then
    if grep -q "$SPIDEV_PARAM" "$CMDLINE_FILE"; then
        ok "Kernel cmdline spidev.bufsiz already configured"
    else
        sudo sed -i "s/$/ ${SPIDEV_PARAM}/" "$CMDLINE_FILE"
        ok "Added spidev.bufsiz=32768 to kernel cmdline"
    fi
else
    warn "$CMDLINE_FILE not found — skipping kernel cmdline method"
fi

if [ "$(cat /sys/module/spidev/parameters/bufsiz 2>/dev/null)" != "32768" ]; then
    warn "Reboot required for spidev bufsiz change to take effect"
    REBOOT_REQUIRED=true
fi

step "Configuring VPU core_freq_min=500 (stable SPI clock)"
BOOT_CONFIG="/boot/firmware/config.txt"
[ ! -f "$BOOT_CONFIG" ] && BOOT_CONFIG="/boot/config.txt"
if [ -f "$BOOT_CONFIG" ]; then
    if grep -q "^core_freq_min=500" "$BOOT_CONFIG"; then
        ok "Already configured"
    elif grep -q "^core_freq_min=" "$BOOT_CONFIG"; then
        # Replace existing value
        sed -i 's/^core_freq_min=.*/core_freq_min=500/' "$BOOT_CONFIG"
        ok "Updated to 500 (was different)"
        REBOOT_REQUIRED=true
    else
        # Add before any [section] or at end
        if grep -q '^\[' "$BOOT_CONFIG"; then
            sed -i '0,/^\[/{s/^\[/# Lock VPU core clock for stable SPI bus timing\ncore_freq_min=500\n\n[/}' "$BOOT_CONFIG"
        else
            echo "" >> "$BOOT_CONFIG"
            echo "# Lock VPU core clock for stable SPI bus timing" >> "$BOOT_CONFIG"
            echo "core_freq_min=500" >> "$BOOT_CONFIG"
        fi
        ok "Added to config.txt"
        REBOOT_REQUIRED=true
    fi
    # Verify current runtime value
    CURRENT_CORE_FREQ=$(vcgencmd measure_clock core 2>/dev/null | grep -oP '=\K[0-9]+' || echo "unknown")
    if [ "$CURRENT_CORE_FREQ" != "unknown" ]; then
        CORE_MHZ=$((CURRENT_CORE_FREQ / 1000000))
        info "Current VPU core frequency: ${CORE_MHZ} MHz"
    fi
else
    warn "Boot config not found — please add core_freq_min=500 manually"
fi

step "Configuring gpu_mem=16 (headless optimisation)"
BOOT_CONFIG="/boot/firmware/config.txt"
[ ! -f "$BOOT_CONFIG" ] && BOOT_CONFIG="/boot/config.txt"
if [ -f "$BOOT_CONFIG" ]; then
    if grep -q "^gpu_mem=16" "$BOOT_CONFIG"; then
        ok "Already configured"
    elif grep -q "^gpu_mem=" "$BOOT_CONFIG"; then
        # Replace existing value
        sed -i 's/^gpu_mem=.*/gpu_mem=16/' "$BOOT_CONFIG"
        ok "Updated to 16 (was different)"
        REBOOT_REQUIRED=true
    else
        # Add before any [section] or at end
        if grep -q '^\[' "$BOOT_CONFIG"; then
            sed -i '0,/^\[/{s/^\[/# Minimise GPU memory for headless operation\ngpu_mem=16\n\n[/}' "$BOOT_CONFIG"
        else
            echo "" >> "$BOOT_CONFIG"
            echo "# Minimise GPU memory for headless operation" >> "$BOOT_CONFIG"
            echo "gpu_mem=16" >> "$BOOT_CONFIG"
        fi
        ok "Added to config.txt"
        REBOOT_REQUIRED=true
    fi
else
    warn "Boot config not found — please add gpu_mem=16 manually"
fi

step "Configuring SPI polling_limit_us=250 (persistent)"
SPI_BCM_CONF="/etc/modprobe.d/spi-bcm2835-opts.conf"
if [ -f "$SPI_BCM_CONF" ] && grep -q "polling_limit_us=250" "$SPI_BCM_CONF"; then
    ok "Already configured"
else
    echo "options spi_bcm2835 polling_limit_us=250" > "$SPI_BCM_CONF"
    ok "Set polling_limit_us=250"
fi
# Apply at runtime immediately if module is loaded
SPI_POLL_PARAM="/sys/module/spi_bcm2835/parameters/polling_limit_us"
if [ -f "$SPI_POLL_PARAM" ]; then
    CURRENT_POLL=$(cat "$SPI_POLL_PARAM" 2>/dev/null)
    if [ "$CURRENT_POLL" != "250" ]; then
        echo 250 > "$SPI_POLL_PARAM" 2>/dev/null
        info "Runtime polling_limit_us: ${CURRENT_POLL} -> 250"
    else
        info "Runtime polling_limit_us: already 250"
    fi
fi

step "Setting CPU governor to performance"
GOV_CHANGED=0
GOV_TOTAL=0
for gov_file in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    [ -f "$gov_file" ] || continue
    GOV_TOTAL=$((GOV_TOTAL + 1))
    current=$(cat "$gov_file" 2>/dev/null)
    if [ "$current" != "performance" ]; then
        echo "performance" > "$gov_file" 2>/dev/null && GOV_CHANGED=$((GOV_CHANGED + 1))
    fi
done
if [ $GOV_TOTAL -eq 0 ]; then
    warn "No CPU governor files found"
elif [ $GOV_CHANGED -gt 0 ]; then
    ok "Set to 'performance' on ${GOV_CHANGED}/${GOV_TOTAL} cores"
else
    ok "Already 'performance' on all ${GOV_TOTAL} cores"
fi


step "Checking I2C for WM1303 temperature sensor and AD5338R DAC"
if [ -e /dev/i2c-1 ]; then
    ok "I2C device found"
else
    modprobe i2c-dev 2>/dev/null || true
    modprobe i2c-bcm2835 2>/dev/null || true
    echo "i2c-dev" > /etc/modules-load.d/i2c-dev.conf

    BOOT_CONFIG="/boot/firmware/config.txt"
    [ ! -f "$BOOT_CONFIG" ] && BOOT_CONFIG="/boot/config.txt"

    if [ -f "$BOOT_CONFIG" ]; then
        if grep -q "^dtparam=i2c_arm=on" "$BOOT_CONFIG"; then
            warn "I2C enabled but /dev/i2c-1 not present. Reboot required."
            REBOOT_REQUIRED=true
        else
            sed -i '/^#.*dtparam=i2c_arm/d' "$BOOT_CONFIG"
            if grep -q '^\[' "$BOOT_CONFIG"; then
                sed -i '0,/^\[/{s/^\[/# enable I2C for WM1303 temperature sensor and AD5338R DAC\ndtparam=i2c_arm=on\n\n[/}' "$BOOT_CONFIG"
            else
                echo "# enable I2C for WM1303 temperature sensor and AD5338R DAC" >> "$BOOT_CONFIG"
                echo "dtparam=i2c_arm=on" >> "$BOOT_CONFIG"
            fi
            ok "I2C enabled in config.txt"
            warn "Reboot required for I2C!"
            REBOOT_REQUIRED=true
        fi
    else
        fail "Cannot find boot config file. Please enable I2C manually."
    fi
fi


# ---------------------------------------------------------------------------
# WiFi Power Save Check (prevents SSH/network dropouts on wireless links)
# ---------------------------------------------------------------------------
step "Checking WiFi power save settings"
WIFI_IFACE=""
for iface in /sys/class/net/wlan*; do
    [ -d "$iface" ] && WIFI_IFACE=$(basename "$iface") && break
done

if [ -z "$WIFI_IFACE" ]; then
    ok "No WiFi interface detected — skipping"
else
    # Check current power save status
    PWRSAVE="unknown"
    if command -v iw >/dev/null 2>&1; then
        PWRSAVE=$(iw dev "$WIFI_IFACE" get power_save 2>/dev/null | awk '{print $NF}' || echo "unknown")
    fi

    if [ "$PWRSAVE" = "off" ]; then
        ok "WiFi power save already disabled on $WIFI_IFACE"
    else
        # Disable immediately
        if command -v iw >/dev/null 2>&1; then
            iw dev "$WIFI_IFACE" set power_save off >> "${LOG_FILE}" 2>&1 || true
        fi

        # Make persistent — try multiple methods for OS compatibility
        WIFI_PS_PERSISTED=false

        # Method 1: NetworkManager (Debian/Ubuntu with NM)
        if command -v nmcli >/dev/null 2>&1 && systemctl is-active --quiet NetworkManager 2>/dev/null; then
            NM_CONF_DIR="/etc/NetworkManager/conf.d"
            NM_CONF_FILE="${NM_CONF_DIR}/99-wifi-powersave-off.conf"
            if [ ! -f "$NM_CONF_FILE" ]; then
                mkdir -p "$NM_CONF_DIR"
                cat > "$NM_CONF_FILE" << 'NMEOF'
[connection]
wifi.powersave = 2
NMEOF
                # NM powersave: 2 = disable, 3 = enable, 0 = default
                systemctl restart NetworkManager >> "${LOG_FILE}" 2>&1 || true
            fi
            WIFI_PS_PERSISTED=true
            ok "WiFi power save disabled on $WIFI_IFACE (NetworkManager)"
        fi

        # Method 2: dhcpcd hook (Raspberry Pi OS Bookworm and older)
        if [ "$WIFI_PS_PERSISTED" = false ] && [ -d "/etc/dhcpcd.conf" ] || [ -f "/etc/dhcpcd.conf" ]; then
            DHCPCD_HOOK="/etc/dhcpcd.exit-hook"
            HOOK_LINE='command -v iw >/dev/null 2>&1 && iw dev wlan0 set power_save off 2>/dev/null || true'
            if [ -f "$DHCPCD_HOOK" ] && grep -q "power_save off" "$DHCPCD_HOOK" 2>/dev/null; then
                WIFI_PS_PERSISTED=true
                ok "WiFi power save disabled on $WIFI_IFACE (dhcpcd hook exists)"
            else
                echo "$HOOK_LINE" >> "$DHCPCD_HOOK"
                chmod +x "$DHCPCD_HOOK"
                WIFI_PS_PERSISTED=true
                ok "WiFi power save disabled on $WIFI_IFACE (dhcpcd hook)"
            fi
        fi

        # Method 3: udev rule (generic fallback for systemd-networkd or other setups)
        if [ "$WIFI_PS_PERSISTED" = false ]; then
            UDEV_RULE="/etc/udev/rules.d/70-wifi-powersave.rules"
            if [ ! -f "$UDEV_RULE" ]; then
                cat > "$UDEV_RULE" << 'UDEVEOF'
# Disable WiFi power save for network stability
ACTION=="add", SUBSYSTEM=="net", KERNEL=="wlan*", RUN+="/usr/sbin/iw dev %k set power_save off"
UDEVEOF
                udevadm control --reload-rules >> "${LOG_FILE}" 2>&1 || true
            fi
            WIFI_PS_PERSISTED=true
            ok "WiFi power save disabled on $WIFI_IFACE (udev rule)"
        fi

        if [ "$WIFI_PS_PERSISTED" = false ]; then
            warn "Could not persist WiFi power save setting — disable manually if needed"
        fi
    fi
fi


# =============================================================================
# Phase 3: Directory Structure
# =============================================================================
phase "Directory Structure Creation"

# A repeated install may encounter an existing service using the files below.
step "Stopping existing radio services before updating their files"
for service_unit in openhop-plugin-manager.service openhop-repeater.service pymc-repeater.service lora_pkt_fwd.service; do
    if systemctl is-active --quiet "${service_unit}" 2>/dev/null; then
        systemctl stop "${service_unit}" >> "${LOG_FILE}" 2>&1
    fi
done
ok "Radio services stopped"

step "Creating installation directories"
# The shared migration preserves identities, WAL files and local checkouts.
# Services are stopped; preflight is repeated immediately before the moves.
step "Migrating installed paths to OpenHop"
python3 "${SCRIPT_DIR}/config/migrate_openhop.py" apply --home "${PI_HOME}" >> "${LOG_FILE}" 2>&1 || fail "OpenHop migration failed; see ${LOG_FILE} for retained recovery paths"
# Older installations may keep this integration checkout inside INSTALL_BASE.
case "$SCRIPT_DIR" in
    "$LEGACY_INSTALL_BASE"/*) SCRIPT_DIR="$INSTALL_BASE/${SCRIPT_DIR#"$LEGACY_INSTALL_BASE"/}" ;;
esac
OVERLAY_DIR="${SCRIPT_DIR}/overlay"
ok "OpenHop paths active; any previous venv retained as venv.pre-openhop"
mkdir -p "${REPO_DIR}" "${CONFIG_DIR}" "${PKTFWD_DIR}"
step "Installing SPI optimization service script"
install -m 755 "${SCRIPT_DIR}/config/spi_optimize.sh" "${INSTALL_BASE}/spi_optimize.sh"
ok "Installed (runs at every service start)"

# --- OpenHop stale-DB quarantine (v2.7.5+, TODO #228) -------------------------
# When ${DATA_DIR}/repeater.db exists from an OLD openhop_repeater install with
# a stale schema (e.g. channel_stats_history has 'pkt_count' but no 'rx_count'
# column), quarantine it so the service creates a fresh DB with the current
# schema at first start. Prevents the recurring
# "table channel_stats_history has no column named rx_count" crash where the
# 60s snapshot loop fails and UI graphs stay empty.
_quarantine_stale_openhop_db() {
    local db="${DATA_DIR}/repeater.db"
    [ -f "${db}" ] || return 0
    command -v sqlite3 >/dev/null 2>&1 || return 0
    local schema
    schema=$(sqlite3 "${db}" ".schema channel_stats_history" 2>/dev/null || echo "")
    if [ -n "${schema}" ] && ! echo "${schema}" | grep -q 'rx_count'; then
        local ts=$(date +%Y%m%d_%H%M%S)
        mv "${db}" "${db}.stale_${ts}" >> "${LOG_FILE}" 2>&1 || true
        [ -f "${db}-shm" ] && mv "${db}-shm" "${db}-shm.stale_${ts}" >> "${LOG_FILE}" 2>&1 || true
        [ -f "${db}-wal" ] && mv "${db}-wal" "${db}-wal.stale_${ts}" >> "${LOG_FILE}" 2>&1 || true
        ok "Quarantined stale ${db} (missing rx_count) -> ${db}.stale_${ts}"
    fi
}
_quarantine_stale_openhop_db

mkdir -p "${LOG_DIR}"
mkdir -p "${DATA_DIR}"
mkdir -p "${PI_HOME}/backups"
ok "Created"

step "Setting directory ownership"
chown -R ${PI_USER}:${PI_GROUP} "${INSTALL_BASE}"
chown -R ${PI_USER}:${PI_GROUP} "${PKTFWD_DIR}"
chown -R ${PI_USER}:${PI_GROUP} "${LOG_DIR}"
chown -R ${PI_USER}:${PI_GROUP} "${DATA_DIR}"
chown -R ${PI_USER}:${PI_GROUP} "${CONFIG_DIR}"
ok "Ownership set"

# =============================================================================
# Phase 4: Clone Repositories
# =============================================================================
phase "Clone Repositories"

clone_or_update_repo() {
    local repo_url="$1" target_dir="$2" branch="$3" revision="$4" version_tag="$5"
    local name
    name=$(basename "$target_dir") || fail "Cannot identify repository $target_dir"
    if [ ! -d "$target_dir/.git" ]; then
        sudo -u "$PI_USER" git clone --no-checkout --no-tags --single-branch --branch "$branch" \
            "$repo_url" "$target_dir" >> "$LOG_FILE" 2>&1 || fail "Failed to clone $name"
    fi
    chown -R "$PI_USER:$PI_GROUP" "$target_dir" || fail "Cannot set repository ownership for $name"
    checkout_dependency_revision "$target_dir" "$repo_url" "$revision" "$version_tag" \
        >> "$LOG_FILE" 2>&1 || fail "Failed to fetch or verify pinned revision for $name; inspect $LOG_FILE"
    ok "$name prepared at ${revision:0:8} (local changes retained in Git stash)"
}

step "Preparing Semtech HAL 2.1.0"
clone_or_update_repo "${HAL_REPO}" "${HAL_DIR}" "${HAL_BRANCH}" "${HAL_REVISION}" "${HAL_TAG}"

step "Preparing OpenHop core ${CORE_TAG}"
clone_or_update_repo "${CORE_REPO}" "${REPO_DIR}/openhop_core" "${CORE_BRANCH}" "${CORE_REVISION}" "${CORE_TAG}"

step "Preparing OpenHop Repeater ${REPEATER_TAG}"
clone_or_update_repo "${REPEATER_REPO}" "${REPO_DIR}/openhop_repeater" "${REPEATER_BRANCH}" "${REPEATER_REVISION}" "${REPEATER_TAG}"


# =============================================================================
# Phase 5: Apply Overlay Modifications
# =============================================================================
phase "Apply Overlay Modifications"

OVERLAY_DIR="${SCRIPT_DIR}/overlay"

if [ ! -d "${OVERLAY_DIR}" ]; then
    fail "Overlay directory not found at ${OVERLAY_DIR}"
fi

step "Applying HAL overlay"
deploy_overlay "${OVERLAY_DIR}/hal" "${HAL_DIR}" >> "${LOG_FILE}" 2>&1
ok "HAL overlay applied"

step "Applying openhop_core overlay"
CORE_ROOT_DIR="${REPO_DIR}/openhop_core/src/openhop_core"
deploy_overlay "${OVERLAY_DIR}/openhop_core/src/openhop_core" "${CORE_ROOT_DIR}" >> "${LOG_FILE}" 2>&1
ok "openhop_core overlay applied"

step "Applying openhop_repeater overlay"
RPT_DIR="${REPO_DIR}/openhop_repeater"
deploy_overlay "${OVERLAY_DIR}/openhop_repeater/repeater" "${RPT_DIR}/repeater" >> "${LOG_FILE}" 2>&1
ok "openhop_repeater overlay applied"


chown -R ${PI_USER}:${PI_GROUP} "${HAL_DIR}"
chown -R ${PI_USER}:${PI_GROUP} "${REPO_DIR}"

# Post-build UI patch: fix Observer/MQTT-tab save bugs in the shipped (minified)
# SPA assets (port=0 default and disallowedInput field-name mismatch). The Vue
# source is not in this repo and there is no npm build step, so the built assets
# are patched in place. Idempotent; safe to run on every install/upgrade.
step "Patching UI assets (Observer/MQTT save fix)"
if [ -f "${SCRIPT_DIR}/_tools/patch_ui_observer_save.sh" ]; then
    bash "${SCRIPT_DIR}/_tools/patch_ui_observer_save.sh" "${RPT_DIR}" >> "${LOG_FILE}" 2>&1 || true
    ok "UI Observer save patch applied"
else
    ok "Patch script not found — skipped"
fi

# =============================================================================
# Phase 6: Build HAL & Packet Forwarder
# =============================================================================
phase "Build HAL & Packet Forwarder"

if [ "$SKIP_BUILD" = false ]; then
    BUILD_JOBS=$(nproc) || fail "Cannot determine HAL build parallelism"
    step "Cleaning previous HAL build artifacts"
    cd "${HAL_DIR}"
    sudo -u ${PI_USER} make clean >> "${LOG_FILE}" 2>&1 || true
    ok "Cleaned"

    step "Building libtools"
    cd "${HAL_DIR}"
    if ! sudo -u "${PI_USER}" make -C libtools "-j${BUILD_JOBS}" >> "${LOG_FILE}" 2>&1; then
        fail "libtools build failed"
    fi
    ok "Built"

    step "Building libloragw"
    cd "${HAL_DIR}"
    if ! sudo -u "${PI_USER}" make -C libloragw "-j${BUILD_JOBS}" >> "${LOG_FILE}" 2>&1; then
        fail "libloragw build failed"
    fi
    ok "Built"

    step "Building lora_pkt_fwd"
    cd "${HAL_DIR}"
    if ! sudo -u "${PI_USER}" make -C packet_forwarder "-j${BUILD_JOBS}" >> "${LOG_FILE}" 2>&1; then
        fail "packet_forwarder build failed"
    fi
    ok "Built"

    step "Installing packet forwarder binary"
    cp "${HAL_DIR}/packet_forwarder/lora_pkt_fwd" "${PKTFWD_DIR}/" >> "${LOG_FILE}" 2>&1
    chown ${PI_USER}:${PI_GROUP} "${PKTFWD_DIR}/lora_pkt_fwd"
    chmod 755 "${PKTFWD_DIR}/lora_pkt_fwd"
    ok "Installed"

    step "Building spectral_scan utility"
    if ! sudo -u "${PI_USER}" make -C util_spectral_scan "-j${BUILD_JOBS}" >> "${LOG_FILE}" 2>&1; then
        fail "spectral_scan build failed"
    fi
    ok "Built"

    step "Installing spectral_scan binary"
    cp "${HAL_DIR}/util_spectral_scan/spectral_scan" "${PKTFWD_DIR}/" >> "${LOG_FILE}" 2>&1
    chown ${PI_USER}:${PI_GROUP} "${PKTFWD_DIR}/spectral_scan"
    chmod 755 "${PKTFWD_DIR}/spectral_scan"
    ok "Installed"

else
    step "Skipping HAL build (--skip-build)"
    ok "Skipped"
fi

# =============================================================================
# Phase 7: Python Virtual Environment & Package Installation
# =============================================================================
phase "Python Virtual Environment & Package Installation"

step "Checking existing venv health"
VENV_REBUILD_NEEDED=false
if [ -d "${VENV_DIR}" ]; then
    if ! "${VENV_DIR}/bin/python3" --version &>/dev/null; then
        warn "Venv Python broken (system Python upgraded?) — will rebuild venv"
        VENV_REBUILD_NEEDED=true
    else
        ok "Existing venv is healthy"
    fi
else
    ok "No existing venv (fresh install)"
fi

if [ "$VENV_REBUILD_NEEDED" = true ]; then
    step "Removing broken venv"
    rm -rf "${VENV_DIR}"
    ok "Removed"
fi

step "Creating Python virtual environment"
if [ ! -d "${VENV_DIR}" ]; then
    if ! sudo -u ${PI_USER} python3 -m venv "${VENV_DIR}" >> "${LOG_FILE}" 2>&1; then
        fail "venv creation failed"
    fi
    ok "Created"
else
    ok "Already exists"
fi

step "Upgrading pip and setuptools"
if ! sudo -u ${PI_USER} "${VENV_DIR}/bin/pip" --no-input install --upgrade pip setuptools wheel >> "${LOG_FILE}" 2>&1; then
    fail "pip upgrade failed"
fi
ok "Done"

step "Symlinking system rrdtool module into venv"
VENV_SITE=$(sudo -u ${PI_USER} "${VENV_DIR}/bin/python3" -c "import site; print(site.getsitepackages()[0])" 2>/dev/null)
SYS_RRD=$(python3 -c "import rrdtool; print(rrdtool.__file__)" 2>/dev/null || true)
if [ -n "${SYS_RRD}" ] && [ -f "${SYS_RRD}" ] && [ -n "${VENV_SITE}" ]; then
    sudo -u ${PI_USER} ln -sf "${SYS_RRD}" "${VENV_SITE}/"
    # Verify import works
    if sudo -u ${PI_USER} "${VENV_DIR}/bin/python3" -c "import rrdtool" 2>/dev/null; then
        ok "Symlinked $(basename ${SYS_RRD})"
    else
        warn "Symlink created but import failed - RRD metrics will be unavailable"
    fi
else
    warn "System rrdtool module not found - RRD metrics will be unavailable"
fi

step "Symlinking system systemd module into venv"
# python3-systemd is installed via apt but lives in the system dist-packages.
# The venv is created with include-system-site-packages=false, so the native
# binding is invisible inside the venv. Symlinking the package dir lets the
# service use the native sd_notify path (Type=notify watchdog keep-alive).
# The code keeps a pure-Python AF_UNIX fallback, so this is a robustness step.
SYS_SYSTEMD=$(python3 -c "import os, systemd; print(os.path.dirname(systemd.__file__))" 2>/dev/null || true)
if [ -n "${SYS_SYSTEMD}" ] && [ -d "${SYS_SYSTEMD}" ] && [ -n "${VENV_SITE}" ]; then
    if [ ! -e "${VENV_SITE}/systemd" ]; then
        sudo -u ${PI_USER} ln -s "${SYS_SYSTEMD}" "${VENV_SITE}/systemd"
    fi
    if sudo -u ${PI_USER} "${VENV_DIR}/bin/python3" -c "import systemd.daemon" 2>/dev/null; then
        ok "Native systemd binding available in venv"
    else
        warn "Symlink created but import failed - sd_notify will use pure-Python fallback"
    fi
else
    warn "System systemd module not found - sd_notify will use pure-Python fallback"
fi

step "Installing openhop_core (editable/dev mode)"
cd "${REPO_DIR}/openhop_core"
if ! sudo -u ${PI_USER} "${VENV_DIR}/bin/pip" --no-input install -e . >> "${LOG_FILE}" 2>&1; then
    fail "openhop_core install failed"
fi
ok "Installed"

step "Installing openhop_repeater (editable/dev mode)"
cd "${REPO_DIR}/openhop_repeater"
if ! sudo -u "${PI_USER}" env "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_OPENHOP_REPEATER=${REPEATER_TAG}+wm1303" "${VENV_DIR}/bin/pip" --no-input install -e . >> "${LOG_FILE}" 2>&1; then
    fail "openhop_repeater install failed"
fi
ok "Installed"

step "Installing additional Python dependencies"
if ! sudo -u ${PI_USER} "${VENV_DIR}/bin/pip" --no-input install \
    spidev \
    RPi.GPIO \
    pyyaml \
    cherrypy \
    pyjwt \
    cryptography \
    aiohttp \
    >> "${LOG_FILE}" 2>&1; then
    fail "Additional dependencies install failed"
fi
ok "Done"

# Preserve additional dependencies from the pre-migration environment.
step "Restoring additional installed packages"
sudo -u "${PI_USER}" "${VENV_DIR}/bin/python3" "${SCRIPT_DIR}/config/restore_venv_packages.py" "${INSTALL_BASE}/.pre-openhop-packages.json" >> "${LOG_FILE}" 2>&1 || fail "Additional package restoration failed; old venv and inventory are retained"
ok "Additional packages checked"

# Verify overlay is accessible after all pip installs.
step "Restoring the local WM1303 core package"
# The repeater declares a Git dependency on upstream openhop_core. Its pip
# install can replace our earlier editable install, so restore the fork last.
if ! sudo -u "${PI_USER}" "${VENV_DIR}/bin/pip" --no-input install --no-deps -e "${REPO_DIR}/openhop_core" >> "${LOG_FILE}" 2>&1; then
    fail "Could not restore the WM1303 core package"
fi
ok "Local core active"

# On Python 3.13 `pip install -e .` may fall back to a non-editable install for
# openhop_core: site-packages then contains *copies* of the repo files and our
# overlay changes to e.g. hardware/__init__.py are invisible. The blocks below
# detect this case via the import path and rsync the full overlay tree on top.
step "Verifying openhop_core overlay is accessible"
CORE_IMPORT_PATH=$(sudo -u "${PI_USER}" "${VENV_DIR}/bin/python3" -I -c "from importlib.metadata import version; version('openhop_core'); import openhop_core.hardware; print(openhop_core.hardware.__file__)" 2>> "${LOG_FILE}") || fail "Core distribution or hardware imports failed"
if echo "$CORE_IMPORT_PATH" | grep -q "site-packages"; then
    SITE_CORE_DIR=$(dirname "$(dirname "$CORE_IMPORT_PATH")")
    deploy_overlay "${OVERLAY_DIR}/openhop_core/src/openhop_core" "${SITE_CORE_DIR}" >> "${LOG_FILE}" 2>&1
    chown -R ${PI_USER}:${PI_GROUP} "${SITE_CORE_DIR}"
    ok "Re-applied overlay to site-packages (rsync)"
else
    ok "Editable install active"
fi

# Sanity check: WM1303Backend must be importable after re-apply, otherwise
# bridge/scheduler init will run in degraded mode at service start.
step "Verifying WM1303Backend import"
if sudo -u ${PI_USER} "${VENV_DIR}/bin/python3" -I -c 'from openhop_core.hardware import WM1303Backend; assert WM1303Backend is not None' >> "${LOG_FILE}" 2>&1; then
    ok "WM1303Backend importable"
else
    fail "WM1303Backend import failed (check ${LOG_FILE})"
fi

# Also verify openhop_repeater overlay
step "Verifying openhop_repeater overlay is accessible"
REPEATER_IMPORT_PATH=$(sudo -u "${PI_USER}" "${VENV_DIR}/bin/python3" -I -c "from importlib.metadata import version; version('openhop_repeater'); import repeater.config; print(repeater.config.__file__)" 2>> "${LOG_FILE}") || fail "Repeater distribution or configuration imports failed"
if echo "$REPEATER_IMPORT_PATH" | grep -q "site-packages"; then
    SITE_REPEATER_DIR=$(dirname "$REPEATER_IMPORT_PATH")
    deploy_overlay "${OVERLAY_DIR}/openhop_repeater/repeater" "${SITE_REPEATER_DIR}" >> "${LOG_FILE}" 2>&1
    chown -R ${PI_USER}:${PI_GROUP} "${SITE_REPEATER_DIR}"
    ok "Re-applied overlay to site-packages (rsync)"
else
    ok "Editable install active"
fi

rm -f "${INSTALL_BASE}/.rebuild-venv"

# Clean Python bytecode caches
step "Cleaning Python bytecode caches"
find ${INSTALL_BASE} -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find ${VENV_DIR} -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
# Repair known runtime files through checked descriptors, preserving their data.
python3 "${SCRIPT_DIR}/config/prepare_runtime_files.py" "${PI_USER}" "${PI_GROUP}" >> "${LOG_FILE}" 2>&1
ok "Cleaned"


# =============================================================================
# Phase 8: Install Configuration Files
# =============================================================================
phase "Install Configuration Files"

step "Installing presets.json (channel presets per region)"
# Always overwrite presets.json since it is read-only catalog data managed by the installer.
if [ -f "${SCRIPT_DIR}/config/presets.json" ]; then
    cp "${SCRIPT_DIR}/config/presets.json" "${CONFIG_DIR}/presets.json" >> "${LOG_FILE}" 2>&1
    ok "presets.json installed"
else
    warn "presets.json not found in config/, skipping"
fi

step "Installing wm1303_ui.json"
if [ ! -f "${CONFIG_DIR}/wm1303_ui.json" ]; then
    cp "${SCRIPT_DIR}/config/wm1303_ui.json" "${CONFIG_DIR}/wm1303_ui.json" >> "${LOG_FILE}" 2>&1
    ok "Installed from template"
else
    ok "Existing config preserved"
fi

step "Normalizing wm1303_ui.json (removing legacy field names)"
NORM_RESULT=$("${VENV_DIR}/bin/python3" - "${SCRIPT_DIR}" "${CONFIG_DIR}/wm1303_ui.json" <<'PYNORM' 2>>"${LOG_FILE}"
import json, runpy, sys
from pathlib import Path

# Load only the stdlib writer, without importing the installed repeater app.
atomic_write_text = runpy.run_path(str(Path(sys.argv[1]) / "overlay/openhop_repeater/repeater/atomic_file.py"))["atomic_write_text"]
try:
    path = sys.argv[2]
    with open(path, encoding="utf-8") as f:
        ui = json.load(f)
    if not isinstance(ui, dict):
        raise ValueError("wm1303_ui.json must contain an object")
    fixes = []
    for ch in ui.get("channels", []):
        label = ch.get("friendly_name", ch.get("name", "?"))
        for short, full in [("sf", "spreading_factor"), ("bw", "bandwidth"), ("cr", "coding_rate")]:
            if short in ch and full in ch:
                fixes.append(f"{label}: removed {short}={ch[short]} (kept {full}={ch[full]})")
                del ch[short]
            elif short in ch:
                ch[full] = ch.pop(short)
                fixes.append(f"{label}: renamed {short} -> {full}={ch[full]}")
    for channel_name in ("channel_e", "channel_f"):
        channel = ui.get(channel_name, {})
        for short, full in [("sf", "spreading_factor"), ("bw", "bandwidth"), ("cr", "coding_rate")]:
            if short in channel and full in channel:
                fixes.append(f"{channel_name}: removed {short}={channel[short]} (kept {full}={channel[full]})")
                del channel[short]
            elif short in channel:
                channel[full] = channel.pop(short)
                fixes.append(f"{channel_name}: renamed {short} -> {full}={channel[full]}")
    if fixes:
        atomic_write_text(path, json.dumps(ui, indent=2, allow_nan=False))
        print("fixed: " + "; ".join(fixes))
    else:
        print("clean")
except Exception as e:
    print("error: " + str(e), file=sys.stderr)
    sys.exit(1)
PYNORM
) || fail "wm1303_ui.json normalization failed; see ${LOG_FILE}"
if [ "${NORM_RESULT}" = "clean" ]; then
    ok "No legacy fields found"
elif echo "${NORM_RESULT}" | grep -q "^fixed:"; then
    ok "${NORM_RESULT}"
else
    fail "Unexpected normalization result; see ${LOG_FILE}"
fi


step "Installing config.yaml"
if [ ! -f "${CONFIG_DIR}/config.yaml" ]; then
    cp "${SCRIPT_DIR}/config/config.yaml.template" "${CONFIG_DIR}/config.yaml" >> "${LOG_FILE}" 2>&1
    # Replace placeholders with detected paths
    sed -i "s|__PKTFWD_DIR__|${PKTFWD_DIR}|g" "${CONFIG_DIR}/config.yaml"
    ok "Installed from template (pktfwd_dir: ${PKTFWD_DIR})"
else
    # Ensure existing config has correct pktfwd_dir for detected user
    if grep -q '/home/pi/wm1303_pf' "${CONFIG_DIR}/config.yaml" 2>/dev/null && [ "${PI_USER}" != "pi" ]; then
        sed -i "s|/home/pi/wm1303_pf|${PKTFWD_DIR}|g" "${CONFIG_DIR}/config.yaml"
        info "Updated pktfwd_dir in existing config to ${PKTFWD_DIR}"
    fi
    ok "Existing config preserved"
fi

# The runtime identity loader preserves inline keys and file-backed identities.
# On first start it atomically creates a missing key as the service user;
# installation must not replace an existing identity or rewrite config.yaml for it.

step "Installing global_conf.json"
if [ ! -f "${PKTFWD_DIR}/global_conf.json" ]; then
    cp "${SCRIPT_DIR}/config/global_conf.json" "${PKTFWD_DIR}/global_conf.json" >> "${LOG_FILE}" 2>&1
    ok "Installed"
else
    ok "Existing config preserved"
fi

step "Setting configuration file ownership"
chown -R ${PI_USER}:${PI_GROUP} "${CONFIG_DIR}"
chown -R ${PI_USER}:${PI_GROUP} "${PKTFWD_DIR}"
ok "Done"

# ---------------------------------------------------------------------------
# Defensive: ensure storage_dir from config.yaml is accessible to service user.
# Root cause background: config.yaml `storage.storage_dir` may point at the
# legacy /var/lib/pymc_repeater path (older configs) while install.sh only
# pre-creates the canonical DATA_DIR (/var/lib/openhop_repeater). Without
# this block, a clean install with a config that resolves to a non-canonical
# path would crash on first service start with PermissionError inside
# repeater.web.http_server._init_auth_handlers (os.makedirs on /var/lib).
# ---------------------------------------------------------------------------
step "Ensuring storage_dir from config.yaml is accessible"
STORAGE_DIR_CFG=$(${VENV_DIR}/bin/python3 -c "
import yaml, sys
try:
    d = yaml.safe_load(open('${CONFIG_DIR}/config.yaml')) or {}
    print((d.get('storage') or {}).get('storage_dir') or '${DATA_DIR}')
except Exception:
    print('${DATA_DIR}')
" 2>/dev/null || echo "${DATA_DIR}")
if [ -n "${STORAGE_DIR_CFG}" ] && [ "${STORAGE_DIR_CFG}" != "${DATA_DIR}" ]; then
    if [ ! -e "${STORAGE_DIR_CFG}" ]; then
        ln -sfn "${DATA_DIR}" "${STORAGE_DIR_CFG}" >> "${LOG_FILE}" 2>&1
        ok "symlink ${STORAGE_DIR_CFG} -> ${DATA_DIR}"
    else
        ok "${STORAGE_DIR_CFG} already exists"
    fi
else
    ok "storage_dir=${STORAGE_DIR_CFG} (canonical)"
fi

step "Installing version file"
cp "${SCRIPT_DIR}/VERSION" "${CONFIG_DIR}/version" >> "${LOG_FILE}" 2>&1
chown ${PI_USER}:${PI_GROUP} "${CONFIG_DIR}/version"
ok "v$(cat "${SCRIPT_DIR}/VERSION")"


# =============================================================================
# Phase 9: Generate GPIO Reset Scripts
# =============================================================================
phase "Generate GPIO Reset Scripts"

step "Reading GPIO pin configuration"
UI_JSON="${CONFIG_DIR}/wm1303_ui.json"
if [ -f "${UI_JSON}" ] && command -v jq &>/dev/null; then
    GPIO_RESET=$(jq -r '.gpio_pins.sx1302_reset // 17' "${UI_JSON}")
    GPIO_POWER=$(jq -r '.gpio_pins.sx1302_power_en // 18' "${UI_JSON}")
    GPIO_SX1261=$(jq -r '.gpio_pins.sx1261_reset // 5' "${UI_JSON}")
    GPIO_AD5338R=$(jq -r '.gpio_pins.ad5338r_reset // 13' "${UI_JSON}")
    GPIO_BASE=$(jq -r '.gpio_pins.gpio_base_offset // 512' "${UI_JSON}")
else
    GPIO_RESET=17
    GPIO_POWER=18
    GPIO_SX1261=5
    GPIO_AD5338R=13
    GPIO_BASE=512
fi
ok "GPIO: reset=BCM${GPIO_RESET}, power=BCM${GPIO_POWER}, sx1261=BCM${GPIO_SX1261}"

SX1302_RESET_PIN=$((GPIO_BASE + GPIO_RESET))
SX1302_POWER_PIN=$((GPIO_BASE + GPIO_POWER))
SX1261_RESET_PIN=$((GPIO_BASE + GPIO_SX1261))
AD5338R_RESET_PIN=$((GPIO_BASE + GPIO_AD5338R))

step "Generating reset_lgw.sh"
# Render the shared template so install, upgrade and manual reset stay in sync.
sed -e "s/^SX1302_RESET_PIN=.*/SX1302_RESET_PIN=${SX1302_RESET_PIN}/" \
    -e "s/^SX1302_POWER_EN_PIN=.*/SX1302_POWER_EN_PIN=${SX1302_POWER_PIN}/" \
    -e "s/^SX1261_RESET_PIN=.*/SX1261_RESET_PIN=${SX1261_RESET_PIN}/" \
    -e "s/^AD5338R_RESET_PIN=.*/AD5338R_RESET_PIN=${AD5338R_RESET_PIN}/" \
    "${SCRIPT_DIR}/config/reset_lgw.sh" > "${PKTFWD_DIR}/reset_lgw.sh"
chmod 755 "${PKTFWD_DIR}/reset_lgw.sh"
chown ${PI_USER}:${PI_GROUP} "${PKTFWD_DIR}/reset_lgw.sh"
ok "Generated"

step "Generating power_cycle_lgw.sh"
# Render the shared template so install, upgrade and manual reset stay in sync.
sed -e "s/^SX1302_RESET_PIN=.*/SX1302_RESET_PIN=${SX1302_RESET_PIN}/" \
    -e "s/^SX1302_POWER_EN_PIN=.*/SX1302_POWER_EN_PIN=${SX1302_POWER_PIN}/" \
    -e "s/^SX1261_RESET_PIN=.*/SX1261_RESET_PIN=${SX1261_RESET_PIN}/" \
    -e "s/^AD5338R_RESET_PIN=.*/AD5338R_RESET_PIN=${AD5338R_RESET_PIN}/" \
    "${SCRIPT_DIR}/config/power_cycle_lgw.sh" > "${PKTFWD_DIR}/power_cycle_lgw.sh"
chmod 755 "${PKTFWD_DIR}/power_cycle_lgw.sh"
chown ${PI_USER}:${PI_GROUP} "${PKTFWD_DIR}/power_cycle_lgw.sh"
ok "Generated"

# =============================================================================
# Phase 10: Install Systemd Service
# =============================================================================
phase "Install Systemd Service"

step "Installing detached WM1303 updater"
install_wm1303_updater "${SCRIPT_DIR}" "${PI_USER}" >> "${LOG_FILE}" 2>&1 || fail "WM1303 updater installation failed"
ok "Installed root-owned launcher and bootstrap"

step "Stopping existing service(s) (if running)"
# Legacy pymc-repeater.service is superseded by openhop-repeater.service. Stop
# and disable the old unit so both cannot run/enable simultaneously.
systemctl stop pymc-repeater.service 2>/dev/null || true
systemctl stop openhop-repeater.service 2>/dev/null || true
if systemctl list-unit-files 2>/dev/null | grep -q '^pymc-repeater.service'; then
    systemctl disable pymc-repeater.service >> "${LOG_FILE}" 2>&1 || true
    rm -f /etc/systemd/system/pymc-repeater.service 2>/dev/null || true
fi
ok "Done"

step "Installing systemd service file"
cp "${SCRIPT_DIR}/config/openhop-repeater.service" /etc/systemd/system/openhop-repeater.service >> "${LOG_FILE}" 2>&1
# Replace placeholders with detected user
sed -i "s|__PI_USER__|${PI_USER}|g" /etc/systemd/system/openhop-repeater.service
sed -i "s|__PI_GROUP__|${PI_GROUP}|g" /etc/systemd/system/openhop-repeater.service
sed -i "s|__PI_HOME__|${PI_HOME}|g" /etc/systemd/system/openhop-repeater.service
ok "Installed (user: ${PI_USER})"

step "Reloading systemd daemon"
install_plugin_manager_service "$SCRIPT_DIR" "$PI_USER" "$PI_GROUP" >> "$LOG_FILE" 2>&1 || fail "Plugin manager service installation failed"
systemctl daemon-reload >> "${LOG_FILE}" 2>&1
systemctl enable openhop-plugin-manager.service >> "$LOG_FILE" 2>&1
systemctl --no-block restart openhop-plugin-manager.service >> "$LOG_FILE" 2>&1 || warn "Optional plugin manager did not start; repeater operation is unaffected"
ok "Reloaded"

step "Enabling service for auto-start"
systemctl enable openhop-repeater.service >> "${LOG_FILE}" 2>&1
ok "Enabled"

# =============================================================================
# Phase 10b: Hardware Watchdog (OS-level)
# =============================================================================
# Two-layer watchdog:
#  - OS layer (here): BCM2835 hardware watchdog + systemd RuntimeWatchdogSec.
#    If the whole OS freezes, the hardware timer reboots the Pi automatically.
#  - Service layer (in openhop-repeater.service): Type=notify + WatchdogSec; the
#    backend feeds sd_notify WATCHDOG=1 so a hung application is restarted.
phase "Hardware Watchdog"

step "Enabling BCM2835 hardware watchdog module"
WDT_MODCONF="/etc/modules-load.d/bcm2835_wdt.conf"
if [ -f "${WDT_MODCONF}" ] && grep -q '^bcm2835_wdt' "${WDT_MODCONF}" 2>/dev/null; then
    ok "Module already configured"
else
    echo "bcm2835_wdt" > "${WDT_MODCONF}"
    ok "Configured ${WDT_MODCONF}"
fi
modprobe bcm2835_wdt 2>/dev/null || warn "modprobe bcm2835_wdt failed (will load at next boot)"
if [ -e /dev/watchdog ]; then
    info "/dev/watchdog present"
else
    warn "/dev/watchdog not present yet (expected after reboot)"
fi

# Note: the BCM2835 hardware watchdog enforces a fixed 60s timeout and does not
# support shorter custom values (wdctl SETTIMEOUT=0). Lower RuntimeWatchdogSec
# values are clamped to 60s by the kernel, so we configure 60s to match the
# effective hardware behaviour. On a full OS freeze the Pi auto-reboots within ~60s.
step "Configuring systemd RuntimeWatchdogSec=60s (BCM2835 hardware limit)"
SYSTEMD_CONF="/etc/systemd/system.conf"
if [ -f "${SYSTEMD_CONF}" ]; then
    TMP_SC="$(mktemp)"
    # Remove any existing (commented or active) RuntimeWatchdogSec line, then append ours
    grep -viE '^[#[:space:]]*RuntimeWatchdogSec' "${SYSTEMD_CONF}" > "${TMP_SC}"
    echo "RuntimeWatchdogSec=60s" >> "${TMP_SC}"
    mv "${TMP_SC}" "${SYSTEMD_CONF}"
    ok "Set RuntimeWatchdogSec=60s in ${SYSTEMD_CONF}"
    step "Re-executing systemd manager to apply watchdog"
    systemctl daemon-reexec >> "${LOG_FILE}" 2>&1 || warn "daemon-reexec failed (applies after reboot)"
    ok "Applied"
else
    warn "${SYSTEMD_CONF} not found; skipped RuntimeWatchdogSec"
fi

# =============================================================================
# Phase 10c: Journal Log Management (TODO #14)
# =============================================================================
# The repeater logs continuously. With the Raspberry Pi OS default the journal
# may grow to 10% of the filesystem and eventually fill a small SD card, which
# takes the service down with it. A drop-in caps total size and retention.
# SQLite metrics retention is handled separately by repeater/metrics_retention.py
# (8-day window, hourly cleanup, weekly VACUUM).
phase "Journal Log Management"

step "Installing systemd-journald limits drop-in"
JOURNALD_DROPIN_DIR="/etc/systemd/journald.conf.d"
# The drop-in filename is LOAD-ORDER CRITICAL. systemd merges journald drop-ins
# by sorting filenames across ALL search directories (/etc, /run, /usr/lib) and
# the LAST value read for a key wins. Raspberry Pi OS ships two drop-ins that
# collide with ours:
#   /usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf -> Storage=volatile
#   /usr/lib/systemd/journald.conf.d/syslog.conf                  -> ForwardToSyslog=yes
# Up to and including v2.7.6 this file was installed as 10-openhop.conf, which
# sorts BEFORE both, so Storage=persistent and ForwardToSyslog=no were silently
# discarded on every boot while the size/retention keys applied normally. The
# journal therefore stayed in /run/log/journal (tmpfs) and every reboot erased
# all evidence. Note that /etc only takes precedence over /usr/lib for an
# IDENTICAL filename, so a higher-sorting name is required: zz- sorts after any
# numeric prefix and after syslog.conf.
JOURNALD_DROPIN="${JOURNALD_DROPIN_DIR}/zz-openhop.conf"
JOURNALD_DROPIN_LEGACY="${JOURNALD_DROPIN_DIR}/10-openhop.conf"
if [ -f "${SCRIPT_DIR}/config/journald-openhop.conf" ]; then
    mkdir -p "${JOURNALD_DROPIN_DIR}"
    cp "${SCRIPT_DIR}/config/journald-openhop.conf" "${JOURNALD_DROPIN}" >> "${LOG_FILE}" 2>&1
    chmod 644 "${JOURNALD_DROPIN}"
    # Never let the superseded name coexist with the current one.
    if [ -f "${JOURNALD_DROPIN_LEGACY}" ]; then
        rm -f "${JOURNALD_DROPIN_LEGACY}" >> "${LOG_FILE}" 2>&1
        ok "Removed superseded ${JOURNALD_DROPIN_LEGACY}"
    fi
    ok "Installed ${JOURNALD_DROPIN} (SystemMaxUse=200M, MaxRetentionSec=8day)"
    step "Restarting systemd-journald to apply limits"
    if systemctl restart systemd-journald >> "${LOG_FILE}" 2>&1; then
        ok "journald restarted"
    else
        warn "journald restart failed (limits apply after reboot)"
    fi
    step "Verifying effective journal storage"
    # Only the LAST Storage= line of the merged configuration is in effect, so
    # read the merged view rather than our own drop-in. grep must be guarded:
    # under 'set -euo pipefail' a non-matching grep would abort the installer.
    EFFECTIVE_STORAGE=$(systemd-analyze cat-config systemd/journald.conf 2>/dev/null | grep -E "^Storage=" | tail -1 || true)
    if [ "${EFFECTIVE_STORAGE}" = "Storage=persistent" ]; then
        ok "Journal storage is persistent (logs survive a reboot)"
    else
        warn "Effective journal storage is '${EFFECTIVE_STORAGE:-unset}', expected persistent - drop-in load-order conflict"
    fi
    step "Flushing journal to persistent storage"
    journalctl --flush >> "${LOG_FILE}" 2>&1 || warn "journal flush failed (non-fatal)"
    step "Vacuuming existing journal to the new size limit"
    journalctl --vacuum-size=200M >> "${LOG_FILE}" 2>&1 || warn "journal vacuum failed (non-fatal)"
    ok "Journal within size limit"
else
    warn "config/journald-openhop.conf not found; skipped journal limits"
fi

# =============================================================================
# Phase 11: NTP Time Synchronization
# =============================================================================
phase "NTP Time Synchronization"

step "Checking NTP synchronization status"
if command -v timedatectl &>/dev/null; then
    NTP_STATUS=$(timedatectl show --property=NTPSynchronized --value 2>/dev/null || echo "unknown")
    TIMESYNCD=$(timedatectl show --property=NTP --value 2>/dev/null || echo "unknown")

    if [ "$NTP_STATUS" = "yes" ]; then
        ok "NTP is synchronized"
    elif [ "$TIMESYNCD" = "yes" ]; then
        ok "NTP client running (not yet synced)"
    else
        systemctl enable systemd-timesyncd >> "${LOG_FILE}" 2>&1 || true
        systemctl start systemd-timesyncd >> "${LOG_FILE}" 2>&1 || true
        ok "NTP client enabled"
    fi

    info "System time: $(date '+%Y-%m-%d %H:%M:%S %Z')"
else
    if systemctl is-active --quiet ntp 2>/dev/null; then
        ok "NTP daemon is running"
    else
        systemctl enable ntp >> "${LOG_FILE}" 2>&1 || true
        systemctl start ntp >> "${LOG_FILE}" 2>&1 || true
        ok "NTP daemon started"
    fi
fi

# =============================================================================
# Phase 11b: Low-Memory Device Maintenance
# =============================================================================
phase "Low-Memory Device Maintenance"

step "Configuring weekly maintenance reboot (low-memory devices)"
MEM_TOTAL_MB=$(free -m | awk '/^Mem:/ {print $2}')
REBOOT_CRON_FILE="/etc/cron.d/openhop-repeater-weekly-reboot"
NO_REBOOT_MARKER="${CONFIG_DIR}/no-auto-reboot"
# Remove legacy cron file from the pre-openhop naming, if present.
rm -f /etc/cron.d/pymc-repeater-weekly-reboot 2>/dev/null || true

if [ -f "${NO_REBOOT_MARKER}" ]; then
    ok "Opt-out marker present (${NO_REBOOT_MARKER}); skipping auto-reboot cron"
elif [ "${MEM_TOTAL_MB}" -lt 700 ]; then
    cat > "${REBOOT_CRON_FILE}" << 'CRON_EOF'
# openhop-wm1303 - Weekly maintenance reboot for low-memory devices
# Auto-installed by install.sh / upgrade.sh on devices with < 700 MB RAM.
# Rationale: clears kernel Slab caches and Python heap fragmentation that
# accumulate over days of uptime, keeping memory usage stable on 512 MB Pis.
#
# To disable one-off:  sudo rm /etc/cron.d/openhop-repeater-weekly-reboot
# To prevent re-install on next upgrade:
#   sudo touch /etc/openhop_repeater/no-auto-reboot

SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

0 4 * * 0 root logger -t openhop-repeater "Weekly maintenance reboot" && /sbin/reboot
CRON_EOF
    chmod 0644 "${REBOOT_CRON_FILE}"
    ok "Detected ${MEM_TOTAL_MB} MB RAM; installed ${REBOOT_CRON_FILE} (Sun 04:00)"
else
    if [ -f "${REBOOT_CRON_FILE}" ]; then
        rm -f "${REBOOT_CRON_FILE}"
        ok "${MEM_TOTAL_MB} MB RAM detected; removed stale ${REBOOT_CRON_FILE}"
    else
        ok "${MEM_TOTAL_MB} MB RAM detected; skipping auto-reboot cron (not needed)"
    fi
fi

# =============================================================================
# Phase 12: Start and Verify Service
# =============================================================================
phase "Start and Verify Service"

if [ "$REBOOT_REQUIRED" = true ]; then
    step "SPI devices not yet available (reboot required)"
    ok "Service will start automatically after reboot"
else
    step "Performing extended hardware drain reset (60s)"
    sudo "${PKTFWD_DIR}/reset_lgw.sh" deep_reset 60 >> "${LOG_FILE}" 2>&1
    ok "Hardware drain reset complete"

    step "Verifying complete core and repeater overlay deployment"
    [ "$(overlay_diff_count "${OVERLAY_DIR}/openhop_core/src/openhop_core" "${CORE_ROOT_DIR}")" = 0 ] || fail "Core overlay deployment is incomplete"
    [ "$(overlay_diff_count "${OVERLAY_DIR}/openhop_repeater/repeater" "${RPT_DIR}/repeater")" = 0 ] || fail "Repeater overlay deployment is incomplete"
    ok "All overlay files match"

    step "Starting openhop-repeater service"
    systemctl start openhop-repeater.service >> "${LOG_FILE}" 2>&1
    sleep 5
    ok "Started"

    step "Checking service status"
    if systemctl is-active --quiet openhop-repeater.service; then
        ok "openhop-repeater service is RUNNING"
    else
        info "Check logs: journalctl -u openhop-repeater -f"
        fail "openhop-repeater service did not remain running"
    fi

    step "Checking web interface availability"
    sleep 5
    WEB_PORT=$("${VENV_DIR}/bin/python3" - "${CONFIG_DIR}/config.yaml" <<'PYWEBPORT'
import sys
import yaml
with open(sys.argv[1], encoding="utf-8") as stream:
    config = yaml.safe_load(stream) or {}
print((config.get("web") or {}).get("port", 8000))
PYWEBPORT
    )
    if command -v curl &>/dev/null; then
        # Retry startup checks with a bounded timeout for each request.
        WEB_OK=0
        WEB_TRY=0
        for WEB_TRY in $(seq 1 15); do
            if curl -s --connect-timeout 2 --max-time 5 -o /dev/null -w "%{http_code}" "http://127.0.0.1:${WEB_PORT}/" 2>/dev/null | grep -q "200\|302\|401"; then
                WEB_OK=1
                break
            fi
            sleep 2
        done
        if [ "${WEB_OK}" = "1" ]; then
            ok "Web interface responding on port ${WEB_PORT} (ready after ${WEB_TRY} attempt(s))"
        else
            ok "Web interface not yet responding after 15 attempts (may need a few more seconds)"
        fi
    else
        ok "curl not available, skipping check"
    fi

    step "Checking concentrator module detection"
    # Unit name must be openhop-repeater. Reading the superseded pymc-repeater
    # unit always returned an empty journal, so a fresh install reported
    # 'concentrator not detected' even when the module came up fine.
    # Retry instead of sampling once: a single look after a fixed sleep can miss
    # the startup lines on a slower board and report a healthy concentrator as
    # missing.
    # A fresh install starts with every channel disabled, so the backend stays
    # in IDLE mode on purpose and never brings the radio up. That is not a
    # hardware fault, so detect it separately instead of waiting 18 x 5s for
    # markers that can never appear and then blaming SPI/GPIO on a healthy board.
    CONC_OK=0
    CONC_IDLE=0
    CONC_TRY=0
    for CONC_TRY in $(seq 1 18); do
        # Anchor the journal window to the moment the unit actually became
        # active, re-read every attempt so it follows a restart mid-loop. A
        # fixed '--since N minutes ago' window fails whenever the service is
        # slower to come up than the window is wide.
        SVC_START=$(systemctl show openhop-repeater -p ActiveEnterTimestamp --value 2>/dev/null || true)
        if [ -n "${SVC_START}" ]; then
            CONCENTRATOR_LOG=$(journalctl -u openhop-repeater --since "${SVC_START}" --no-pager 2>/dev/null || true)
        else
            CONCENTRATOR_LOG=$(journalctl -u openhop-repeater --since '10 minutes ago' --no-pager 2>/dev/null || true)
        fi
        # Check IDLE first: when no channel is enabled the radio never starts,
        # so the markers below can never appear and waiting is pointless.
        if echo "${CONCENTRATOR_LOG}" | grep -qi 'NO active channels configured\|Running in IDLE mode'; then
            CONC_IDLE=1
            break
        fi
        if echo "${CONCENTRATOR_LOG}" | grep -qi 'lora_pkt_fwd started\|pktfwd ready\|backend started'; then
            CONC_OK=1
            break
        fi
        sleep 5
    done
    if [ "${CONC_IDLE}" = "1" ]; then
        ok "Radio idle - no channels enabled yet (expected on a fresh install)"
        echo ""
        echo -e "  ${BOLD}${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
        echo -e "  ${BOLD}${CYAN}║  NEXT STEP: enable at least one channel                  ║${NC}"
        echo -e "  ${BOLD}${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
        echo ""
        echo -e "  The service is running and the web UI is available, but the radio"
        echo -e "  has not been started because no channel is enabled yet. This is"
        echo -e "  normal for a new installation and is ${BOLD}not${NC} a hardware fault."
        echo ""
        echo -e "  ${BOLD}To bring the radio up:${NC}"
        echo -e "  - Open the web UI: ${CYAN}http://<this-pi-ip>:${WEB_PORT}/wm1303.html${NC}"
        echo -e "  - Enable and configure at least one channel"
        echo -e "  - Restart the service: ${CYAN}sudo systemctl restart openhop-repeater${NC}"
        echo ""
    elif [ "${CONC_OK}" = "1" ]; then
        ok "SX1302 concentrator module detected and running (confirmed after ${CONC_TRY} attempt(s))"
    else
        if echo "${CONCENTRATOR_LOG}" | grep -qi 'Failed to set SX1250\|ERROR.*spi\|ERROR.*gpio\|pktfwd.*fail'; then
            warn "Concentrator module detection failed (SPI/GPIO errors found)"
        else
            warn "Concentrator module not yet confirmed (may need more time)"
        fi
        echo ""
        echo -e "  ${BOLD}${YELLOW}╔══════════════════════════════════════════════════════════╗${NC}"
        echo -e "  ${BOLD}${YELLOW}║  ⚠️  CONCENTRATOR MODULE NOT DETECTED                     ║${NC}"
        echo -e "  ${BOLD}${YELLOW}╚══════════════════════════════════════════════════════════╝${NC}"
        echo ""
        echo -e "  The installation completed successfully, but the SX1302 concentrator"
        echo -e "  module was not detected. This usually means:"
        echo ""
        echo -e "  1. GPIO pin numbers may not match your board"
        echo -e "  2. SPI device path may be different on your system"
        echo -e "  3. Power supply may be insufficient (ensure >= 3A)"
        echo ""
        echo -e "  ${BOLD}Next steps:${NC}"
        echo -e "  - Open the web UI: ${CYAN}http://<this-pi-ip>:${WEB_PORT}/wm1303.html${NC}"
        echo -e "  - Go to ${CYAN}Adv. Config → SPI Device Configuration${NC}"
        echo -e "  - Go to ${CYAN}Adv. Config → GPIO Pin Configuration${NC}"
        echo -e "  - Verify and adjust the SPI paths and GPIO pins for your board"
        echo -e "  - See: ${CYAN}https://github.com/asamonik/openhop-wm1303/blob/main/docs/spi-troubleshooting.md${NC}"
        echo ""
        echo -e "  The service is installed and will start automatically on boot."
        echo -e "  You can restart it after adjusting settings:"
        echo -e "  ${CYAN}sudo systemctl restart openhop-repeater${NC}"
        echo ""
    fi
fi

# =============================================================================
# Installation Complete
# =============================================================================
INSTALL_SUCCESS=true

echo -e "\n${BOLD}${GREEN}"
echo "  ╔══════════════════════════════════════════════════════════╗"
echo "  ║     Installation Completed Successfully!                 ║"
echo "  ╚══════════════════════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "  ${BOLD}Quick Reference:${NC}"
echo -e "  ─────────────────────────────────────────────────────────"
echo -e "  Service control:  ${CYAN}sudo systemctl {start|stop|restart} openhop-repeater${NC}"
echo -e "  Service logs:     ${CYAN}journalctl -u openhop-repeater -f${NC}"
echo -e "  Web interface:    ${CYAN}http://<this-pi-ip>:${WEB_PORT:-8000}/wm1303.html${NC}"
echo -e "  Repeater UI:      ${CYAN}http://<this-pi-ip>:${WEB_PORT:-8000}/${NC}"
echo -e "  Full log:         ${CYAN}${LOG_FILE}${NC}"
echo ""

if [ "$REBOOT_REQUIRED" = true ]; then
    echo -e "  ${BOLD}${YELLOW}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "  ${BOLD}${YELLOW}║  REBOOT REQUIRED to activate SPI and start the service   ║${NC}"
    echo -e "  ${BOLD}${YELLOW}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  The service is installed and enabled. It will start automatically after reboot."
    echo ""
    if [ -t 0 ] && read -r -p "  Press ENTER to reboot now (or Ctrl+C to cancel)... "; then
        echo -e "\n  ${CYAN}Rebooting...${NC}"
        reboot
    else
        echo -e "  ${YELLOW}Run: sudo reboot${NC}"
    fi
else
    echo -e "  ${GREEN}The service is running. No reboot required.${NC}"
    echo ""
fi
