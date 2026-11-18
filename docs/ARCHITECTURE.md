# 未然 EDU — 架构与实现边界

状态更新：2026-11-18（V1.4 基线）。已实现：固定双 Sheet Excel 导入、仓库外 SQLite、教师凭证与授权、统计/群体/证据 API、确定性分析内核与合成 AI 通道。
**V1.4 P0 尚未实现**：通用资料接入（ZIP/HTML 优先）、代码与测试点证据入库、真实 Qwen Live 接线、前端真实接入与浏览器端到端验收。完整历史状态见 [IMPLEMENTATION_STATUS.md](archive/IMPLEMENTATION_STATUS.md)。

## 模块边界

| 目录 | 当前职责 | 负责人 |
| --- | --- | --- |
| `apps/web/` | Next.js 教师 Demo，后续接入服务端数据 | AooMing（林明） |
| `apps/api/app/importing/edu/` | 资料接入：文件识别 → 适配器分发 → 质量报告 → 安全预览；**当前只实现了固定双 Sheet 适配器**，V1.4 要求扩展为通用入口 | Peng-Kai789（彭仲楷） |
| `apps/api/app/edu/`、`routers/edu.py` | 教师试点凭证与授权、导入确认、批次修订、分析和证据 HTTP 适配 | Peng-Kai789（彭仲楷） |
| `apps/api/app/storage/` | 仓库外 SQLite、显式事务、校验和迁移和不可变事实约束 | Peng-Kai789（彭仲楷） |
| `apps/api/app/analysis/edu/` | 精确统计、AND 筛选、画像、相关性、评语复核与候选内核 | ZAMYL |
| `apps/api/app/ai/edu/` | 证据受控的 AI 内核及合成模型边界 | ZAMYL |
| `docs/`、`evals/` | 需求、契约、运行与合成验收资料 | 团队按职责维护 |

## V1.4 目标数据流

上传（单/多文件/ZIP）→ 内容识别（文件角色、字段语义）→ 适配器抽取（题目、学生、代码、测试点、评分、来源）→ 证据入库（批次快照）→ 五维分析与错误模式聚合 → 真实 Qwen Agent 工具调查（统计 / 群体比较 / 代码特征 / 证据定位）→ 教师核验 → 报告保存与导出。

与当前实现的差异：入口从"固定模板校验"改为"内容识别 + 适配器分发"；分析对象从"五维评分"扩展到"代码与测试点证据"；AI 从合成通道升级为真实 Live。

- 业务路由仅在配置 `WEIRAN_EDU_DATABASE` 后注册。HTTP 不自动建库、迁移或签发凭证；初始化与授权使用管理员 CLI。
- 身份口径按 V1.4：`participant_id` 仅作内部关联键，姓名与学号取自上传资料并原样展示；原始文件与代码留在本地服务端。
- 分数使用整数百分之一分，分析适配显式映射五维编码并转成 Fraction。完整批次和完整筛选群体先计算，结果再分页，不用一页数据作为统计分母。
- 事实修订追加新批次，旧评分/依据/来源保持不变。快照不授予权限，每次读取重验教师状态、凭证、来源成员和批次授权。
- 分析计算复用现有确定性引擎；模型只做调查与解释，不得回写评分事实。

## 统一证据模型（V1.4 增量）

在不推翻现有批次/评分/来源结构的前提下，按最小增量迁移增加适用于原始代码答卷的实体：

| 实体 | 关键字段 | 用途 |
| --- | --- | --- |
| `SourceAsset` | asset_id, file_name, content_type, source_path, file_hash | 文件类型、原位置与提取来源 |
| `Assessment / Problem` | assessment_id, problem_id, title, statement_ref | 组织测评与题目 |
| `Participant` | participant_id, display_name, student_no | 内部关联键 + 界面原样展示 |
| `Submission` | submission_id, participant_id, problem_id, language, code_ref | 学生 × 题目 × 源代码 |
| `TestObservation` | submission_id, test_index, status, duration, memory, source_ref | 评测现象 |
| `CodeFeature` | submission_id, feature_type, value, location | 可查询的代码结构特征 |
| `Finding / EvidenceRef` | finding_id, dimension_code, scope, support_refs, model_origin | 五维结果、错误发现与来源 |

`EvidenceRef.locator.kind` 仅允许 `html-node`、`test-case`、`code-span`。

## 接入与分析接口（设计建议，实现时优先复用已有函数）

```text
detect_assets(files)             -> DetectedAsset[]
understand_content(assets)       -> ExtractionPlan
extract_evidence(plan, files)    -> AssessmentEvidence
analyze_local(evidence)          -> FiveDimensionView + ErrorPatterns
investigate_with_qwen(context)   -> Findings + EvidenceRefs
```

适配器可迭代扩展，**不按文件名、人数字段或固定题号写业务分支**；题目数、学生数、评分与测试点计数一律由解析结果动态统计。

## 前端与后续边界

前端仍消费本地 Demo 模型。接入时需替换 service、分页和状态，保留林明的布局设计；首次浏览器验收应贯通上传 → 预览 → 入库 → 五维与错误模式 → 群体到个人姓名/学号/代码证据 → 真实 AI 调查 → 报告导出，并核对权限失败与快照冲突。

V1.4 明确要求真实 Qwen 接通：现有 AI 任务路由在服务未安装时返回 `AI_LIVE_NOT_ENABLED`，这属于兜底形态，**不能作为已交付**。学校 SSO、备份加密与正式保留策略仍未落实。

运行方法见 [数据库与权限](DATABASE_BATCH_AUTH.md)、[统计/群体/证据 API](STATISTICS_GROUP_EVIDENCE_API.md) 和 [BOOTSTRAP.md](BOOTSTRAP.md)。
需求口径见 [PRD](PRD.md)；`docs/archive/` 下的 v0.1 契约与联调草案仅作历史记录，**不再具有约束力**。

SQLite 当前面向受控试点。学校认证、部署 ACL、备份/加密、资源隔离及正式保留方案仍需落实；仓库不保存真实学生数据、数据库或凭据。
