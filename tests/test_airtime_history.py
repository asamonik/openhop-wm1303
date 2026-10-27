"""Hardware-independent airtime history tests across readers and clock changes."""

import importlib.util
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / 'overlay/openhop_repeater/repeater/airtime.py'
spec = importlib.util.spec_from_file_location('airtime_history_fixture', path)
airtime = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'openhop_core.protocol.packet_utils': SimpleNamespace(
        calculate_lora_airtime_ms=lambda *args, **kwargs: 0)}):
    spec.loader.exec_module(airtime)


class AirtimeHistoryTests(unittest.TestCase):
    def manager(self, enabled=True):
        return airtime.AirtimeManager({'duty_cycle': {
            'max_airtime_per_minute': 100, 'enforcement_enabled': enabled}})

    def test_wall_clock_adjustment_does_not_change_rolling_budget(self):
        for jump in (-3600, 3600):
            with self.subTest(jump=jump):
                manager = self.manager()
                with (patch.object(airtime.time, 'time', return_value=1000) as wall,
                      patch.object(airtime.time, 'monotonic', return_value=100) as elapsed):
                    manager.record_tx(90)
                    wall.return_value = 1000 + jump
                    elapsed.return_value = 100.1
                    allowed, wait = manager.can_transmit(20)
                    self.assertFalse(allowed)
                    self.assertAlmostEqual(wait, 59.9)
                    self.assertEqual(manager.get_stats()['current_airtime_ms'], 90)
                    elapsed.return_value = 161
                    self.assertTrue(manager.can_transmit(20)[0])
                    self.assertEqual(manager.get_stats()['current_airtime_ms'], 0)
                    self.assertEqual(manager.total_airtime_ms, 90)

    def test_recording_prunes_history_even_without_enforcement_or_dashboard_reads(self):
        manager = self.manager(enabled=False)
        with (patch.object(airtime.time, 'time', return_value=1000) as wall,
              patch.object(airtime.time, 'monotonic', return_value=100) as elapsed):
            manager.record_tx(25)
            wall.return_value += 61
            elapsed.return_value += 61
            self.assertTrue(manager.can_transmit(75)[0])
            manager.record_tx(75)
            self.assertEqual(len(manager.tx_history), 1)
            self.assertEqual(manager.tx_history[0][1], 75)
            self.assertEqual(manager.total_airtime_ms, 100)

    def test_stats_refresh_cannot_overwrite_scheduler_record(self):
        manager = self.manager()
        manager.record_tx(25)
        snapshot_started = threading.Event()
        release_snapshot = threading.Event()
        writer_attempted_or_finished = threading.Event()
        failures = []

        class PausedHistory(list):
            def __iter__(self):
                iterator = super().__iter__()
                if threading.current_thread().name == 'fixture-stats':
                    snapshot_started.set()
                    if not release_snapshot.wait(2):
                        raise AssertionError('reader was not released')
                yield from iterator

        class ObservedLock:
            # Notify on a real lock attempt so this controlled interleaving
            # needs no sleeps or assumptions about thread scheduling speed.
            def __init__(self):
                self.lock = threading.RLock()

            def __enter__(self):
                if threading.current_thread().name == 'fixture-writer':
                    writer_attempted_or_finished.set()
                return self.lock.__enter__()

            def __exit__(self, *args):
                return self.lock.__exit__(*args)

        manager.tx_history = PausedHistory(manager.tx_history)
        manager._history_lock = ObservedLock()

        def reader():
            try:
                manager.get_stats()
            except BaseException as exc:
                failures.append(exc)

        def writer():
            try:
                manager.can_transmit(0)  # Filters/replaces the history, as the real scheduler does.
                manager.record_tx(50)
            except BaseException as exc:
                failures.append(exc)
            finally:
                writer_attempted_or_finished.set()

        readers = threading.Thread(target=reader, name='fixture-stats')
        writers = threading.Thread(target=writer, name='fixture-writer')
        readers.start()
        try:
            self.assertTrue(snapshot_started.wait(2))
            writers.start()
            self.assertTrue(writer_attempted_or_finished.wait(2))
        finally:
            release_snapshot.set()
            readers.join(2)
            if writers.ident is not None:
                writers.join(2)
        self.assertFalse(readers.is_alive() or writers.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(manager.total_airtime_ms, 75)
        self.assertEqual(manager.get_stats()['current_airtime_ms'], 75)
        self.assertFalse(manager.can_transmit(30)[0])


if __name__ == '__main__':
    unittest.main()
