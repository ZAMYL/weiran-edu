# V1.3.1 后端接线：已落地的准入闸门与剩余门槛

> ⚠️ 本文部分内容已被 V1.4 取代，以 docs/V1.4_BASELINE.md 为准。

日期：2026-11-16。基线 `95b6494`，AI 核心取 `codex/edu-v131-qwen-agent-core`（`36f8cc8`，含 #82/#83/#95 整合）。A 段的离线接线、迁移与「不可用」状态见 [QWEN_V131_A_STAGE.md](QWEN_V131_A_STAGE.md)；真实付费运行仍未发生。

## 本轮已实现（可离线验证）

新增 `prototypes/durable_teacher_agent/live_admission.py`：

- `DatabaseLiveApproval`：数据库来源的 Live 许可。字段与 #82 审批同构，但 `source_fingerprints` 绑定的是**数据库快照摘要**，不是合成 recipe 指纹——因此合成审批无法被挪用来外发教师批次。
- `LiveAdmission.load()`：启动时从环境读取审批文件与账本根目录；审批文件必须在仓库外、可读、不超过 8 KiB；账本根必须是仓库外绝对路径。任一条件不满足即抛错，**不会降级为 Mock**。
- `LiveAdmission.require(scope)`：传给 AI 核心的外发回调。校验用途、模型、端点 SHA-256、来源摘要是否登记、审批是否过期、代码指纹是否一致。
- `DatabaseLiveBudget`：复用 #95 的服务端账本与审批索引，绑定 `store`/`task_id`/`campaign_id`，`origin=qwen_live`；同一 campaign 只能开一次，拒绝调用方自带目录。

离线测试 `tests/durable_teacher_agent/test_live_admission.py`（9 项，全部通过）：缺环境、审批在仓库内、过期、代码指纹不符、账本缺失/相对/在仓库内、用途/模型/端点/来源四类作用域不匹配、账本单次消费、拒绝调用方目录。

## 距离真实 Qwen 网页请求仍缺的安全门槛

以下五项按依赖顺序排列，前两项**不在后端可独立完成的范围**：

1. **独立的数据库 Live 许可与费用批准（阻塞）**
   需求方必须针对 `code_fingerprint()` 与本审批结构出具独立审核记录，并明确费用上限。没有它，`LiveAdmission.load()` 一律拒绝，任何请求都不会发出。仓库里现有的是 #82 的合成许可，不能替代。

2. **AI 核心缺少离线执行缝（阻塞离线验收）**
   `run_database_profiles(binding, *, limits)` 不接受 transport；`DatabasePlanner._admit_execution` 要求 `transport is None` 才放行。因此**正常路径无法在不发真实请求的前提下做端到端验收**。两条出路都需要 AI 核心负责人决定：
   - 新增一个受审的离线 transport 注入点（推荐，能让整条链路离线跑通）；
   - 或先批准一次付费运行作为验收。
   在这一点明确前，后端不会为了让测试通过而伪造模型响应。

3. **grant purpose 迁移（后端可做，依赖 1）**
   现有 DDL 为 `CHECK(purpose='synthetic_mock')`，SQLite 需重建表。迁移会做成可校验、可回退的版本，并与 `store.authorize` 的两处目的校验同步放开到 `database_live`。

4. **画像投影与分批披露重建（后端可做）**
   交接文档第 4 条：`raw_result["profiles"]` 不能直接替代持久化 `ProfileInvestigationResult`；须用 `project_selected(book, raw_result, executor, staged=…, selection_origin=raw_result["origin"])` 重建并比较，再把实际 staged 分区交给 `extend_proof`。不能用空列表绕过累计披露。

5. **来源与回读（后端可做）**
   结果、proof 与真实 `ExecutionProvenance` 绑定；`replay_profiles` 内部默认 `http_mock` 的 selection_origin，Live 回读必须从可信执行模式重建，不能采信待核验 JSON 的自报值。

## 前端契约（草案，交林明）

任务查询响应在原 `TaskView` 基础上**只增不改**：

```json
{
  "task_id": "…", "state": "completed",
  "origin": "qwen_live",
  "provenance": {
    "origin": "qwen_live",
    "state": "completed",
    "request_state": "responses_received",
    "model_requests": 4,
    "tool_attempts": 5,
    "source_replay_verified": true,
    "error_code": null
  },
  "result": { "…现有字段…", "real_qwen": "completed" }
}
```

解码规则（必须遵守）：

- `origin` 只取 `qwen_live` / `http_mock` / `not_run`，不得把 Live 改标为 `http_mock` 以复用旧解码。
- `source_replay_verified` 必须为 `true` 才允许展示为可信结果；否则按失败处理，不显示画像。
- `provenance` 只来自服务端预算 journal 与执行账本，**前端不得自行推断或补全**。
- 合成演示仍需同时显示「合成」标注，不能与真实运行混淆。

## 本轮未做

- 未发起任何付费模型请求，未读取 API Key。
- 未修改 `apps/web`、未改评分/画像算法、未动 release/main。
- 未实现第 3–5 条接线（等待第 1、2 条明确）。
