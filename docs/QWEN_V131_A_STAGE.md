# V1.3.1 A 段：离线接线与「不可用」状态

日期：2026-11-16。范围：不发起真实模型请求，不读取 API Key，不启用付费运行。

## 已落地

- **生产 AI 路由骨架**：`app/routers/ai.py` 注册任务查询、取消、记录回查、评分依据、审核提交和事件入口；无服务端 AI 服务时统一返回 `503 AI_LIVE_NOT_ENABLED`，不再让正式页面得到误导性的 404。
- **能力查询**：`GET /api/v1/edu/ai-capability` 返回 `available/mode/reason/message`。正式环境默认挂载；测试宿主安装服务后返回 `http_mock`。
- **开关**：`Settings.ai_task_routes`。生产环境默认开启，开发环境需显式设置 `WEIRAN_EDU_AI_TASK_ROUTES=1`；测试宿主走原有 `api.install()`，不重复挂载。
- **前端**：任务 DTO 接受 `qwen_live` / `completed` / `failed` 并校验 `provenance`；连接后读取能力，正式页面在不可用时显示「AI 分析暂不可用」，合成演示仍显示原面板。
- **Next relay**：`EDU_AI_LIVE_MODE=1` 与合成开关分离；`ai-capability` 始终可读，任务路由仍按开关放行。
- **迁移**：已有演示库通过 `ALTER TABLE` 增加 `provenance_json`；`TaskView` 从已校验的 provenance 推导 `execution_origin/real_qwen`，不把 Live 标成 Mock。
- **拒绝路径**：未配置批准文件的 `database_live` 任务以 `AI_LIVE_ADMISSION_REQUIRED` 失败，不读取 Key、不创建 HTTP 客户端。
- **离线 E2E**：通过 #98 的 `run_database_profiles_offline` 驱动真实任务行，覆盖 HTTP 提交 → 事实 → 记录/评分依据 → 审核 → MD/XLSX 导出；结果保持 `http_mock` / `not_run`。

## 验证

```powershell
# apps/api
python -m pytest tests/test_ai_routes.py tests/test_config.py `
  tests/durable_teacher_agent/test_live_execution.py `
  tests/durable_teacher_agent/test_offline_wiring.py `
  tests/durable_teacher_agent/test_offline_live_e2e.py `
  tests/durable_teacher_agent/test_provenance_migration.py -q
python -m pytest tests/durable_teacher_agent -q

# apps/web
corepack pnpm lint
corepack pnpm format:check
corepack pnpm typecheck
corepack pnpm test
corepack pnpm build
```

实际结果：后端新增/定向测试 18 passed；`tests/durable_teacher_agent` 108 passed（含原有用例）；前端 258 passed，lint、format、typecheck、build 通过。

## 未做

- 未发起真实 `qwen_live` 请求；没有真实延迟、Token、费用或模型质量结论。
- 生产 Live 的批准、费用上限和账本根仍需独立审核；A 段的 `http_mock` E2E 不能替代真实网络验收。
- 生产 `_execute_live` 的付费成功路径尚未经过真实网络验证；在批准前保持 fail-closed。
