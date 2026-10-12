"""Duty semantics, packet scope resolution, and public node information."""

import ast
import base64
from copy import deepcopy
import hashlib
import hmac
import json
import math
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cherrypy
import yaml

from test_radio_bridge import backend_module, frame
from test_web_persistence import wm1303_api
from repeater.config import validate_owner_info
from repeater.config_manager import ConfigManager
from repeater.region_scope import packet_scope


ROOT = Path(__file__).resolve().parents[1]


class DutyCycleTests(unittest.TestCase):
    def test_restored_cumulative_counters_do_not_inflate_live_duty(self):
        backend = backend_module.WM1303Backend({})
        backend._stats_baseline = {'channel_a': {
            'rx_count': 100, 'tx_count': 500, 'tx_bytes': 5000,
            'total_tx_airtime_ms': 600000,
        }}
        backend._tx_queue_manager = SimpleNamespace(get_status=lambda: {
            'channel_a': {'total_sent': 1},
        })
        with patch.object(backend_module.time, 'monotonic', return_value=100):
            backend._update_tx_stats('channel_a', 5, 1200, 5000, 30)
            stats = backend.get_channel_stats()['channel_a']
            self.assertEqual(stats['tx_count'], 501)
            self.assertEqual(stats['rx_count'], 100)
            self.assertEqual(stats['total_tx_airtime_ms'], 601200)
            self.assertEqual(stats['total_tx_airtime_ms_session'], 1200)
            self.assertEqual(stats['tx_bytes'], 5030)
            self.assertEqual(stats['tx_duty_pct'], 2)

    def test_recent_airtime_is_not_lifetime_average_or_budget_usage(self):
        backend = backend_module.WM1303Backend({})
        backend._start_time = 1
        with patch.object(backend_module.time, 'monotonic', return_value=100):
            backend._update_tx_stats('channel_a', 5, 1800, 5000, 30)
            backend._update_tx_stats('channel_e', 5000, 1200, 5000, 30)
            backend._record_duty_airtime('channel_a', 'rx', 600)
            duty = backend.get_duty_cycle_stats()
            self.assertEqual(duty['tx_duty_pct'], 5)
            self.assertEqual(duty['rx_duty_pct'], 1)
            self.assertEqual(duty['rx_availability_pct'], 95)
            self.assertEqual(duty['channels']['channel_e']['tx_duty_pct'], 2)
            self.assertEqual(duty['window_seconds'], 60)
        with patch.object(backend_module.time, 'monotonic', return_value=161):
            self.assertEqual(backend.get_duty_cycle_stats()['tx_duty_pct'], 0)
            self.assertEqual(backend._channel_tx_stats['channel_e']['total_airtime_ms'], 1200)

    def test_received_airtime_uses_received_sf_bandwidth_and_coding_rate(self):
        config = {'spreading_factor': 0, 'bandwidth': 125000, 'coding_rate': '4/5', 'preamble_length': 17}
        slow = backend_module.WM1303Backend._rx_airtime_ms({'datr': 'SF12BW125', 'codr': '4/8'}, b'x' * 50, config)
        fast = backend_module.WM1303Backend._rx_airtime_ms({'datr': 'SF7BW125', 'codr': '4/5'}, b'x' * 50, config)
        self.assertGreater(slow, fast * 10)
        expected = backend_module.estimate_lora_airtime_ms(50, sf=12, bw_hz=125000, cr=8, preamble=17)
        self.assertEqual(slow, expected)

    def test_history_aggregates_every_channel_and_counter_resets_for_all_intervals(self):
        api = wm1303_api.WM1303API.__new__(wm1303_api.WM1303API)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'repeater.db'
            with sqlite3.connect(path) as conn:
                conn.execute('CREATE TABLE channel_stats_history(timestamp REAL, channel_id TEXT, tx_airtime_ms REAL)')
                conn.executemany('INSERT INTO channel_stats_history VALUES(?,?,?)', [
                    (3540, 'channel_a', 0), (3600, 'channel_a', 1800),
                    (3660, 'channel_a', 600), (3720, 'channel_a', 600),
                    (3540, 'channel_e', 0), (3600, 'channel_e', 1200),
                    (3660, 'channel_e', 1200), (3720, 'channel_e', 1200),
                ])
            with patch.object(wm1303_api, '_DB_PATH', str(path)), \
                    patch.object(wm1303_api.time, 'time', return_value=3780), \
                    patch.object(wm1303_api, '_load_ui', return_value={'channels': [{'name': 'Hill'}]}):
                result = json.loads(api._duty_cycle_history('1'))
                self.assertEqual([point['tx_duty_pct'] for point in result['data']], [5, 1, 0])
                self.assertEqual(result['channels'][0]['label'], 'Hill')
                for hours in (1, 6, 12, 24, 72, 168):
                    data = json.loads(api._duty_cycle_history(str(hours)))
                    self.assertAlmostEqual(data['interval_tx_duty_pct'], round(3600 / (hours * 36000), 3))
                with self.assertRaises(cherrypy.HTTPError):
                    api._duty_cycle_history('0')
            with patch.object(wm1303_api, '_DB_PATH', str(Path(directory) / 'empty.db')):
                self.assertIsNone(json.loads(api._duty_cycle_history('1'))['interval_tx_duty_pct'])

    def test_failed_spectrum_samples_are_not_displayed_as_measurements(self):
        api = wm1303_api.WM1303API.__new__(wm1303_api.WM1303API)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'scan.json'
            path.write_text(json.dumps({'timestamp': 1234, 'channels': {
                '868000000': {'samples': 0, 'rssi_avg': -120},
                '868200000': {'samples': 10, 'rssi_avg': float('nan')},
                '868400000': {'samples': 10, 'rssi_avg': -104},
            }}))
            with patch.object(wm1303_api, '_SPECTRAL_RES', path), \
                    patch.object(wm1303_api, '_COLLECTOR_AVAILABLE', False), \
                    patch.object(wm1303_api, '_load_global_conf', return_value={}):
                measured = json.loads(api._do_spectrum_scan())
                self.assertEqual(measured['scan_points'], [{'freq_mhz': 868.4, 'rssi_dbm': -104}])
                self.assertEqual(measured['status'], 'ok')

    def test_scan_control_reports_saved_settings_when_restart_fails(self):
        api = wm1303_api.WM1303API.__new__(wm1303_api.WM1303API)
        with patch.object(wm1303_api, '_body', return_value={'action': 'toggle', 'enable': True}), \
                patch.object(wm1303_api, '_toggle_spectral_scan', return_value=True), \
                patch.object(wm1303_api.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
            result = json.loads(api._spectrum_post())
            self.assertTrue(result['saved'])
            self.assertTrue(result['restart_required'])
            self.assertIn('restart failed', result['restart_error'])


class ScopePacket:
    def read_from(self, raw):
        self.header = raw[0]
        self.transport_codes = [int.from_bytes(raw[1:3], 'little'), int.from_bytes(raw[3:5], 'little')]
        self.payload = raw[6:]
        return len(raw) >= 6

    def has_transport_codes(self):
        return self.header & 3 in (0, 3)

    def get_payload_type(self):
        return (self.header >> 2) & 15

    def get_payload(self):
        return self.payload


def transport_code(key, packet):
    value = int.from_bytes(hmac.new(key, bytes([packet.get_payload_type()]) + packet.get_payload(), hashlib.sha256).digest()[:2], 'little')
    return 1 if value == 0 else 65534 if value == 65535 else value


class PacketScopeTests(unittest.TestCase):
    def test_known_unknown_malformed_and_colliding_scope_keys(self):
        key = b'x' * 16
        packet = ScopePacket()
        raw = bytearray(frame(route=0))
        packet.read_from(raw)
        raw[1:3] = transport_code(key, packet).to_bytes(2, 'little')
        region = {'name': '#vienna', 'transport_key': base64.b64encode(key).decode(), 'flood_policy': 'deny'}
        storage = SimpleNamespace(get_transport_keys=lambda: [{'transport_key': 'invalid'}, region])
        modules = {'openhop_core.protocol': SimpleNamespace(Packet=ScopePacket),
                   'openhop_core.protocol.transport_keys': SimpleNamespace(calc_transport_code=transport_code)}
        with patch.dict(sys.modules, modules):
            self.assertEqual(packet_scope({'raw_packet': raw.hex()}, storage), {'scope': '#vienna', 'scope_known': True})
            for invalid in ('not hex', '', frame(route=1).hex(), frame(route=2).hex()):
                self.assertEqual(packet_scope({'raw_packet': invalid}, storage)['scope'], 'unknown scope')
            other = {**region, 'name': '#other'}
            storage.get_transport_keys = lambda: [region, other]
            self.assertFalse(packet_scope({'raw_packet': raw.hex()}, storage)['scope_known'])
            storage.get_transport_keys = lambda: []
            self.assertFalse(packet_scope({'raw_packet': raw.hex()}, storage)['scope_known'])


class NodeInformationTests(unittest.TestCase):
    def test_owner_save_preserves_pending_settings_and_failed_save_keeps_live_value(self):
        source = ROOT / 'overlay/openhop_repeater/repeater/web/api_endpoints.py'
        cls = next(node for node in ast.parse(source.read_text()).body if isinstance(node, ast.ClassDef) and node.name == 'APIEndpoints')
        cls.body = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in (
            '_success', '_error', 'repeater_info', 'update_radio_config')]
        for method in cls.body:
            method.decorator_list = []
        namespace = dict(math=math, logger=Mock(), cherrypy=cherrypy)
        exec(compile(ast.Module(body=[cls], type_ignores=[]), str(source), 'exec'), namespace)
        api = namespace['APIEndpoints']()
        api._set_cors_headers = api._require_post = lambda: None
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.yaml'
            api.config = {'radio_type': 'wm1303', 'repeater': {'node_name': 'Hill', 'owner_info': 'old'}, 'radio': {'frequency': 869000000}}
            saved = deepcopy(api.config)
            saved['radio']['frequency'] = 868000000
            saved['repeater']['security'] = {'admin_password': 'secret'}
            path.write_text(yaml.safe_dump(saved))
            api.config_manager = ConfigManager(str(path), api.config)
            request = SimpleNamespace(method='GET', json={})
            with patch.object(cherrypy, 'request', request):
                self.assertNotIn('security', api.repeater_info()['data'])
                request.method = 'POST'
                request.json = {'owner_info': 'Österreich\nContact: OE1ABC'}
                self.assertTrue(api.update_radio_config()['success'])
                self.assertEqual(api.config['repeater']['owner_info'], request.json['owner_info'])
                persisted = yaml.safe_load(path.read_text())
                self.assertEqual(persisted['radio']['frequency'], 868000000)
                self.assertEqual(persisted['repeater']['security'], saved['repeater']['security'])
                previous = path.read_text()
                for invalid in ('ö' * 58, None, 42, 'nul\x00text'):
                    request.json = {'owner_info': invalid}
                    self.assertFalse(api.update_radio_config()['success'])
                    self.assertEqual(path.read_text(), previous)
                request.json = {'owner_info': 'new'}
                with patch.object(api.config_manager, 'save_to_file', return_value=False):
                    self.assertFalse(api.update_radio_config()['success'])
                self.assertEqual(api.config['repeater']['owner_info'], 'Österreich\nContact: OE1ABC')
                from repeater.handler_helpers.mesh_cli import MeshCLI
                cli = MeshCLI(str(path), api.config, api.config_manager)
                self.assertEqual(cli.handle_command(b'x' * 32, 'get owner.info', is_admin=True), '> Österreich|Contact: OE1ABC')
                self.assertEqual(cli.handle_command(b'x' * 32, 'set owner.info OE1ABC|Maintainer', is_admin=True), 'OK')
                self.assertEqual(api.config['repeater']['owner_info'], 'OE1ABC\nMaintainer')
                request.json = {'owner_info': ''}
                self.assertTrue(api.update_radio_config()['success'])
                self.assertEqual(api.config['repeater']['owner_info'], '')
        self.assertEqual(validate_owner_info('ö' * 57), 'ö' * 57)
