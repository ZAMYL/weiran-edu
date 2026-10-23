# 基础工程安装、运行与测试

本阶段只提供可独立运行的工程基础，不包含学生数据处理、业务 API、数据库、认证或 AI 实现。无需任何外部模型密钥。

## 环境与依赖安装

| 工具 | 版本约束 |
| --- | --- |
| Node.js | 24.x |
| pnpm | 11.25.0（见 apps/web/package.json） |
| Python | 3.12.x（见 apps/api/.python-version） |
| uv | CI 固定 0.11.14 |

如未安装 pnpm，可在已安装 Node.js 24 后按团队环境安装 `pnpm@11.25.0`。不要同时维护 npm/yarn 锁文件。

```powershell
cd D:\AI\weiran-edu\apps\web
pnpm install --frozen-lockfile

cd D:\AI\weiran-edu\apps\api
uv sync --locked --group dev
```

其他电脑将绝对路径替换为自己的克隆路径。macOS/Linux 使用同样的 pnpm/uv 命令。依赖安装需要访问包注册表，运行与测试不需要模型 API。

锁文件已提交；`--frozen-lockfile` 和 `--locked` 防止安装时静默更新依赖。更新公共依赖和锁文件须协调，不手工编辑锁文件。

## 开发运行

在两个终端分别执行：

```powershell
cd D:\AI\weiran-edu\apps\web
pnpm dev
```

浏览器访问 [http://127.0.0.1:3000](http://127.0.0.1:3000)，显示“未然 EDU — 学情分析平台”。这是前端基础状态，不代表后端、数据库或 AI 已连接。

```powershell
cd D:\AI\weiran-edu\apps\api
uv run --locked uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --reload --no-access-log
```

另一个终端可执行：

```powershell
curl.exe -i http://127.0.0.1:8000/api/v1/health
```

预期 HTTP 200、`{"status":"ok"}`、非空 `X-Request-ID`。macOS/Linux 使用 `curl`。按 Ctrl+C 停止服务。

Swagger 文档在 `/docs`，OpenAPI 在 `/openapi.json`，唯一应用 API 为 `GET /api/v1/health`。它只表示进程存活，不检查数据库或外部依赖。前后端目前独立运行，未配置 CORS、API 代理或业务客户端；后续由接口方案决定。

如默认端口被占用，前端用 `pnpm dev --port 3100`，后端调整 `--port`。这些命令只用于本机开发，不是公网部署方案。

## 服务端配置与错误边界

`app/core/config.py` 只读取显式支持的 `WEIRAN_EDU_ENV`，取值为 `development/test/production`，默认 development。非法配置会阻止启动，但不会回显原始值；环境名不自动配置认证、CORS 或部署策略。

配置不隐式加载 .env、不探测其他环境中的密钥，也不向客户端返回配置。当前没有 AI 配置或凭据需求。未来凭据字段应使用 SecretStr 并单独完成授权与脱敏设计。

可直接在终端设置变量，或在仓库根目录复制安全示例：

```powershell
cd D:\AI\weiran-edu
Copy-Item .env.example .env
cd apps\api
uv run --env-file ../../.env --locked uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

这里仅 uv 显式加载所指定的文件。真实 .env 不得提交，任何凭据都不得使用 `NEXT_PUBLIC_*`。默认关闭访问日志以免 URL/query 进入日志；修改日志方案前须审查数据边界。

错误统一为：

```json
{
  "error": {
    "code": "RESOURCE_NOT_FOUND",
    "message": "资源不存在",
    "request_id": "req_<server-generated-uuid>"
  }
}
```

失败保留 4xx/5xx 状态。请求编号由服务端重新生成，不信任客户端同名头。错误正文编号与响应头一致，错误响应带 `Cache-Control: no-store`。输入校验及未知异常使用固定消息，不回显提交值、异常内容或栈；应用错误日志仅包含服务端请求编号。只有代码内预定义的安全消息可以传入 `APIError`。

当前请求边界针对普通 HTTP JSON 响应；后续如增加流式输出、文件上传或异步任务，需要另行设计其失败与日志边界。

## 本地检查

前端：

```powershell
cd D:\AI\weiran-edu\apps\web
pnpm lint
pnpm format:check
pnpm typecheck
pnpm build
pnpm test
```

`typecheck` 先生成 Next 路由类型再执行严格 TypeScript 检查。`pnpm test` 需要先 build，会自行选本机临时端口、启动生产服务，实际请求首页和不存在的页面，检查 200/404、中文内容，并停止服务。开发服务不必同时开启。`pnpm start` 可手动运行已构建的生产服务。

后端：

```powershell
cd D:\AI\weiran-edu\apps\api
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -q
```

测试覆盖健康检查、OpenAPI 路由边界、404/405、422/非法 JSON、HTTP 失败、预定义错误、未知异常脱敏、并发请求编号和配置读取。临时测试路由仅在测试创建的 app 上注册；不暴露在运行服务中。自动 fixture 阻止测试的外网连接（仅允许 Windows 事件循环所需的字面回环地址）；数据均为合成字符串。没有真实模型测试开关或付费调用。

检查忽略规则（在仓库根目录）：

```powershell
git check-ignore -- .env .env.production sample.xlsx sample.csv app.sqlite3
git check-ignore -- .env.example apps/api/.env.example apps/api/migrations/001_example.sql
```

第一条应输出全部路径；第二条应无输出且退出码为 1，表示这些安全示例和未来 SQL 源文件不被忽略。不要因此提交数据库转储；原始数据、数据库和个人信息仍禁止提交。

## CI

`.github/workflows/ci.yml` 对指向 main 的 PR、main 的 push 和手动触发执行：

- 前端：锁文件安装、ESLint、Prettier、TypeScript、生产构建、生产服务 smoke test。
- 后端：Ubuntu / Windows 的 Python 3.12、uv 锁文件安装、Ruff 检查与格式检查、离线 pytest。

CI 仅授予 contents:read，checkout 不持久化凭据；不设置应用密钥、不使用原始学生资料、不请求模型服务。CI 结果以当前 PR 对应提交的 GitHub 检查为准。

## 后续开发入口

- 林明：从 `apps/web/src/app/` 开始；当前页面不构成视觉设计约束。
- 彭仲楷：从 `app/main.py`、`core/`、`routers/`、`schemas/` 继续；数据库与认证尚未选择实现。
- ZAMYL：先确认分析口径与评测标准；`evals/` 保持占位。AI 内部目录与接口在评估后协调建立。

公共接口与数据结构先确认，业务开发另开 Issue、分支和 PR。不要在这次基础工程中补做业务功能。

## 已知维护限制

- ESLint 9 已被注册表标记为停止支持；Next 配套插件尚未声明支持 ESLint 10，故当前保留兼容版本，后续共同升级工具链，详见 MIGRATION_MAP.md。
- 当前 Starlette 的 TestClient 对 httpx 适配发出弃用警告；测试仍可运行，不影响服务行为。未为消除警告额外引入第二套 HTTP 客户端，后续由后端负责人协调测试适配升级。
