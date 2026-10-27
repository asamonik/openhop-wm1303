"""Bridge and engine arrivals share bounded recent-record bookkeeping."""

from collections import deque
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_engine_acks import AckPacket, engine, make_handler
from test_radio_bridge import bridge_engine


class RecentIndexTests(unittest.TestCase):
    def setUp(self):
        self.handler = make_handler()
        self.handler.rx_count = self.handler.forwarded_count = self.handler.dropped_count = 0
        self.handler.recv_direct_count = self.handler.direct_dup_count = 0
        self.handler.recent_packets = deque(maxlen=3)
        self.handler.max_recent_packets = 3
        self.handler.max_duplicates_per_packet = 20
        self.handler._recent_hash_index = {}
        self.handler.storage = SimpleNamespace(record_packet=Mock())
        self.handler.radio_config = {'spreading_factor': 8}
        self.bridge = bridge_engine.BridgeEngine.__new__(bridge_engine.BridgeEngine)
        self.bridge._repeater_engine = self.handler
        self.packet = AckPacket('0e01cc4dabaf95')

    def test_mixed_arrivals_do_not_retain_evicted_index_entries(self):
        for cycle in range(4):
            for index in range(3):
                self.handler._append_recent_packet({'packet_hash': f'engine-{cycle}-{index}'})
            for index in range(3):
                self.bridge._update_repeater_counters(self.packet.write_to(), 'channel_a',
                                                      pkt_hash=f'bridge-{cycle}-{index}')
        self.assertEqual(len(self.handler.recent_packets), 3)
        self.assertEqual(set(self.handler._recent_hash_index),
                         {record['packet_hash'] for record in self.handler.recent_packets})
        for record in self.handler.recent_packets:
            self.assertIs(self.handler._recent_hash_index[record['packet_hash']], record)
        self.assertEqual(self.handler.rx_count, 12)
        self.assertEqual(self.handler.storage.record_packet.call_count, 12)

    def test_raw_duplicate_attaches_to_bridge_original(self):
        packet_hash = self.packet.calculate_packet_hash().hex().upper()
        self.bridge._update_repeater_counters(self.packet.write_to(), 'channel_a', pkt_hash=packet_hash)
        original = self.handler.recent_packets[0]
        headers = SimpleNamespace(parse_header=lambda value: {
            'payload_type': (value >> 2) & 15, 'route_type': value & 3,
        })
        with patch.object(engine, 'PacketHeaderUtils', headers):
            self.handler.record_duplicate(self.packet, rssi=-80, snr=3)
        self.assertEqual(len(self.handler.recent_packets), 1)
        self.assertIs(self.handler.recent_packets[0], original)
        self.assertEqual(len(original['duplicates']), 1)
        self.assertEqual(original['duplicates'][0]['snr'], 3)
        self.assertEqual(self.handler.rx_count, 2)
        self.assertEqual(self.handler.storage.record_packet.call_count, 2)


if __name__ == '__main__':
    unittest.main()
