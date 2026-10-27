import logging
import threading
import time
from typing import Optional, Tuple

from openhop_core.protocol.packet_utils import calculate_lora_airtime_ms

logger = logging.getLogger("AirtimeManager")


class AirtimeManager:
    def __init__(self, config: dict):
        self.config = config
        self.radio_config = config.get("radio", {})
        self.max_airtime_per_minute = config.get("duty_cycle", {}).get(
            "max_airtime_per_minute", 3600
        )

        # Store radio settings for airtime calculations
        self.refresh_radio_params(self.radio_config)

        # Shared by the event-loop scheduler and background statistics readers.
        self._history_lock = threading.RLock()
        # Track elapsed time, independent of NTP/GPS wall-clock corrections.
        self.tx_history = []  # [(timestamp, airtime_ms), ...]
        self.window_size = 60  # seconds
        self.total_airtime_ms = 0
        self.total_rx_airtime_ms = 0

    def refresh_radio_params(self, radio_config: Optional[dict] = None) -> None:
        """Reload cached modulation params used by airtime estimation.

        Call after a successful live radio reconfiguration. Does not reset
        TX/RX history or duty-cycle totals.
        """
        if radio_config is None:
            radio_config = self.config.get("radio", {}) or {}
        self.radio_config = radio_config
        self.spreading_factor = self.radio_config.get("spreading_factor", 7)
        self.bandwidth = self.radio_config.get("bandwidth", 125000)
        self.coding_rate = self.radio_config.get("coding_rate", 5)
        self.preamble_length = self.radio_config.get("preamble_length", 8)

    def calculate_airtime(
        self,
        payload_len: int,
        spreading_factor: int = None,
        bandwidth_hz: int = None,
        coding_rate: int = None,
        preamble_len: int = None,
        crc_enabled: bool = True,
        explicit_header: bool = True,
    ) -> float:
        """
        Calculate LoRa packet airtime via the shared core estimator.

        Delegates to ``calculate_lora_airtime_ms``, which matches RadioLib's
        ``getTimeOnAir`` (the firmware reference), including its symbol-time
        low-data-rate-optimization auto rule. Coding rate accepts either the
        denominator form (5..8) or the legacy index form (1..4).

        Args:
            payload_len: Payload length in bytes
            spreading_factor: SF7-SF12 (uses config value if None)
            bandwidth_hz: Bandwidth in Hz (uses config value if None)
            coding_rate: CR denominator, 5=4/5, 6=4/6, 7=4/7, 8=4/8 (uses config value if None)
            preamble_len: Preamble symbols (uses config value if None)
            crc_enabled: Whether CRC is enabled (default: True)
            explicit_header: Whether explicit header mode is used (default: True)

        Returns:
            Airtime in milliseconds
        """
        return calculate_lora_airtime_ms(
            payload_len,
            spreading_factor or self.spreading_factor,
            bandwidth_hz or self.bandwidth,
            coding_rate or self.coding_rate,
            preamble_len or self.preamble_length,
            crc_enabled=crc_enabled,
            explicit_header=explicit_header,
        )

    def can_transmit(self, airtime_ms: float) -> Tuple[bool, float]:
        enforcement_enabled = self.config.get("duty_cycle", {}).get("enforcement_enabled", True)
        if not enforcement_enabled:
            # Duty cycle enforcement disabled - always allow
            return True, 0.0

        with self._history_lock:
            now = time.monotonic()
            self._prune_tx_history(now)
            current_airtime = sum(at for _, at in self.tx_history)
            if current_airtime + airtime_ms <= self.max_airtime_per_minute:
                return True, 0.0
            if self.tx_history:
                oldest_ts, _ = self.tx_history[0]
                wait_time = (oldest_ts + self.window_size) - now
                return False, max(0, wait_time)
            return False, 1.0

    def _prune_tx_history(self, now: float) -> None:
        """Caller holds _history_lock through pruning and its read or update."""
        self.tx_history = [(ts, at) for ts, at in self.tx_history if now - ts < self.window_size]

    def record_tx(self, airtime_ms: float):
        with self._history_lock:
            now = time.monotonic()
            # Keep history bounded even with enforcement off and no dashboard.
            self._prune_tx_history(now)
            self.tx_history.append((now, airtime_ms))
            self.total_airtime_ms += airtime_ms
            total = self.total_airtime_ms
        logger.debug(f"TX recorded: {airtime_ms: .1f}ms (total: {total: .0f}ms)")

    def record_rx(self, airtime_ms: float):
        """Record received packet airtime (for total RX airtime stats)."""
        with self._history_lock:
            self.total_rx_airtime_ms += airtime_ms

    def get_stats(self) -> dict:
        with self._history_lock:
            self._prune_tx_history(time.monotonic())
            current_airtime = sum(at for _, at in self.tx_history)
            max_airtime = self.max_airtime_per_minute
            return {
                "current_airtime_ms": current_airtime,
                "max_airtime_ms": max_airtime,
                "utilization_percent": (current_airtime / max_airtime) * 100,
                "total_airtime_ms": self.total_airtime_ms,
                "total_rx_airtime_ms": self.total_rx_airtime_ms,
            }
