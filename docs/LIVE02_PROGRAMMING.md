# LIVE-02 正式编程任务真实 Qwen 接线

本文件描述受控服务端接线，不是产品前端说明。实际执行仍受独立审批、价格有效期、
本机凭证与代码指纹四重约束；任一缺失都会在创建或执行前失败关闭。

## 1. 边界

- 正式入口：`POST /api/v1/edu/assessments/{assessment_id}/ai-tasks`。
- 浏览器只能提交 `purpose`、`idempotency_key`、`timeout_seconds`。模型密钥、来源
  指纹、审批路径、工具列表、消费额度均不接受客户端输入。
- FastAPI 只入队；生产 `ProgrammingWorker` 用条件 `UPDATE` 认领 `queued` 行。
- Codex 的 `ProgrammingPlanner` 决策、工具 Schema、完成规则保持不变。服务端只增加
  Live 准入覆盖和持久化。
- Codex 的 LIVE-01 `prototypes/research_agent_v2/programming_live.py` 是固定
  `task_id` / 内置合成 ZIP 的操作员 CLI；正式 POST 使用动态测评来源，因此本接线新增
  一个服务端 `ProgrammingLiveApproval`，字段语义与其保持一致，但没有修改 Codex 的
  决策逻辑。

## 2. 环境变量

| 变量 | 含义 |
| --- | --- |
| `WEIRAN_EDU_DATABASE` | 生产 SQLite 路径，必须在仓库外 |
| `DASHSCOPE_API_KEY` | 仅进程读取；不写日志、不进请求体 |
| `DASHSCOPE_BASE_URL` | 必须是已审定的北京兼容端点 |
| `WEIRAN_QWEN_MODEL` | 仅允许 `qwen3.8-flash` |
| `WEIRAN_EDU_PROGRAMMING_LIVE_APPROVAL` | 独立审批 JSON 的绝对路径，仓库外 |
| `WEIRAN_EDU_CAMPAIGN_LEDGER_ROOT` | 服务端独占账本根，绝对路径、仓库外 |

部署时该文件要同时挂给 `api` 与 `programming_worker` 两个容器；接线、挂载与启动顺序见
[`deploy/README.md`](../deploy/README.md)，可复制的审批样例见
[`deploy/programming-live-approval.example.json`](../deploy/programming-live-approval.example.json)。

启动服务端：

```bash
cd apps/api
uv sync --locked
uv run --locked uvicorn app.production:create_production_app --factory
```

启动 Worker（独立进程）：

```bash
cd apps/api
uv run --locked python -m app.programming_worker_main
```

## 3. 审批 JSON

审批文件只由操作员创建，路径不进入 HTTP。字段：

- `campaign_id`：唯一账本 id。
- `human_approval_reference`：人工审批引用。
- `purpose`：固定 `programming_live`。
- `model`：固定 `qwen3.8-flash`。
- `endpoint_sha256`：`sha256(base_url.rstrip('/'))`。
- `source_fingerprints`：`{assessment_id: source_snapshot_id}`，只允许精确匹配。
- `price_fingerprint`、`valid_until`：当前审核价快照；当前 `REVIEWED_PRICE_20261125`
  有效至 `2026-12-02`。
- `max_requests`：3–6；`max_output_tokens`：1–800；`total_token_limit` 与
  `cost_cap_cny` 为服务端费用上限。
- `independent_review_reference`、`reviewed_code_sha256`：独立审查引用与代码指纹。
- `reviewed_code_sha256` 必须在所有 AI/kernel 代码冻结后重新计算，任何 app/
  prototypes 改动都会改变它。
- **一条 `human_approval_reference` 只能资助一个 campaign**：同一个测评重新调查也要
  新审批文件，复用会报 `AI_CAMPAIGN_ALREADY_USED`。

审批文件读取时校验：绝对路径、仓库外、≤8192 字节、价格有效期、代码指纹和来源格式。

## 4. 状态与持久化

- `ai_programming_tasks.state`：`queued / running / completed / failed /
  cancelled / timed_out / revoked`。
- `budget_json`：服务端单调递增的 `request_count`、`tool_attempts`。
- `provenance_json`：真实 `execution_origin`（`qwen_live` / `http_mock` / `none`）、
  `execution_mode`、`real_qwen`、`production_enabled`、审批引用/摘要、预算轨迹、
  受控工具轨迹与结果摘要。
- `ai_programming_events`：`queued`、`running`、`model_budget`、`tool_audit`、
  `run_summary`、`result_committed`、`stopped_without_result`。
- Mock 测试明确写 `execution_origin=http_mock`、`real_qwen=not_run`、
  `production_enabled=false`，不得冒充真实 Qwen。
- 失败、取消、撤权、超时清空 `result_json/result_sha`；已完成结果重启后仍需逐字节
  回读原件并通过摘要校验。
- 生产启动不再整站 fail-closed：`WEIRAN_EDU_PROGRAMMING_LIVE_APPROVAL` 存在但审批
  过期、指纹不符或缺 Key 时，`api` 照常启动，状态记在 `app.state.programming_live`
  （`unavailable: …`），只有编程调查被拒（`AI_LIVE_ADMISSION_REQUIRED`）；上传、查询和
  其他 AI 路径不受影响。
- 撤权没有 HTTP 入口，走 operator CLI：
  `python -m app.edu --database <db> programming-revoke --assessment-id <id> [--snapshot-id <snap>]`。
  撤销后授权标记 `revoked`，未发布结果清空。
- 运行被取消、撤权或超时时，账本里仍在途的预留会写成 `closed_without_settlement`：
  保留上限金额、标明“用量未知”，既不冒充 0 成本，也不长期挂着 `reserved`。注意：若取消
  后 worker 进程被立即杀掉，账本可能仍停在 `reserved`（同上保留上限）；该收尾覆盖率依赖
  worker 能执行到 `finally`。

## 5. 验收命令

```bash
cd <repo-root>
PYTHONPATH=. apps/api/.venv/bin/python -m pytest apps/api/tests/edu/test_programming_live.py -q
PYTHONPATH=. apps/api/.venv/bin/python -m pytest apps/api/tests/edu -q
PYTHONPATH=. apps/api/.venv/bin/python -m pytest apps/api/tests/storage apps/api/tests/importing -q
cd apps/api && uv run ruff check . && uv run ruff format --check .
```

## 6. 真实运行状态与证据

2026-11-24 的两轮真实运行都跑在 GitHub Actions `ubuntu-latest` 上。第一轮（旧指纹
`18f593…`）3 次请求、2 次工具调用；代码冻结后第二轮在 head `5c56abb`（指纹
`b2d5533e…`）上重跑，**证据以第二轮为准**。

- 正向（run `45409512840`，job `real`）：任务 `completed`，`execution_origin=qwen_live`、
  `execution_mode=live_network`、`real_qwen=completed`、`production_enabled=true`；
  2 次真实请求全部 HTTP 200，1 次工具调用 verified，`charged_tokens=1947`、
  `charged_cny_upper_bound=0.0017457`，`result_sha=499c382e2663bcc69a15136a0f62d629ff38fdaca3b0e9a4cabb150197533e04`；
  重启后 `GET` 仍为 `completed` 且结果一致。模型结论是“当前证据未支持可披露的编程发现”
  （`finding_count=0`），这是对样本的真实结论，不是失败。
- 失败关闭（同一轮换了 3 次尝试）：模型给出无法用已核验事实支撑的结论时，任务以
  `failed` / `AI_PROGRAMMING_EVIDENCE_UNAVAILABLE` 结束，`result_sha` 为空，
  `findings` 不落库——校验器拒绝了“看起来像结论”的答案。
- 负面（run `45409512840`，job `negative`）：截止时间过期 `timed_out` /
  `AI_TASK_TIMEOUT`、预算不足 `failed` / `AI_BUDGET_AUDIT_MISMATCH`、认领前取消
  `cancelled` / `AI_TASK_CANCELLED`，三者 `request_count` 均为 0 且结果为空；运行中取消
  在发出 1 次请求后停止（`request_count=1`），仍无结果。
- 证据：`evidence/live02-evidence.json`、`evidence/live02-negative.json`（分支
  `live02-evidence`）；两份 JSON 里 `reviewed_code_sha256` 都是 `b2d5533e…`。

样本说明（必须和证据一起看）：旧的 10 份全失败合成答卷在新指纹上连续三次得到不可核验
结论，于是这一轮把合成答卷改成 **1 份失败 + 9 份通过**，给模型一个单一、明确的失败
方向。这是测试用样本的调整，没有改动产品代码；它也解释了为什么正向结果是“无可披露
发现”而不是“查出一个 bug”。

证据边界：

- 上述运行使用的 `human_approval_reference` 与 `independent_review_reference` 都是
  占位字符串，只证明代码与链路可用，**不构成独立审批放行**。
- 正式验收仍需两条真实、来源不同的审批引用，并在冻结后的代码指纹上再跑一次。
- 价格快照 `REVIEWED_PRICE_20261125` 有效至 `2026-12-02`；过期必须重新做价格审查、
  生成新 `price_fingerprint`，不能只改日期。

## 7. 变更记录

- 2026-11-24 第二批：启动降级（审批失效只拒编程任务，不拖垮上传/查询）、
  operator `programming-revoke` CLI、预算账本 `closed_without_settlement` 收尾。
  本提交改动了 `apps/api/app/**`，代码指纹随之变化，已在 `b2d5533e4c25cc198d4c24c7892656dd1828831da103fe2865c1f2d24961d061`
  上重跑正向与负面（run `45409512840`），旧证据被本轮覆盖。
