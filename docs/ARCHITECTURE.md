# 未然 EDU — 架构与实现边界

状态更新：2026-10-25。前端设计 Demo、后端导入/数据库/授权/统计/筛选/证据 API 已有实现，前端仍使用合成数据，尚未完成真实 API 接入和浏览器整条流程验收。完整状态与早期草案差异见 [IMPLEMENTATION_STATUS.md](archive/IMPLEMENTATION_STATUS.md)。

## 模块边界

| 目录 | 当前职责 | 负责人 |
| --- | --- | --- |
| `apps/web/` | Next.js 教师 Demo，后续接入服务端数据 | AooMing（林明） |
| `apps/api/app/importing/edu/` | 固定双表 Excel 解析、质量报告、安全预览、上传子进程 | Peng-Kai789（彭仲楷） |
| `apps/api/app/edu/`、`routers/edu.py` | 教师试点凭证与授权、导入确认、批次修订、分析和证据 HTTP 适配 | Peng-Kai789（彭仲楷） |
| `apps/api/app/storage/` | 仓库外 SQLite、显式事务、校验和迁移和不可变事实约束 | Peng-Kai789（彭仲楷） |
| `apps/api/app/analysis/edu/` | 精确统计、AND 筛选、画像、相关性、评语复核与候选内核 | ZAMYL |
| `apps/api/app/ai/edu/` | 证据受控的 AI 内核及合成模型边界 | ZAMYL |
| `docs/`、`evals/` | 需求、契约、运行与合成验收资料 | 团队按职责维护 |

## 已实现的后端数据流

管理员初始化仓库外数据库并配置教师/来源权限 → 教师上传固定模板 → 有界子进程解析 → 安全预览与全量质量报告 → 教师确认警告及预览版本 → 短 SQLite 写事务形成不可变批次 → 同一获授权快照上的统计、群体筛选、匿名学生及证据/依据来源查询。

- 业务路由仅在配置 `WEIRAN_EDU_DATABASE` 后注册。HTTP 不自动建库、迁移或签发凭证；初始化与授权使用管理员 CLI。
- 原始身份、文件及原文保存在受控数据表/BLOB，普通 DTO 采用字段白名单。非空评语及依据原文暂不展示，不宣称已完成脱敏审核。
- 分数使用整数百分之一分，分析适配显式映射五维编码并转成 Fraction。完整批次和完整筛选群体先计算，结果再分页，不用一页数据作为统计分母。
- 事实修订追加新批次，旧评分/依据/来源保持不变。快照不授予权限，每次读取重验教师状态、凭证、来源成员和批次授权。
- 分析计算复用现有确定性引擎；没有把内部引用直接当成公开证据授权，也没有将真实上传接入合成模型通道。

## 前端与后续边界

前端三页和抽屉仍消费本地 Demo 模型。接入时需替换 service、分页和状态，保留林明的布局设计。首次浏览器验收应贯通上传、预览、警告确认、入库、切换批次、统计、群体和证据，并核对权限失败和快照冲突。

内部候选和 AI 模块已存在，但正式批次洞察查询、逐指标参与记录 HTTP 回查、真实学生模型调用、导出、学校 SSO 和原文审核流程未交付。单次测评不直接证明学生真实能力、AI 依赖或学习提升。

运行方法见 [数据库与权限](DATABASE_BATCH_AUTH.md)、[统计/群体/证据 API](STATISTICS_GROUP_EVIDENCE_API.md) 和 [BOOTSTRAP.md](BOOTSTRAP.md)。四条已确认行为见 [后端基线](archive/BACKEND_CONTRACT_V0.1.md)；旧 [API](archive/API_CONTRACT_V0.1.md)、[物理模型](archive/DATA_MODEL_V0.1.md)、[Demo 对齐](archive/FRONTEND_API_ALIGNMENT_V0.1.md) 和 OpenAPI 文件保留为历史设计，不覆盖运行时契约。

SQLite 当前面向受控试点。学校认证、部署 ACL、备份/加密、资源隔离及正式保留方案仍需落实；仓库不保存真实学生数据、数据库或凭据。
