# Codex 額度來源、登入方式與資料介面規格

查證時間：2026-08-05（UTC）

本文件是 `Codex Balance Tray` 的實作依據，目標是讓 Windows 通知區工具可靠地顯示 ChatGPT Codex 方案的 rate-limit 剩餘百分比，同時不自行管理或暴露 OAuth/API 憑證。

## 1. 結論與採用順序

### 1.1 P0：以官方 `codex app-server` 作為主要資料來源

官方 app-server 提供可供程式使用的 JSON-RPC 方法 `account/rateLimits/read`，用途是取得 ChatGPT rate limits、可用的月度 credit limit、spend-control 狀態與 earned reset credits；`account/usage/read` 則是 token activity 摘要，不是 quota 剩餘百分比。[4]

在 Windows 上，tray 應啟動本機 Codex CLI 的 `codex app-server --listen stdio://`，讓官方 Codex 自己負責 OAuth token refresh、`CODEX_HOME`、檔案儲存與作業系統 credential store。這避免 Python 程式直接讀取 Windows credential store 或複製 Codex 的登入邏輯。ChatGPT token 在使用期間會由 Codex 自動 refresh。[1]

app-server 的 stdio transport 是換行分隔 JSON（JSONL）的雙向 JSON-RPC 2.0；線上訊息省略 `jsonrpc` 欄位。每個連線必須先完成一次 `initialize` request 和 `initialized` notification，才能送其他 request。[4]

### 1.2 P1：直接 HTTP 僅作相容性 fallback

目前 upstream Codex backend client 對 ChatGPT base URL（包含 `/backend-api`）組成 `GET {base}/wham/usage`；非 ChatGPT base URL 則組成 `GET {base}/api/codex/usage`。[7] 本專案的 `codex_tray/client.py` 已依此規則選擇 endpoint；舊版 `/codex/usage` 不應作為 ChatGPT path 的候選。

這些 backend path 在查到的官方文件中沒有被列為公開、長期穩定的第三方 API；它們是 Codex CLI 的內部實作細節。因此 direct HTTP 必須是可停用的 fallback，不能取代 app-server，也不能無限嘗試猜測新 endpoint。若 upstream schema 或 path 改變，應回報 `schema_changed`／`unsupported_route`，而不是把錯誤資料顯示成 0% 或 100%。

### 1.3 不把 API key 使用量與 ChatGPT 方案額度混用

官方登入文件將 ChatGPT subscription access 與 API-key usage-based access 分開；API key 使用量遵循 API organization 的政策，而不是 ChatGPT workspace 的 rate-limit snapshot。[1][3] app-server source 也會拒絕以非 Codex backend 的 authentication 讀取 ChatGPT rate limits，並回報需要 ChatGPT authentication。[9]

因此 `apiKey`、Amazon Bedrock 或其他非 ChatGPT auth mode 應顯示「目前登入方式沒有 ChatGPT Codex 額度資料」，不可把 OpenAI API billing usage 當成 ChatGPT Codex 剩餘百分比。

## 2. 來源／介面選擇表

| 來源 | 認證由誰管理 | 可取得資料 | 穩定性與用途 |
|---|---|---|---|
| `codex app-server` + `account/rateLimits/read` | 官方 Codex CLI；支援 ChatGPT OAuth、keyring/file/auto | primary/secondary windows、used percent、reset、plan、credits、spend control、reset credits | P0；官方程式整合面，應以執行中的 CLI 版本 schema 為準 |
| `codex app-server` + `account/usage/read` | 同上 | lifetime token、daily buckets、streak 等 activity | P1 optional；不能用來計算 quota 剩餘 |
| ChatGPT backend `GET .../wham/usage` | 應由官方 Codex auth provider 提供 headers | snake_case rate-limit payload | P1 fallback；內部 endpoint，需 feature flag 與版本檢查 |
| Codex API `GET .../api/codex/usage` | 同上 | 同類 payload | 只在非 `/backend-api` base URL 使用；不可與 ChatGPT path 混用 |
| `codex login`／`codex login status` | 官方 CLI | 登入與登入狀態 | 用於登入／診斷，不作 tray 的 quota response parser |
| API key／OpenAI Platform usage | OpenAI API credential | API organization 的 usage/billing | 與 ChatGPT Codex plan quota 不同；本工具不納入同一個百分比 |

## 3. Windows 認證與憑證邊界

### 3.1 建議登入流程

1. 使用者先執行 `codex login`，完成瀏覽器登入；這是官方 CLI 在沒有有效 session 時的預設 ChatGPT 登入路徑。[1]
2. 若環境沒有可用瀏覽器，使用者依官方 CLI 提供的 device-code 流程登入；tray 不自行模擬 OAuth callback。
3. tray 啟動 app-server，呼叫 `account/read` 做狀態檢查，再呼叫 `account/rateLimits/read`。
4. token refresh、workspace/account selection 與登入資料儲存全部交給 Codex。

### 3.2 `CODEX_HOME`、file、keyring、auto

官方文件指定登入快取可以放在 `~/.codex/auth.json` 或 OS-specific credential store；`CODEX_HOME` 可改變 `auth.json` 所在位置。[1]

可由使用者在 Codex 的 `config.toml` 選擇：

```toml
# file | keyring | auto
cli_auth_credentials_store = "keyring"
```

- `file`：在 `CODEX_HOME` 下儲存 `auth.json`。
- `keyring`：使用作業系統 credential store。
- `auto`：有 OS credential store 時使用它，否則回退到 `auth.json`。[1]

官方文件只承諾「OS-specific credential store」，沒有要求第三方程式自行建立或查詢某個 Windows service/account 名稱；設定參考也把 `cli_auth_credentials_store` 定義為控制 cached credentials 儲存位置的設定。[2] upstream Codex 的 keyring wrapper 透過 Rust `keyring::Entry` 操作 service/account，而不是在應用程式中硬編碼 Windows credential database 格式。[10] 因此本專案的 Windows Credential Manager 策略是：

- 首選透過官方 app-server 取用，讓 Codex 使用正確的 Windows keyring backend。
- 不要用 Python `requests`、Registry 或自訂 service name 直接列舉／解密 Credential Manager。
- 不要在 keyring 不可用時無聲地改讀 `auth.json`；只有使用者明確選擇 `file`，或 Codex 的 `auto` 已由官方 CLI 處理，才可走檔案模式。
- `auth.json` 內的 token 欄位是秘密；官方 source 的 token model 包含 `access_token`、`refresh_token` 與 `account_id`。[11] 這些值不可寫入 log、例外訊息、通知 tooltip、測試輸出、命令列參數或 Kanban comment。

### 3.3 direct HTTP 的最低安全要求

若 P1 fallback 必須讀 file mode：

- 路徑優先使用 `CODEX_HOME`，沒有設定時才使用使用者 home 下的 `.codex/auth.json`。
- 只在記憶體中短暫使用 access token；不要保存 refresh token，也不要把完整 `auth.json` 複製到 tray 設定檔。
- `subprocess` 不可使用 `shell=True`，不可把 token 放進命令列或環境變數之外的可見參數；app-server 由 Codex 自己讀 credential store。
- 所有 exception 和 debug log 先遮罩 `Authorization`、JWT（三段以 `.` 分隔）、`access_token`、`refresh_token`、`api_key`、`account_id` 的值。
- HTTP error body 不可原樣回傳給 UI 或寫 log；只保留安全的分類、HTTP status、Retry-After 秒數與本地 request id。
- TLS 憑證驗證必須保持開啟，不接受 `verify=False`。

## 4. app-server JSON-RPC 介面

### 4.1 啟動與 handshake

Windows 實作建議：

```text
executable = shutil.which("codex")
argv = [executable, "app-server", "--listen", "stdio://"]
```

實際執行時以 `subprocess.Popen` 的 argv list 啟動，不使用 shell；stdout 僅保留 JSONL protocol，stderr 由獨立 reader drain。若 PATH 找不到 `codex`，進入 `cli_unavailable`，不要改以猜測安裝路徑掃描整個磁碟。

傳送：

```json
{"method":"initialize","id":1,"params":{"clientInfo":{"name":"codex_balance_tray","title":"Codex Balance Tray","version":"0.1.0"}}}
{"method":"initialized","params":{}}
```

接著先查登入狀態：

```json
{"method":"account/read","id":2,"params":{"refreshToken":false}}
```

再查額度：

```json
{"method":"account/rateLimits/read","id":3}
```

`initialize` 只能對同一 connection 呼叫一次；request id 必須由 client 追蹤，並忽略沒有 `id` 的 server notifications。app-server 版本更新時可由該版本產生 JSON Schema；官方 README 明確指出產生的 schema 與執行它的 Codex 版本相符，所以不要把另一個版本的 schema 永久硬編碼成唯一契約。[4]

### 4.2 `account/read` 狀態判定

可將下列欄位轉為內部狀態：

```json
{
  "id": 2,
  "result": {
    "account": {
      "type": "chatgpt",
      "email": "[REDACTED]",
      "planType": "plus"
    },
    "requiresOpenaiAuth": true
  }
}
```

安全規則：

- `account.type == "chatgpt"`：可嘗試 `account/rateLimits/read`。
- `account.type == "apiKey"` 或 `requiresOpenaiAuth == false`：不可假設有 ChatGPT quota；若 rate-limit request 失敗，分類為 `unsupported_auth`。
- `account == null`：分類為 `auth_required`。
- email 只供本地 UI 顯示（若產品確實需要），不得寫入診斷 log 或跨程序傳送。

### 4.3 `account/rateLimits/read` 成功回應

官方 v2 schema 的 response 必填 `rateLimits`，另有可為 null 的 `rateLimitsByLimitId` 與 `rateLimitResetCredits`；`rateLimits` 是向後相容的單一 bucket view，`rateLimitsByLimitId` 可用來處理多個 metered limit。[5]

最小成功 fixture：

```json
{
  "id": 3,
  "result": {
    "rateLimits": {
      "limitId": "codex",
      "limitName": null,
      "planType": "plus",
      "primary": {
        "usedPercent": 37,
        "windowDurationMins": 10080,
        "resetsAt": 1786160194
      },
      "secondary": null,
      "rateLimitReachedType": null,
      "individualLimit": null,
      "credits": {
        "hasCredits": false,
        "unlimited": false,
        "balance": "0"
      },
      "spendControlReached": null
    },
    "rateLimitsByLimitId": {
      "codex": {
        "limitId": "codex",
        "primary": {
          "usedPercent": 37,
          "windowDurationMins": 10080,
          "resetsAt": 1786160194
        },
        "secondary": null,
        "rateLimitReachedType": null
      }
    },
    "rateLimitResetCredits": null
  }
}
```

轉換規則：

- 主要顯示 bucket：先找 `rateLimitsByLimitId.codex`；沒有時使用 `rateLimits`。不要依 JSON object 順序猜 bucket。
- `usedPercent` 是 quota window 中的已使用百分比；`remainingPercent = 100 - usedPercent`。官方文件將 `usedPercent` 定義為目前 quota window 使用量，`resetsAt` 是 Unix seconds 的下一次重置時間。[4]
- 只有在 `usedPercent` 是數字且落在 0–100 時才計算剩餘百分比；缺值、null、NaN、非整數或超界都轉為 `None` 並記錄 `schema_changed`，不可顯示假精確值。
- `windowDurationMins` 轉成秒供既有 UI 使用；`resetsAt` 直接保留 Unix seconds。倒數顯示用 `max(0, resetsAt - now)`，但不要改寫 server timestamp。
- `secondary == null` 是正常情況，顯示時可省略 secondary；primary 缺失則整體 quota 狀態不可用。
- `credits.balance` 保留字串，不轉成浮點數也不把 credits balance 假算成百分比；`unlimited` 與 `hasCredits` 是獨立布林欄位。
- `rateLimitResetCredits.availableCount` 是可用 reset 的總數；詳細 `credits` 可能是 null 或被 backend cap，不能以 detail array 長度取代 count。[4][5]

### 4.4 `account/usage/read`（可選）

這個方法的 response 是 `summary` 加上可為 null 的 `dailyUsageBuckets`；summary 可包含 `lifetimeTokens`、`peakDailyTokens`、`longestRunningTurnSec`、streak 等欄位。[6] 這是活動統計，不能代替 `account/rateLimits/read` 的 primary window。

## 5. direct HTTP fallback 介面

### 5.1 URL path style

```text
ChatGPT base URL: https://chatgpt.com/backend-api
GET https://chatgpt.com/backend-api/wham/usage

Codex API base URL（非 /backend-api）:
GET {base_url}/api/codex/usage
```

upstream client 的 path style 是依 base URL 是否包含 `/backend-api` 判定，且會將常見 ChatGPT hostname 正規化為 `/backend-api`。[7][8] 本專案目前的 `/backend-api/codex/usage` 應視為舊實作，不應繼續作為第一候選。

### 5.2 HTTP request

只允許由官方 Codex auth manager 或明確 opt-in 的 file mode 提供 credential。request 的安全形狀如下，`[REDACTED]` 僅表示記憶體中的值，絕不可把 literal placeholder 寫入 production request：

```http
GET /backend-api/wham/usage HTTP/1.1
Host: chatgpt.com
Authorization: Bearer [REDACTED]
ChatGPT-Account-Id: [REDACTED]
Accept: application/json
User-Agent: codex-tray/0.1
```

不要把 `Authorization` 或 account id 放入 URL query；不要在錯誤訊息中顯示 request headers。`OAI-Product-Sku` 等未被本文件列為公開穩定契約的 header 只能由經過版本驗證的 adapter 使用，不能把它當成長期 API 保證。

### 5.3 ChatGPT backend snake_case payload

upstream backend model 目前描述的外層欄位包含 `plan_type`、`rate_limit`、`credits`、`spend_control`、`additional_rate_limits` 與 `rate_limit_reached_type`；rate limit 內有 `allowed`、`limit_reached`、`primary_window`／`secondary_window`；window 使用 `used_percent`、`limit_window_seconds`、`reset_after_seconds`、`reset_at`。[12][13][14]

其中 credits model 目前包含 `has_credits`、`unlimited` 與字串型 `balance`，因此 adapter 應保留原始字串精度。[15]

相容性 fixture：

```json
{
  "plan_type": "plus",
  "rate_limit": {
    "allowed": true,
    "limit_reached": false,
    "primary_window": {
      "used_percent": 37,
      "limit_window_seconds": 604800,
      "reset_after_seconds": 271722,
      "reset_at": 1786160194
    },
    "secondary_window": null
  },
  "credits": {
    "has_credits": false,
    "unlimited": false,
    "balance": "0"
  }
}
```

snake_case adapter 與 app-server adapter 必須輸出相同的內部 `CodexQuotaSnapshot`，UI 不得知道 upstream 使用哪種命名。

## 6. 建議的內部資料介面

不要讓 presentation layer 直接解析 upstream JSON。建議將 `codex_tray/usage.py` 的 `UsageSnapshot` 擴充或以新的不可變 dataclass 包裝，概念介面如下：

```python
@dataclass(frozen=True)
class QuotaWindow:
    kind: Literal["primary", "secondary"]
    used_percent: int | None
    remaining_percent: int | None
    window_seconds: int | None
    resets_at: int | None

@dataclass(frozen=True)
class CodexQuotaSnapshot:
    status: Literal[
        "ok", "stale", "auth_required", "unsupported_auth",
        "cli_unavailable", "network_error", "rate_limited",
        "unsupported_route", "schema_changed", "service_error",
    ]
    source: Literal["app_server", "chatgpt_backend", "none"]
    retrieved_at: str | None
    last_success_at: str | None
    plan_type: str | None
    primary: QuotaWindow | None
    secondary: QuotaWindow | None
    credits_balance: str | None
    has_credits: bool | None
    unlimited: bool | None
    rate_limit_reached_type: str | None
    spend_control_reached: bool | None
    token_usage: Mapping[str, object] | None
    retry_after_seconds: int | None
    error_code: str | None
```

欄位規則：

- 所有未知／缺失 upstream 欄位都用 `None`，不要用 0 代替。
- `status == "ok"` 且 `primary.used_percent` 有效時才顯示百分比。
- `status == "stale"` 時可顯示 last-good 百分比，但 UI 必須明確標示「資料過期」與 `last_success_at`。
- `source` 是診斷欄位，不要顯示 access token、完整 URL query 或 raw error body。
- `error_code` 只能是固定 allow-list，例如 `AUTH_REQUIRED`、`CLI_NOT_FOUND`、`HTTP_401`、`HTTP_429`、`TIMEOUT`、`SCHEMA_CHANGED`；不可直接使用第三方回傳的任意字串作為 log key。

## 7. 錯誤分類與重試

| 情況 | 內部狀態 | 是否保留 last-good | 重試策略 | UI |
|---|---|---:|---|---|
| 找不到 `codex` | `cli_unavailable` | 否／若有 cache 則 stale | 不要掃磁碟；手動刷新或重新安裝 CLI 後再試 | `--`，提示安裝或設定 Codex CLI |
| app-server 無 auth | `auth_required` | 是，標成 stale | 不自動密集重試；使用者重新登入後手動刷新 | `--` 或 stale，提示「請先在 Codex 登入」 |
| `apiKey`／非 Codex backend | `unsupported_auth` | 是，標成 stale | 不重試 | 顯示「API key 模式沒有 ChatGPT 額度資料」 |
| HTTP 401／403 | `auth_required` | 是，標成 stale | 不循環重試；重新登入後再試 | 不顯示 response body |
| HTTP 404／405 | `unsupported_route` | 是，標成 stale | 不盲試更多路徑；切回 app-server 或停用 fallback | 顯示「Codex 版本／endpoint 不相容」 |
| HTTP 429 | `rate_limited` | 是，標成 stale | 優先遵循 `Retry-After`；設上限並加入 jitter | 顯示上次成功資料與下次可重試時間 |
| timeout、DNS、TLS、connection reset | `network_error` | 是，標成 stale | exponential backoff：60、120、300、600 秒上限，加 jitter | 顯示「網路暫時無法取得」 |
| HTTP 5xx／app-server overloaded | `service_error` | 是，標成 stale | exponential backoff；不可每秒輪詢 | 顯示 stale 狀態 |
| HTTP 200 但 JSON 非 object | `schema_changed` | 是，標成 stale | 停止本輪重試；等待下一個正常週期或手動刷新 | 顯示「Codex 回應格式已變更」 |
| HTTP 200 但 primary/window 缺失 | `schema_changed` 或 `unavailable` | 是，標成 stale | 不把缺失解讀成 0%／100% | 顯示「目前沒有可用額度資料」 |
| app-server 回報 no snapshots | `service_error` | 是，標成 stale | 等待下一個週期 | 顯示「目前沒有可用額度資料」 |

每次 refresh 必須 single-flight；同時只能有一個 app-server request 或 HTTP request。預設啟動立即刷新，成功後每 300 秒刷新一次；手動刷新不得繞過 single-flight，也不應在 60 秒內重複打相同 endpoint。這是本工具的保守 polling policy，不是 upstream 公布的 rate limit。

## 8. 成功與失敗回應範例

### 8.1 app-server 成功

```json
{"id":3,"result":{"rateLimits":{"limitId":"codex","planType":"plus","primary":{"usedPercent":37,"windowDurationMins":10080,"resetsAt":1786160194},"secondary":null,"rateLimitReachedType":null},"rateLimitsByLimitId":{"codex":{"limitId":"codex","primary":{"usedPercent":37,"windowDurationMins":10080,"resetsAt":1786160194}}},"rateLimitResetCredits":null}}
```

轉換成 UI 內部資料時，primary 顯示 `63%`，並以 `resetsAt` 倒數；若 `resetsAt` 缺失，只顯示百分比，不自行猜測 reset 時間。

### 8.2 app-server 未登入／auth mode 不相容

upstream account processor 對缺少 account auth 與非 Codex backend 分別使用下列錯誤訊息：[9]

```json
{"id":3,"error":{"code":-32600,"message":"codex account authentication required to read rate limits"}}
{"id":3,"error":{"code":-32600,"message":"chatgpt authentication required to read rate limits"}}
```

實作不應依賴固定 numeric code；至少比對安全化後的 message category，並將它轉為 `auth_required` 或 `unsupported_auth`。

### 8.3 HTTP 認證失敗

```text
HTTP/1.1 401 Unauthorized
Content-Type: application/json

{"detail":"Bearer [REDACTED] is invalid"}
```

內部結果應是：

```json
{
  "status": "auth_required",
  "source": "chatgpt_backend",
  "error_code": "HTTP_401",
  "retry_after_seconds": null,
  "last_success_at": "2026-08-05T05:55:00Z"
}
```

`detail` 不得原樣進入 tooltip、exception 或 log；上例中的 `[REDACTED]` 只代表脫敏後的測試資料。

### 8.4 HTTP 429

```text
HTTP/1.1 429 Too Many Requests
Retry-After: 120

{"error":"rate limited"}
```

轉換為 `status = "rate_limited"`、`retry_after_seconds = 120`，保留 last-good snapshot 並標示 stale；不要在 120 秒內自動重試。

### 8.5 格式變更或沒有 quota window

```json
{"plan_type":"plus","rate_limit":null,"credits":null}
```

這不是「剩餘 100%」。結果應為 `status = "schema_changed"` 或 `unavailable`、`primary = null`；若有 last-good，顯示 stale，否則顯示不可用狀態。

## 9. 無法取得額度時的通知區 UI

| 狀態 | 圖示標題 | Tooltip 必要資訊 |
|---|---|---|
| `ok` | `63%` | 方案、已使用 37%、主要視窗、reset 倒數、credits（若有） |
| `stale` | `63%*` 或 `--` | `資料過期`、上次成功時間、原因；不可讓使用者誤以為是即時值 |
| `auth_required` | `--` | `Codex 尚未登入或登入已失效；請執行 `codex login` 後重新整理 |
| `unsupported_auth` | `--` | 目前是 API key／非 ChatGPT auth，沒有 ChatGPT Codex 額度 snapshot |
| `cli_unavailable` | `--` | 找不到 `codex` CLI；請安裝或設定 PATH |
| `network_error` | `--` 或 last-good* | 網路暫時不可用；顯示下次自動重試時間 |
| `rate_limited` | last-good* | 已被服務端限流；顯示 `Retry-After` 對應的重試時間 |
| `schema_changed`／`service_error` | `--` 或 last-good* | Codex 回應暫時無法解析；不要顯示 0%／100% |

UI 內的 `credits.balance`、plan name、account email 都不可當作 rate-limit 百分比；其中 email 如需顯示也要保持本地，不落入 log。

## 10. 實作驗收清單

### app-server adapter

- [ ] Windows 以 argv list 啟動 `codex app-server --listen stdio://`，不使用 `shell=True`。
- [ ] stdout JSONL 與 stderr 分流；bounded line size，避免單行異常資料耗盡記憶體。
- [ ] 完成 `initialize` → `initialized` 後才送 `account/read`／`account/rateLimits/read`。
- [ ] 以 request id 配對 response，忽略未知 notification，不因 `account/rateLimits/updated` 導致重複 request storm。
- [ ] 正確分類 account null、apiKey、ChatGPT auth 與 server error。
- [ ] 進程退出、timeout、broken pipe 時回收 child process，不留下背景 Codex。

### direct HTTP adapter

- [ ] `/backend-api` base URL 使用 `/wham/usage`，非 `/backend-api` base URL 才使用 `/api/codex/usage`。
- [ ] fixture 覆蓋 primary、secondary null、credits null、`rateLimitsByLimitId` 與 missing fields。
- [ ] 401／403／404／405／429／5xx／timeout 都不回傳 raw body。
- [ ] 測試確認任何 token、JWT、account id 都不會出現在 exception、log、UI 或測試失敗訊息。
- [ ] schema 失敗時不會把缺失值轉成 0% 或 100%。
- [ ] direct HTTP fallback 可由設定停用，且不會盲目嘗試未知 endpoint。

### UI／更新器

- [ ] 首次無成功資料時顯示明確 unavailable/auth 狀態，而非空白或 100%。
- [ ] last-good 資料一定有 stale 標記與上次成功時間。
- [ ] 成功 polling 預設 300 秒；single-flight；錯誤採 exponential backoff 與 jitter。
- [ ] `Retry-After` 受合理上限約束，手動刷新也不能並發打 API。
- [ ] tooltip 只顯示安全欄位；不顯示 access token、refresh token、raw HTTP body。

## 11. 已知限制與後續決策

1. `codex app-server` protocol 的 JSON Schema 應以安裝中的 Codex CLI 版本為準；啟動後可記錄 CLI version，但不可把完整環境或 token 寫入 telemetry。[4]
2. direct backend path／payload 可能在 upstream 更新後改變；應保留 feature flag、fixture 與明確 `schema_changed` 狀態，而不是在背景中無限嘗試。
3. 官方文件對 Windows 只抽象描述 OS-specific credential store；本文件不把某個 Credential Manager service/account 名稱視為公共契約。以 app-server 作為 Credential Manager bridge 是較安全且可維護的選擇。[1][10]
4. 本文件未取得真實帳號回應，也沒有要求讀取本機 `auth.json`；所有 response 範例均使用 fixture／`[REDACTED]`，需在具備測試帳號且使用者明確授權的環境做一次 end-to-end smoke test。

## 12. 對現有專案的具體變更建議

- `codex_tray/client.py:45`：不要再把 `/codex/usage` 視為 ChatGPT base URL 的唯一 path；至少改成以 app-server adapter 為 P0，direct fallback 選擇 `/wham/usage`。
- `codex_tray/usage.py`：保留既有 snake_case parser 的相容能力，但增加 app-server camelCase adapter、狀態欄位、stale/error metadata 與 strict window validation。
- `README.md:14-20`：將「直接讀取 `auth.json`」改成「優先由 official Codex app-server 讀取；只有 file mode fallback 才讀取 `auth.json`」，並說明 keyring/auto 不應由 tray 自行解析。
- `README.md:42`：將目前 endpoint 說明改成 P0 app-server、P1 private backend fallback，並標明 `/wham/usage` 是 upstream current path style 而非公開穩定 API。
- `tests/`：新增 app-server JSONL fixture、path style、auth mode、stale/backoff、redaction 測試；不可用真實 token 或真實服務端回應作為單元測試。

## Sources

[1] https://developers.openai.com/codex/auth.md
[2] https://developers.openai.com/codex/config-reference.md
[3] https://learn.chatgpt.com/docs/auth
[4] https://raw.githubusercontent.com/openai/codex/main/codex-rs/app-server/README.md
[5] https://raw.githubusercontent.com/openai/codex/main/codex-rs/app-server-protocol/schema/json/v2/GetAccountRateLimitsResponse.json
[6] https://raw.githubusercontent.com/openai/codex/main/codex-rs/app-server-protocol/schema/json/v2/GetAccountTokenUsageResponse.json
[7] https://raw.githubusercontent.com/openai/codex/main/codex-rs/backend-client/src/client/rate_limit_resets.rs
[8] https://raw.githubusercontent.com/openai/codex/main/codex-rs/backend-client/src/client.rs
[9] https://raw.githubusercontent.com/openai/codex/main/codex-rs/app-server/src/request_processors/account_processor.rs
[10] https://raw.githubusercontent.com/openai/codex/main/codex-rs/keyring-store/src/lib.rs
[11] https://raw.githubusercontent.com/openai/codex/main/codex-rs/login/src/token_data.rs
[12] https://raw.githubusercontent.com/openai/codex/main/codex-rs/codex-backend-openapi-models/src/models/rate_limit_status_payload.rs
[13] https://raw.githubusercontent.com/openai/codex/main/codex-rs/codex-backend-openapi-models/src/models/rate_limit_status_details.rs
[14] https://raw.githubusercontent.com/openai/codex/main/codex-rs/codex-backend-openapi-models/src/models/rate_limit_window_snapshot.rs
[15] https://raw.githubusercontent.com/openai/codex/main/codex-rs/codex-backend-openapi-models/src/models/credit_status_details.rs
