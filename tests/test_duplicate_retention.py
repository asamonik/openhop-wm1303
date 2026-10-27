"""Raw dispatcher duplicate records obey the existing UI retention cap."""

from collections import deque
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_engine_acks import AckPacket, engine, make_handler


class DuplicateRetentionTests(unittest.TestCase):
    def test_raw_duplicates_remain_bounded_without_losing_counts_or_storage(self):
        headers = SimpleNamespace(parse_header=lambda value: {
            'payload_type': (value >> 2) & 15, 'route_type': value & 3,
        })
        for route in (1, 2):
            with self.subTest(route=route), patch.object(engine, 'PacketHeaderUtils', headers):
                handler = make_handler()
                handler.rx_count = 0
                handler.recv_flood_count = handler.recv_direct_count = 0
                handler.flood_dup_count = handler.direct_dup_count = 0
                handler.recent_packets = deque()
                handler.max_recent_packets = 2
                handler.max_duplicates_per_packet = 20
                handler._recent_hash_index = {}
                handler.radio_config = {'spreading_factor': 8}
                handler.storage = SimpleNamespace(record_packet=Mock())
                packet = AckPacket('0e01cc4dabaf95')
                packet.header = (packet.header & ~3) | route
                for snr in range(50):
                    handler.record_duplicate(packet, rssi=-80, snr=snr)
                self.assertEqual(handler.rx_count, 50)
                self.assertEqual(handler.recv_flood_count, 50 if route == 1 else 0)
                self.assertEqual(handler.flood_dup_count, 50 if route == 1 else 0)
                self.assertEqual(handler.recv_direct_count, 50 if route == 2 else 0)
                self.assertEqual(handler.direct_dup_count, 50 if route == 2 else 0)
                self.assertEqual(handler.storage.record_packet.call_count, 50)
                self.assertEqual(len(handler.recent_packets), 1)
                original = handler.recent_packets[0]
                self.assertEqual(len(original['duplicates']), 20)
                self.assertEqual([record['snr'] for record in original['duplicates']], list(range(1, 21)))
                self.assertTrue(all(record['is_duplicate'] for record in original['duplicates']))
                self.assertIs(handler._recent_hash_index[original['packet_hash']], original)


if __name__ == '__main__':
    unittest.main()
