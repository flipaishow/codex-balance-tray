# Codex Balance Tray

A Windows notification-area utility that displays the remaining percentage, reset information, and safe status of the primary quota window for a Codex ChatGPT plan.

[Traditional Chinese](README_cht.md) · [Simplified Chinese](README_chs.md) · [日本語](README_jp.md)

> This repository contains only publishable source code, offline test fixtures, and documentation. It does not contain Codex credentials, raw traces, research citation caches, model files, or build artifacts.

## Features

- Shows the remaining percentage of the primary quota window in the tray icon; stale data is marked with `*`.
- Uses green above 50%, yellow from 21–50%, red at 20% or below, and gray for unknown or unavailable data.
- Shows the plan, usage, reset countdown/time, Credits balance, retrieval time, and safe error status in the tooltip.
- When enough observations and complete window data are available, shows a conservative linear estimate of average daily usage, estimated exhaustion time, projected remaining quota at reset, and risk assessment.
- Uses English by default. The tray context menu can switch among English, Traditional Chinese, Simplified Chinese, and Japanese, and the choice is restored on the next launch.
- The context menu supports refresh now, refresh intervals (1/5/15/30 minutes), language, details, and quit.
- Missing or malformed values are never guessed as `0%` or `100%`; after a successful result, failures retain the value and mark it explicitly as stale.

## Data Sources and Authentication Boundary

The application uses the official `codex app-server --listen stdio://` by default to obtain a `BalanceResult`. The Codex CLI owns OAuth token refresh, `CODEX_HOME`, and Windows Credential Manager/keyring access. The tray application does not directly read or decrypt Credential Manager data and does not write tokens to its own settings or logs.

Before first use, complete the official login from a command prompt:

```text
codex login
```

To use a private HTTP-compatible source, the caller must explicitly inject an `HttpBalanceClient` and provide a secure credential provider. By default, only HTTPS and trusted official hosts are accepted, and automatic redirects are rejected. Private hosts require `allow_untrusted_base_url=True`; internal HTTP endpoints also require `allow_insecure_http=True`. ChatGPT base URLs use `/wham/usage`; non-ChatGPT base URLs use `/api/codex/usage`. These endpoints are not public stable APIs, so the application does not blindly try unknown paths.

## Quick Start

Requirements:

- Windows 10 or later;
- Python 3.11 (the version used for offline test verification);
- the official Codex CLI (required only when actually running the tray application);
- `requests`, `Pillow`, and `pystray`, installable from `requirements.txt`.

Install dependencies and start the application:

```bash
python -m pip install -r requirements.txt
python main.py
```

If `codex` cannot be found, the application safely shows a `CLI_NOT_FOUND`/unavailable state instead of crashing. Install the official Codex CLI, confirm that a new command prompt can run `codex`, and run `codex login` when needed.

On first launch, the application best-effort registers the current startup command in the current Windows user's
`HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run` so the tray icon starts at Windows login. This is a per-user setting and does not require administrator rights; `CodexBalanceTray` can be disabled from Windows Task Manager → Startup.

The language preference stores only a locale identifier, not tokens or quota data. On Windows the default path is
`%APPDATA%\\CodexBalanceTray\\settings.json`. A new installation or invalid settings file defaults to English.

## Tests

Tests use simulated app-server JSONL and HTTP responses; no local login or real network is required:

```bash
python -m unittest discover -s tests -v
python -m compileall -q codex_tray tests main.py
```

Fixtures are under `tests/fixtures/` and cover successful responses, unauthenticated states, API keys, schema changes, HTTP errors, and redaction. Tests do not read the user's Codex credentials or send real network requests.

## Building a Windows EXE

Install PyInstaller and use the version-controlled spec file:

```bash
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean CodexBalanceTray.spec
```

The artifact is written to `dist/CodexBalanceTray.exe`; `build` and `dist` are in `.gitignore` and must not be committed.

## Project Structure

```text
codex_tray/                  provider, data model, monitor, and tray UI
codex_tray/i18n.py           English default, Traditional Chinese, Simplified Chinese, Japanese, and locale persistence
codex_tray/balance.py        app-server/HTTP providers and safe error classification
codex_tray/forecast.py       conservative linear quota-exhaustion estimate
tests/                       offline unit and provider contract tests
tests/fixtures/              secret-free JSON/JSONL test data
CODEX_USAGE_INTERFACE.md     data-source, interface, and security-boundary notes
CodexBalanceTray.spec        Windows EXE build configuration
```

## Updates and Known Limitations

- The default refresh interval is 5 minutes; the application also starts one background refresh immediately. Manual and scheduled refreshes share a single-flight guard and do not block the tray event loop.
- The app-server protocol schema can change with the installed Codex CLI version. Incompatible responses show a schema-change state instead of guessing a quota.
- API-key billing usage is not the same as ChatGPT Codex plan quota. API-key or other non-ChatGPT authentication modes are reported as unsupported rather than mixed into the percentage.
- Exhaustion forecasting is a linear estimate based on the current window's used percentage. It appears only after at least one hour of observation and complete window data; it is not a guarantee from the Codex service.
- Current tests are offline contract/UI tests; the repository does not claim to have completed a real-account end-to-end smoke test.

See [`CODEX_USAGE_INTERFACE.md`](CODEX_USAGE_INTERFACE.md) for more detail about sources and interfaces.

## Security Reports

Read [`SECURITY.md`](SECURITY.md) before reporting a security issue. Do not commit `auth.json`, `.env`, tokens, raw HTTP/JSON-RPC traces, internal paths, or model files.

## License

This project is licensed under the [MIT License](LICENSE).
