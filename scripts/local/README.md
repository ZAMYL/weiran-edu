# 本机演示启动（未然 EDU · V1.4 演示版）

这两个脚本用于在本机一键启动未然 EDU V1.4 完整功能演示（真实 FastAPI + SQLite + Next.js），无需依赖外部临时脚本，也不触网调用付费模型。

## 首次准备（如本机尚未构建依赖）

```powershell
cd apps\web
corepack pnpm install --frozen-lockfile --registry=https://registry.npmmirror.com
corepack pnpm build
cd ..\..\apps\api
uv sync --locked --group dev
cd ..\..
```

要求：Node.js 24、corepack（pnpm 11.25.0）、Python 3.12、uv。

## 一键启动（默认 3418 端口）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\local\start-demo.ps1
```

默认以 `sample` 身份启动在 `http://127.0.0.1:3418`：
- 预置 6 个五维评价合成样例批次，可直接体验学情诊断、画像探索、测评数据与报告；
- 已启用离线编程 AI 核查（`http_mock`，`real_qwen=not_run`）；
- 启动后终端会直接打印可用的**合成五维 Excel** 与**合成编程答题卡 ZIP**路径，可直接在统一上传页导入体验。

常用可选参数：

```powershell
# 空数据教师（0 个预置批次，用于体验首次上传流程）
powershell -ExecutionPolicy Bypass -File scripts\local\start-demo.ps1 -Identity first

# 指定端口或不自动打开浏览器
powershell -ExecutionPolicy Bypass -File scripts\local\start-demo.ps1 -Port 3418 -NoBrowser

# 指定自动退出时长（秒，0 表示持续运行直到 Ctrl+C）
powershell -ExecutionPolicy Bypass -File scripts\local\start-demo.ps1 -HostSeconds 3600
```

## 停止

在启动窗口按 `Ctrl+C`，或使用启动器打印的会话目录精准停止：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\local\stop-demo.ps1 -SessionDirectory "<启动器打印的本次会话目录>"
```

## 边界说明

- V1.4 定位为功能演示版本，默认使用合成样例体验完整流程；真实跨届/多格式资料通用解析留待 V2.0。
- 仅绑定本机回环 `127.0.0.1`。
- 全程真实 HTTP + SQLite 合成数据；不调用真实 Qwen；AI 结果为 `http_mock`。
- 会话凭据与临时数据库均写入 `%LOCALAPPDATA%\weiran-edu-ai-integration`，不进入仓库。
