"""Loopback HTTP contracts with real CherryPy and the overlaid dependencies.

Run explicitly with the installed core/repeater on PYTHONPATH. All policy files,
SQLite outcomes, authentication fixtures, and HTTP sockets are test-local.
"""

import base64
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from wsgiref.simple_server import WSGIRequestHandler, make_server

import cherrypy
import yaml

from repeater.data_acquisition.sqlite_handler import SQLiteHandler
from repeater.web.api_endpoints import APIEndpoints
from repeater.web.auth.cherrypy_tool import register_require_auth_tool
from repeater.web.auth_endpoints import AuthEndpoints
from repeater.web import packet_trace, wm1303_api
from repeater.web.wm1303_api import WM1303API
from repeater.web.http_server import _cors_response_headers


class QuietRequestHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


class AnalyticsHTTPTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        with patch.object(SQLiteHandler, "_start_wal_checkpoint_thread"):
            self.db = SQLiteHandler(self.path)
        self.addCleanup(self.db.close_thread_connection)
        self.config = {"radio_type": "wm1303", "web": {"cors_enabled": True},
                       "policy": {"policy_file": "policy.yaml"}}
        daemon = SimpleNamespace(config=self.config, bridge_engine=object(),
                                 repeater_handler=SimpleNamespace(storage=self.db))
        self.api = APIEndpoints(config=self.config, daemon_instance=daemon,
                                config_path=str(self.path / "config.yaml"))
        self.saved_config = dict(cherrypy.config)
        self.saved_apps = dict(cherrypy.tree.apps)
        self.addCleanup(self.restore_cherrypy)
        self.jwt = SimpleNamespace(verify_jwt=lambda token: {
            "sub": "http-fixture", "client_id": "fixture",
        } if token == "fixture-token" else None,
            create_jwt=Mock(return_value="new-fixture-token"), expiry_minutes=15)
        cherrypy.config.update({
            "log.screen": False,
            "jwt_handler": self.jwt,
            "token_manager": SimpleNamespace(verify_token=lambda token: None),
        })
        register_require_auth_tool()
        cherrypy.tree.mount(self.api, "/api", {"/": {
            "tools.require_auth.on": True,
            "tools.response_headers.on": True,
            "tools.response_headers.headers": _cors_response_headers(),
        }})
        self.auth_manager = Mock()
        auth = AuthEndpoints({"repeater": {"security": {"admin_password": "fixture-password"}}},
                             self.jwt, cherrypy.config["token_manager"], self.auth_manager)
        cherrypy.tree.mount(auth, "/api/auth", {"/": {"tools.require_auth.on": False}})
        cherrypy.tree.mount(WM1303API.__new__(WM1303API), "/api/wm1303", {"/": {
            "tools.require_auth.on": True,
            "tools.trailing_slash.on": False,
        }})

        def application(environ, start_response):
            try:
                yield from cherrypy.tree(environ, start_response)
            finally:
                self.db.close_thread_connection()

        self.server = make_server("127.0.0.1", 0, application,
                                  handler_class=QuietRequestHandler)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def restore_cherrypy(self):
        cherrypy.config.clear()
        cherrypy.config.update(self.saved_config)
        cherrypy.tree.apps.clear()
        cherrypy.tree.apps.update(self.saved_apps)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())

    def request(self, path, method="GET", body=None, authenticated=True):
        headers = {"Authorization": "Bearer fixture-token"} if authenticated else {}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        req = Request(self.base + path, data=data, headers=headers, method=method)
        try:
            response = urlopen(req, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            status, headers, raw = response.status, response.headers, response.read()
        payload = json.loads(raw) if raw and headers.get_content_type() == "application/json" else raw
        return status, headers, payload

    def test_region_edits_refresh_the_live_transport_key_cache(self):
        from openhop_core.protocol import Packet
        from openhop_core.protocol.transport_keys import calc_transport_code
        from repeater.engine import RepeaterHandler

        handler = RepeaterHandler.__new__(RepeaterHandler)
        handler.storage = self.db
        handler._transport_keys_cache = None
        handler._transport_keys_cache_time = 0
        handler._transport_keys_cache_ttl = 60
        handler._transport_keys_cache_lock = threading.RLock()
        self.api.daemon_instance.repeater_handler = handler
        key = b'k' * 16
        packet = Packet()
        packet.header = 15 << 2  # RAW_CUSTOM over TRANSPORT_FLOOD
        packet.payload = bytearray(b'transport-cache-http-fixture')
        packet.payload_len = len(packet.payload)
        packet.transport_codes = [calc_transport_code(key, packet), 0]

        status, _, payload = self.request('/api/transport_keys', 'POST', {
            'name': 'test-region', 'flood_policy': 'allow',
            'transport_key': base64.b64encode(key).decode(),
        })
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload['success'], payload)
        key_id = payload['data']['id']
        self.assertTrue(handler._check_transport_codes(packet)[0])
        for policy, allowed in (('deny', False), ('allow', True)):
            status, _, payload = self.request(f'/api/transport_key/{key_id}', 'PUT', {
                'flood_policy': policy,
            })
            self.assertEqual(status, 200, payload)
            self.assertTrue(payload['success'], payload)
            self.assertEqual(handler._check_transport_codes(packet)[0], allowed)
        status, _, payload = self.request(f'/api/transport_key/{key_id}', 'DELETE')
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload['success'], payload)
        self.assertFalse(handler._check_transport_codes(packet)[0])

    def test_auth_input_errors_are_json_http_errors_without_mutation(self):
        for endpoint in ("login", "refresh", "change_password"):
            for body in ([], None, b"{", b"\xff"):
                with self.subTest(endpoint=endpoint, body=body):
                    status, headers, payload = self.request(
                        "/api/auth/" + endpoint, "POST", b"null" if body is None else body)
                    self.assertEqual(status, 400, payload)
                    self.assertEqual(headers.get_content_type(), "application/json")
                    self.assertFalse(payload["success"])
        self.jwt.create_jwt.assert_not_called()
        self.auth_manager.update_and_save.assert_not_called()
        self.assertEqual(self.request("/api/auth/refresh", "POST", {}, authenticated=False)[0], 401)
        status, _, payload = self.request("/api/auth/login", "POST", {
            "username": "admin", "password": "fixture-password", "client_id": "fixture",
        }, authenticated=False)
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["success"])
        self.jwt.create_jwt.assert_called_once_with("admin", "fixture")

    def test_radio_power_errors_preserve_settings_through_actual_http_routes(self):
        ui_path = self.path / 'wm1303_ui.json'
        original = json.dumps({'channels': [], 'region': {'code': 'EU868'}})
        with patch.object(wm1303_api, '_UI_JSON', ui_path), \
                patch.object(wm1303_api, '_sync_config_yaml_channels') as yaml_sync, \
                patch.object(wm1303_api, 'sync_global_conf', return_value={'status': 'ok'}) as hal_sync:
            for resource in ('channels', 'channel_e', 'channel_f'):
                for power in (11, 128, 12, 27):
                    with self.subTest(resource=resource, power=power):
                        ui_path.write_text(original)
                        yaml_sync.reset_mock()
                        hal_sync.reset_mock()
                        channel = {'frequency': 869525000, 'tx_power': power}
                        if resource == 'channels':
                            channel['active'] = True
                            body = [channel]
                        else:
                            channel['enabled'] = True
                            body = channel
                        status, _, result = self.request('/api/wm1303/' + resource, 'POST', body)
                        if power not in (12, 27):
                            self.assertEqual(status, 400, result)
                            self.assertEqual(ui_path.read_text(), original)
                            yaml_sync.assert_not_called()
                            hal_sync.assert_not_called()
                        else:
                            self.assertEqual(status, 200, result)
                            self.assertEqual(result['status'], 'ok')
                            saved = json.loads(ui_path.read_text())
                            saved_channel = saved['channels'][0] if resource == 'channels' else saved[resource]
                            self.assertEqual(saved_channel['tx_power'], power)
                            hal_sync.assert_called_once()

    def test_lbt_http_summary_errors_preflight_and_auth(self):
        record = dict(timestamp=time.time() - 60, channel_id="channel_e", packet_type=5,
                      ack_received=True, ok=True, cad_enabled=True, cad_detected=False,
                      cad_retries=0, cad_reason="clear", lbt_enabled=False,
                      scheduler_attempt=1)
        self.db.store_tx_diagnostic(record)
        self.db.store_tx_diagnostic(dict(record, cad_retries=2, cad_reason="cleared_after_retries"))
        self.db.store_tx_diagnostic(dict(record, ack_received=False, cad_detected=None,
                                         cad_retries=None, cad_reason=None))
        status, headers, result = self.request(
            "/api/lbt_diagnostics?hours=1&bucket_seconds=60&severe_attempt_threshold=3")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get_content_type(), "application/json")
        self.assertTrue(result["success"])
        data = result["data"]
        self.assertEqual(data["data_source"], "wm1303_tx_diagnostics")
        summary = data["summary"]
        self.assertEqual(summary["recorded_transmissions"], 3)
        self.assertEqual(summary["total_transmissions"], 2)
        self.assertEqual(summary["total_attempts"], 4)
        self.assertEqual(summary["unknown_transmissions"], 1)
        self.assertEqual(summary["retry_rate_pct"], 50)
        self.assertEqual(summary["busy_channel_events"], 1)
        self.assertEqual(summary["max_attempts"], 3)
        self.assertEqual(data["packet_types"][0]["packet_type"], 5)
        self.assertIsNone(data["buckets"][0]["rf"]["avg_snr"])
        status, _, result = self.request("/api/lbt_diagnostics?hours=invalid")
        self.assertEqual(status, 200)  # Existing analytics error envelope contract.
        self.assertFalse(result["success"])
        self.assertIn("Invalid parameter format", result["error"])
        status, headers, result = self.request("/api/lbt_diagnostics", "OPTIONS", authenticated=False)
        self.assertIn(status, (200, 204))
        self.assertEqual(headers["Access-Control-Allow-Origin"], "*")
        self.assertIn(result, ("", b""))  # Newer auth tools short-circuit preflight.
        self.assertEqual(self.request("/api/lbt_diagnostics", authenticated=False)[0], 401)

    def test_trace_http_filters_repeated_queries_and_internal_errors(self):
        collector = packet_trace.TraceCollector()
        with patch.object(packet_trace, '_CHANNEL_FRIENDLY_NAMES', {'channel_a': 'High Alpine'}):
            collector.trace_event('fixture-packet', 'received', channel='channel_a')
            collector.trace_event('fixture-packet', 'self_echo')
            with patch.object(packet_trace, 'get_collector', return_value=collector):
                status, _, payload = self.request(
                    '/api/wm1303/packet_traces?channel=channel_a&status=self_echo&limit=1')
                self.assertEqual(status, 200, payload)
                self.assertEqual([trace['pkt_hash'] for trace in payload['traces']], ['fixture-packet'])
        for query in ('limit=-1', 'limit=1&limit=2', 'channel=a&channel=b', 'status=ok&status=error'):
            with self.subTest(query=query):
                self.assertEqual(self.request('/api/wm1303/packet_traces?' + query)[0], 400)
        self.assertEqual(self.request('/api/wm1303/packet_traces', authenticated=False)[0], 401)
        with patch.object(packet_trace, 'get_traces', side_effect=RuntimeError('fixture-private-path')):
            status, _, payload = self.request('/api/wm1303/packet_traces')
        self.assertEqual(status, 500)
        self.assertNotIn('fixture-private-path', str(payload))

    def test_policy_http_read_validate_save_and_group_json_bodies(self):
        status, _, result = self.request("/api/policy")
        self.assertEqual(status, 200)
        self.assertTrue(result["data"]["supported"])
        self.assertFalse(result["data"]["policy_engine"]["enabled"])
        draft = {"policy_engine": {"enabled": True, "default_action": "drop",
                                    "rules": [], "objects": {}}}
        status, _, result = self.request("/api/policy_validate", "POST", draft)
        self.assertEqual(status, 200)
        self.assertTrue(result["data"]["valid"])
        self.assertFalse((self.path / "policy.yaml").exists())
        status, _, result = self.request("/api/policy", "POST", draft)
        self.assertEqual(status, 200)
        self.assertTrue(result["success"])
        self.assertTrue(result["saved"])
        self.assertTrue(result["live_updated"])
        self.assertEqual(self.config["policy_engine"]["default_action"], "drop")
        with (self.path / "policy.yaml").open() as stream:
            self.assertTrue(yaml.safe_load(stream)["policy_engine"]["enabled"])
        group = dict(kind="channel_hashes", group_id="local_channels", friendly_name="Local Channels")
        status, _, result = self.request("/api/policy_groups", "POST", group)
        self.assertEqual(status, 200)
        self.assertEqual(result["data"]["group_id"], "local_channels")
        entry = dict(kind="channel_hashes", group_id="local_channels", entry_id="mesh",
                     friendly_name="Mesh", value="0xab")
        status, _, result = self.request("/api/policy_group_entries", "POST", entry)
        self.assertEqual(status, 200)
        self.assertEqual(result["data"]["entry"]["value"].lower(), "0xab")
        status, _, result = self.request("/api/policy_groups?kind=channel_hashes")
        self.assertEqual(status, 200)
        self.assertEqual(result["data"]["groups"][0]["id"], "local_channels")
        status, _, result = self.request(
            "/api/policy_group_entries?kind=channel_hashes&group_id=local_channels")
        self.assertEqual(status, 200)
        self.assertEqual(len(result["data"]["entries"]), 1)
        status, _, result = self.request("/api/policy_group_entries", "DELETE", {
            "kind": "channel_hashes", "group_id": "local_channels", "entry_id": "mesh",
        })
        self.assertEqual(status, 200)
        self.assertTrue(result["success"])
        status, _, result = self.request("/api/policy_groups", "DELETE", {
            "kind": "channel_hashes", "group_id": "local_channels",
        })
        self.assertEqual(status, 200)
        self.assertTrue(result["success"])

    def test_policy_http_error_and_query_delete_contracts(self):
        status, _, result = self.request("/api/policy", "POST", [])
        self.assertEqual(status, 400)
        self.assertFalse(result["success"])
        self.assertIn("JSON object", result["error"])
        status, _, result = self.request("/api/policy_groups?kind=invalid")
        self.assertEqual(status, 400)
        self.assertFalse(result["success"])
        status, headers, result = self.request("/api/policy", "PUT", {})
        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "GET, POST, OPTIONS")
        self.assertFalse(result["success"])
        self.assertEqual(self.request("/api/policy", authenticated=False)[0], 401)
        status, _, result = self.request("/api/policy_groups", "POST", {
            "kind": "pubkeys", "group_id": "neighbors", "friendly_name": "Neighbors",
        })
        self.assertEqual(status, 200)
        status, _, result = self.request(
            "/api/policy_groups?kind=pubkeys&group_id=neighbors", "DELETE")
        self.assertEqual(status, 200)
        self.assertTrue(result["success"])


if __name__ == "__main__":
    unittest.main()
