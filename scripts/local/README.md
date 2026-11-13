# 本机演示启动（未然 EDU）

这两个脚本让你在本机直接启动完整产品演示，无需手动配置凭证或数据库。

## 首次准备（一次性）

```powershell
cd apps\web
corepack pnpm install --frozen-lockfile --registry=https://registry.npmmirror.com
corepack pnpm build
cd ..\..\apps\api
uv sync --locked --group dev
cd ..\..
```

以上命令最后回到仓库根目录，之后可直接执行下方「启动」命令。

要求：Node.js 24、corepack（提供 pnpm 11.25.0）、Python 3.12、uv。

> 说明：`--registry=https://registry.npmmirror.com` 用于国内直连 npmjs 过慢导致大包（next / @next/swc）下载超时（error 23）的情况。网络较快时可省略该参数。`--frozen-lockfile` 仍按锁文件校验完整性，不改变依赖版本。

## 启动

空数据教师（推荐，用于验证首次上传流程）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\local\start-demo.ps1 -Identity first
```

合成样例教师（6 个样例批次，用于体验 AI 分析、群体画像与报告）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\local\start-demo.ps1 -Identity sample
```

启动后自动打开浏览器进入「学情诊断」。启动器会校验 `test-session / me / scopes / batches` 四个接口全部返回 200 后才会报告成功。

使用空数据教师时，启动器会打印本次宿主已登记的合成样例路径。请在上传页的文件选择框中打开该路径，确认导入后即可启动合成 AI。演示宿主每次启动都会生成本次样例。

## 停止

```powershell
powershell -ExecutionPolicy Bypass -File scripts\local\stop-demo.ps1
```

或在启动窗口按 Ctrl+C。合成宿主达到时限（默认 1 小时）后会自动撤销凭证并清理临时数据库。

## 边界

- 仅绑定本机回环 `127.0.0.1`。
- 全程真实 HTTP + SQLite 合成数据；不调用真实 Qwen；AI 结果为 `http_mock`。
- 不提交数据库、token、真实学生资料或私有会话文件（会话文件写入 `%LOCALAPPDATA%\weiran-edu-ai-integration`）。
