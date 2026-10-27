"""Packet trace query validation and collector integration."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cherrypy
from test_web_persistence import wm1303_api
from repeater.web import packet_trace


class TraceQueryTests(unittest.TestCase):
    def setUp(self):
        self.api = wm1303_api.WM1303API.__new__(wm1303_api.WM1303API)

    def test_malformed_query_values_return_400_before_collecting_traces(self):
        queries = [dict(limit=value) for value in ('x', '-1', '0', '1.5', '1001', ['1', '2'])]
        queries += [dict(status=['ok', 'error']), dict(channel=['channel_a', 'channel_b'])]
        for query in queries:
            with self.subTest(query=query), patch.object(packet_trace, 'get_traces') as collect:
                with self.assertRaises(cherrypy.HTTPError) as error:
                    self.api._packet_traces_get(**query)
                self.assertEqual(error.exception.status, 400)
                collect.assert_not_called()

    def test_valid_query_filters_real_collector_then_applies_limit(self):
        collector = packet_trace.TraceCollector()
        with patch.object(packet_trace, '_CHANNEL_FRIENDLY_NAMES', {'channel_a': 'High Alpine'}):
            for packet in ('old', 'new'):
                collector.trace_event(packet, 'received', channel='channel_a')
                collector.trace_event(packet, 'self_echo')
            collector.trace_event('elsewhere', 'received', channel='channel_b')
            with patch.object(packet_trace, 'get_collector', return_value=collector):
                result = json.loads(self.api._packet_traces_get(
                    limit='1', channel='channel_a', status='self_echo'))
            self.assertEqual([trace['pkt_hash'] for trace in result['traces']], ['new'])

    def test_collector_failure_is_a_server_error_without_internal_details(self):
        response = SimpleNamespace(status=200, headers={})
        with patch.object(packet_trace, 'get_traces', side_effect=RuntimeError('fixture-private-path')), \
                patch.object(cherrypy, 'response', response):
            result = json.loads(self.api._packet_traces_get())
        self.assertEqual(response.status, 500)
        self.assertEqual(result, {'error': 'Unable to read packet traces'})


if __name__ == '__main__':
    unittest.main()
