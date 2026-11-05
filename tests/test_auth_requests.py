"""Authentication request/response contracts without tokens or a running server."""

import ast
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cherrypy


class AuthRequestTests(unittest.TestCase):
    def setUp(self):
        source = (Path(__file__).resolve().parents[1]
                  / 'overlay/openhop_repeater/repeater/web/auth_endpoints.py')
        tree = ast.parse(source.read_text())
        cls = next(node for node in tree.body
                   if isinstance(node, ast.ClassDef) and node.name == 'AuthEndpoints')
        for method in cls.body:
            if isinstance(method, ast.FunctionDef):
                method.decorator_list = [decorator for decorator in method.decorator_list
                                         if isinstance(decorator, ast.Name)
                                         and decorator.id == 'staticmethod']
        helpers = [node for node in tree.body
                   if isinstance(node, (ast.ClassDef, ast.FunctionDef))
                   and node.name in {'_InvalidRequest', '_read_json_object', '_text_field'}]
        namespace = dict(cherrypy=cherrypy, json=json, logger=Mock(), _MIN_ADMIN_PASSWORD_LEN=8)
        exec(compile(ast.Module(body=helpers + [cls], type_ignores=[]), str(source), 'exec'), namespace)
        self.jwt = Mock(expiry_minutes=60)
        self.jwt.verify_jwt.return_value = {'sub': 'admin', 'client_id': 'original'}
        self.jwt.create_jwt.return_value = 'new-token'
        self.tokens = Mock()
        self.tokens.verify_token.return_value = None
        self.throttle = Mock()
        self.throttle.get_retry_after.return_value = 0
        self.throttle.register_failure.return_value = 0
        self.manager = Mock()
        self.auth = namespace['AuthEndpoints'](
            {'repeater': {'security': {'admin_password': 'valid-password'}}},
            self.jwt, self.tokens, self.manager, self.throttle)
        self.request = SimpleNamespace(method='POST', headers={'Authorization': 'Bearer fixture'},
                                       remote=SimpleNamespace(ip='127.0.0.1'))
        self.response = SimpleNamespace(status=200, headers={})
        for name, value in (
            ('request', self.request), ('response', self.response),
            ('config', {'jwt_handler': self.jwt, 'token_manager': self.tokens}),
        ):
            context = patch.object(cherrypy, name, value)
            context.start()
            self.addCleanup(context.stop)

    def call(self, endpoint, body):
        self.response.status = 200
        self.request.body = io.BytesIO(body)
        return json.loads(getattr(self.auth, endpoint)())

    def test_malformed_bodies_are_client_errors_without_mutations(self):
        for endpoint in ('login', 'refresh', 'change_password'):
            for body in (b'{', b'\xff', b'null', b'[]', b'"text"', b'42'):
                with self.subTest(endpoint=endpoint, body=body):
                    self.assertFalse(self.call(endpoint, body)['success'])
                    self.assertEqual(self.response.status, 400)
        self.jwt.create_jwt.assert_not_called()
        self.manager.update_and_save.assert_not_called()

    def test_login_rejects_invalid_credentials_and_unconfigured_system(self):
        valid = dict(username='admin', password='valid-password', client_id='client')
        for field in valid:
            for value in (None, [], {}, 123, True, '', '\ud800'):
                body = json.dumps({**valid, field: value}).encode()
                with self.subTest(field=field, value=value):
                    self.assertFalse(self.call('login', body)['success'])
                    self.assertEqual(self.response.status, 400)
        self.jwt.create_jwt.assert_not_called()
        self.throttle.register_failure.assert_not_called()
        self.auth.config = {}
        self.assertFalse(self.call('login', json.dumps(valid).encode())['success'])
        self.assertEqual(self.response.status, 503)
        self.jwt.create_jwt.assert_not_called()

    def test_refresh_reports_unauthorized_and_invalid_client_ids(self):
        self.jwt.verify_jwt.return_value = None
        self.assertFalse(self.call('refresh', b'{}')['success'])
        self.assertEqual(self.response.status, 401)
        self.jwt.verify_jwt.return_value = {'sub': 'admin'}
        for value in (None, [], {}, 123, True, '', '\ud800'):
            with self.subTest(value=value):
                self.assertFalse(self.call('refresh', json.dumps({'client_id': value}).encode())['success'])
                self.assertEqual(self.response.status, 400)
        self.jwt.verify_jwt.return_value = {'sub': 'admin', 'client_id': None}
        self.assertFalse(self.call('refresh', b'{}')['success'])
        self.assertEqual(self.response.status, 400)
        self.jwt.create_jwt.assert_not_called()

    def test_valid_login_refresh_and_token_failures_have_correct_status(self):
        body = json.dumps(dict(username=' admin ', password='valid-password', client_id=' client ')).encode()
        self.assertTrue(self.call('login', body)['success'])
        self.jwt.create_jwt.assert_called_with('admin', 'client')
        self.assertEqual(self.response.status, 200)
        self.assertTrue(self.call('refresh', b'{}')['success'])
        self.jwt.create_jwt.assert_called_with('admin', 'original')
        self.assertEqual(self.response.status, 200)
        self.jwt.create_jwt.side_effect = RuntimeError('unavailable')
        for endpoint, payload in (('login', body), ('refresh', b'{}')):
            with self.subTest(endpoint=endpoint):
                self.assertFalse(self.call(endpoint, payload)['success'])
                self.assertEqual(self.response.status, 500)


if __name__ == '__main__':
    unittest.main()
