"""Authentication input contracts, without a server or real credentials."""

import importlib.util
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cherrypy


def auth_module():
    source = (Path(__file__).resolve().parents[1]
              / "overlay/openhop_repeater/repeater/web/auth_endpoints.py")
    spec = importlib.util.spec_from_file_location("repeater.web._auth_input_test", source)
    module = importlib.util.module_from_spec(spec)
    # These tests exercise parsing and mutation boundaries; HTTP integration
    # separately covers the real authentication middleware.
    with patch.dict(sys.modules, {
        "repeater.web.auth.middleware": SimpleNamespace(require_auth=lambda f: f),
    }):
        spec.loader.exec_module(module)
    return module


class AuthInputTests(unittest.TestCase):
    def setUp(self):
        module = auth_module()
        self.jwt, self.tokens, self.manager = Mock(), Mock(), Mock()
        self.jwt.verify_jwt.return_value = {"sub": "admin", "client_id": "fixture"}
        self.jwt.create_jwt.return_value = "fixture-token"
        self.jwt.expiry_minutes = 15
        self.tokens.verify_token.return_value = None
        self.tokens.create_token.return_value = (1, "fixture-api-token")
        self.auth = module.AuthEndpoints(
            {"repeater": {"security": {"admin_password": " fixture password "}}},
            self.jwt, self.tokens, self.manager,
        )
        self.token_api = module.TokensAPIEndpoint()
        self.request = SimpleNamespace(
            method="POST", headers={"Authorization": "Bearer fixture"},
            user={"username": "admin"}, remote=SimpleNamespace(ip="127.0.0.1"),
        )
        self.response = SimpleNamespace(status=200, headers={})
        for name, value in (
            ("request", self.request), ("response", self.response),
            ("config", {"jwt_handler": self.jwt, "token_manager": self.tokens}),
        ):
            context = patch.object(cherrypy, name, value)
            context.start()
            self.addCleanup(context.stop)

    def call(self, endpoint, body):
        self.response.status = 200
        self.request.body = io.BytesIO(body)
        result = endpoint()
        return json.loads(result) if isinstance(result, bytes) else result

    def test_malformed_bodies_are_client_errors_without_side_effects(self):
        for endpoint in (self.auth.login, self.auth.refresh, self.auth.change_password,
                         self.token_api.index):
            for body in (b"[]", b"null", b"true", b"42", b'"text"', b"{", b"\xff"):
                with self.subTest(endpoint=endpoint.__name__, body=body):
                    result = self.call(endpoint, body)
                    self.assertFalse(result["success"])
                    self.assertEqual(self.response.status, 400, result)
        self.jwt.create_jwt.assert_not_called()
        self.tokens.create_token.assert_not_called()
        self.manager.update_and_save.assert_not_called()

    def test_text_fields_reject_non_strings_and_invalid_unicode(self):
        valid = {"username": "admin", "password": " fixture password ", "client_id": "fixture"}
        for endpoint, base, fields in (
            (self.auth.login, valid, ("username", "password", "client_id")),
            (self.auth.refresh, {"client_id": "fixture"}, ("client_id",)),
            (self.token_api.index, {"name": "fixture"}, ("name",)),
        ):
            for field in fields:
                for value in (None, True, 5, [], {}, "\ud800"):
                    with self.subTest(endpoint=endpoint.__name__, field=field, value=value):
                        result = self.call(endpoint, json.dumps({**base, field: value}).encode())
                        self.assertFalse(result["success"])
                        self.assertEqual(self.response.status, 400, result)
        self.jwt.create_jwt.assert_not_called()
        self.tokens.create_token.assert_not_called()

    def test_valid_requests_keep_password_whitespace_and_refresh_defaults(self):
        result = self.call(self.auth.login, json.dumps({
            "username": " admin ", "password": " fixture password ", "client_id": " fixture ",
        }).encode())
        self.assertTrue(result["success"], result)
        self.jwt.create_jwt.assert_called_with("admin", "fixture")
        self.assertTrue(self.call(self.auth.refresh, b"{}")['success'])
        self.jwt.create_jwt.assert_called_with("admin", "fixture")
        self.assertTrue(self.call(self.token_api.index, b'{"name":" fixture "}')['success'])
        self.tokens.create_token.assert_called_once_with("fixture")

    def test_missing_fields_authentication_and_internal_errors_have_correct_status(self):
        self.assertFalse(self.call(self.auth.login, b"{}")['success'])
        self.assertEqual(self.response.status, 400)
        self.jwt.verify_jwt.return_value = None
        self.assertFalse(self.call(self.auth.refresh, b"{}")['success'])
        self.assertEqual(self.response.status, 401)
        self.jwt.create_jwt.side_effect = RuntimeError("fixture signing failure")
        self.assertFalse(self.call(self.auth.login, json.dumps({
            "username": "admin", "password": " fixture password ", "client_id": "fixture",
        }).encode())['success'])
        self.assertEqual(self.response.status, 500)

    def test_invalid_token_ids_do_not_reach_storage(self):
        self.request.method = "DELETE"
        for token_id in ("0", "-1", "1.5", "9223372036854775808", "invalid"):
            with self.subTest(token_id=token_id):
                self.response.status = 200
                result = self.token_api.default(token_id)
                self.assertFalse(result['success'])
                self.assertEqual(self.response.status, 400)
        self.tokens.revoke_token.assert_not_called()
        self.tokens.revoke_token.return_value = True
        self.assertTrue(self.token_api.default("1")['success'])
        self.tokens.revoke_token.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
