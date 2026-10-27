"""Deduplication history uses one selected period across storage paths."""

import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cherrypy

from test_radio_bridge import bridge_engine
from test_web_persistence import wm1303_api


class DedupDashboardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / 'repeater.db'
        self.events = [dict(ts=ts, type=kind, src='channel_a', hash=str(ts), size=3,
                            pkt_type='RAW_CUSTOM') for ts, kind in (
            (119, 'filtered'), (120, 'forwarded'), (121, 'duplicate'),
            (181, 'tx_echo'), (240, 'duplicate'),
        )]
        bridge = SimpleNamespace(
            get_stats=Mock(return_value=dict(forwarded_packets=900, dropped_duplicate=800,
                                            fwd_echo_detected=700, dropped_filtered=600,
                                            dedup_seen_active=2, dedup_events_buffered=5)),
            get_dedup_events=lambda **kwargs: self.events,
        )
        for patcher in (patch.object(bridge_engine, '_active_bridge', bridge),
                        patch.object(wm1303_api, '_DB_PATH', str(self.database)),
                        patch.object(wm1303_api.time, 'time', return_value=240)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.api = wm1303_api.WM1303API.__new__(wm1303_api.WM1303API)

    def create_database(self):
        with sqlite3.connect(self.database) as conn:
            conn.execute('CREATE TABLE dedup_events(ts REAL, event_type TEXT, source TEXT, '
                         'pkt_hash TEXT, pkt_size INTEGER, pkt_type TEXT)')
            conn.executemany('INSERT INTO dedup_events VALUES(?,?,?,?,?,?)', [
                (e['ts'], e['type'], e['src'], e['hash'], e['size'], e['pkt_type'])
                for e in self.events
            ])

    def assert_period(self, result):
        stats = result['stats']
        self.assertEqual((stats['total_forwarded'], stats['total_duplicate'],
                          stats['total_tx_echo'], stats['total_filtered']), (1, 1, 1, 0))
        self.assertEqual(stats['dedup_ratio'], 0.6667)
        self.assertEqual(stats['dedup_seen_active'], 2)
        self.assertEqual(stats['dedup_events_buffered'], 5)

    def test_database_raw_and_buckets_use_period_totals_and_same_boundaries(self):
        self.create_database()
        for raw in ('false', 'true'):
            with self.subTest(raw=raw):
                result = json.loads(self.api._dedup_events_get(since='120', until='240', raw=raw))
                self.assert_period(result)
                if raw == 'true':
                    self.assertEqual([e['ts'] for e in result['events']], [120, 121, 181])
                else:
                    self.assertEqual([b['ts'] for b in result['buckets']], [120, 180])

    def test_missing_or_broken_database_returns_memory_history_in_requested_format(self):
        for database_state in ('missing', 'broken'):
            if database_state == 'broken':
                self.database.write_text('not a database')
            for raw in ('false', 'true'):
                with self.subTest(database=database_state, raw=raw):
                    result = json.loads(self.api._dedup_events_get(since='120', until='240', raw=raw))
                    self.assert_period(result)
                    if raw == 'true':
                        self.assertEqual(result['mode'], 'raw')
                        self.assertEqual([e['ts'] for e in result['events']], [120, 121, 181])
                    else:
                        self.assertEqual([b['ts'] for b in result['buckets']], [120, 180])
                        self.assertEqual(result['buckets'][0]['forwarded'], 1)
                        self.assertEqual(result['buckets'][0]['duplicate'], 1)
                        self.assertEqual(result['buckets'][1]['tx_echo'], 1)

    def test_bad_query_values_return_400_before_reading_storage(self):
        bad_values = [dict(since=value) for value in ('x', 'NaN', 'inf', '-1')]
        bad_values += [dict(since='120', until=value) for value in ('x', 'NaN', 'inf', '120', '119')]
        bad_values += [dict(bucket=value) for value in ('x', 'NaN', '0', '-1', '1.5', str(10**400))]
        bad_values += [dict(until=value) for value in ('240', 'NaN')]
        bad_values += [dict(raw=['true', 'false']), dict(range=['1h', '6h'])]
        for params in bad_values:
            with self.subTest(params=params), patch.object(wm1303_api, '_db_conn') as connect:
                with self.assertRaises(cherrypy.HTTPError) as error:
                    self.api._dedup_events_get(**params)
                self.assertEqual(error.exception.status, 400)
                connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
