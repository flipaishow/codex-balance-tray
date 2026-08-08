import json
import os
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

import requests

from codex_tray.balance import (
    AppServerBalanceClient,
    BalanceResult,
    CodexBalanceProvider,
    HttpBalanceClient,
    RetryPolicy,
    find_codex_executable,
)


class FakeProcess:
    def __init__(self, stdout_lines):
        self.stdout_lines = stdout_lines
        self.calls = []
        self.killed = False

    def communicate(self, input=None, timeout=None):
        self.calls.append((input, timeout))
        return "\n".join(json.dumps(line) for line in self.stdout_lines) + "\n", ""

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None, json_error=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.payload = payload
        self.json_error = json_error

    def json(self):
        if self.json_error:
            raise self.json_error
        return self.payload


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


def app_server_lines(account, rate_limits=None, rate_error=None):
    lines = [
        {"id": 1, "result": {}},
        {"id": 2, "result": {"account": account, "requiresOpenaiAuth": True}},
    ]
    if rate_error:
        lines.append({"id": 3, "error": {"code": -32600, "message": rate_error}})
    else:
        lines.append({"id": 3, "result": rate_limits or {}})
    return lines


class AppServerBalanceClientTests(unittest.TestCase):
    def test_discovers_codex_desktop_executable_when_path_is_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            local_app_data = Path(temporary)
            candidate = local_app_data / "OpenAI" / "Codex" / "bin" / "build-123" / "codex.exe"
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(b"fake executable marker")

            executable = find_codex_executable(
                which=lambda _name: None,
                platform="nt",
                local_app_data=local_app_data,
            )

        self.assertEqual(executable, str(candidate))

    def test_fetches_chatgpt_rate_limits_through_official_app_server(self):
        process = FakeProcess(
            [
                {"id": 1, "result": {"userAgent": "codex"}},
                {
                    "id": 2,
                    "result": {
                        "account": {"type": "chatgpt", "planType": "plus"},
                        "requiresOpenaiAuth": True,
                    },
                },
                {
                    "id": 3,
                    "result": {
                        "rateLimits": {
                            "limitId": "codex",
                            "planType": "plus",
                            "primary": {
                                "usedPercent": 37,
                                "windowDurationMins": 10080,
                                "resetsAt": 1786160194,
                            },
                            "secondary": None,
                            "credits": {
                                "hasCredits": False,
                                "unlimited": False,
                                "balance": "0",
                            },
                        },
                        "rateLimitsByLimitId": None,
                        "rateLimitResetCredits": None,
                    },
                },
            ]
        )

        def process_factory(args, **kwargs):
            self.assertEqual(args, ["C:/bin/codex", "app-server", "--listen", "stdio://"])
            self.assertFalse(kwargs["shell"])
            if os.name == "nt":
                self.assertEqual(kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
            else:
                self.assertNotIn("creationflags", kwargs)
            return process

        client = AppServerBalanceClient(
            which=lambda name: "C:/bin/codex" if name == "codex" else None,
            process_factory=process_factory,
            now=lambda: datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
        )

        result = client.fetch()

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "app_server")
        self.assertEqual(result.balance, 63)
        self.assertEqual(result.unit, "%")
        self.assertEqual(result.reset_at, 1786160194)
        self.assertEqual(result.retrieved_at, "2026-08-05T12:00:00+00:00")
        self.assertEqual(len(process.calls), 1)
        request_lines = process.calls[0][0].splitlines()
        self.assertEqual(json.loads(request_lines[0])["method"], "initialize")
        self.assertEqual(json.loads(request_lines[1])["method"], "initialized")
        self.assertEqual(json.loads(request_lines[2])["method"], "account/read")
        self.assertEqual(len(request_lines), 3)

    def _client_for(self, lines):
        process = FakeProcess(lines)
        return AppServerBalanceClient(
            which=lambda _name: "C:/bin/codex",
            process_factory=lambda _args, **_kwargs: process,
            now=lambda: datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
        )

    def test_unlogged_account_is_classified_without_exposing_server_text(self):
        client = self._client_for(
            app_server_lines(
                None,
                rate_error="codex account authentication required to read rate limits",
            )
        )

        result = client.fetch()

        self.assertEqual(result.status, "auth_required")
        self.assertEqual(result.error_code, "AUTH_REQUIRED")
        self.assertNotIn("account authentication", result.error_message)

    def test_api_key_account_is_not_treated_as_chatgpt_quota(self):
        client = self._client_for(
            app_server_lines(
                {"type": "apiKey"},
                rate_error="chatgpt authentication required to read rate limits",
            )
        )

        result = client.fetch()

        self.assertEqual(result.status, "unsupported_auth")
        self.assertEqual(result.balance, None)

    def test_malformed_primary_window_is_schema_error_not_full_balance(self):
        client = self._client_for(
            app_server_lines(
                {"type": "chatgpt"},
                rate_limits={
                    "rateLimits": {
                        "primary": {"usedPercent": "37"},
                    }
                },
            )
        )

        result = client.fetch()

        self.assertEqual(result.status, "schema_changed")
        self.assertIsNone(result.balance)
        self.assertIsNone(result.remaining_percent)

    def test_detail_bucket_keeps_compatibility_metadata_from_single_bucket(self):
        client = self._client_for(
            app_server_lines(
                {"type": "chatgpt"},
                rate_limits={
                    "rateLimits": {
                        "planType": "plus",
                        "primary": {"usedPercent": 37},
                        "credits": {"hasCredits": True, "unlimited": False, "balance": "2"},
                    },
                    "rateLimitsByLimitId": {
                        "codex": {"primary": {"usedPercent": 37}},
                    },
                },
            )
        )

        result = client.fetch()

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.plan_type, "plus")
        self.assertEqual(result.credits_balance, "2")

    def test_timeout_is_classified_and_process_is_killed(self):
        class TimeoutProcess(FakeProcess):
            def communicate(self, input=None, timeout=None):
                raise __import__("subprocess").TimeoutExpired(["codex"], timeout)

        process = TimeoutProcess([])
        client = AppServerBalanceClient(
            which=lambda _name: "C:/bin/codex",
            process_factory=lambda _args, **_kwargs: process,
            timeout=0.01,
        )

        result = client.fetch()

        self.assertEqual(result.status, "network_error")
        self.assertEqual(result.error_code, "TIMEOUT")
        self.assertTrue(process.killed)

    def test_missing_cli_is_classified_without_starting_process(self):
        def process_factory(*_args, **_kwargs):
            self.fail("process must not start when codex is not on PATH")

        client = AppServerBalanceClient(
            which=lambda _name: None,
            process_factory=process_factory,
        )

        result = client.fetch()

        self.assertEqual(result.status, "cli_unavailable")
        self.assertEqual(result.error_code, "CLI_NOT_FOUND")
        self.assertIsNone(result.balance)


class HttpBalanceClientTests(unittest.TestCase):
    NOW = lambda _self: datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)

    def _client(self, response=None, error=None, base_url="https://chatgpt.com/backend-api"):
        return HttpBalanceClient(
            session=FakeSession(response, error),
            base_url=base_url,
            credential_provider=lambda: ("secret-token", "secret-account"),
            now=self.NOW,
        )

    def test_chatgpt_backend_uses_wham_path_and_parses_success(self):
        session = FakeSession(
            FakeResponse(
                200,
                {
                    "plan_type": "plus",
                    "rate_limit": {
                        "primary_window": {
                            "used_percent": 37,
                            "limit_window_seconds": 604800,
                            "reset_after_seconds": 271722,
                            "reset_at": 1786160194,
                        },
                        "secondary_window": None,
                    },
                    "credits": None,
                },
            )
        )
        client = HttpBalanceClient(
            session=session,
            base_url="https://chatgpt.com/backend-api",
            credential_provider=lambda: ("secret-token", "secret-account"),
            now=self.NOW,
        )

        result = client.fetch()

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.balance, 63)
        self.assertEqual(session.calls[0][0], "https://chatgpt.com/backend-api/wham/usage")
        self.assertEqual(session.calls[0][1]["headers"]["Authorization"], "Bearer secret-token")

    def test_non_chatgpt_base_uses_codex_api_path(self):
        session = FakeSession(FakeResponse(404, {"detail": "secret-token invalid"}))
        result = HttpBalanceClient(
            session=session,
            base_url="https://api.example.test",
            credential_provider=lambda: ("secret-token", "secret-account"),
            allow_untrusted_base_url=True,
            now=self.NOW,
        ).fetch()

        self.assertEqual(result.status, "unsupported_route")
        self.assertEqual(session.calls[0][0], "https://api.example.test/api/codex/usage")
        self.assertNotIn("secret-token", result.error_message)
        self.assertNotIn("secret-token", repr(result))

    def test_network_failure_does_not_escape_or_include_credentials(self):
        result = self._client(error=requests.ConnectionError("Bearer secret-token failed")).fetch()

        self.assertEqual(result.status, "network_error")
        self.assertNotIn("secret-token", result.error_message)

    def test_server_failure_is_classified_without_response_body(self):
        result = self._client(
            response=FakeResponse(503, {"error": "secret-token backend failure"})
        ).fetch()

        self.assertEqual(result.status, "service_error")
        self.assertEqual(result.error_code, "HTTP_503")
        self.assertNotIn("secret-token", result.error_message)

    def test_rate_limit_retry_after_preserves_server_value(self):
        result = self._client(
            response=FakeResponse(429, {"error": "secret-token"}, {"Retry-After": "999999"})
        ).fetch()

        self.assertEqual(result.status, "rate_limited")
        self.assertEqual(result.retry_after_seconds, 999999)
        self.assertNotIn("secret-token", result.error_message)


class CodexBalanceProviderTests(unittest.TestCase):
    def _success(self):
        return AppServerBalanceClient(
            which=lambda _name: "C:/bin/codex",
            process_factory=lambda _args, **_kwargs: FakeProcess(
                app_server_lines(
                    {"type": "chatgpt"},
                    rate_limits={
                        "rateLimits": {
                            "primary": {"usedPercent": 10, "resetsAt": 1786160194}
                        }
                    },
                )
            ),
        )

    def test_retries_transient_failure_and_returns_success(self):
        class Flaky:
            def __init__(self):
                self.calls = 0

            def fetch(self):
                self.calls += 1
                if self.calls == 1:
                    return BalanceResult(
                        status="network_error",
                        source="app_server",
                        error_message="network",
                    )
                return BalanceResult(
                    status="ok",
                    source="app_server",
                    retrieved_at="now",
                )

        flaky = Flaky()
        sleeps = []
        provider = CodexBalanceProvider(
            app_server=flaky,
            retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=(0,), jitter_ratio=0),
            sleep=sleeps.append,
        )

        result = provider.fetch()

        self.assertEqual(result.status, "ok")
        self.assertEqual(flaky.calls, 2)
        self.assertEqual(sleeps, [0.0])

    def test_last_good_value_becomes_stale_after_failure(self):
        class Sequence:
            def __init__(self):
                self.results = [self._ok(), self._failure()]

            @staticmethod
            def _ok():
                return BalanceResult(
                    status="ok",
                    source="app_server",
                    balance=63,
                    remaining_percent=63,
                    retrieved_at="2026-08-05T12:00:00+00:00",
                )

            @staticmethod
            def _failure():
                return BalanceResult(
                    status="auth_required",
                    source="app_server",
                    error_code="AUTH_REQUIRED",
                    error_message="登入失效",
                    retrieved_at="2026-08-05T12:05:00+00:00",
                )

            def fetch(self):
                return self.results.pop(0)

        provider = CodexBalanceProvider(app_server=Sequence())

        first = provider.fetch()
        second = provider.fetch()

        self.assertEqual(first.status, "ok")
        self.assertEqual(second.status, "stale")
        self.assertEqual(second.balance, 63)
        self.assertEqual(second.error_code, "AUTH_REQUIRED")

    def test_controlled_http_fallback_is_used_when_cli_is_unavailable(self):
        class CliUnavailable:
            def fetch(self):
                return BalanceResult(
                    status="cli_unavailable",
                    source="app_server",
                    error_code="CLI_NOT_FOUND",
                    error_message="找不到 Codex CLI",
                )

        fallback = type(
            "Fallback",
            (),
            {
                "fetch": lambda _self: BalanceResult(
                    status="ok", source="chatgpt_backend", remaining=63
                )
            },
        )()
        result = CodexBalanceProvider(
            app_server=CliUnavailable(),
            fallback=fallback,
            retry_policy=RetryPolicy(max_attempts=1),
        ).fetch()

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "chatgpt_backend")

    def test_concurrent_fetches_share_one_inflight_result(self):
        started = threading.Event()
        release = threading.Event()

        class Blocking:
            calls = 0

            def fetch(self):
                self.calls += 1
                started.set()
                release.wait(1)
                return BalanceResult(status="ok", source="app_server", remaining=63)

        client = Blocking()
        provider = CodexBalanceProvider(app_server=client)
        results = []
        first = threading.Thread(target=lambda: results.append(provider.fetch()))
        second = threading.Thread(target=lambda: results.append(provider.fetch()))
        first.start()
        self.assertTrue(started.wait(1))
        second.start()
        release.set()
        first.join(1)
        second.join(1)

        self.assertEqual(client.calls, 1)
        self.assertEqual([result.remaining for result in results], [63, 63])


if __name__ == "__main__":
    unittest.main()
