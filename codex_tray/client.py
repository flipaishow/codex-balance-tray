"""HTTP client for the local Codex ChatGPT usage endpoint."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from .balance import (
    AppServerBalanceClient,
    BalanceResult,
    CodexBalanceProvider,
    HttpBalanceClient,
    RetryPolicy,
)
from .usage import UsageError, UsageSnapshot, load_access_credentials, parse_usage_payload


DEFAULT_BASE_URL = "https://chatgpt.com/backend-api"
DEFAULT_TIMEOUT_SECONDS = 20
_TRUSTED_USAGE_HOSTS = {"chatgpt.com", "www.chatgpt.com", "chat.openai.com"}
_CHATGPT_USAGE_HOSTS = {"chatgpt.com", "www.chatgpt.com"}


def _validate_base_url(base_url: str, *, allow_untrusted: bool) -> str:
    value = base_url.rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme != "https":
        raise ValueError("legacy UsageClient 只允許 HTTPS base URL")
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("base_url 必須是沒有認證、query 或 fragment 的 URL")
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("base_url 必須使用有效的 port") from None
    host = parsed.hostname.lower()
    if not allow_untrusted and host not in _TRUSTED_USAGE_HOSTS:
        raise ValueError("legacy UsageClient 只允許官方 usage host")
    if not allow_untrusted and host in _TRUSTED_USAGE_HOSTS and port not in (None, 443):
        raise ValueError("官方 UsageClient 不允許非預設 port")
    return value


def _usage_endpoint(base_url: str) -> str:
    """Select the upstream-compatible path for the legacy HTTP client."""

    parsed = urlsplit(base_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/backend-api"):
        return f"{base_url}/wham/usage"
    if parsed.hostname and parsed.hostname.lower() in _CHATGPT_USAGE_HOSTS:
        return f"{base_url}/backend-api/wham/usage"
    return f"{base_url}/api/codex/usage"


class UsageClient:
    """Legacy HTTP adapter; file authentication is disabled unless opted in."""

    def __init__(
        self,
        *,
        session: Any | None = None,
        auth_path: Path | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        allow_file_auth: bool = False,
        allow_untrusted_base_url: bool = False,
    ) -> None:
        self.session = session or requests.Session()
        self.auth_path = auth_path
        self.base_url = _validate_base_url(base_url, allow_untrusted=allow_untrusted_base_url)
        self.timeout = timeout
        self.allow_file_auth = allow_file_auth

    def fetch(self) -> UsageSnapshot:
        if not self.allow_file_auth:
            raise UsageError("legacy UsageClient 必須明確啟用檔案認證；請改用安全的 provider")
        token, account_id = load_access_credentials(self.auth_path)
        headers = {
            "Authorization": f"Bearer {token}",
            "ChatGPT-Account-Id": account_id,
            "OAI-Product-Sku": "codex",
            "User-Agent": "codex-tray/0.1",
            "Accept": "application/json",
        }

        try:
            response = self.session.get(
                _usage_endpoint(self.base_url),
                headers=headers,
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise UsageError("無法連線到 Codex usage API，請檢查網路連線") from exc

        status_code = getattr(response, "status_code", None)
        if status_code in (401, 403):
            raise UsageError("Codex 認證已過期或無效，請先在 Codex 重新登入")
        if isinstance(status_code, int) and 300 <= status_code < 400:
            raise UsageError("Codex usage API 的重新導向已被拒絕")
        if status_code != 200:
            raise UsageError(f"Codex usage API 回傳 HTTP {status_code}")

        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise UsageError("Codex usage API 回傳了無法解析的資料") from exc
        return parse_usage_payload(payload)
