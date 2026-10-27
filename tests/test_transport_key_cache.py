"""Transport-key cache expiration and HTTP/RX invalidation races."""

import ast
import base64
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_engine_protocol import engine

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / 'overlay/openhop_repeater/repeater/web/api_endpoints.py'
method = next(node for node in ast.walk(ast.parse(source.read_text()))
              if isinstance(node, ast.FunctionDef) and node.name == '_invalidate_region_cache')
namespace = {}
exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
invalidate_region_cache = namespace['_invalidate_region_cache']


class TransportCacheTests(unittest.TestCase):
    def setUp(self):
        self.handler = engine.RepeaterHandler.__new__(engine.RepeaterHandler)
        self.handler._transport_keys_cache = None
        self.handler._transport_keys_cache_time = 0
        self.handler._transport_keys_cache_ttl = 60
        self.handler._transport_keys_cache_lock = threading.RLock()
        self.key = {'id': 1, 'name': 'test', 'transport_key': base64.b64encode(b'k' * 16).decode(),
                    'flood_policy': 'allow'}
        self.storage = SimpleNamespace(get_transport_keys=Mock(return_value=[self.key]),
                                       update_transport_key=Mock())
        self.handler.storage = self.storage
        self.packet = SimpleNamespace(has_transport_codes=lambda: True, transport_codes=[1234, 0])
        self.api = SimpleNamespace(daemon_instance=SimpleNamespace(repeater_handler=self.handler))
        imports = patch.dict(sys.modules, {
            'openhop_core.protocol.transport_keys': SimpleNamespace(calc_transport_code=lambda *args: 1234),
        })
        imports.start()
        self.addCleanup(imports.stop)

    def test_cache_refresh_uses_elapsed_time_and_keeps_last_used_as_wall_time(self):
        for correction in (-3600, 3600):
            with self.subTest(correction=correction):
                self.handler._transport_keys_cache = None
                self.storage.get_transport_keys.return_value = [self.key]
                with patch.object(engine.time, 'time', return_value=1000), \
                     patch.object(engine.time, 'monotonic', return_value=100):
                    self.assertTrue(self.handler._check_transport_codes(self.packet)[0])
                    self.storage.update_transport_key.assert_called_with(key_id=1, last_used=1000)
                self.storage.get_transport_keys.return_value = []
                for elapsed, expected in ((10, True), (60, True), (61, False)):
                    wall = 1000 + correction + elapsed
                    with patch.object(engine.time, 'time', return_value=wall), \
                         patch.object(engine.time, 'monotonic', return_value=100 + elapsed):
                        self.assertEqual(self.handler._check_transport_codes(self.packet)[0], expected)
                        if expected:
                            self.storage.update_transport_key.assert_called_with(key_id=1, last_used=wall)

    def test_api_invalidation_is_not_lost_during_snapshot_refresh(self):
        captured = threading.Event()
        release = threading.Event()
        invalidator_attempted = threading.Event()
        read_count = 0
        results = []
        errors = []

        class ObservedLock:
            def __init__(self):
                self.lock = threading.RLock()

            def __enter__(self):
                if threading.current_thread().name == 'cache-invalidation-test':
                    invalidator_attempted.set()
                self.lock.acquire()

            def __exit__(self, *args):
                self.lock.release()

        self.handler._transport_keys_cache_lock = ObservedLock()

        def read_keys():
            nonlocal read_count
            read_count += 1
            if read_count == 1:
                snapshot = [self.key]
                captured.set()
                if not release.wait(2):
                    raise TimeoutError('test did not release snapshot read')
                return snapshot
            return []

        def read_packet():
            try:
                results.append(self.handler._check_transport_codes(self.packet))
            except BaseException as exc:
                errors.append(exc)

        def invalidate():
            try:
                invalidate_region_cache(self.api)
            except BaseException as exc:
                errors.append(exc)
            finally:
                # Old code does not acquire the lock; let it finish invalidating
                # before allowing the captured snapshot to be published.
                invalidator_attempted.set()

        self.storage.get_transport_keys.side_effect = read_keys
        reader = threading.Thread(target=read_packet)
        invalidator = threading.Thread(target=invalidate, name='cache-invalidation-test')
        with patch.object(engine.time, 'time', return_value=1000), \
             patch.object(engine.time, 'monotonic', return_value=1):
            reader.start()
            try:
                self.assertTrue(captured.wait(2))
                invalidator.start()
                self.assertTrue(invalidator_attempted.wait(2))
            finally:
                release.set()
                reader.join(2)
                if invalidator.ident is not None:
                    invalidator.join(2)
            self.assertFalse(reader.is_alive())
            self.assertFalse(invalidator.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(results, [(True, '')])  # In-flight read keeps its own snapshot.
            self.assertFalse(self.handler._check_transport_codes(self.packet)[0])
            self.assertEqual(read_count, 2)


if __name__ == '__main__':
    unittest.main()
