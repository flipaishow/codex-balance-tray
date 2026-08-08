# Security Policy

## Scope

這個專案會顯示 Codex 額度，並可能透過官方 Codex CLI 的 `app-server` 取得資料。安全問題包括憑證外洩、未授權網路請求、將服務端原始錯誤或秘密顯示在通知區，以及會讓使用者誤判額度的資料解析錯誤。

## 憑證邊界

- 預設資料來源是 `codex app-server`；OAuth refresh、`CODEX_HOME` 與 Windows credential store 由官方 Codex CLI 管理。
- 本工具不應把 access token、refresh token、account id、Authorization header 或原始 HTTP error body 寫入設定、日誌、tooltip 或測試輸出。
- 舊版 HTTP fallback 必須由呼叫端明確啟用安全的 credential provider；不要把真實憑證放進 issue、pull request、fixture 或 commit。

## 請勿公開的內容

請不要提交：

- `auth.json`、`.env`、API key、OAuth token 或任何 credential store 匯出內容；
- 原始服務端日誌、未脫敏的 HTTP／JSON-RPC trace、帳號識別資料；
- 本機絕對路徑、內部主機位址、模型權重與 build 產物。

## 回報方式

請不要在公開 issue 張貼秘密或可利用的細節。若儲存庫啟用 GitHub Private Vulnerability Reporting，請優先使用該管道；否則請先聯絡儲存庫維護者，再提供最小化、已脫敏的重現資訊。

提交前請先撤銷已外洩的 token，並清理所有包含秘密的歷史 commit；僅刪除工作樹檔案不足以撤銷已外洩憑證。
