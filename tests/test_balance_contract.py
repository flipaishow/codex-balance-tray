"""Offline contract tests for the Codex BalanceResult providers.

These tests deliberately stop at the CLI-process and HTTP-session seams.  They
never invoke ``codex``, open a real socket, read the user's credential store,
or put a real credential in a fixture.
"""

from __future__ import annotations

import json
import queue
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

from codex_tray.balance import (
    AppServerBalanceClient,
    BalanceProviderError,
    BalanceResult,
    CodexBalanceProvider,
    HttpBalanceClient,
    RetryPolicy,
    parse_app_server_rate_limits,
    parse_http_usage_payload,
)


FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
TEST_TOKEN = "fixture-access-token"
TEST_ACCOUNT = "fixture-account-id"


def jsonl_fixture(name: str) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def json_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FixtureProcess:
    """Small ``Popen`` substitute that replays a JSONL fixture."""

    def __init__(
        self,
        messages: Iterable[dict[str, Any]] | str,
        *,
        timeout: bool = False,
        io_error: bool = False,
    ):
        self.stdout = (
            messages
            if isinstance(messages, str)
            else "\n".join(json.dumps(message) for message in messages) + "\n"
        )
        self.timeout = timeout
        self.io_error = io_error
        self.calls: list[tuple[str | None, float | None]] = []
        self.killed = False
        self.waited = False

    def communicate(self, input: str | None = None, timeout: float | None = None):
        self.calls.append((input, timeout))
        if self.timeout:
            raise subprocess.TimeoutExpired(["codex"], timeout)
        if self.io_error:
            raise OSError("broken pipe")
        return self.stdout, ""

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        return 0


class ControlledStdout:
    def __init__(self, process: "InteractiveProcess", responses: Iterable[dict[str, Any]]):
        self.process = process
        self.responses = iter(responses)
        self.lines: queue.Queue[str] = queue.Queue()
        self.read_count = 0

    def release_response(self) -> None:
        try:
            response = next(self.responses)
        except StopIteration:
            self.lines.put("")
        else:
            self.lines.put(json.dumps(response) + "\n")

    def readline(self) -> str:
        line = self.lines.get()
        if line:
            self.read_count += 1
        return line

    def close(self) -> None:
        self.lines.put("")


class ControlledStdin:
    def __init__(self, process: "InteractiveProcess"):
        self.process = process
        self.closed = False

    def write(self, value: str) -> int:
        for line in value.splitlines():
            message = json.loads(line)
            method = message.get("method")
            if method == "account/read":
                if self.process.stdout.read_count < 1:
                    raise AssertionError("account/read sent before initialize response")
                self.process.stdout.release_response()
            elif method == "account/rateLimits/read":
                if self.process.stdout.read_count < 2:
                    raise AssertionError("rate-limits request sent before account response")
                self.process.stdout.release_response()
            elif method == "initialize":
                self.process.stdout.release_response()
            self.process.writes.append(message)
        return len(value)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True
        self.process.stdout.close()


class SilentStderr:
    def readline(self) -> str:
        return ""


class InteractiveProcess:
    """Popen-like process that only releases each response after its request."""

    def __init__(self, responses: Iterable[dict[str, Any]]):
        self.writes: list[dict[str, Any]] = []
        self.stdout = ControlledStdout(self, responses)
        self.stdin = ControlledStdin(self)
        self.stderr = SilentStderr()
        self.killed = False
        self.waited = False

    def communicate(self, *_args: Any, **_kwargs: Any):
        raise AssertionError("interactive process must not use communicate()")

    def kill(self) -> None:
        self.killed = True
        self.stdout.close()

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        return 0


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: Any = None,
        *,
        headers: dict[str, str] | None = None,
        json_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self.payload = payload
        self.headers = headers or {}
        self.json_error = json_error

    def json(self) -> Any:
        if self.json_error is not None:
            raise self.json_error
        return self.payload


class FakeSession:
    def __init__(self, response: FakeResponse | None = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


class SequenceClient:
    def __init__(self, results: Iterable[Any]):
        self.results = iter(results)
        self.calls = 0
        self.last: Any = None

    def fetch(self) -> Any:
        self.calls += 1
        try:
            self.last = next(self.results)
        except StopIteration:
            pass
        return self.last


def failure(
    status: str,
    *,
    error_code: str = "PROVIDER_ERROR",
    retry_after_seconds: int | None = None,
) -> BalanceResult:
    return BalanceResult(
        status=status,  # type: ignore[arg-type]
        source="app_server",
        error_code=error_code,
        error_message="safe provider failure",
        retry_after_seconds=retry_after_seconds,
        retrieved_at="2026-08-05T12:00:00+00:00",
    )


class AppServerFixtureTests(unittest.TestCase):
    def make_client(
        self,
        process: FixtureProcess,
        *,
        executable: str = r"C:\Program Files\Codex\codex.exe",
        **kwargs: Any,
    ) -> tuple[AppServerBalanceClient, dict[str, Any]]:
        captured: dict[str, Any] = {}

        def process_factory(args: list[str], **options: Any) -> FixtureProcess:
            captured["args"] = args
            captured["options"] = options
            return process

        return (
            AppServerBalanceClient(
                which=lambda _name: executable,
                process_factory=process_factory,
                now=lambda: NOW,
                **kwargs,
            ),
            captured,
        )

    def test_success_fixture_maps_balance_unit_window_and_timestamps(self):
        process = FixtureProcess(jsonl_fixture("app_server_success.jsonl"))
        client, captured = self.make_client(process)

        result = client.fetch()

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "app_server")
        self.assertEqual(result.balance, 63)
        self.assertEqual(result.remaining, 63)
        self.assertEqual(result.unit, "%")
        self.assertEqual(result.used_percent, 37)
        self.assertEqual(result.remaining_percent, 63)
        self.assertEqual(result.window_seconds, 10080 * 60)
        self.assertEqual(result.reset_at, 1786160194)
        self.assertEqual(result.reset_after_seconds, 1786160194 - int(NOW.timestamp()))
        self.assertEqual(result.retrieved_at, "2026-08-05T12:00:00+00:00")
        self.assertEqual(result.last_success_at, result.retrieved_at)
        self.assertEqual(result.plan_type, "plus")
        self.assertEqual(result.credits_balance, "0")
        self.assertFalse(result.has_credits)
        self.assertFalse(result.unlimited)
        self.assertIsNotNone(datetime.fromisoformat(result.retrieved_at).tzinfo)

        self.assertEqual(
            captured["args"],
            [r"C:\Program Files\Codex\codex.exe", "app-server", "--listen", "stdio://"],
        )
        self.assertFalse(captured["options"]["shell"])
        self.assertEqual(len(process.calls), 1)
        request_lines = process.calls[0][0].splitlines()
        self.assertEqual(json.loads(request_lines[0])["method"], "initialize")
        self.assertEqual(json.loads(request_lines[1])["method"], "initialized")
        self.assertEqual(json.loads(request_lines[2])["method"], "account/read")
        self.assertEqual(len(request_lines), 3)

    def test_windows_cli_arguments_never_include_credentials(self):
        process = FixtureProcess(jsonl_fixture("app_server_success.jsonl"))
        client, captured = self.make_client(process)

        result = client.fetch()

        self.assertEqual(result.status, "ok")
        command_text = " ".join(captured["args"])
        request_text = process.calls[0][0]
        for secret in (TEST_TOKEN, TEST_ACCOUNT, "Authorization", "Bearer"):
            self.assertNotIn(secret, command_text)
            self.assertNotIn(secret, request_text)
        self.assertFalse(captured["options"]["shell"])

    def test_missing_cli_returns_safe_classification_without_starting_process(self):
        process_started = False

        def process_factory(*_args: Any, **_kwargs: Any) -> None:
            nonlocal process_started
            process_started = True

        client = AppServerBalanceClient(
            which=lambda _name: None,
            process_factory=process_factory,
            now=lambda: NOW,
        )

        result = client.fetch()

        self.assertEqual(result.status, "cli_unavailable")
        self.assertEqual(result.error_code, "CLI_NOT_FOUND")
        self.assertIsNone(result.balance)
        self.assertEqual(result.retrieved_at, "2026-08-05T12:00:00+00:00")
        self.assertFalse(process_started)

    def test_cli_start_failure_does_not_echo_exception_or_credentials(self):
        def process_factory(*_args: Any, **_kwargs: Any) -> None:
            raise OSError(f"cannot start {TEST_TOKEN} on Windows")

        client = AppServerBalanceClient(
            which=lambda _name: r"C:\Codex\codex.exe",
            process_factory=process_factory,
            now=lambda: NOW,
        )

        result = client.fetch()

        self.assertEqual(result.status, "cli_unavailable")
        self.assertEqual(result.error_code, "CLI_START_FAILED")
        self.assertNotIn(TEST_TOKEN, repr(result))
        self.assertNotIn(TEST_TOKEN, result.error_message or "")

    def test_timeout_kills_process_and_returns_network_error(self):
        process = FixtureProcess([], timeout=True)
        client, _captured = self.make_client(process)

        result = client.fetch()

        self.assertEqual(result.status, "network_error")
        self.assertEqual(result.error_code, "TIMEOUT")
        self.assertIsNone(result.balance)
        self.assertTrue(process.killed)
        self.assertEqual(result.retrieved_at, "2026-08-05T12:00:00+00:00")

    def test_broken_pipe_kills_and_waits_for_process_cleanup(self):
        process = FixtureProcess([], io_error=True)
        client, _captured = self.make_client(process)

        result = client.fetch()

        self.assertEqual(result.status, "network_error")
        self.assertEqual(result.error_code, "CLI_IO_ERROR")
        self.assertTrue(process.killed)
        self.assertTrue(process.waited)

    def test_app_server_handshake_waits_before_sending_follow_up_requests(self):
        process = InteractiveProcess(jsonl_fixture("app_server_success.jsonl"))
        client, _captured = self.make_client(process)

        result = client.fetch()

        self.assertEqual(result.status, "ok")
        self.assertEqual(
            [message["method"] for message in process.writes],
            ["initialize", "initialized", "account/read", "account/rateLimits/read"],
        )
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.waited)

    def test_unknown_account_type_is_rejected_before_rate_limits_request(self):
        process = InteractiveProcess(
            [
                {"id": 1, "result": {}},
                {"id": 2, "result": {"account": {"type": "other"}, "requiresOpenaiAuth": True}},
            ]
        )
        client, _captured = self.make_client(process)

        result = client.fetch()

        self.assertEqual(result.status, "unsupported_auth")
        self.assertEqual(
            [message["method"] for message in process.writes],
            ["initialize", "initialized", "account/read"],
        )

    def test_invalid_jsonl_is_schema_error_not_full_balance(self):
        process = FixtureProcess("not-json\n")
        client, _captured = self.make_client(process)

        result = client.fetch()

        self.assertEqual(result.status, "schema_changed")
        self.assertEqual(result.error_code, "SCHEMA_CHANGED")
        self.assertIsNone(result.balance)
        self.assertIsNone(result.remaining_percent)

    def test_invalid_schema_fixture_is_schema_error_not_full_balance(self):
        process = FixtureProcess(jsonl_fixture("app_server_invalid_schema.jsonl"))
        client, _captured = self.make_client(process)

        result = client.fetch()

        self.assertEqual(result.status, "schema_changed")
        self.assertEqual(result.error_code, "SCHEMA_CHANGED")
        self.assertIsNone(result.balance)
        self.assertIsNone(result.remaining_percent)

    def test_oversized_jsonl_line_is_schema_error(self):
        process = FixtureProcess("x" * 32)
        client, _captured = self.make_client(process, max_line_bytes=8)

        result = client.fetch()

        self.assertEqual(result.status, "schema_changed")
        self.assertEqual(result.error_code, "SCHEMA_CHANGED")
        self.assertIsNone(result.balance)

    def test_no_account_fixture_is_auth_required(self):
        process = FixtureProcess(jsonl_fixture("app_server_no_account.jsonl"))
        client, _captured = self.make_client(process)

        result = client.fetch()

        self.assertEqual(result.status, "auth_required")
        self.assertEqual(result.error_code, "AUTH_REQUIRED")
        self.assertIsNone(result.balance)
        self.assertNotIn("account", result.error_message or "")

    def test_api_key_fixture_is_unsupported_auth_not_chatgpt_quota(self):
        process = FixtureProcess(jsonl_fixture("app_server_api_key.jsonl"))
        client, _captured = self.make_client(process)

        result = client.fetch()

        self.assertEqual(result.status, "unsupported_auth")
        self.assertEqual(result.error_code, "UNSUPPORTED_AUTH")
        self.assertIsNone(result.balance)
        self.assertNotIn("chatgpt authentication", result.error_message or "")

    def test_no_snapshot_error_is_service_error_without_server_text(self):
        lines = jsonl_fixture("app_server_success.jsonl")
        lines[-1] = {
            "id": 3,
            "error": {
                "code": -32600,
                "message": "failed to fetch codex rate limits: no snapshots returned",
            },
        }
        process = FixtureProcess(lines)
        client, _captured = self.make_client(process)

        result = client.fetch()

        self.assertEqual(result.status, "service_error")
        self.assertEqual(result.error_code, "NO_SNAPSHOT")
        self.assertIsNone(result.balance)
        self.assertNotIn("no snapshots", result.error_message or "")


class ParserContractTests(unittest.TestCase):
    def test_app_server_parser_uses_codex_detail_and_keeps_compatibility_fields(self):
        payload = {
            "result": {
                "rateLimits": {
                    "planType": "plus",
                    "credits": {"hasCredits": True, "unlimited": False, "balance": "2"},
                    "primary": {"usedPercent": 81},
                },
                "rateLimitsByLimitId": {
                    "other": {"primary": {"usedPercent": 99}},
                    "codex": {"primary": {"usedPercent": 37}},
                },
            }
        }

        result = parse_app_server_rate_limits(payload, now=lambda: NOW)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.balance, 63)
        self.assertEqual(result.used_percent, 37)
        self.assertEqual(result.plan_type, "plus")
        self.assertEqual(result.credits_balance, "2")

    def test_app_server_missing_primary_is_schema_error_not_100_percent(self):
        result = parse_app_server_rate_limits(
            {"result": {"rateLimits": {"secondary": None}}},
            now=lambda: NOW,
        )

        self.assertEqual(result.status, "schema_changed")
        self.assertEqual(result.error_code, "SCHEMA_CHANGED")
        self.assertIsNone(result.balance)
        self.assertIsNone(result.remaining_percent)

    def test_http_fixture_preserves_string_credits_and_reset_fields(self):
        result = parse_http_usage_payload(json_fixture("http_success.json"), now=lambda: NOW)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "chatgpt_backend")
        self.assertEqual(result.balance, 63)
        self.assertEqual(result.unit, "%")
        self.assertEqual(result.used_percent, 37)
        self.assertEqual(result.remaining_percent, 63)
        self.assertEqual(result.window_seconds, 604800)
        self.assertEqual(result.reset_at, 1786160194)
        self.assertEqual(result.reset_after_seconds, 271722)
        self.assertEqual(result.credits_balance, "0")
        self.assertEqual(result.retrieved_at, "2026-08-05T12:00:00+00:00")

    def test_http_missing_window_fixture_is_schema_error_not_full_balance(self):
        result = parse_http_usage_payload(json_fixture("http_schema_changed.json"), now=lambda: NOW)

        self.assertEqual(result.status, "schema_changed")
        self.assertIsNone(result.balance)
        self.assertIsNone(result.remaining_percent)


class HttpFixtureTests(unittest.TestCase):
    def make_client(
        self,
        response: FakeResponse | None = None,
        *,
        error: Exception | None = None,
        base_url: str = "https://chatgpt.com/backend-api",
        credential_provider: Any = lambda: (TEST_TOKEN, TEST_ACCOUNT),
        **kwargs: Any,
    ) -> tuple[HttpBalanceClient, FakeSession]:
        session = FakeSession(response, error)
        return (
            HttpBalanceClient(
                session=session,
                base_url=base_url,
                credential_provider=credential_provider,
                now=lambda: NOW,
                **kwargs,
            ),
            session,
        )

    def assert_safe_result(self, result: BalanceResult, secret: str = TEST_TOKEN):
        self.assertNotIn(secret, repr(result))
        self.assertNotIn(secret, result.error_message or "")
        self.assertNotIn(secret, result.error or "")

    def test_success_fixture_uses_chatgpt_wham_endpoint_and_maps_fields(self):
        response = FakeResponse(200, json_fixture("http_success.json"))
        client, session = self.make_client(response)

        result = client.fetch()

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "chatgpt_backend")
        self.assertEqual(result.balance, 63)
        self.assertEqual(result.unit, "%")
        self.assertEqual(result.reset_at, 1786160194)
        self.assertEqual(result.reset_after_seconds, 271722)
        self.assertEqual(result.retrieved_at, "2026-08-05T12:00:00+00:00")
        self.assertEqual(session.calls[0][0], "https://chatgpt.com/backend-api/wham/usage")
        self.assertEqual(session.calls[0][1]["timeout"], 20.0)
        self.assertFalse(session.calls[0][1]["allow_redirects"])
        self.assertEqual(session.calls[0][1]["headers"]["Authorization"], f"Bearer {TEST_TOKEN}")
        self.assertEqual(session.calls[0][1]["headers"]["ChatGPT-Account-Id"], TEST_ACCOUNT)
        self.assert_safe_result(result)

    def test_non_chatgpt_base_url_uses_codex_api_endpoint(self):
        response = FakeResponse(404, json_fixture("http_error_body.json"))
        client, session = self.make_client(
            response,
            base_url="https://api.example.test",
            allow_untrusted_base_url=True,
        )

        result = client.fetch()

        self.assertEqual(result.status, "unsupported_route")
        self.assertEqual(result.error_code, "HTTP_404")
        self.assertEqual(session.calls[0][0], "https://api.example.test/api/codex/usage")
        self.assert_safe_result(result)

    def test_http_base_url_requires_explicit_insecure_opt_in(self):
        with self.assertRaises(ValueError):
            self.make_client(base_url="http://api.example.test")

    def test_untrusted_https_base_url_requires_explicit_opt_in(self):
        with self.assertRaises(ValueError):
            self.make_client(base_url="https://api.example.test")

    def test_official_host_rejects_non_default_port_before_credentials_or_network(self):
        with self.assertRaises(ValueError):
            self.make_client(base_url="https://chatgpt.com:8443")

    def test_explicit_untrusted_opt_in_allows_custom_port_for_fixture_endpoint(self):
        response = FakeResponse(404, json_fixture("http_error_body.json"))
        client, session = self.make_client(
            response,
            base_url="https://chatgpt.com:8443",
            allow_untrusted_base_url=True,
        )

        result = client.fetch()

        self.assertEqual(result.status, "unsupported_route")
        self.assertEqual(session.calls[0][0], "https://chatgpt.com:8443/backend-api/wham/usage")

    def test_redirect_response_is_not_followed_or_treated_as_success(self):
        client, session = self.make_client(
            FakeResponse(302, json_fixture("http_error_body.json"))
        )

        result = client.fetch()

        self.assertEqual(result.status, "unsupported_route")
        self.assertEqual(result.error_code, "HTTP_302")
        self.assertFalse(session.calls[0][1]["allow_redirects"])
        self.assert_safe_result(result)

    def test_auth_statuses_do_not_expose_http_body(self):
        for status_code in (401, 403):
            with self.subTest(status_code=status_code):
                client, _session = self.make_client(
                    FakeResponse(status_code, json_fixture("http_error_body.json"))
                )

                result = client.fetch()

                self.assertEqual(result.status, "auth_required")
                self.assertEqual(result.error_code, f"HTTP_{status_code}")
                self.assert_safe_result(result)

    def test_unsupported_routes_do_not_retry_or_echo_body(self):
        for status_code in (404, 405):
            with self.subTest(status_code=status_code):
                session = FakeSession(FakeResponse(status_code, json_fixture("http_error_body.json")))
                client = HttpBalanceClient(
                    session=session,
                    credential_provider=lambda: (TEST_TOKEN, TEST_ACCOUNT),
                    now=lambda: NOW,
                )

                result = client.fetch()

                self.assertEqual(result.status, "unsupported_route")
                self.assertEqual(len(session.calls), 1)
                self.assert_safe_result(result)

    def test_rate_limit_preserves_server_retry_after_even_when_policy_has_maximum(self):
        client, _session = self.make_client(
            FakeResponse(
                429,
                json_fixture("http_error_body.json"),
                headers={"Retry-After": "999999"},
            )
        )

        result = client.fetch()

        self.assertEqual(result.status, "rate_limited")
        self.assertEqual(result.error_code, "HTTP_429")
        self.assertEqual(result.retry_after_seconds, 999999)
        self.assert_safe_result(result)

    def test_invalid_retry_after_is_not_leaked_or_trusted(self):
        for raw_value, expected in (("not-a-number", None), ("-30", 0)):
            with self.subTest(raw_value=raw_value):
                client, _session = self.make_client(
                    FakeResponse(
                        429,
                        json_fixture("http_error_body.json"),
                        headers={"Retry-After": raw_value},
                    )
                )

                result = client.fetch()

                self.assertEqual(result.retry_after_seconds, expected)
                self.assert_safe_result(result)

    def test_http_date_retry_after_is_converted_to_a_bounded_seconds_delay(self):
        client, _session = self.make_client(
            FakeResponse(
                429,
                json_fixture("http_error_body.json"),
                headers={"Retry-After": "Wed, 05 Aug 2026 12:02:00 GMT"},
            )
        )

        result = client.fetch()

        self.assertEqual(result.retry_after_seconds, 120)

    def test_5xx_retry_after_is_preserved_for_provider_retry(self):
        client, _session = self.make_client(
            FakeResponse(
                503,
                json_fixture("http_error_body.json"),
                headers={"Retry-After": "Wed, 05 Aug 2026 12:02:00 GMT"},
            )
        )

        result = client.fetch()

        self.assertEqual(result.status, "service_error")
        self.assertEqual(result.error_code, "HTTP_503")
        self.assertEqual(result.retry_after_seconds, 120)

    def test_server_5xx_is_service_error_without_response_body(self):
        for status_code in (500, 502, 503, 504):
            with self.subTest(status_code=status_code):
                client, _session = self.make_client(
                    FakeResponse(status_code, json_fixture("http_error_body.json"))
                )

                result = client.fetch()

                self.assertEqual(result.status, "service_error")
                self.assertEqual(result.error_code, f"HTTP_{status_code}")
                self.assertIsNone(result.balance)
                self.assert_safe_result(result)

    def test_connection_and_http_timeout_are_network_errors(self):
        for exc in (requests.ConnectionError(f"Bearer {TEST_TOKEN} failed"), requests.Timeout("timed out")):
            with self.subTest(exception=type(exc).__name__):
                client, _session = self.make_client(error=exc)

                result = client.fetch()

                self.assertEqual(result.status, "network_error")
                self.assertEqual(result.error_code, "NETWORK_ERROR")
                self.assert_safe_result(result)

    def test_non_json_and_non_object_success_bodies_are_schema_errors(self):
        cases = (
            FakeResponse(200, json_error=ValueError("raw response contains secret")),
            FakeResponse(200, ["not", "an", "object"]),
        )
        for response in cases:
            with self.subTest(response=response):
                client, _session = self.make_client(response)

                result = client.fetch()

                self.assertEqual(result.status, "schema_changed")
                self.assertEqual(result.error_code, "SCHEMA_CHANGED")
                self.assertIsNone(result.balance)
                self.assert_safe_result(result)

    def test_missing_safe_credential_provider_is_unsupported_auth(self):
        client = HttpBalanceClient(session=FakeSession(), now=lambda: NOW)

        result = client.fetch()

        self.assertEqual(result.status, "unsupported_auth")
        self.assertEqual(result.error_code, "UNSUPPORTED_AUTH")
        self.assertIsNone(result.balance)
        self.assertEqual(len(client.session.calls), 0)

    def test_malformed_credential_provider_is_auth_error_without_value(self):
        client, session = self.make_client(
            credential_provider=lambda: {"tokens": {"access_token": TEST_TOKEN}},
        )

        result = client.fetch()

        self.assertEqual(result.status, "auth_required")
        self.assertEqual(result.error_code, "AUTH_REQUIRED")
        self.assertIsNone(result.balance)
        self.assertEqual(session.calls, [])
        self.assert_safe_result(result)

    def test_credential_provider_balance_error_never_exposes_exception_message(self):
        leaked = f"credential provider failed with Bearer {TEST_TOKEN}"

        def credential_provider():
            raise BalanceProviderError("auth_required", "AUTH_REQUIRED", leaked)

        client, session = self.make_client(credential_provider=credential_provider)

        result = client.fetch()

        self.assertEqual(result.status, "auth_required")
        self.assertEqual(result.error_code, "AUTH_REQUIRED")
        self.assertEqual(result.error_message, "無法取得 Codex 登入資料，請先在 Codex 重新登入")
        self.assertNotIn(leaked, repr(result))
        self.assertNotIn(TEST_TOKEN, repr(result))
        self.assertEqual(session.calls, [])

    def test_file_auth_secret_is_used_only_in_request_and_not_result(self):
        with tempfile.TemporaryDirectory() as directory:
            auth_path = Path(directory) / "auth.json"
            auth_path.write_text(
                json.dumps(
                    {
                        "tokens": {
                            "access_token": TEST_TOKEN,
                            "account_id": TEST_ACCOUNT,
                            "refresh_token": "refresh-fixture-only",
                        }
                    }
                ),
                encoding="utf-8",
            )
            session = FakeSession(FakeResponse(503, json_fixture("http_error_body.json")))
            client = HttpBalanceClient(
                session=session,
                auth_path=auth_path,
                allow_file_auth=True,
                now=lambda: NOW,
            )

            result = client.fetch()

        self.assertEqual(result.status, "service_error")
        self.assert_safe_result(result)
        self.assertNotIn("refresh-fixture-only", repr(result))
        self.assertEqual(session.calls[0][1]["headers"]["Authorization"], f"Bearer {TEST_TOKEN}")


class ProviderRetryTests(unittest.TestCase):
    def provider(self, client: SequenceClient, *, max_attempts: int = 3, **kwargs: Any) -> CodexBalanceProvider:
        return CodexBalanceProvider(
            app_server=client,
            retry_policy=RetryPolicy(
                max_attempts=max_attempts,
                backoff_seconds=(10.0, 20.0, 30.0),
                jitter_ratio=0,
                **kwargs.pop("retry_policy", {}),
            ),
            sleep=kwargs.pop("sleep"),
            now=lambda: NOW,
            **kwargs,
        )

    def test_service_error_retries_until_success_with_configured_delays(self):
        client = SequenceClient(
            [
                failure("service_error", error_code="HTTP_503"),
                failure("service_error", error_code="HTTP_503"),
                BalanceResult(
                    status="ok",
                    source="app_server",
                    balance=63,
                    unit="%",
                    retrieved_at="2026-08-05T12:00:00+00:00",
                ),
            ]
        )
        sleeps: list[float] = []
        provider = self.provider(client, sleep=sleeps.append)

        result = provider.fetch()

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.balance, 63)
        self.assertEqual(client.calls, 3)
        self.assertEqual(sleeps, [10.0, 20.0])

    def test_http_400_is_not_retried_as_a_transient_service_error(self):
        client = SequenceClient([failure("service_error", error_code="HTTP_400")])
        sleeps: list[float] = []
        provider = self.provider(client, max_attempts=4, sleep=sleeps.append)

        result = provider.fetch()

        self.assertEqual(result.status, "service_error")
        self.assertEqual(result.error_code, "HTTP_400")
        self.assertEqual(client.calls, 1)
        self.assertEqual(sleeps, [])

    def test_retry_attempts_are_hard_capped(self):
        client = SequenceClient([failure("network_error", error_code="NETWORK_ERROR")])
        sleeps: list[float] = []
        provider = self.provider(client, max_attempts=2, sleep=sleeps.append)

        result = provider.fetch()

        self.assertEqual(result.status, "network_error")
        self.assertEqual(result.error_code, "NETWORK_ERROR")
        self.assertEqual(client.calls, 2)
        self.assertEqual(sleeps, [10.0])

    def test_nonretryable_auth_error_is_returned_without_retry(self):
        client = SequenceClient([failure("auth_required", error_code="AUTH_REQUIRED")])
        sleeps: list[float] = []
        provider = self.provider(client, max_attempts=4, sleep=sleeps.append)

        result = provider.fetch()

        self.assertEqual(result.status, "auth_required")
        self.assertEqual(result.error_code, "AUTH_REQUIRED")
        self.assertEqual(client.calls, 1)
        self.assertEqual(sleeps, [])

    def test_rate_limit_retry_after_controls_next_attempt(self):
        client = SequenceClient(
            [
                failure("rate_limited", error_code="HTTP_429", retry_after_seconds=120),
                BalanceResult(status="ok", source="app_server", balance=50, unit="%"),
            ]
        )
        sleeps: list[float] = []
        provider = self.provider(client, max_attempts=2, sleep=sleeps.append)

        result = provider.fetch()

        self.assertEqual(result.status, "ok")
        self.assertEqual(client.calls, 2)
        self.assertEqual(sleeps, [120.0])

    def test_rate_limit_retry_after_preserves_server_minimum_over_policy_maximum(self):
        client = SequenceClient(
            [
                failure("rate_limited", error_code="HTTP_429", retry_after_seconds=999999),
                failure("rate_limited", error_code="HTTP_429", retry_after_seconds=999999),
            ]
        )
        sleeps: list[float] = []
        provider = self.provider(client, max_attempts=2, sleep=sleeps.append)

        result = provider.fetch()

        self.assertEqual(result.status, "rate_limited")
        self.assertEqual(result.retry_after_seconds, 999999)
        self.assertEqual(client.calls, 2)
        self.assertEqual(sleeps, [999999.0])

    def test_provider_error_never_retries_even_with_transient_status(self):
        client = SequenceClient([failure("network_error", error_code="PROVIDER_ERROR")])
        sleeps: list[float] = []
        provider = self.provider(client, max_attempts=4, sleep=sleeps.append)

        result = provider.fetch()

        self.assertEqual(result.status, "network_error")
        self.assertEqual(result.error_code, "PROVIDER_ERROR")
        self.assertEqual(client.calls, 1)
        self.assertEqual(sleeps, [])

    def test_retry_after_is_a_minimum_even_when_jitter_is_configured(self):
        client = SequenceClient(
            [
                failure("rate_limited", error_code="HTTP_429", retry_after_seconds=120),
                BalanceResult(status="ok", source="app_server", balance=50, unit="%"),
            ]
        )
        sleeps: list[float] = []
        provider = CodexBalanceProvider(
            app_server=client,
            retry_policy=RetryPolicy(
                max_attempts=2,
                backoff_seconds=(10.0,),
                jitter_ratio=0.5,
            ),
            sleep=sleeps.append,
            random_value=lambda low, _high: low,
            now=lambda: NOW,
        )

        result = provider.fetch()

        self.assertEqual(result.status, "ok")
        self.assertEqual(sleeps, [120.0])

    def test_last_good_result_becomes_stale_and_keeps_safe_fields(self):
        client = SequenceClient(
            [
                BalanceResult(
                    status="ok",
                    source="app_server",
                    balance=63,
                    remaining_percent=63,
                    unit="%",
                    retrieved_at="2026-08-05T12:00:00+00:00",
                    last_success_at="2026-08-05T12:00:00+00:00",
                    plan_type="plus",
                ),
                failure("auth_required", error_code="AUTH_REQUIRED"),
            ]
        )
        provider = self.provider(client, max_attempts=1, sleep=lambda _seconds: None)

        first = provider.fetch()
        second = provider.fetch()

        self.assertEqual(first.status, "ok")
        self.assertEqual(second.status, "stale")
        self.assertEqual(second.balance, 63)
        self.assertEqual(second.remaining_percent, 63)
        self.assertEqual(second.unit, "%")
        self.assertEqual(second.plan_type, "plus")
        self.assertEqual(second.error_code, "AUTH_REQUIRED")
        self.assertEqual(second.last_success_at, "2026-08-05T12:00:00+00:00")

    def test_cli_unavailable_uses_explicit_http_fallback(self):
        primary = SequenceClient([failure("cli_unavailable", error_code="CLI_NOT_FOUND")])
        fallback = SequenceClient(
            [BalanceResult(status="ok", source="chatgpt_backend", balance=63, unit="%")]
        )
        provider = CodexBalanceProvider(
            app_server=primary,
            http_fallback=fallback,
            retry_policy=RetryPolicy(max_attempts=1),
            sleep=lambda _seconds: None,
        )

        result = provider.fetch()

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "chatgpt_backend")
        self.assertEqual(result.balance, 63)
        self.assertEqual(primary.calls, 1)
        self.assertEqual(fallback.calls, 1)

    def test_provider_exception_is_classified_without_exception_text(self):
        class ExplodingClient:
            def fetch(self):
                raise RuntimeError(f"Authorization: Bearer {TEST_TOKEN} leaked")

        provider = CodexBalanceProvider(
            app_server=ExplodingClient(),
            retry_policy=RetryPolicy(max_attempts=1),
            sleep=lambda _seconds: None,
            now=lambda: NOW,
        )

        result = provider.fetch()

        self.assertEqual(result.status, "service_error")
        self.assertEqual(result.error_code, "PROVIDER_ERROR")
        self.assertEqual(result.error_message, "Codex 服務暫時無法提供額度資料")
        self.assertNotIn(TEST_TOKEN, repr(result))

    def test_balance_provider_error_from_adapter_is_redacted(self):
        leaked = f"Authorization: Bearer {TEST_TOKEN}"

        class ExplodingClient:
            def fetch(self):
                raise BalanceProviderError("auth_required", "AUTH_REQUIRED", leaked)

        provider = CodexBalanceProvider(
            app_server=ExplodingClient(),
            retry_policy=RetryPolicy(max_attempts=1),
            sleep=lambda _seconds: None,
            now=lambda: NOW,
        )

        result = provider.fetch()

        self.assertEqual(result.status, "auth_required")
        self.assertEqual(result.error_code, "AUTH_REQUIRED")
        self.assertEqual(result.error_message, "無法取得 Codex 登入資料，請先在 Codex 重新登入")
        self.assertNotIn(TEST_TOKEN, repr(result))

    def test_invalid_provider_result_is_classified_and_not_retried(self):
        client = SequenceClient([{"balance": 63}])
        sleeps: list[float] = []
        provider = CodexBalanceProvider(
            app_server=client,
            retry_policy=RetryPolicy(max_attempts=4),
            sleep=sleeps.append,
            now=lambda: NOW,
        )

        result = provider.fetch()

        self.assertEqual(result.status, "service_error")
        self.assertEqual(result.error_code, "INVALID_PROVIDER_RESULT")
        self.assertIsNone(result.balance)
        self.assertEqual(client.calls, 1)
        self.assertEqual(sleeps, [])


if __name__ == "__main__":
    unittest.main()
