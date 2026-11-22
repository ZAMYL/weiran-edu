# B-04C：编程错误调查任务的正式入口（评估作用域）

本文件是 B-04C 的接口契约与复用说明，供前端（林明）与编程 Planner（Codex）对照。
实现代码：`apps/api/app/edu/programming_tasks.py`、`apps/api/app/routers/programming.py`、
迁移 `apps/api/app/storage/migrations/014_programming_tasks.sql`。

## 1. 接口

| 方法 | 路径 | 语义 |
| --- | --- | --- |
| POST | `/api/v1/edu/assessments/{assessment_id}/ai-tasks` | 创建；202 已受理并立刻执行，200 幂等重放同一任务 |
| GET | `/api/v1/edu/assessments/{assessment_id}/ai-tasks/{task_id}` | 状态与结果回读 |
| POST | `/api/v1/edu/assessments/{assessment_id}/ai-tasks/{task_id}/cancel` | 取消；未发布结果一律清空 |
| GET | `/api/v1/edu/assessments?limit=&offset=` | 教师本人测评历史列表（不含原始代码） |

创建请求只允许两个字段，其余一律服务端绑定：

```json
{"purpose": "programming_error_investigation", "idempotency_key": "teacher-scoped-key", "timeout_seconds": 120}
```

`ProgrammingTaskView` 字段：`task_id`、`assessment_id`、`source_snapshot_id`、`purpose`、
`state`（`queued/running/completed/failed/cancelled/timed_out/revoked`）、`error_code`、
`created_at`、`deadline_at`、`expires_at`、`execution_origin`（`http_mock/qwen_live/none`）、
`real_qwen`（`not_run/completed/failed`）、`production_enabled`、`tool_attempts`、
`model_requests`、`result`。

`ProgrammingResult`：`assessment_id`、`source_snapshot_id`、`asset_sha256[]`、
`statement`、`findings[]`；每条 finding 的证据必须带 `asset_id`、`source_entry`、
`student_key`（考号）、`problem_label`，可选 `source_index`、`source_row`、`test_index`。

`AssessmentList` 的每项：`assessment_id`、`title`、`course`、`assessment_date`、`created_at`、
`student_count`、`problem_count`、`submission_count`、`source_snapshot_id`、`review_state`、
`storage_state`（`stored/partial/absent`），另有 `total/limit/offset`。

## 2. 复用与阻塞（如实记录）

旧五维任务的 `TaskStore` 与 `batches` 深度耦合：`ai_synthetic_grants.batch_id NOT NULL
REFERENCES batches`，`check()` 走 `BatchReader`，`source()` 走 `DatabaseProvider.capture`。
要把 assessment 接进去属于任务书定义的「重大重构」，因此本轮采用**最小增量实现**：

- 复用语义（不复制实现）：三表结构（grant/task/event）、状态机、写事务内幂等
  （`AI_IDEMPOTENCY_CONFLICT`）、campaign 一次性（`AI_CAMPAIGN_ALREADY_USED`）、
  事件序号 `MAX(sequence)+1`、预算单调且封顶（`AI_BUDGET_RESET_FORBIDDEN` /
  `AI_BUDGET_EXHAUSTED` / `AI_TOOL_BUDGET_EXHAUSTED`）、失败即清空结果。
- 新建 `ai_programming_*` 三张表承接 assessment 作用域，**不伪造 batch_id、不复用五维结果契约**。
- 未改造 `prototypes/durable_teacher_agent`：真实 Live 仍由其 `LiveAdmission` 把关，
  本轮在 API 侧稳定拒绝（见第 4 节）。旧五维任务与其测试不受影响。

## 3. 绑定关系

服务端在建任务时一次性写入并此后每次校验：

- `assessment_id` + `owner_teacher_id`：教师只能操作本人测评（越权统一 404）。
- `snapshot_id`：取 `assessments.source_snapshot_id`，缺失即拒（`AI_SOURCE_UNVERIFIED`）。
- `source_sha`：按 `source_index` 排序的 `(asset_id, sha256, entry)` 规范化哈希；
  任何资产增删改都会让指纹变化（`AI_SOURCE_CONFLICT`）。
- `credential_sha`：创建者当时的凭证摘要，用于判定撤权后不可继续。
- `campaign_id`：`assessment-<dimension 40 of sha256(teacher, assessment, snapshot)>`，
  一个测评一个来源版本只消耗一次调查预算。
- 浏览器不能提交来源指纹、模型密钥、工具清单或授权凭证；执行器只能由服务端注入。

执行期三道闸：模型请求前、工具读取前（`guard()`）、结果发布前（`finish()` 内 `check` +
逐字节回读原件 + 证据归属校验）都会重新校验。

## 4. Live 准入

- `environment == "test"` 且注入了受控执行器 → `purpose=assessment_mock`，`execution_origin=http_mock`，
  `real_qwen=not_run`，绝不标记为真实 Qwen。
- 其余情况（包括审批文件存在）→ `AI_LIVE_ADMISSION_REQUIRED` / `AI_LIVE_NOT_ENABLED`，
  不落任务、不记账、不产生成功结果。真实联网由后续 Agent 接线任务开放。

## 5. 错误码

| 场景 | 状态 | 码 |
| --- | --- | --- |
| 越权/不存在 | 404 | `RESOURCE_NOT_FOUND` |
| 授权撤销/过期 | 403 / 410 | `AI_PURPOSE_REVOKED` / `AI_TASK_EXPIRED` |
| 来源快照或资产变化 | 409 | `AI_SOURCE_CONFLICT` |
| 无不可变快照 | 409 | `AI_SOURCE_UNVERIFIED` |
| 原件缺失或被改动 | 409 | `ORIGINAL_UNAVAILABLE` |
| 重复请求不同内容 | 409 | `AI_IDEMPOTENCY_CONFLICT` |
| 预算已用 | 409 | `AI_CAMPAIGN_ALREADY_USED` |
| 预算回退/超限 | 409 | `AI_BUDGET_RESET_FORBIDDEN` / `AI_BUDGET_EXHAUSTED` / `AI_TOOL_BUDGET_EXHAUSTED` |
| 结果引用伪造证据 | 409 | `AI_EVIDENCE_NOT_FOUND` |
| 取消后不可运行 | 409 | `AI_TASK_CANCELLED` |
| 结果摘要不一致 | 503 | `DATA_INTEGRITY_ERROR` |
| Live 未准入 | 409 / 503 | `AI_LIVE_ADMISSION_REQUIRED` / `AI_LIVE_NOT_ENABLED` |

## 6. 需要 Codex 接入的最小接口

1. 实现 `runner(service, task_id, row, actor, guard)` 回调，在自身模型请求前与工具调用前各调
   `guard()`；不得自行读取环境变量或浏览器提交的指纹。
2. 结果必须按 `ProgrammingResult` 组装，证据锚点只能用入库记录（`asset_id` + 考号 + 题号，
   可选 `source_index/source_row/test_index`）。
3. 预算通过 `service.budget(task_id, request_count=…, tool_attempts=…)` 记账，只增不减。
4. 由 Codex 提供受控的 Live 准入与价格/预算来源，替换本轮 `_require_live()` 的拒绝分支；
   API 路径、DTO 与错误码在本 PR 内保持稳定。
