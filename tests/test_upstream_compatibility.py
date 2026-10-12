"""Compatibility contracts added when moving the dependencies to OpenHop."""

import ast
import asyncio
from pathlib import Path
from typing import Dict, List
import unittest
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]


class ScopeQueryLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_cancels_and_joins_scope_queries_and_closes_admission(self):
        source = ROOT / 'overlay/pymc_repeater/repeater/handler_helpers/neighbor_scopes.py'
        definition = next(node for node in ast.parse(source.read_text()).body
                          if isinstance(node, ast.ClassDef) and node.name == 'NeighborScopeHelper')
        definition.body = [node for node in definition.body
                           if isinstance(node, ast.AsyncFunctionDef) and node.name in ('sweep', 'close')]
        namespace = dict(asyncio=asyncio, List=List, Dict=Dict,
                         NeighborSnapshot=object, ScopeResult=object)
        exec(compile(ast.Module(body=[definition], type_ignores=[]), str(source), 'exec'), namespace)
        helper = namespace['NeighborScopeHelper']()
        helper._closing, helper._owned_sweeps = False, set()
        entered, finished = asyncio.Event(), asyncio.Event()

        async def pending(_targets):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()

        helper._sweep_impl = pending
        task = asyncio.create_task(helper.sweep([object()]))
        await entered.wait()
        await helper.close()
        self.assertTrue(task.cancelled())
        self.assertTrue(finished.is_set())
        self.assertEqual(helper._owned_sweeps, set())
        with self.assertRaisesRegex(RuntimeError, 'shutting down'):
            await helper.sweep([])
        await helper.close()


class SourceCompatibilityTests(unittest.TestCase):
    def test_unconfigured_and_disabled_radios_do_not_require_hardware(self):
        from repeater.config import get_radio_for_board, NullRadio
        for config in ({}, {'radio_type': None}, {'radio_type': 'disabled'}, {'radio_type': 'off'}):
            with self.subTest(config=config):
                radio = get_radio_for_board(config)
                self.assertIsInstance(radio, NullRadio)
                self.assertTrue(radio.begin())

    def test_new_frontend_adapters_remain_idempotent(self):
        from test_console_views import console_assets
        for name, replacements in console_assets.OPENHOP_114_REPLACEMENTS.items():
            with self.subTest(module=name):
                source = ';'.join(before if isinstance(before, str) else before[0]
                                  for before, _ in replacements)
                adapted = console_assets.adapt_module(source, replacements)
                self.assertEqual(console_assets.adapt_module(adapted, replacements), adapted)


if __name__ == '__main__':
    unittest.main()
