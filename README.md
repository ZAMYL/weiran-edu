# 未然 EDU

面向高校教师的学生测评数据分析与 AI 教学解释项目，独立于 [未然决策推演平台](https://github.com/ZAMYL/weiran)。

## 当前阶段

已搭建独立的 Next.js + FastAPI 基础工程：最小首页、加载与错误页面、健康检查、统一错误结构、请求编号、服务端环境配置、离线测试及 CI。

尚未实现 Excel 导入、清洗、六类学情分析、学生画像、群体筛选、证据追溯、评语分类或 AI 教学解释。多批次测评与成长追踪属于后续方向；六类分析的定义和验收标准仍待团队确认。没有业务接口、数据库 Schema、教师认证或模型调用实现。

[PRD](docs/PRD.md) 仍待确认；[架构](docs/ARCHITECTURE.md)、[搭建与验证](docs/BOOTSTRAP.md)、[迁移评估](docs/MIGRATION_MAP.md) 分别记录工程边界、运行方法和参考来源。

## 技术栈

- 前端：Node.js 24、pnpm 11.25.0、Next.js 16.4.1、React 19.3.1、TypeScript 5.9.3。
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

| 成员 | 分工 / 入口 |
| --- | --- |
| ZAMYL | 负责人，分析规则、后续 AI 模块与 `evals/` |
| AooMing（林明） | `apps/web/` 前端，后续布局与视觉自主设计 |
| Peng-Kai789（彭仲楷） | `apps/api/` 后端、API、数据库 |

```text
apps/web/             Next.js App Router 与前端工具链
apps/api/app/core/    配置、错误处理、请求编号
apps/api/app/routers/ 健康检查；后续 API 由后端负责人设计
apps/api/app/schemas/ 仅健康检查与通用错误响应
apps/api/tests/       离线测试
docs/                产品状态、架构、运行与迁移说明
evals/               后续分析与 AI 评测占位
.github/workflows/   PR / main 的独立 CI
```

协作约定见 [AGENTS.md](AGENTS.md)。

## 数据与隐私

不上传《评价.xlsx》、原始 Excel/CSV、真实学生资料、密钥、密码或 SQLite 数据库，私有仓库也不例外。开发测试使用经审查的合成内容。不要在文档、日志、截图、PR 或测试夹具中包含个人信息。

`.gitignore` 允许安全的 `.env.example` 和 SQL 迁移源文件；真实环境文件、数据和数据库仍被忽略。忽略规则不能识别任意文件里的隐私内容，提交前仍需审查。

开发前需共同确认 PRD v0.2、分析与评分口径、输入字段、证据追溯、数据授权与保留策略、API 契约、数据库设计和验收标准。

## 后端设计基线与草案

2026-10-23，用户已确认[后端冻结规则 v0.1](docs/BACKEND_CONTRACT_V0.1.md)。完整 PRD 与六类分析算法仍待团队确认。

- [API 与 DTO 草案](docs/API_CONTRACT_V0.1.md)：11 个 EDU 接口设计，复用基础工程的统一错误信封、通用错误码与请求编号。
- [数据结构草案](docs/DATA_MODEL_V0.1.md)：独立数据库、学生与身份映射、批次、评分评语、证据和事务边界。
- [OpenAPI 3.1.1 草案](docs/contracts/edu-api.v0.1.openapi.json)：可供接口工具读取的契约与合成示例，不表示 EDU 业务服务已经实现。

本次仅补充设计文档，不改变现有健康检查、运行方式、依赖、CI 或基础工程代码。新增读取接口、DTO、物理模型及认证接入建议仍需联调评审；个人数据外发须另行授权。
