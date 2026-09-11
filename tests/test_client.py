import json
import tempfile
import unittest
from pathlib import Path

from codex_tray.client import UsageClient
from codex_tray.usage import UsageError


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class UsageClientTests(unittest.TestCase):
    def _auth_file(self, token="test-access-token", account="test-account"):
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "auth.json"
        path.write_text(
            json.dumps({"tokens": {"access_token": token, "account_id": account}}),
            encoding="utf-8",
        )
        self.addCleanup(directory.cleanup)
        return path

    def test_fetches_and_parses_usage_with_codex_headers(self):
        response = FakeResponse(
            200,
            {
                "plan_type": "plus",
                "rate_limit": {"primary_window": {"used_percent": 12}},
                "credits": {"balance": "0"},
            },
        )
        session = FakeSession(response)
        client = UsageClient(
            session=session,
            auth_path=self._auth_file(),
            base_url="https://example.test/backend-api",
            allow_file_auth=True,
            allow_untrusted_base_url=True,
        )

        snapshot = client.fetch()

        self.assertEqual(snapshot.remaining_percent, 88)
        self.assertEqual(len(session.calls), 1)
        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://example.test/backend-api/wham/usage")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-access-token")
        self.assertEqual(kwargs["headers"]["ChatGPT-Account-Id"], "test-account")
        self.assertEqual(kwargs["headers"]["OAI-Product-Sku"], "codex")
        self.assertFalse(kwargs["allow_redirects"])

    def test_bare_chatgpt_host_uses_backend_api_wham_endpoint(self):
        response = FakeResponse(
            200,
            {
                "plan_type": "plus",
                "rate_limit": {"primary_window": {"used_percent": 12}},
                "credits": {"balance": "0"},
            },
        )
        session = FakeSession(response)
        client = UsageClient(
            session=session,
            auth_path=self._auth_file(),
            base_url="https://chatgpt.com",
            allow_file_auth=True,
        )

        client.fetch()

        self.assertEqual(session.calls[0][0], "https://chatgpt.com/backend-api/wham/usage")

    def test_official_host_rejects_non_default_port(self):
        with self.assertRaises(ValueError):
            UsageClient(base_url="https://chatgpt.com:8443")

    def test_explicit_untrusted_opt_in_allows_custom_official_host_port(self):
        response = FakeResponse(404, {})
        session = FakeSession(response)
        client = UsageClient(
            session=session,
            auth_path=self._auth_file(),
            base_url="https://chatgpt.com:8443",
            allow_file_auth=True,
            allow_untrusted_base_url=True,
        )

        with self.assertRaises(UsageError):
            client.fetch()

        self.assertEqual(session.calls[0][0], "https://chatgpt.com:8443/backend-api/wham/usage")

    def test_authentication_error_is_actionable_without_echoing_token(self):
        response = FakeResponse(401, {"detail": "test-access-token invalid"})
        client = UsageClient(
            session=FakeSession(response),
            auth_path=self._auth_file(),
            allow_file_auth=True,
        )

        with self.assertRaises(UsageError) as context:
            client.fetch()

        self.assertIn("重新登入", str(context.exception))
        self.assertNotIn("test-access-token", str(context.exception))

    def test_file_auth_requires_explicit_opt_in(self):
        session = FakeSession(FakeResponse(200, {}))
        client = UsageClient(session=session, auth_path=self._auth_file())

        with self.assertRaises(UsageError) as context:
            client.fetch()

        self.assertIn("明確啟用", str(context.exception))
        self.assertEqual(session.calls, [])


if __name__ == "__main__":
    unittest.main()
