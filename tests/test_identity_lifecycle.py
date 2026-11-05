"""Local identity activation uses the same namespaces as startup validation."""

import ast
import asyncio
from copy import deepcopy
import functools
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'overlay/openhop_repeater'))
sys.path.append(str(ROOT / 'tests/fixtures/upstream'))

from repeater.identity_manager import IdentityManager
from repeater.room_lifecycle import RoomLifecycleMixin


class Identity:
    def __init__(self, seed):
        self.key = seed

    def get_public_key(self):
        return self.key


class IdentityConfigTests(unittest.TestCase):
    def test_api_validation_matches_runtime_namespaces(self):
        source = ROOT / 'overlay/openhop_repeater/repeater/web/api_endpoints.py'
        cls = next(node for node in ast.parse(source.read_text()).body
                   if isinstance(node, ast.ClassDef) and node.name == 'APIEndpoints')
        cls.body = [node for node in cls.body if isinstance(node, ast.FunctionDef)
                    and node.name == '_validate_configured_identity_hashes']
        namespace = dict(derive_companion_public_key_hex=lambda key: key)
        exec(compile(ast.Module(body=[cls], type_ignores=[]), str(source), 'exec'), namespace)
        api = namespace['APIEndpoints']()
        api.config = {'repeater': {'identity_key': '11' * 32}}
        config = deepcopy(api.config)
        config['identities'] = {
            'room_servers': [{'name': 'room', 'identity_key': '22' * 32}],
            'companions': [{'name': 'companion', 'identity_key': '22' + '33' * 31}],
        }
        # Both a room and the main repeater may share a companion's prefix.
        for prefix in ('22', '11'):
            with self.subTest(prefix=prefix):
                config['identities']['companions'][0]['identity_key'] = prefix + '33' * 31
                api._validate_configured_identity_hashes(config)

        for section, entry in (
            ('room_servers', {'name': 'other-room', 'identity_key': '22' + '44' * 31}),
            ('room_servers', {'name': 'other-room', 'identity_key': '11' + '44' * 31}),
            ('companions', {'name': 'other-companion', 'identity_key': '11' + '44' * 31}),
            ('companions', {'name': 'room', 'identity_key': '55' * 32}),
        ):
            with self.subTest(section=section, name=entry['name'], key=entry['identity_key'][:2]):
                invalid = deepcopy(config)
                invalid['identities'][section].append(entry)
                with self.assertRaises(ValueError):
                    api._validate_configured_identity_hashes(invalid)


class RoomActivationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.daemon = RoomLifecycleMixin()
        daemon = self.daemon
        daemon._shutdown_started = False
        daemon._companion_activation_lock = asyncio.Lock()
        daemon._room_activation_tasks = set()
        daemon._room_sync_failure = None
        daemon._room_sync_failure_event = asyncio.Event()
        daemon.local_hash = 0x11
        daemon.companion_bridges = {}
        daemon.identity_manager = IdentityManager({})
        self.config = {'name': 'room', 'identity_key': (b'\x22' * 32).hex(), 'settings': {}}
        saved = {'identities': {'room_servers': [self.config]}}
        daemon.config_manager = SimpleNamespace(
            _lock=threading.RLock(), read_saved_config=lambda: deepcopy(saved))
        self.rooms = []

        def register_login(*, identity, **kwargs):
            key = identity.get_public_key()[0]
            daemon.login_helper.acls[key] = object()
            daemon.login_helper.handlers[key] = SimpleNamespace(local_identity=identity)

        def entry(identity):
            acl = daemon.login_helper.acls[identity.get_public_key()[0]]
            handler = SimpleNamespace(local_identity=identity, contacts=SimpleNamespace(_acl=acl))
            return {'identity': identity, 'handler': handler}

        def register_text(*, identity, **kwargs):
            key = identity.get_public_key()[0]
            daemon.text_helper.handlers[key] = entry(identity)
            room = SimpleNamespace(local_identity=identity, acl=daemon.login_helper.acls[key],
                                   room_hash=key, _running=True,
                                   _sync_task=asyncio.create_task(asyncio.Event().wait()))

            async def stop():
                room._running = False
                room._sync_task.cancel()
                await asyncio.gather(room._sync_task, return_exceptions=True)

            room.stop = stop
            self.rooms.append(room)
            daemon.text_helper.room_servers[key] = room

        def register_protocol(*, identity, **kwargs):
            daemon.protocol_request_helper.handlers[identity.get_public_key()[0]] = entry(identity)

        daemon.login_helper = SimpleNamespace(acls={}, handlers={}, register_identity=register_login)
        daemon.text_helper = SimpleNamespace(handlers={}, room_servers={}, _pending_tasks=set(),
                                            register_identity=register_text)
        daemon.protocol_request_helper = SimpleNamespace(handlers={}, register_identity=register_protocol)
        context = patch.dict(sys.modules, {'openhop_core': SimpleNamespace(LocalIdentity=Identity)})
        context.start()
        self.addCleanup(context.stop)

    async def asyncTearDown(self):
        self.daemon._shutdown_started = True
        for room in self.rooms:
            await room.stop()

    async def test_room_can_share_prefix_with_companion(self):
        manager = self.daemon.identity_manager
        companion = Identity(b'\x22' + b'\x33' * 31)
        self.assertTrue(manager.register_identity('companion', companion, {}, 'companion'))
        self.daemon.companion_bridges[0x22] = object()
        self.assertTrue(await self.daemon.add_room_from_config(self.config))
        self.assertIs(manager.get_identity_by_hash(0x22, 'companion')[0], companion)
        self.assertEqual(manager.get_identity_by_hash(0x22, 'server')[2], 'room_server')
        self.assertTrue(self.rooms[0]._running)

    async def test_room_still_rejects_server_prefix_and_duplicate_name(self):
        manager = self.daemon.identity_manager
        for name, key, kind in (('server', b'\x22' * 32, 'room_server'),
                                ('room', b'\x33' * 32, 'companion')):
            with self.subTest(name=name):
                manager.identities.clear()
                manager.named_identities.clear()
                manager.registered_hashes.clear()
                manager.register_identity(name, Identity(key), {}, kind)
                before = dict(manager.identities)
                with self.assertRaises(ValueError):
                    await self.daemon.add_room_from_config(self.config)
                self.assertEqual(manager.identities, before)
                self.assertFalse(self.rooms)

    async def test_failed_publication_removes_registration_and_allows_retry(self):
        manager = self.daemon.identity_manager
        with patch.object(self.daemon, '_monitor_room_sync', side_effect=RuntimeError('monitor failed')):
            with self.assertRaisesRegex(RuntimeError, 'monitor failed'):
                await self.daemon.add_room_from_config(self.config)
        self.assertFalse(manager.identities)
        self.assertFalse(manager.named_identities)
        self.assertFalse(manager.registered_hashes)
        self.assertTrue(all(not mapping for mapping in self.daemon._room_handler_maps().values()))
        self.assertTrue(self.rooms[0]._sync_task.done())
        self.assertTrue(await self.daemon.add_room_from_config(self.config))


class CompanionActivationTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_activation_does_not_keep_raw_packet_subscribers(self):
        source = ROOT / 'overlay/openhop_repeater/repeater/main.py'
        cls = next(node for node in ast.parse(source.read_text()).body
                   if isinstance(node, ast.ClassDef) and node.name == 'RepeaterDaemon')
        cls.bases = []
        cls.body = [node for node in cls.body if isinstance(node, ast.AsyncFunctionDef)
                    and node.name == '_add_companion_from_config']
        namespace = dict(functools=functools, logger=Mock(),
                         normalize_companion_identity_key=lambda value: value,
                         validate_companion_node_name=lambda value: value)
        exec(compile(ast.Module(body=[cls], type_ignores=[]), str(source), 'exec'), namespace)
        daemon = namespace['RepeaterDaemon']()
        daemon._shutdown_started = False
        daemon.local_hash = 0x11
        daemon.companion_bridges = {}
        daemon.companion_frame_servers = []
        daemon.identity_manager = IdentityManager({})
        daemon._region_map = object()
        daemon._region_map_lock = threading.RLock()
        daemon.discovery_helper = None
        daemon._get_companion_radio_settings = lambda: {}
        daemon._get_companion_max_tx_power = lambda: 22
        daemon._get_companion_stats = lambda: {}
        daemon._companion_injector = AsyncMock()
        daemon._sync_companion_node_name = Mock()
        subscribers = [Mock()]
        daemon.dispatcher = SimpleNamespace(add_raw_packet_subscriber=subscribers.append)
        config = {'name': 'companion', 'identity_key': (b'\x22' * 32).hex()}
        storage = SimpleNamespace(prepare_companion_storage=Mock(),
                                  companion_load_contacts=Mock(return_value=[]),
                                  companion_load_channels=Mock(return_value=[]))
        daemon.repeater_handler = SimpleNamespace(storage=SimpleNamespace(sqlite_handler=storage))

        for failure in ('contacts', 'channels', 'bridge_start', 'frame_start', None):
            with self.subTest(failure=failure):
                storage.companion_load_contacts.return_value = None if failure == 'contacts' else []
                storage.companion_load_channels.return_value = None if failure == 'channels' else []
                bridge = SimpleNamespace(
                    contacts=SimpleNamespace(max_contacts=10),
                    channels=SimpleNamespace(max_channels=40),
                    get_channel=Mock(return_value=object()), note_flood_copy=Mock(),
                    start=AsyncMock(side_effect=RuntimeError('bridge failed') if failure == 'bridge_start' else None),
                    stop=AsyncMock(),
                )
                server = SimpleNamespace(
                    start=AsyncMock(side_effect=RuntimeError('bind failed') if failure == 'frame_start' else None),
                    stop_clients=AsyncMock(), stop=AsyncMock(),
                )
                imports = {
                    'openhop_core': SimpleNamespace(LocalIdentity=Identity),
                    'openhop_core.companion.models': SimpleNamespace(Channel=Mock()),
                    'repeater.companion': SimpleNamespace(RepeaterCompanionBridge=Mock(return_value=bridge)),
                    'repeater.companion.frame_server_lifecycle': SimpleNamespace(CompanionFrameServer=Mock(return_value=server)),
                    'repeater.companion.constants': SimpleNamespace(DEFAULT_PUBLIC_CHANNEL_SECRET=b'\x00' * 16),
                }
                with patch.dict(sys.modules, imports):
                    if failure:
                        with self.assertRaises(RuntimeError):
                            await daemon._add_companion_from_config(config)
                        self.assertEqual(len(subscribers), 1)
                        self.assertFalse(daemon.companion_bridges)
                        self.assertFalse(daemon.identity_manager.identities)
                    else:
                        await daemon._add_companion_from_config(config)
                        self.assertEqual(subscribers[1:], [bridge.note_flood_copy])
                        self.assertIs(daemon.companion_bridges[0x22], bridge)


if __name__ == '__main__':
    unittest.main()
