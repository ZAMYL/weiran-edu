# V1.3.1 真实 Live 路径集成修复记录

日期：2026-11-17。范围：P0-1 原子提交、P0-2 正式 FastAPI Live 挂载。真实付费联网请求未执行；本记录中的网络均由 `httpx.MockTransport` 拦截。

## P0-1 原子提交

`TaskStore.finish(..., provenance=...)` 在**一个 SQLite 写事务**内更新：

- `state=completed`
- `result_json` / `result_sha`
- `proof_json` / `disclosure_json`
- `provenance_json`
- `result_committed` 事件

`_execute_live()` 先计算并校验 `ExecutionProvenance`，再调用 `finish()`；不再存在“先 completed、后 provenance”的第二段写入。

回归：

- `test_live_completion_rolls_back_result_and_provenance_together`：在 `result_committed` 事件处模拟中断，断言任务变为 `failed`、`result_json is None`、`provenance_json == "{}"`。
- `test_live_formal_path_create_execute_readback_concurrent_and_restart`：完成态被 8 个并发 `service.view()` 读取，结果与 provenance 一致；新建 `TaskService`/`TaskStore` 重启后回读一致，队列为空。

## P0-2 正式 FastAPI Live 挂载

`app.main.create_app()` 在以下条件同时满足时：

```text
WEIRAN_EDU_ENV=production
WEIRAN_EDU_REAL_DATA_APPROVAL 已配置
```

直接构建 `ProductionDatabaseProvider`、`TaskStore`、`TaskService`，调用 `install_production()` 挂载真实任务路由；**不挂 503 骨架**。

`app.production:create_production_app` 复用同一路径。

正式路径回归 `test_live_formal_path_create_execute_readback_concurrent_and_restart`：

1. `create_app` production + 真实数据批准文件；
2. `grant_database_live()`；
3. `POST /ai-investigation-tasks` → 202 queued；
4. `TaskWorker.run_once()` → 真实 `_execute_live()`；
5. MockTransport 返回原生工具调用，模型选择/工具执行/事实核验；
6. `GET` → completed，`execution_origin=qwen_live`、`real_qwen=completed`、`source_replay_verified=true`；
7. 评分依据 HTTP 查询；
8. 8 路并发回读；
9. 重启 `TaskService` 后回读一致。

`test_formal_create_app_installs_the_live_service_when_approved` 另外证明：正式 app 的 `POST` 由真实 `TaskService` 处理（未授权 grant 返回 403 `AI_PURPOSE_REQUIRED`，不是 503）。

## 验证记录

```text
tests/durable_teacher_agent/test_live_formal_path.py                       1 passed
tests/durable_teacher_agent/test_live_branch_offline.py                   2 passed
tests/durable_teacher_agent/test_live_admission.py                       10 passed
tests/durable_teacher_agent/test_production_routes.py                    3 passed
tests/durable_teacher_agent（完整）                                      122 passed
```

Ruff check / format：通过。

未完成：真实 DashScope 联网验收、真实学生数据运行、Docker 构建（本机无 Docker）。这些需要最终代码授权和服务器。
