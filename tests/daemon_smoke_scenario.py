"""Real-package daemon initialization/shutdown, with radio I/O simulated."""

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from repeater.config import save_config
from repeater.main import RepeaterDaemon


class SimulatedRadio:
    frequency = 869618000
    bandwidth = 125000
    spreading_factor = 8
    coding_rate = 5
    preamble_length = 17
    tx_power = 14
    crc_error_count = 0

    def __init__(self):
        self.is_alive = True

    def begin(self):
        return True

    def set_rx_callback(self, callback):
        self.callback = callback

    def sleep(self):
        self.is_alive = False

    def cleanup(self):
        self.sleep()

    async def send(self, data):
        return True

    async def wait_for_rx(self):
        await asyncio.Event().wait()

    def get_last_rssi(self):
        return -100

    def get_last_snr(self):
        return 5

    def measure_noise_floor(self, **kwargs):
        return -110


class DaemonSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_package_initialization_and_owned_shutdown_without_rf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {
                'radio_type': 'wm1303',
                'repeater': {'node_name': 'Fixture', 'mode': 'no_tx',
                             'identity_key': bytes(range(32)), 'security': {}},
                'storage': {'storage_dir': str(root / 'data')},
                'mesh': {}, 'web': {}, 'http': {'enabled': False},
                'logging': {'level': 'ERROR'},
            }
            path = root / 'config.yaml'
            self.assertTrue(save_config(config, str(path)))
            radio = SimulatedRadio()
            daemon = RepeaterDaemon(config, radio=radio)
            daemon.config_path = str(path)
            with patch('repeater.main.socket.gethostbyname', return_value='192.0.2.1'), \
                    patch('repeater.main._UNIFORM_TRACER_AVAILABLE', False):
                try:
                    await daemon.initialize()
                    self.assertIsNotNone(daemon.dispatcher.region_map)
                    self.assertIsNotNone(daemon.neighbor_scope_helper)
                finally:
                    await daemon._shutdown()
            self.assertFalse(radio.is_alive)
            self.assertTrue(daemon.neighbor_scope_helper._closing)
            self.assertEqual(daemon.neighbor_scope_helper._owned_sweeps, set())


if __name__ == '__main__':
    unittest.main()
