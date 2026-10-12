"""Channel F (chan_Lora_std on SX1302 RF0) Bridge Plugin for pymc-repeater.

Channel F is the SX1302 LoRa Service channel (chan_Lora_std). Unlike Channel E
(SX1261 separate radio chip with its own UDP path), Channel F packets arrive
through the SAME HAL packet_forwarder UDP port as channels A-D, because
chan_Lora_std physically lives on the same SX1302 silicon as chan_multiSF_0-3.

What this plugin does:
  * Registers a TX handler in BridgeEngine for the 'channel_f' endpoint so
    bridge rules with target=channel_f can route packets through the
    GlobalTXScheduler / TXQueueManager (parallel to channels A-D and E).
  * Registers an RX callback on WM1303Backend (`_channel_f_rx_callback`) so the
    backend can hand chan_Lora_std-classified packets back to us. We then
    inject them into BridgeEngine with origin_channel='channel_f' so bridge
    rules with source=channel_f fire correctly.

Channel F parameters (frequency, BW, SF) are read dynamically from
/etc/openhop_repeater/wm1303_ui.json (or legacy /etc/pymc_repeater/wm1303_ui.json) at runtime by the backend. This plugin only
needs the TX queue id and a friendly name resolver.

Usage (inside pymc-repeater main):
    from repeater.channel_f_bridge import ChannelFBridge
    ch_f = ChannelFBridge(bridge_engine, backend=radio)
    asyncio.create_task(ch_f.run())
"""
import asyncio
import json
import logging
from repeater.bridge_engine import _stable_hash
from repeater.channel_bridge_tx import send_channel_packet
from openhop_core.paths import resolve_config_path  # WM1303 v2.7: central config-path helper

logger = logging.getLogger(__name__)

CHANNEL_F_NAME = 'channel_f'
CHANNEL_F_TX_CHANNEL = 'channel_f'
UI_CONFIG_PATH = resolve_config_path('wm1303_ui.json')


def _load_channel_f_ui() -> dict:
    """Load channel_f settings from wm1303_ui.json."""
    try:
        if UI_CONFIG_PATH.exists():
            return json.loads(UI_CONFIG_PATH.read_text()).get('channel_f', {})
    except Exception as e:
        logger.warning('ChannelFBridge: failed to read UI config: %s', e)
    return {}


class ChannelFBridge:
    """Bridge plugin that hooks Channel F (chan_Lora_std) into BridgeEngine.

    No UDP listener: Channel F packets share the HAL pkt_fwd UDP port with
    channels A-D and are dispatched by WM1303Backend via the registered RX
    callback. The TX handler routes outgoing packets through the existing
    per-channel TXQueue managed by TXQueueManager / GlobalTXScheduler.
    """

    def __init__(self, bridge_engine, backend=None):
        self.bridge = bridge_engine
        self.backend = backend  # WM1303Backend for TX + RX callback
        self.packets_received = 0
        self.packets_injected = 0
        self.packets_errors = 0
        self.tx_packets = 0
        self.tx_errors = 0
        self._running = False
        self._task = None
        self._rx_tasks = set()
        self._loop = None  # stored in run() for cross-thread async injection

    async def _tx_handler(self, data: bytes):
        """Transmit on Channel F through the shared scheduler or backend fallback."""
        return await send_channel_packet(
            self, data, channel=CHANNEL_F_TX_CHANNEL,
            load_ui=_load_channel_f_ui, default_tx_power=14, logger=logger,
        )

    def _rx_from_backend(self, payload, rssi=0, snr=0.0):
        """Callback invoked by WM1303Backend when a chan_Lora_std packet is matched.

        Channel F shares the HAL pkt_fwd UDP port with channels A-D. The
        backend RX dispatcher first tries to match incoming packets against
        the multi-SF virtual radios (A-D), then channel_e (SX1261), and finally
        channel_f (this hook) — using frequency + bandwidth + spreading factor
        from wm1303_ui.json's channel_f section.
        """
        self.packets_received += 1
        pkt_hash = _stable_hash(payload)
        pkt_hash8 = pkt_hash[:8] if pkt_hash else _stable_hash(payload)[:8]
        logger.info("Channel F RX (via backend): %dB rssi=%d snr=%.1f hash=%s",
                    len(payload), rssi, snr, pkt_hash)
        # Emit a `received` trace step BEFORE inject_packet so it becomes the
        # first step in the Tracing UI for channel_f packets.
        try:
            if self.bridge is not None and hasattr(self.bridge, 'emit_received_trace'):
                _pt = 'UNKNOWN'
                try:
                    _pt = self.bridge._get_packet_type_name(payload)
                except Exception:
                    pass
                self.bridge.emit_received_trace(
                    pkt_hash8, CHANNEL_F_NAME, _pt, len(payload),
                    rssi=rssi, snr=snr)
        except Exception as _te:
            logger.debug('Channel F RX: received-trace emit failed: %s', _te)
        try:
            if self._loop is not None and self._loop.is_running():
                self._loop.call_soon_threadsafe(
                    self._schedule_rx, payload, rssi, snr)
            else:
                logger.warning("Channel F RX: no event loop stored, cannot inject")
        except Exception as e:
            self.packets_errors += 1
            logger.error("Channel F RX inject error: %s", e)

    def _schedule_rx(self, payload, rssi, snr):
        if not self._running:
            return
        async def inject():
            try:
                accepted = await self.bridge.inject_packet(
                    CHANNEL_F_NAME, payload, origin_channel=CHANNEL_F_NAME,
                    rssi=rssi, snr=snr)
                if accepted is not False:
                    self.packets_injected += 1
            except Exception:
                self.packets_errors += 1
                logger.exception("Channel F RX injection failed")
        task = asyncio.create_task(inject())
        self._rx_tasks.add(task)
        task.add_done_callback(self._rx_tasks.discard)

    async def _wait_for_bridge(self, timeout=30):
        """Wait for bridge_engine to be fully initialized."""
        for i in range(timeout * 10):
            if (self.bridge is not None
                    and hasattr(self.bridge, '_endpoint_handlers')
                    and self.bridge._endpoint_handlers is not None):
                return True
            await asyncio.sleep(0.1)
        return False

    async def run(self):
        """Register the endpoint until stopped; release callbacks on every exit."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.current_task()
        sock = None
        try:
            if not await self._wait_for_bridge():
                logger.error('Channel F: bridge engine not ready after timeout')
                return
            self._loop = asyncio.get_running_loop()
            self._running = True
            self.bridge._endpoint_handlers[CHANNEL_F_NAME] = self._tx_handler
            if self.backend is not None:
                self.backend._channel_f_rx_callback = self._rx_from_backend
            logger.info('Channel F: bridge endpoint active')
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
            self.stop()
            if self._rx_tasks:
                await asyncio.gather(*tuple(self._rx_tasks), return_exceptions=True)
            if sock is not None:
                sock.close()

    def stop(self):
        self._running = False
        self._loop = None
        handlers = getattr(self.bridge, '_endpoint_handlers', {})
        if handlers.get(CHANNEL_F_NAME) == self._tx_handler:
            handlers.pop(CHANNEL_F_NAME, None)
        if (self.backend is not None
                and getattr(self.backend, '_channel_f_rx_callback', None) == self._rx_from_backend):
            self.backend._channel_f_rx_callback = None
        task = self._task
        if task is not None and not task.done():
            task.get_loop().call_soon_threadsafe(task.cancel)
        for task in tuple(self._rx_tasks):
            if not task.done():
                task.get_loop().call_soon_threadsafe(task.cancel)

    def get_stats(self) -> dict:
        return {
            'received': self.packets_received,
            'injected': self.packets_injected,
            'errors': self.packets_errors,
            'tx_packets': self.tx_packets,
            'tx_errors': self.tx_errors,
        }
