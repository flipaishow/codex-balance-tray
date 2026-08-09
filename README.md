# Codex Balance Tray

Windows 通知區工具：在通知區圖示顯示 Codex ChatGPT 方案主要額度視窗的剩餘百分比、重置資訊與安全狀態。

> 這個儲存庫只包含可公開的原始碼、離線測試 fixture 與文件；不包含 Codex 憑證、原始 trace、研究引用快取、模型檔案或 build 產物。

## 功能

- 圖示文字顯示主要額度視窗剩餘百分比；資料過期時加上 `*`。
- 剩餘比例超過 50% 顯示綠色、21–50% 顯示黃色、20% 以下顯示紅色；未知或不可用時顯示灰色。
- Tooltip 顯示方案、使用率、重置倒數／時間、Credits 餘額、資料取得時間與安全錯誤狀態。
- 觀察時間與視窗資料足夠時，另外顯示線性估算的平均每日消耗、預估耗盡時間、重置前預估剩餘與風險判斷。
- 介面預設使用英文；右鍵選單可切換 English／繁體中文，選擇會保存到使用者設定並在下次啟動時沿用。
- 右鍵選單支援立即重新整理、更新間隔（1／5／15／30 分鐘）、語言、詳細資訊與結束。
- 缺少或格式錯誤的數值不會被猜成 `0%` 或 `100%`；已有上次成功資料時，錯誤期間會保留並明確標示為過期。

## 資料來源與認證邊界

程式預設透過官方 `codex app-server --listen stdio://` 取得 `BalanceResult`。Codex CLI 負責 OAuth token refresh、`CODEX_HOME` 與 Windows Credential Manager／keyring；通知區程式不直接讀取或解密 Credential Manager，也不會把 token 寫入自己的設定檔或日誌。

第一次使用前，先在命令列完成官方登入：

```text
codex login
```

若要使用私有 HTTP 相容性來源，必須由呼叫端明確注入 `HttpBalanceClient` 並提供安全的 credential provider。預設只接受 HTTPS、只允許官方 host 且拒絕自動 redirect；私有 host 必須明確設定 `allow_untrusted_base_url=True`，內部 HTTP endpoint 還必須設定 `allow_insecure_http=True`。ChatGPT base URL 使用 `/wham/usage`，非 ChatGPT base URL 才使用 `/api/codex/usage`。這些 endpoint 不是公開穩定 API，預設不會盲目嘗試未知路徑。

## 快速開始

需求：

- Windows 10 或更新版本；
- Python 3.11（目前離線測試驗證版本）；
- 官方 Codex CLI（只有實際啟動通知區程式時才需要）；
- `requests`、`Pillow`、`pystray`，可由 `requirements.txt` 安裝。

安裝相依套件並啟動：

```bash
python -m pip install -r requirements.txt
python main.py
```

若找不到 `codex`，程式會安全顯示 `CLI_NOT_FOUND`／不可用狀態，不會因為找不到 CLI 而崩潰。請安裝官方 Codex CLI、確認新的命令提示字元能執行 `codex`，再依需要執行 `codex login`。

第一次啟動時，程式會 best-effort 將目前的啟動命令寫入目前 Windows 使用者的
`HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`，讓通知區圖示在登入 Windows 時自動啟動。這是使用者自己的設定，不需要系統管理員權限；可從 Windows 的「工作管理員 → 啟動」停用 `CodexBalanceTray`。

語言偏好只保存 locale 名稱，不保存 token 或額度資料；Windows 預設位置是
`%APPDATA%\\CodexBalanceTray\\settings.json`。新安裝或無有效設定檔時預設為 English。

## 測試

測試使用模擬的 app-server JSONL 與 HTTP 回應，不需要本機登入或真實網路：

```bash
python -m unittest discover -s tests -v
python -m compileall -q codex_tray tests main.py
```

fixtures 位於 `tests/fixtures/`，涵蓋成功回應、未登入、API key、schema 變更、HTTP 錯誤與脫敏。測試不會讀取使用者的 Codex 憑證，也不會發送真實網路請求。

## 建立 Windows EXE

先安裝 PyInstaller，再使用版本控制中的 spec：

```bash
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean CodexBalanceTray.spec
```

產物會放在 `dist/CodexBalanceTray.exe`；build 與 dist 目錄已列入 `.gitignore`，不應提交到儲存庫。

## 專案結構

```text
codex_tray/                 核心 provider、資料模型、監控器與通知區 UI
codex_tray/i18n.py         英文預設、繁體中文翻譯與語言偏好保存
codex_tray/balance.py       app-server／HTTP provider 與安全錯誤分類
codex_tray/forecast.py      保守的額度耗盡線性估算
tests/                      離線單元與 provider contract 測試
tests/fixtures/             不含秘密的 JSON／JSONL 假資料
CODEX_USAGE_INTERFACE.md    資料來源、介面與安全邊界研究筆記
CodexBalanceTray.spec       Windows EXE 建置設定
```

## 更新與已知限制

- 預設每 5 分鐘重新整理一次，啟動後立即在背景取得一次；手動與排程刷新共用 single-flight guard，不會阻塞通知區事件迴圈。
- app-server protocol schema 會隨安裝的 Codex CLI 版本變化；格式不相容時顯示「回應格式已變更」，不會猜測額度。
- API key billing usage 不等同 ChatGPT Codex 方案額度；API key 或其他非 ChatGPT auth mode 會顯示不支援，而不是混用百分比。
- 耗盡預估是依目前視窗已使用百分比做線性估算；至少觀察一小時且回應包含完整視窗資料後才顯示，不能視為 Codex 服務保證的預測。
- 目前測試是離線 contract／UI 測試；不宣稱已完成真實帳號的 end-to-end smoke test。

更完整的來源與介面說明請見 [`CODEX_USAGE_INTERFACE.md`](CODEX_USAGE_INTERFACE.md)。

## 安全回報

請先閱讀 [`SECURITY.md`](SECURITY.md)。不要提交 `auth.json`、`.env`、token、原始 HTTP／JSON-RPC trace、內部路徑或模型檔案。

## 授權

本專案採用 [MIT License](LICENSE)。
