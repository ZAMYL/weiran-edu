# 未然 EDU

面向高校编程教学与算法竞赛训练的编程能力与学习成效诊断平台，独立于 [未然决策推演平台](https://github.com/ZAMYL/weiran)。

## 当前阶段

已搭建独立的 Next.js + FastAPI 工程。前端增加了按设计稿实现的本地演示工作台：学情洞察、画像探索、测评数据、证据与学生详情抽屉、浏览器内 Excel 预览校验、批次切换及全屏。

前端设计 Demo 已完成，目前仍使用合成记录和浏览器内存批次。后端已实现固定 Excel 解析、预览/质量报告、SQLite 确认入库、批次/修订管理、教师试点凭证与授权、五维统计、群体筛选和证据来源查询。前端尚未切换这些真实 API，尚未完成浏览器整条流程验收。已有分析与受控合成 AI 模块不等于真实学生数据的 AI 服务已开放。

前端运行、演示导入格式与验证见 [apps/web/README.md](apps/web/README.md)。当前功能和历史草案差异见 [实现状态](docs/IMPLEMENTATION_STATUS.md)。

[PRD](docs/PRD.md) 仍待确认；[架构](docs/ARCHITECTURE.md)、[搭建与验证](docs/BOOTSTRAP.md)、[迁移评估](docs/MIGRATION_MAP.md) 分别记录工程边界、运行方法和参考来源。

## 技术栈

- 前端：Node.js 24、pnpm 11.25.0、Next.js 16.4.1、React 19.3.1、TypeScript 5.9.3。
- 界面与交互：Ant Design 6、ECharts 6、Zustand 5；xlsx 在浏览器内由 read-excel-file 解析，未对接后端导入。
- 前端质量工具：ESLint 9、Prettier 3，使用 `pnpm-lock.yaml` 锁定依赖。
- 后端：Python 3.12、uv 0.11.14、FastAPI、Pydantic、Uvicorn，使用 `uv.lock` 锁定依赖。
- 后端质量工具：Ruff、pytest、httpx；测试禁止外网连接。
- 后端使用独立 SQLite 与校验和 SQL 迁移；数据库和真实文件必须保存在仓库外。

## 本地运行

先安装 Node.js 24、pnpm 11.25.0、Python 3.12 和 uv。以下命令分别在两个终端运行，默认仅绑定本机回环地址，无需任何密钥。

前端：

```powershell
cd apps/web
pnpm install --frozen-lockfile
pnpm dev
```

访问 [http://127.0.0.1:3000](http://127.0.0.1:3000)。

后端：

```powershell
cd apps/api
uv sync --locked --group dev
uv run --locked uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --reload --no-access-log
```

访问 [健康检查](http://127.0.0.1:8000/api/v1/health)，返回 `{"status":"ok"}` 及 `X-Request-ID`。接口文档位于 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)。这是进程存活检查，不探测数据库或模型服务。

无需创建 `.env` 即可运行基础服务。业务 API 需显式配置仓库外 `WEIRAN_EDU_DATABASE` 并先执行管理员迁移与授权，详见 [数据库、批次和教师权限](docs/DATABASE_BATCH_AUTH.md)。Excel 工具见 [解析与质量报告](docs/EXCEL_PARSER.md)，统计、筛选和取证调用见 [接口说明](docs/STATISTICS_GROUP_EVIDENCE_API.md)。通用环境及质量命令见 [BOOTSTRAP](docs/BOOTSTRAP.md)。

## 团队与目录

| 成员                  | 分工 / 入口                               |
| --------------------- | ----------------------------------------- |
| ZAMYL                 | 负责人，分析规则、后续 AI 模块与 `evals/` |
| AooMing（林明）       | `apps/web/` 前端，后续布局与视觉自主设计  |
| Peng-Kai789（彭仲楷） | `apps/api/` 后端、API、数据库             |

```text
apps/web/             Next.js App Router、本地演示工作台与前端工具链
apps/api/app/core/    配置、错误处理、请求编号
apps/api/app/routers/ 健康检查与获授权的 EDU 业务路由
apps/api/app/importing/ 固定 Excel 解析、预览与质量报告
apps/api/app/edu/      教师权限、批次生命周期与分析 HTTP 适配
apps/api/app/storage/ 独立 SQLite 与校验和迁移
apps/api/app/analysis/ 确定性分析与候选内核
apps/api/app/ai/       受控 AI 内核与合成模型边界
apps/api/app/schemas/ 通用响应；业务 DTO 位于相应模块
apps/api/tests/       离线测试
docs/                产品状态、架构、运行与迁移说明
evals/               后续分析与 AI 评测占位
.github/workflows/   PR / main 的独立 CI
```

协作约定见 [AGENTS.md](AGENTS.md)。

## 数据与隐私

不上传《评价.xlsx》、原始 Excel/CSV、真实学生资料、密钥、密码或 SQLite 数据库，私有仓库也不例外。开发测试使用经审查的合成内容。不要在文档、日志、截图、PR 或测试夹具中包含个人信息。

`.gitignore` 允许安全的 `.env.example` 和 SQL 迁移源文件；真实环境文件、数据和数据库仍被忽略。忽略规则不能识别任意文件里的隐私内容，提交前仍需审查。

已实现范围不代表完整 PRD v0.2 已获共同确认。真实教师接入、原文审核、数据保留、部署保护和浏览器验收仍需后续落实。

## 后端契约与 Demo 联调草案

2026-10-23 用户确认[四条后端基线](docs/BACKEND_CONTRACT_V0.1.md)。2026-10-24 对齐已完成的前端 Demo 和 main 的确定性候选：

- [API 与 DTO](docs/API_CONTRACT_V0.1.md)、[数据结构](docs/DATA_MODEL_V0.1.md)、[OpenAPI 与合成 Mock](docs/contracts/edu-api.v0.1.openapi.json)。
- [Demo/API 对齐清单](docs/FRONTEND_API_ALIGNMENT_V0.1.md)：三页调用、三方编码、预览名称修改、警告和定位、全批/群体统计、分值矩阵及候选证据回查。
- [后端/引擎对接](docs/BACKEND_ANALYSIS_HANDOFF_V0.1.md)：定点评分与 Fraction、内部记录与匿名证据引用。

以上 v0.1 文件保留设计背景，不能直接作为现行客户端契约；其中部分功能已实现、部分仍待后续任务。实际 HTTP 形状以启用业务路由后的 `/openapi.json`、`/docs` 和 [实现状态与差异清单](docs/IMPLEMENTATION_STATUS.md) 为准。真实上传不接现有合成模型模式。
