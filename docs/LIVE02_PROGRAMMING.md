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
- `price_fingerprint`、`valid_until`：当前审核价快照；当前 `REVIEWED_PRICE_20261119`
  有效至 `2026-11-26`。
- `max_requests`：3–6；`max_output_tokens`：1–800；`total_token_limit` 与
  `cost_cap_cny` 为服务端费用上限。
- `independent_review_reference`、`reviewed_code_sha256`：独立审查引用与代码指纹。
- `reviewed_code_sha256` 必须在所有 AI/kernel 代码冻结后重新计算，任何 app/
  prototypes 改动都会改变它。

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

## 5. 验收命令

```bash
cd <repo-root>
PYTHONPATH=. apps/api/.venv/bin/python -m pytest apps/api/tests/edu/test_programming_live.py -q
PYTHONPATH=. apps/api/.venv/bin/python -m pytest apps/api/tests/edu -q
PYTHONPATH=. apps/api/.venv/bin/python -m pytest apps/api/tests/storage apps/api/tests/importing -q
cd apps/api && uv run ruff check . && uv run ruff format --check .
```

## 6. 真实 Qwen 运行前提（当前阻塞）

当前构建环境没有 `DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`、
`WEIRAN_EDU_PROGRAMMING_LIVE_APPROVAL`、`WEIRAN_EDU_CAMPAIGN_LEDGER_ROOT`。因此
本机只能验证合成 Mock 接线和失败关闭，不能声称完成真实 Qwen 联网调查。补齐这些
环境变量、生成审批 JSON 并在最终代码 SHA 上重算 `reviewed_code_sha256` 后，才可
运行 `ProgrammingWorker`。
