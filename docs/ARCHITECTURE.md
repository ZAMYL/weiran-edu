# 未然 EDU — 架构与实现边界（V1.4）

状态基准：2026-11-24，集成候选 **#136** `c2d4288a88be92b1d2ec9fb924dcfb3d7b46ccb9`
（= #129 `33e39161b58c799200c0e690e4943220feabec79` + #132 `c0bb138f953ab908555bc69df78be1a686386852` + #133 `49b626f9846e5ae3535f3343af7dda5fd2370732`）。
并行参考线（**未并入本基准**，单独标注）：#134 `d628301b1f386308c9313d3f9b52424a8053720e`（LIVE-02 真实 Qwen 服务端接线）、#135 `37fc94583e5d634ea3d558aba4667bf2908494e5`（Ant Design 6.7.1 教师工作台）。
本文只描述基准内真实 import/路由关系与已有运行证据；旧 2026-10-25 状态由本文取代，早期草案见 [archive/IMPLEMENTATION_STATUS.md](archive/IMPLEMENTATION_STATUS.md)。

## 产品定位与主流程

AI 时代的编程能力与学习成效诊断平台：让教师看见编程表现背后的能力差异。
目标链路：**上传资料 → 文件提取 → AI 资料理解 → 可信测评与证据 → 确定性统计 → 编程 Agent 调查 → 前端展示与报告**。
五维固定为：算法与数据结构、代码实现能力、问题建模能力、效率意识、解题策略。**没有来源证据就不虚构该维度评分、个人能力或原因。**
上传资料是核对样本，不是业务模板：不按固定 Sheet 名、人数、文件名、行序或题量写死逻辑；ZIP 答题卡可以只提供代码/成绩/测试点等实际证据，不要求必须同时有五维评价 Excel。无法确定角色或字段时明确待确认或不支持，不静默造数据或丢失来源。

## 基准内正式运行链路（实际调用关系）

- **装配** `apps/api/app/main.py`：`EduService`（`importing.edu.worker.parse_isolated`）+ `AssessmentService`/`AssessmentReader`（+`OriginalStore`）+ `ProgrammingTaskService`（离线）；路由 `edu` / `edu_discoveries` / `edu_reviews` / `edu_support` / `ai_capability` / `programming`。业务路由仅在配置 `WEIRAN_EDU_DATABASE` 后注册；HTTP 不自动建库、迁移或签发凭证。
- **导入与证据** `app/importing/edu/`：`detect` → `extract`（#133 表头保真：结构感知表头定位、说明行移入 `text_lines` 并给 `HEADER_PREAMBLE_SKIPPED:<idx>` / `HEADER_UNCERTAIN` 诊断、全表宽补齐禁止静默截列）→ `adapter`/`answersheet`/`parser`/`archive` → `EduService.create_import/commit` → SQLite（迁移 001–014，校验和、fail-closed）→ `AssessmentService/Reader` 统计、群体、个人证据与原件受控回读（B-03，009/012/013）。
- **确定性统计与发现** `app/analysis/code/`（DS-03）：被 `edu/analysis`、`edu/discoveries`、`edu/insights`、`edu/results`、`edu/report_exports` 与 `ai/edu/tools` 消费；分数整数百分之一分，完整群体先计算再分页。
- **五维 Live（生产受控）**：`environment=production` 且 `WEIRAN_EDU_REAL_DATA_APPROVAL` → `app/live_wiring.install_live_from_env` → `prototypes/durable_teacher_agent`（`api.install_production`/`TaskStore`/`TaskService`/`TaskWorker`/`live_admission`）+ `prototypes/teacher_agent_bridge.provider` + `prototypes/qwen_execution.code_fingerprint` + `prototypes/adaptive_assessment.live`（budget/pricing/planner）。非生产 `ai_task_router` 为合成 `http_mock` 边界，缺准入返回 503；合成主机不 import live_wiring。
- **编程任务（离线）** `app/edu/programming_tasks.py` + `routers/programming.py`（B-04C，014 迁移）：任务/授权/审计/撤权与受控回读。真实 Qwen runner 在 **#134** `app/edu/programming_live.py`（`ProgrammingLiveApproval`/`ProgrammingLiveBudget`、独立 Worker 条件认领），**尚未并入本基准**。

## prototypes 真实职责（不按目录名判断废弃）

| 模块 | 分类 | 依据（基准内 import/路由） |
| --- | --- | --- |
| `prototypes/durable_teacher_agent` | 正式运行依赖 | `live_wiring`/`main` 直接 import store/service/worker/api/live_admission；五维任务与 Live 准入 |
| `prototypes/teacher_agent_bridge` | 正式运行依赖 | `live_wiring` import provider；durable api/models 消费；**非历史废弃** |
| `prototypes/qwen_execution` | 正式运行依赖 | `code_fingerprint` 被 live_wiring/production 使用；runtime/task_contract 供执行边界 |
| `prototypes/adaptive_assessment.live` | 正式运行依赖 | durable live_admission/service 消费 budget/pricing/planner |
| `prototypes/capability_profiles` | 正式运行依赖 | durable profiles/service 消费；demo 入口除外 |
| `prototypes/research_agent_v2`（C-01、database_evidence、programming_task_adapter） | 未接入实验 | 基准内仅 tests/evaluation/scripts 消费；正式接线见 #134 `programming_live.py` |
| `app/ai/edu/material_*`（DS-04，#132） | 集成候选、未接路由 | routers/service 零引用；`extract_material_input`/`understand_materials`/`MaterialPlan.needs_confirmation` 待 B-02 承接 |
| `apps/api/evaluation/ds06a`、`evals/material_semantics` | 离线评测 harness | 仅离线运行，no_model_call 规则/合成探针对照 |
| `adaptive_assessment.agent`、capability_profiles/durable 的 demo/serve、`reporting/edu/demo.py` | 历史/演示入口 | 不在生产 import 链 |
| `docs/archive/`、旧 OpenAPI/物理模型 | 历史设计 | 保留为记录，不覆盖运行时契约 |

## 已真实验证 vs 未完成

**基准内已验证**：通用提取表头保真与诊断（#133，定向测试）；ZIP 答题卡 → 不可变批次 → 五维统计/证据/原件回读（B-03）；离线编程任务与受控回读（B-04C）；五维 Live 离线接线与合成边界（经 #129）；DS-04 陌生资料理解：候选 + 审阅投影 + 待确认保留、隐私墙与来源绑定（#132，#136 上 112 项定向测试通过）。
**参考线证据（未并入）**：#134 已完成真实 Qwen **技术验证**（run 45364955318：3 次请求 HTTP 200、2 次工具 verified、预算审计落库），但其 `human_approval_reference`/`independent_review_reference` 为占位字符串，**不构成独立审批放行**，正式放行版重跑未做；价格指纹有效期至 2026-11-26。#135 工作台重构通过生产构建/ESLint/TypeScript/269 项 Vitest，**网页真实 AI 调查端到端未全部完成**。
**未完成**：B-02 通用表格确认入库（审阅凭据服务端持久化、source_id 内容版本绑定、coverage/authority 证明、可信身份域证明）；`evaluations` 旧 schema `CHECK(sheet_name='Sheet2')` 需公共 schema 批准后才能灵活入库；C-01 正式接线（#134 待并入）；网页真实 AI 调查端到端验收；学校 SSO、部署 ACL、备份/加密与正式保留方案。单次测评不直接证明学生真实能力、AI 依赖或学习提升。

## 运行与契约指针

本地运行：[BOOTSTRAP.md](BOOTSTRAP.md)、[DATABASE_BATCH_AUTH.md](DATABASE_BATCH_AUTH.md)。
正式接口：[STATISTICS_GROUP_EVIDENCE_API.md](STATISTICS_GROUP_EVIDENCE_API.md)、[B03_EVIDENCE_CONTRACT.md](B03_EVIDENCE_CONTRACT.md)、[B04C_PROGRAMMING_TASKS.md](B04C_PROGRAMMING_TASKS.md)、[DS04A_MATERIAL_SEMANTICS.md](DS04A_MATERIAL_SEMANTICS.md)、[DS04B_MATERIAL_EVALUATION.md](DS04B_MATERIAL_EVALUATION.md)、[C01_PROGRAMMING_INVESTIGATION.md](C01_PROGRAMMING_INVESTIGATION.md)。
文档索引与优先阅读顺序见 [docs/README.md](README.md)。仓库不保存真实学生数据、数据库或凭据。