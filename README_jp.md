# Codex Balance Tray

[English README](README.md) ｜ [繁體中文](README_cht.md) ｜ [简体中文](README_chs.md) ｜ 日本語

Windows の通知領域／macOS のメニューバーで、Codex ChatGPT プランの主要な使用量ウィンドウについて、残量の割合、リセット情報、安全状態を表示するユーティリティです。

> このリポジトリには公開可能なソースコード、オフラインテスト fixture、ドキュメントのみを含めます。Codex の認証情報、raw trace、研究引用キャッシュ、モデルファイル、build 生成物は含みません。

## 機能

- 通知領域アイコンに主要な使用量ウィンドウの残量割合を表示します。古いデータには `*` を付けます。
- 残量が 50% 超なら緑、21–50% なら黄、20% 以下なら赤、未知または利用できない場合は灰色で表示します。
- Tooltip にプラン、使用率、リセットまでの時間、Credits 残高、取得時刻、安全なエラー状態を表示します。
- 観測時間とウィンドウ情報が十分な場合、1 日あたりの平均使用量、使い切るまでの推定時間、リセット時の推定残量、リスク判定を線形推定で表示します。
- 初期言語は English です。通知領域の右クリックメニューから English／繁體中文／简体中文／日本語を選択でき、選択した言語は次回起動時にも維持されます。
- 右クリックメニューから今すぐ更新、更新間隔（1／5／15／30 分）、言語、詳細表示、終了を操作できます。
- 欠落または不正な値を `0%` や `100%` と推測しません。最後に成功したデータがある場合、エラー中も保持し、古いデータであることを明示します。

## データソースと認証境界

アプリケーションは既定で公式の `codex app-server --listen stdio://` を通じて `BalanceResult` を取得します。OAuth token refresh、`CODEX_HOME`、プラットフォームの credential store／keyring の処理は Codex CLI が担当します。通知領域アプリケーションが Credential Manager を直接読み取ったり復号したりすることはなく、独自の設定ファイルやログに token を保存しません。

初回使用前に、コマンドプロンプトで公式ログインを完了してください。

```text
codex login
```

プライベートな HTTP 互換ソースを使う場合は、呼び出し側が明示的に `HttpBalanceClient` と安全な credential provider を指定する必要があります。既定では HTTPS のみ、公式 host のみを許可し、自動 redirect を拒否します。プライベート host には `allow_untrusted_base_url=True`、内部 HTTP endpoint にはさらに `allow_insecure_http=True` が必要です。ChatGPT の base URL は `/wham/usage`、ChatGPT 以外の base URL は `/api/codex/usage` を使用します。これらの endpoint は公開された安定 API ではないため、未知のパスを無条件に試しません。

## クイックスタート

必要なもの：

- Windows 10 以降、または macOS
- Python 3.11（オフラインテストで検証したバージョン）
- 公式 Codex CLI（通知領域アプリケーションを実際に起動する場合のみ必要）
- `requirements.txt` からインストールできる `requests`、`Pillow`、`pystray`

依存関係をインストールして起動します。

```bash
python -m pip install -r requirements.txt
python main.py
```

`codex` が見つからない場合は、アプリケーションがクラッシュせず `CLI_NOT_FOUND`／利用不可状態を表示します。公式 Codex CLI をインストールし、新しいコマンドプロンプトで `codex` を実行できることを確認してから、必要に応じて `codex login` を実行してください。

Windows では現在のユーザーの
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run` に起動コマンドを best-effort で登録します。macOS ではユーザー単位の LaunchAgent：
`~/Library/LaunchAgents/com.flipaishow.codexbalancetray.plist` に保存します。どちらも管理者権限は不要です。

言語設定には locale 名だけを保存し、token や使用量データは保存しません。Windows の既定の保存先は
`%APPDATA%\CodexBalanceTray\settings.json`、macOS は
`~/Library/Application Support/CodexBalanceTray/settings.json` です。新規インストール時、または設定ファイルが無効な場合は English を使用します。

## テスト

テストは app-server JSONL と HTTP 応答をシミュレートするため、ローカルログインや実ネットワークは不要です。

```bash
python -m unittest discover -s tests -v
python -m compileall -q codex_tray tests main.py
```

fixture は `tests/fixtures/` にあり、成功応答、未ログイン、API key、schema 変更、HTTP エラー、脱敏をカバーします。テストはユーザーの Codex 認証情報を読み取らず、実ネットワークにも接続しません。

## Windows EXE の作成

PyInstaller をインストールし、バージョン管理されている spec を使用します。

```bash
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean CodexBalanceTray.spec
```

生成物は `dist/CodexBalanceTray.exe` に出力されます。`build` と `dist` は `.gitignore` に含まれているため、リポジトリへコミットしないでください。

## macOS App の作成

macOS のネイティブ生成物は Mac または macOS CI runner 上で作成してください。Windows 上の PyInstaller から macOS App をクロスビルドすることはできません。

```bash
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean --windowed --name CodexBalanceTray main.py
```

ウィンドウ表示の生成物は通常 `dist/CodexBalanceTray.app` に出力されます。`requirements.txt` から macOS の `pystray` backend に必要な Cocoa／Quartz 依存関係もインストールされます。

## プロジェクト構成

```text
codex_tray/                  provider、データモデル、監視、通知領域 UI
codex_tray/i18n.py           English 既定、繁體中文／简体中文／日本語翻訳、言語設定保存
codex_tray/balance.py        app-server／HTTP provider と安全なエラー分類
codex_tray/forecast.py       保守的な使用量消尽の線形推定
tests/                       オフライン単体テストと provider contract テスト
tests/fixtures/              秘密を含まない JSON／JSONL テストデータ
CODEX_USAGE_INTERFACE.md     データソース、インターフェース、安全境界のノート
CodexBalanceTray.spec        Windows EXE ビルド設定
```

## 更新と既知の制限

- 既定の更新間隔は 5 分です。起動直後にもバックグラウンドで 1 回更新します。手動更新と定期更新は single-flight guard を共有し、通知領域のイベントループをブロックしません。
- app-server protocol schema はインストールされている Codex CLI のバージョンにより変わる可能性があります。不互換な応答では「応答形式が変更されました」と表示し、使用量を推測しません。
- API key の billing usage は ChatGPT Codex プランの使用量とは異なります。API key または ChatGPT 以外の認証方式は、不正に割合へ混ぜず未対応として表示します。
- 使い切るまでの推定は現在のウィンドウの使用率に基づく線形推定です。少なくとも 1 時間の観測と完全なウィンドウ情報が揃った場合のみ表示し、Codex サービスによる保証ではありません。
- 現在のテストはオフラインの contract／UI テストです。実アカウントでの end-to-end smoke test が完了したとは主張しません。

データソースとインターフェースの詳細は [`CODEX_USAGE_INTERFACE.md`](CODEX_USAGE_INTERFACE.md) を参照してください。

## セキュリティ報告

報告前に [`SECURITY.md`](SECURITY.md) を読んでください。`auth.json`、`.env`、token、raw HTTP／JSON-RPC trace、内部パス、モデルファイルをコミットしないでください。

## ライセンス

このプロジェクトは [MIT License](LICENSE) で公開されています。
