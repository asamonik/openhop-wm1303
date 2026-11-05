"""Live CherryPy listener checks using temporary configuration and loopback only."""

import json
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from urllib.request import Request, urlopen

import cherrypy

from repeater.config_manager import ConfigManager


class HTTPBindingTests(unittest.TestCase):
    def test_saved_binding_edits_report_pending_while_the_current_listener_stays_usable(self):
        with tempfile.TemporaryDirectory() as directory:
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
            target_port = port + 1 if port < 65535 else port - 1
            server = SimpleNamespace(host='127.0.0.1', port=port,
                                     app=SimpleNamespace(apply_web_config=lambda: None))
            daemon = SimpleNamespace(http_server=server)
            manager = ConfigManager(str(Path(directory, 'config.yaml')), {}, daemon)
            daemon.config = manager.config

            class Endpoints:
                @cherrypy.expose
                @cherrypy.tools.json_out()
                def index(self):
                    return {'host': server.host, 'port': server.port}

                @cherrypy.expose
                @cherrypy.tools.json_in()
                @cherrypy.tools.json_out()
                def apply(self):
                    return manager.update_and_save(cherrypy.request.json)

            saved_apps = dict(cherrypy.tree.apps)
            saved_config = dict(cherrypy.config)
            cherrypy.config.update({
                'environment': 'test_suite', 'log.screen': False,
                'server.socket_host': '127.0.0.1', 'server.socket_port': port,
                'engine.autoreload.on': False, 'checker.on': False,
            })
            cherrypy.tree.mount(Endpoints(), '/')
            try:
                cherrypy.engine.start()
                cases = (
                    (True, {'http': {'port': target_port}}, False),
                    (True, {'http': {'host': '127.0.0.2'}}, False),
                    (False, {'web': {'port': target_port}}, False),
                    (True, {'web': {'port': target_port}}, True),
                    (True, {'http': {'port': str(port)}}, True),
                )
                for dedicated_http, updates, live_updated in cases:
                    with self.subTest(dedicated_http=dedicated_http, updates=updates):
                        manager.config.clear()
                        manager.config['web'] = {'host': server.host, 'port': port}
                        if dedicated_http:
                            manager.config['http'] = {'host': server.host, 'port': port}
                        self.assertTrue(manager.save_to_file())
                        request = Request(
                            f'http://127.0.0.1:{port}/apply',
                            data=json.dumps(updates).encode(),
                            headers={'Content-Type': 'application/json'})
                        with urlopen(request, timeout=3) as response:
                            result = json.load(response)
                        self.assertTrue(result['success'] and result['saved'])
                        self.assertEqual(result['live_updated'], live_updated)
                        self.assertEqual((server.host, server.port), ('127.0.0.1', port))
                        self.assertEqual(cherrypy.server.socket_port, port)
                        with urlopen(f'http://127.0.0.1:{port}/', timeout=3) as response:
                            self.assertEqual(json.load(response), {'host': '127.0.0.1', 'port': port})
            finally:
                cherrypy.engine.exit()
                cherrypy.engine.block()
                cherrypy.tree.apps.clear()
                cherrypy.tree.apps.update(saved_apps)
                cherrypy.config.clear()
                cherrypy.config.update(saved_config)


if __name__ == '__main__':
    unittest.main()
