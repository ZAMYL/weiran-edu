# 未然 EDU

面向高校教师的学生测评数据分析与 AI 教学解释项目，独立于 [未然决策推演平台](https://github.com/ZAMYL/weiran)。

## 当前阶段

已搭建独立的 Next.js + FastAPI 工程。前端增加了按设计稿实现的本地演示工作台：学情洞察、画像探索、测评数据、证据与学生详情抽屉、浏览器内 Excel 预览校验、批次切换及全屏。

前端使用合成记录，新增批次仅保留在当前浏览器内存；教学分析明确标注为规则模板，未调用 AI。现有 FastAPI 后端及分析规则草案保持独立，前端尚未对接业务 API、持久化、认证或真实模型服务。评分标准、Excel 格式和客户端统计仅是演示约定，不构成团队确认的业务契约。六类分析的定义和验收标准仍待团队确认。

前端运行、导入格式与验证见 [apps/web/README.md](apps/web/README.md)；组件及视觉规范见 [frontend-architecture.md](docs/frontend-architecture.md)；选用设计稿见 [design/edu-suite](design/edu-suite/README.md)。

[PRD](docs/PRD.md) 仍待确认；[架构](docs/ARCHITECTURE.md)、[搭建与验证](docs/BOOTSTRAP.md)、[迁移评估](docs/MIGRATION_MAP.md) 分别记录工程边界、运行方法和参考来源。

## 技术栈

- 前端：Node.js 24、pnpm 11.25.0、Next.js 16.4.1、React 19.3.1、TypeScript 5.9.3。
- 界面与交互：Ant Design 6、ECharts 6、Zustand 5；xlsx 在浏览器内由 read-excel-file 解析，未对接后端导入。
- 前端质量工具：ESLint 9、Prettier 3，使用 `pnpm-lock.yaml` 锁定依赖。
- 后端：Python 3.12、uv 0.11.14、FastAPI、Pydantic、Uvicorn，使用 `uv.lock` 锁定依赖。
- 后端质量工具：Ruff、pytest、httpx；测试禁止外网连接。
- SQLite 是未来计划，当前没有数据库文件、表或迁移。

## 本地运行

先安装 Node.js 24、pnpm 11.25.0、Python 3.12 和 uv。以下命令分别在两个终端运行，默认仅绑定本机回环地址，无需任何密钥。

前端：

```powershell
cd D:\AI\weiran-edu\apps\web
pnpm install --frozen-lockfile
pnpm dev
```

访问 [http://127.0.0.1:3000](http://127.0.0.1:3000)。

后端：

```powershell
cd D:\AI\weiran-edu\apps\api
uv sync --locked --group dev
uv run --locked uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --reload --no-access-log
```

访问 [健康检查](http://127.0.0.1:8000/api/v1/health)，返回 `{"status":"ok"}` 及 `X-Request-ID`。接口文档位于 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)。这是进程存活检查，不探测数据库或模型服务。

无需创建 `.env` 即可运行。可选环境配置及完整测试命令见 [BOOTSTRAP](docs/BOOTSTRAP.md)。

## 团队与目录

| 成员                  | 分工 / 入口                               |
| --------------------- | ----------------------------------------- |
| ZAMYL                 | 负责人，分析规则、后续 AI 模块与 `evals/` |
| AooMing（林明）       | `apps/web/` 前端，后续布局与视觉自主设计  |
| Peng-Kai789（彭仲楷） | `apps/api/` 后端、API、数据库             |

```text
apps/web/             Next.js App Router、本地演示工作台与前端工具链
apps/api/app/core/    配置、错误处理、请求编号
apps/api/app/routers/ 健康检查；后续 API 由后端负责人设计
apps/api/app/schemas/ 仅健康检查与通用错误响应
apps/api/tests/       离线测试
docs/                产品状态、架构、运行与迁移说明
design/              合成 UI 设计稿、选用说明与设计追溯资料
evals/               后续分析与 AI 评测占位
.github/workflows/   PR / main 的独立 CI
```

协作约定见 [AGENTS.md](AGENTS.md)。

## 数据与隐私

不上传《评价.xlsx》、原始 Excel/CSV、真实学生资料、密钥、密码或 SQLite 数据库，私有仓库也不例外。开发测试使用经审查的合成内容。不要在文档、日志、截图、PR 或测试夹具中包含个人信息。

`.gitignore` 允许安全的 `.env.example` 和 SQL 迁移源文件；真实环境文件、数据和数据库仍被忽略。忽略规则不能识别任意文件里的隐私内容，提交前仍需审查。

开发前需共同确认 PRD v0.2、分析与评分口径、输入字段、证据追溯、数据授权与保留策略、API 契约、数据库设计和验收标准。

## 后端契约与 Demo 联调草案

2026-10-23 用户确认[四条后端基线](docs/BACKEND_CONTRACT_V0.1.md)。2026-10-24 对齐已完成的前端 Demo 和 main 的确定性候选：

- [API 与 DTO](docs/API_CONTRACT_V0.1.md)、[数据结构](docs/DATA_MODEL_V0.1.md)、[OpenAPI 与合成 Mock](docs/contracts/edu-api.v0.1.openapi.json)。
- [Demo/API 对齐清单](docs/FRONTEND_API_ALIGNMENT_V0.1.md)：三页调用、三方编码、预览名称修改、警告和定位、全批/群体统计、分值矩阵及候选证据回查。
- [后端/引擎对接](docs/BACKEND_ANALYSIS_HANDOFF_V0.1.md)：定点评分与 Fraction、内部记录与匿名证据引用。

新增操作、统计展示与视图模型仍为待三方评审草案。本轮只修改设计文档，不改前端页面、HTTP 业务、数据库或现有分析/AI 实现；不把合成模型模式接到真实上传流程。
