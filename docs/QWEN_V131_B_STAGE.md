# V1.3.1 B 段：真实数据生产接入

日期：2026-11-17。基线：A 段之后的 `wip/v131-live-branch-diagnosis`。真实付费请求只在服务器、审批和真实数据授权就绪后执行；本文记录代码和离线验收，不把合成运行当作真实数据验收。

## 已完成

### B1 授权发放

- `TaskStore.grant_database_live()` 是操作员入口，只接受 `synthetic_mock` / `database_live` 两种 purpose。
- 已存在的 grant 不会被静默升级；purpose 不一致返回 `AI_GRANT_PURPOSE_CONFLICT`。
- 浏览器请求无法选择 purpose；`app.grant_live` 从服务端环境读取教师 token。

### B2 生产 Worker

- `TaskStore.queued()` 只返回未过期的 `queued` 行。
- `TaskWorker` 逐条领取，第二个 worker 在原子 `start()` 上失败；HTTP 进程不再在 `BackgroundTasks` 里执行生产任务。
- `app.worker_main` 作为独立进程入口。

### B3 Live 结果适配

- `_execute_live()` 调用 `run_database_profiles` 后，由 `_persist_live_result()` 转换为持久化 `ProfileInvestigationResult`：
  - 使用 `Invocation.verified_facts()` 生成事实，并把 `records_path` 改到任务路由；
  - 用真实 `staged` 分区调用 `project_selected`/`extend_proof`；
  - 写入 `qwen_live` / `completed` 结果与 `ExecutionProvenance`；
  - `dimension_evidence_http=live_task_profile_scores_v1`。
- `TaskStore.finish(..., provenance=...)` 把 `completed` 结果、proof、disclosure 和 provenance 放在**同一个 SQLite 事务**里；`result_committed` 事件失败会整体回滚，测试覆盖了“完成态与来源证明同时不存在”的路径。
- `InvestigationResult` 的 origin/real_qwen 联合类型已放宽；前端 DTO 接受 Live 结果和 Live 评分依据。
- `DatabaseLiveBudget` 把预算镜像回 `ai_synthetic_tasks.budget_json`，回读/状态不再依赖 CLI 账本。

### B4 真实数据准入

- `RealDataAdmission` 是仓库外的操作员 allowlist；来源 SHA 未列出时返回 `AI_REAL_DATA_NOT_APPROVED`。
- `app.production` 启动时把 `reviewed_code_sha256` 与当前 `code_fingerprint()` 比较；代码变化即拒绝。
- `ProductionDatabaseProvider` 懒解析真实原文件、校验原评分坐标，并拒绝未登记来源。
- 测试证明默认拒绝、批准后可构建聚合材料；模型视图不包含姓名或评语。

### B5 单机部署包

- `deploy/`：API/Web Dockerfile、Compose、Caddy TLS、`.env.example`、secrets/data/ledger 目录约定。
- **正式 `create_app` 自动接线**：当 `environment=production` 且配置了 `WEIRAN_EDU_REAL_DATA_APPROVAL` 时，`app.main.create_app()` 直接构建并安装 `Live TaskService` 真实路由；只有未配置批准文件时才挂 503 骨架。`app.production:create_production_app` 复用同一路径。
- `app.worker_main`、`app.grant_live` 两个操作员入口。

## 验证

```powershell
# apps/api
python -m pytest tests/durable_teacher_agent/test_live_grant.py `
  tests/durable_teacher_agent/test_live_branch_offline.py `
  tests/durable_teacher_agent/test_worker.py `
  tests/durable_teacher_agent/test_real_data_admission.py `
  tests/durable_teacher_agent/test_production_routes.py -q
python -m pytest tests/durable_teacher_agent -q

# apps/web
corepack pnpm lint
corepack pnpm format:check
corepack pnpm typecheck
corepack pnpm test
corepack pnpm build
```

实际结果（本机）：B1-B5 定向测试通过；完整 `tests/durable_teacher_agent` **121 passed**（含正式 `create_app` 安装真实路由、Live 完成原子回滚）；前端 **258 passed**（`vitest --no-file-parallelism --maxWorkers=1`），lint、format、typecheck 通过。`next build` 在 B 段后因本机 Node 堆内存不足（`Zone Allocation failed` / worker exit 134）未完成，不是 TypeScript 编译错误；A 段时同一构建曾通过。Docker 未在本机实际构建（本机无 Docker）。

## 真实合成数据运行（A 段之外，CLI）

2026-11-16 已在 `_qwen-approved` 干净副本上通过 reviewed CLI 完成一次真实 Qwen 运行：

- 4 次 HTTP 200，`origin=qwen_live`，`real_qwen=responses_received`；
- 29,459 Token，目录价估算 **0.0135214 元**（上限 4.78 元）；
- 2 个画像，`source_replay_verified=true`。

这是**合成数据**证据，不是真实学生数据，也不是产品 FastAPI 任务链路。

## 未完成 / 限制

- 没有服务器，Docker 部署未实际运行；Caddy/TLS/域名未验收。
- 真实学生数据尚未运行；`RealDataAdmission` 需要真实的来源 SHA、审批和有效期。
- `DatabaseLiveApproval` 仍是单文件、每 campaign 一份；并发任务需要操作员串行更换文件，或后续改成服务端审批表。
- SQLite + 本地账本 + 单 worker 只适合单机试点；多实例、Postgres、备份、SSO、监控、告警仍未实现。
- 真实教师数据外发字段和保留/删除策略需独立审核；代码只提供拒绝门禁，不替代合规判断。
