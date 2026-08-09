# Codex Balance Tray

[English README](README.md) ｜ [繁體中文](README_cht.md) ｜ 简体中文 ｜ [日本語](README_jp.md)

Windows 通知区域／macOS 菜单栏工具：在通知区域图标或菜单栏显示 Codex ChatGPT 方案主要额度窗口的剩余百分比、重置信息和安全状态。

> 此仓库只包含可公开的源代码、离线测试 fixture 和文档；不包含 Codex 凭证、原始 trace、研究引用缓存、模型文件或 build 产物。

## 功能

- 图标文字显示主要额度窗口剩余百分比；数据过期时加上 `*`。
- 剩余比例超过 50% 显示绿色、21–50% 显示黄色、20% 以下显示红色；未知或不可用时显示灰色。
- Tooltip 显示方案、使用率、重置倒计时／时间、Credits 余额、数据获取时间和安全错误状态。
- 当观察时间和窗口数据足够时，额外显示线性估算的平均每日消耗、预计耗尽时间、重置前预计剩余和风险判断。
- 界面默认使用英文；右键菜单可切换 English／繁體中文／简体中文／日本語，选择会保存到用户设置并在下次启动时沿用。
- 右键菜单支持立即刷新、刷新间隔（1／5／15／30 分钟）、语言、详细信息和退出。
- 缺少或格式错误的数值不会被猜成 `0%` 或 `100%`；已有上次成功数据时，错误期间会保留并明确标示为过期。

## 数据来源与认证边界

程序默认通过官方 `codex app-server --listen stdio://` 获取 `BalanceResult`。Codex CLI 负责 OAuth token refresh、`CODEX_HOME` 以及 平台 credential store／keyring；通知区域程序不会直接读取或解密 Credential Manager，也不会把 token 写入自己的设置文件或日志。

第一次使用前，先在命令行完成官方登录：

```text
codex login
```

如果要使用私有 HTTP 兼容来源，必须由调用方明确注入 `HttpBalanceClient` 并提供安全的 credential provider。默认只接受 HTTPS、只允许官方 host，并拒绝自动 redirect；私有 host 必须明确设置 `allow_untrusted_base_url=True`，内部 HTTP endpoint 还必须设置 `allow_insecure_http=True`。ChatGPT base URL 使用 `/wham/usage`，非 ChatGPT base URL 才使用 `/api/codex/usage`。这些 endpoint 不是公开稳定 API，默认不会盲目尝试未知路径。

## 快速开始

需求：

- Windows 10 或更新版本，或 macOS；
- Python 3.11（目前离线测试验证版本）；
- 官方 Codex CLI（只有实际启动通知区域程序时才需要）；
- `requests`、`Pillow`、`pystray`，可由 `requirements.txt` 安装。

安装依赖并启动：

```bash
python -m pip install -r requirements.txt
python main.py
```

如果找不到 `codex`，程序会安全显示 `CLI_NOT_FOUND`／不可用状态，不会因为找不到 CLI 而崩溃。请安装官方 Codex CLI，确认新的命令提示符可以执行 `codex`，再根据需要执行 `codex login`。

在 Windows，程序会 best-effort 将当前启动命令写入当前用户的
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`；在 macOS，会写入用户专用的 LaunchAgent：
`~/Library/LaunchAgents/com.flipaishow.codexbalancetray.plist`。两者都不需要管理员权限。macOS plist 会写入最小化的 `EnvironmentVariables.PATH`，包含当前 PATH、Homebrew 和常见的用户 CLI 目录。更新现有安装后，请重新加载 LaunchAgent：

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.flipaishow.codexbalancetray.plist" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.flipaishow.codexbalancetray.plist"
launchctl kickstart -k "gui/$(id -u)/com.flipaishow.codexbalancetray"
```

语言偏好只保存 locale 名称，不保存 token 或额度数据；Windows 默认位置是
`%APPDATA%\CodexBalanceTray\settings.json`，macOS 默认位置是
`~/Library/Application Support/CodexBalanceTray/settings.json`。新安装或无效设置文件时默认使用 English。

## 测试

测试使用模拟的 app-server JSONL 和 HTTP 响应，不需要本机登录或真实网络：

```bash
python -m unittest discover -s tests -v
python -m compileall -q codex_tray tests main.py
```

fixtures 位于 `tests/fixtures/`，涵盖成功响应、未登录、API key、schema 变更、HTTP 错误和脱敏。测试不会读取用户的 Codex 凭证，也不会发送真实网络请求。

## 创建 Windows EXE

先安装 PyInstaller，再使用版本控制中的 spec：

```bash
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean CodexBalanceTray.spec
```

产物会放在 `dist/CodexBalanceTray.exe`；build 和 dist 目录已列入 `.gitignore`，不应提交到仓库。

## 创建 macOS App

原生 macOS 产物必须在 Mac 或 macOS CI runner 上构建；Windows 上的 PyInstaller 不能交叉生成 macOS App：

```bash
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean --windowed --name CodexBalanceTray main.py
```

窗口化产物通常会放在 `dist/CodexBalanceTray.app`。`requirements.txt` 会安装 macOS `pystray` backend 所需的 Cocoa／Quartz 依赖。

## 项目结构

```text
codex_tray/                  核心 provider、数据模型、监控器和通知区域 UI
codex_tray/i18n.py           英文默认、繁体中文／简体中文／日文翻译和语言偏好保存
codex_tray/balance.py        app-server／HTTP provider 与安全错误分类
codex_tray/forecast.py       保守的额度耗尽线性估算
tests/                       离线单元与 provider contract 测试
tests/fixtures/              不含秘密的 JSON／JSONL 假数据
CODEX_USAGE_INTERFACE.md     数据来源、接口与安全边界研究笔记
CodexBalanceTray.spec        Windows EXE 构建设置
```

## 更新与已知限制

- 默认每 5 分钟刷新一次，启动后立即在后台获取一次；手动与定时刷新共用 single-flight guard，不会阻塞通知区域事件循环。
- app-server protocol schema 会随安装的 Codex CLI 版本变化；格式不兼容时显示“响应格式已变更”，不会猜测额度。
- API key billing usage 不等同 ChatGPT Codex 方案额度；API key 或其他非 ChatGPT auth mode 会显示不支持，而不是混用百分比。
- 耗尽预估是根据当前窗口已使用百分比做线性估算；至少观察一小时且响应包含完整窗口数据后才显示，不能视为 Codex 服务保证的预测。
- 当前测试是离线 contract／UI 测试；不宣称已完成真实账号的 end-to-end smoke test。

更完整的来源与接口说明请见 [`CODEX_USAGE_INTERFACE.md`](CODEX_USAGE_INTERFACE.md)。

## 安全报告

请先阅读 [`SECURITY.md`](SECURITY.md)。不要提交 `auth.json`、`.env`、token、原始 HTTP／JSON-RPC trace、内部路径或模型文件。

## 许可证

本项目采用 [MIT License](LICENSE)。
