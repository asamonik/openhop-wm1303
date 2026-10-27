"""Concurrent region refreshes must not publish older maps or default keys last."""

import asyncio
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_engine_protocol import main_module


class RegionRefreshTests(unittest.TestCase):
    def test_slow_older_map_cannot_replace_newer_settings(self):
        self.check_refresh_race(default_only=False)

    def test_slow_default_resolution_cannot_restore_an_old_key(self):
        self.check_refresh_race(default_only=True)

    def check_refresh_race(self, *, default_only):
        old = SimpleNamespace(regions=['old'], key=b'o' * 16)
        new = SimpleNamespace(regions=['new'], key=b'n' * 16)
        live = [old]
        captured = threading.Event()
        release = threading.Event()
        newer_attempted_or_finished = threading.Event()
        errors = []

        class ObservedLock:
            def __init__(self):
                self.lock = threading.RLock()

            def __enter__(self):
                if threading.current_thread().name == 'region-refresh-new':
                    newer_attempted_or_finished.set()
                self.lock.acquire()

            def __exit__(self, *args):
                self.lock.release()

        daemon = main_module.RepeaterDaemon.__new__(main_module.RepeaterDaemon)
        daemon.config = {'mesh': {'default_region': 'test'}}
        daemon._region_map = old
        daemon._region_map_lock = ObservedLock()
        daemon.repeater_handler = None
        daemon.dispatcher = SimpleNamespace(region_map=old, default_flood_transport_key=old.key)
        daemon.companion_bridges = {i: SimpleNamespace(region_map=old) for i in (1, 2)}

        def hold_old_snapshot():
            captured.set()
            if not release.wait(2):
                raise TimeoutError('test did not release old snapshot')

        def build(config, storage):
            snapshot = live[0]
            if not default_only and threading.current_thread().name == 'region-refresh-old':
                hold_old_snapshot()
            return snapshot

        def resolve(config, snapshot):
            if default_only and threading.current_thread().name == 'region-refresh-old':
                hold_old_snapshot()
            return snapshot.key

        def refresh(is_old):
            try:
                if is_old and default_only:
                    daemon.refresh_default_flood_scope()
                else:
                    daemon.refresh_region_map()
            except BaseException as exc:
                errors.append(exc)
            finally:
                # Before the fix there is no lock acquisition to observe; wait
                # for the newer refresh to finish before releasing the older one.
                if not is_old:
                    newer_attempted_or_finished.set()

        older = threading.Thread(target=refresh, args=(True,), name='region-refresh-old')
        newer = threading.Thread(target=refresh, args=(False,), name='region-refresh-new')
        with patch.object(main_module, 'build_region_map', build), \
             patch.object(main_module, 'resolve_default_scope_key', resolve):
            older.start()
            try:
                self.assertTrue(captured.wait(2))
                live[0] = new
                newer.start()
                self.assertTrue(newer_attempted_or_finished.wait(2))
            finally:
                release.set()
                older.join(2)
                if newer.ident is not None:
                    newer.join(2)
        self.assertFalse(older.is_alive())
        self.assertFalse(newer.is_alive())
        self.assertEqual(errors, [])
        self.assertIs(daemon._region_map, new)
        self.assertIs(daemon.dispatcher.region_map, new)
        self.assertTrue(all(bridge.region_map is new for bridge in daemon.companion_bridges.values()))
        self.assertEqual(daemon.dispatcher.default_flood_transport_key, new.key)
        self.assertEqual(old.regions, ['old'])  # Readers can retain their immutable snapshot.


class RegionActivationTests(unittest.IsolatedAsyncioTestCase):
    async def test_companion_startup_picks_up_a_map_refreshed_while_awaiting(self):
        old = SimpleNamespace(regions=['old'], key=b'o' * 16)
        new = SimpleNamespace(regions=['new'], key=b'n' * 16)
        daemon = main_module.RepeaterDaemon.__new__(main_module.RepeaterDaemon)
        daemon.config = {}
        daemon._shutdown_started = False
        daemon._companion_activation_lock = asyncio.Lock()
        daemon._region_map_lock = threading.RLock()
        daemon._region_map = old
        daemon.repeater_handler = None
        daemon.local_hash = 1
        daemon.discovery_helper = None
        daemon.companion_bridges = {}
        daemon.companion_frame_servers = []
        daemon.dispatcher = SimpleNamespace(region_map=old, default_flood_transport_key=old.key,
                                            add_raw_packet_subscriber=Mock())
        daemon.identity_manager = SimpleNamespace(named_identities={},
            registration_error=Mock(return_value=None), register_identity=Mock(return_value=True))
        daemon._get_companion_radio_settings = Mock(return_value={})
        identity = SimpleNamespace(get_public_key=lambda: b'k' * 32)
        bridge = SimpleNamespace(start=AsyncMock(), stop=AsyncMock(), note_flood_copy=Mock(),
                                  get_channel=lambda index: object())

        async def start_frame_server():
            self.assertIs(bridge.region_map, old)
            # A storage edit can finish while network startup is awaiting I/O.
            await asyncio.to_thread(daemon.refresh_region_map)
            self.assertIs(daemon._region_map, new)

        server = SimpleNamespace(start=AsyncMock(side_effect=start_frame_server),
                                 stop=AsyncMock(), stop_clients=AsyncMock())
        imports = {
            'openhop_core': SimpleNamespace(LocalIdentity=lambda **kwargs: identity),
            'openhop_core.companion.models': SimpleNamespace(Channel=object),
            'repeater.companion': SimpleNamespace(RepeaterCompanionBridge=lambda **kwargs: bridge),
            'repeater.companion.frame_server_lifecycle': SimpleNamespace(CompanionFrameServer=lambda **kwargs: server),
            'repeater.companion.constants': SimpleNamespace(DEFAULT_PUBLIC_CHANNEL_SECRET=b''),
            'repeater.companion_storage': SimpleNamespace(
                companion_limits_from_settings=lambda settings: {}, legacy_owner_from_settings=lambda settings: None,
                storage_key_for_public_key=lambda key: key.hex(), validated_channel_rows=lambda rows, **kwargs: rows),
        }
        with patch.dict(sys.modules, imports), \
             patch.object(main_module, 'validate_companion_node_name', lambda value: value), \
             patch.object(main_module, 'build_region_map', return_value=new), \
             patch.object(main_module, 'resolve_default_scope_key', return_value=new.key):
            await daemon.add_companion_from_config({'name': 'new', 'identity_key': b'a' * 32})
        self.assertIs(daemon.companion_bridges[ord('k')], bridge)
        self.assertIs(bridge.region_map, new)
        self.assertEqual(daemon.companion_frame_servers, [server])
        self.assertIs(daemon.dispatcher.region_map, new)
        self.assertEqual(daemon.dispatcher.default_flood_transport_key, new.key)
        daemon.identity_manager.register_identity.assert_called_once()
        bridge.stop.assert_not_awaited()
        server.stop.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
