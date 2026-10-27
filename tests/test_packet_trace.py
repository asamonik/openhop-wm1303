"""Bounded packet-trace storage and isolated dashboard snapshots."""

import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / 'overlay/openhop_repeater/repeater/web/packet_trace.py'
SPEC = importlib.util.spec_from_file_location('wm1303_packet_trace_test', SOURCE)
packet_trace = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packet_trace)


class PacketTraceTests(unittest.TestCase):
    def test_concurrent_first_events_share_one_collector(self):
        original = packet_trace.TraceCollector
        producers = 2
        ready = threading.Barrier(producers)

        def interrupted_construction(*args, **kwargs):
            # Let another producer run before the first collector is published.
            time.sleep(0.02)
            return original(*args, **kwargs)

        def record(index):
            ready.wait(timeout=2)
            collector = packet_trace.get_collector()
            collector.trace_event(str(index), 'received')
            return collector

        with patch.object(packet_trace, '_collector', None), \
                patch.object(packet_trace, 'TraceCollector', side_effect=interrupted_construction) as factory:
            with ThreadPoolExecutor(max_workers=producers) as pool:
                collectors = list(pool.map(record, range(producers)))
            self.assertEqual(len({id(collector) for collector in collectors}), 1)
            factory.assert_called_once()
            self.assertEqual({trace['pkt_hash'] for trace in packet_trace.get_traces()},
                             {str(index) for index in range(producers)})

    def test_unique_packets_bound_all_per_packet_bookkeeping(self):
        collector = packet_trace.TraceCollector(maxlen=4)
        for index in range(1000):
            collector.trace_event(str(index), 'received')
        self.assertEqual([item['pkt_hash'] for item in collector.get_traces()],
                         ['999', '998', '997', '996'])
        self.assertLessEqual(len(collector._active_key), 4)
        self.assertLessEqual(len(collector._seq), 4)

    def test_expired_traces_remain_distinct_until_eviction(self):
        collector = packet_trace.TraceCollector(maxlen=3)
        with patch.object(packet_trace.time, 'monotonic', return_value=1):
            collector.trace_event('same', 'original')
        with patch.object(packet_trace.time, 'monotonic', return_value=400):
            collector.trace_event('same', 'later')
            collector.trace_event('other', 'received')
            collector.trace_event('new', 'received')  # Evict only the original.
            collector.trace_event('same', 'followup')
        matches = [item for item in collector.get_traces() if item['pkt_hash'] == 'same']
        self.assertEqual(len(matches), 1)
        self.assertEqual([step['name'] for step in matches[0]['steps']], ['later', 'followup'])
        collector.clear()
        self.assertFalse(collector.get_traces())
        self.assertFalse(collector._seq)
        self.assertFalse(collector._active_key)

    def test_snapshots_do_not_change_after_new_events_or_mutate_storage(self):
        collector = packet_trace.TraceCollector()
        collector.trace_event('packet', 'received', path_hashes=['ab'])
        snapshot = collector.get_traces()[0]
        collector.trace_event('packet', 'forwarded')
        self.assertEqual([step['name'] for step in snapshot['steps']], ['received'])
        snapshot['steps'][0]['name'] = 'corrupted'
        snapshot['path_hashes'].append('cd')
        latest = collector.get_traces()[0]
        self.assertEqual([step['name'] for step in latest['steps']], ['received', 'forwarded'])
        self.assertEqual(latest['path_hashes'], ['ab'])

    def test_metadata_preserves_zero_values_and_copies_received_paths(self):
        for path, hops in (([], 0), (['ab'], 1)):
            with self.subTest(path=path):
                collector = packet_trace.TraceCollector()
                original_path = list(path)
                collector.trace_event('packet', 'received', path_hashes=path,
                                      hops=hops, payload_full_size=0)
                path.append('caller-mutation')
                collector.trace_event('packet', 'forwarded', path_hashes=['cd'],
                                      hops=hops + 1, payload_full_size=9)
                trace = collector.get_traces()[0]
                self.assertEqual(trace['path_hashes'], original_path)
                self.assertEqual(trace['hops'], hops)
                self.assertEqual(trace['payload_full_size'], 0)

        collector = packet_trace.TraceCollector()
        collector.trace_event('deferred', 'queued')
        path = ['ef']
        collector.trace_event('deferred', 'received', path_hashes=path, hops=1)
        path.clear()
        self.assertEqual(collector.get_traces()[0]['path_hashes'], ['ef'])

    def test_steps_keep_each_channels_complete_name(self):
        collector = packet_trace.TraceCollector()
        collector.trace_event('packet', 'received', channel='Local RX')
        collector.trace_event('packet', 'tx_send', channel='High Alpine', detail='sent')
        trace = collector.get_traces()[0]
        self.assertEqual(trace['channel'], 'Local RX')
        self.assertEqual([step['channel'] for step in trace['steps']], ['Local RX', 'High Alpine'])

    def test_channel_filter_accepts_canonical_id_and_display_name(self):
        with patch.object(packet_trace, '_CHANNEL_FRIENDLY_NAMES', {
            'channel_a': 'High Alpine', 'channel_b': 'Low Valley',
        }):
            collector = packet_trace.TraceCollector()
            collector.trace_event('first', 'received', channel='channel_a')
            collector.trace_event('second', 'received', channel='channel_b')
            for channel in ('channel_a', 'High Alpine'):
                with self.subTest(channel=channel):
                    self.assertEqual([trace['pkt_hash'] for trace in collector.get_traces(channel=channel)],
                                     ['first'])

    def test_echo_filters_use_steps_without_reclassifying_successful_transmissions(self):
        collector = packet_trace.TraceCollector()
        for name in ('plain', 'self_echo', 'mesh_echo', 'echo_dedup'):
            collector.trace_event(name, 'received')
            collector.trace_event(name, 'tx_send', status='ok')
            if name != 'plain':
                collector.trace_event(name, name, status='ok')
        for status, expected in (
            ('self_echo', {'self_echo', 'echo_dedup'}),
            ('mesh_echo', {'mesh_echo'}),
            ('echo_dedup', {'self_echo', 'mesh_echo', 'echo_dedup'}),
        ):
            with self.subTest(status=status):
                traces = collector.get_traces(status=status)
                self.assertEqual({trace['pkt_hash'] for trace in traces}, expected)
                self.assertTrue(all(trace['status'] == 'ok' for trace in traces))
        self.assertEqual(len(collector.get_traces(status='ok')), 4)


if __name__ == '__main__':
    unittest.main()
