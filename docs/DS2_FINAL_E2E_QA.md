# DS-2 未然 EDU 全链路技术质检（ds/final-e2e）

基线：`release/v13-final-candidate` @ `e1c15ca092522aeb5b9fb1d4431cf09b7a114576`
范围：前端 DTO ↔ FastAPI/合成宿主契约、AI 任务生命周期、群体画像、权限/撤权/超时/快照、
教师待核查与报告导出、40 秒代理冲突、AI 运行来源真实性。

## 一、结论概览

- 前端契约层（`apps/web/src/services/edu-api/`）与后端 Pydantic DTO（`app/edu/*`、
  `prototypes/durable_teacher_agent/*`、`prototypes/teacher_agent_bridge/*`）在**已接通路径**
  上基本一致；解码器对未知字段 fail-open、对必需字段/字面量 fail-closed，安全取向正确。
- 本基线遗留一个已定位但**未合入的 Windows 子进程 15s 超时**问题（见 P1-1），本次已修复。
- 一个 P1 阻断需彭仲楷处理：`review-submissions` 同步重放证明耗时可能 >40s，与前端代理
  40s 超时冲突（见 P1-2）。本轮不再修改该路径。

## 二、已复现/已定位 Bug

### P1-1（已修复，属 Windows 超时缓解）：Windows 子进程 spawn 慢，15s 超时在负载下必超时
- 位置（修复前）：
  - `apps/api/app/edu/discovery_bridge.py:34` `MAX_SECONDS = 15.0`
  - `apps/api/app/edu/discovery_worker.py:85` `timeout: float = 15`
  - `apps/api/app/importing/edu/worker.py:26` `parse_isolated(..., timeout: float = 15)`
- 现象：Windows `multiprocessing` spawn 冷启动（解释器 + 模块导入）在负载下约 7–10s，
  15s 预算对「发现计算」与「Excel 解析」在并发下必然超时 → `DISCOVERY_TIMEOUT` /
  `PARSE_TIME_LIMIT` / `PARSER_UNAVAILABLE`。
- 修复：三处默认超时统一 15 → 60（仅改默认值；显式传入的超时/负值分支保持不变）。
- **性质：Windows 超时缓解，不等于端到端性能问题已彻底解决。** 它把「15s 内必超时」
  放宽为「60s 内可完成」，但受前端 40s 代理上限与 `review-submissions` 同步重放的共同制约
  （见下）。

### P1-2（阻断，交彭仲楷，本轮不修改）：教师提交待核查 >40s vs 前端 40s 代理
- `review-submissions`（`prototypes/durable_teacher_agent/review.py::submit`）会
  `service.replay()` 重放整个 agent 证明（约 8 次工具查询 × 子进程 spawn，受
  `SLOTS=2` 串行化）。轻载约 33s，E2E 并发下可 >240s。
- 前端代理 `apps/web/src/app/api/edu/[...path]/route.ts` 对上游固定 40s 超时，客户端 45s。
  该 POST 在负载下必然被代理 504 截断。需要把「提交待核查」改为异步受理或削减同步重放。

### P2（潜在，未触发，本轮不重构）：question 格式前端死代码契约不一致
- `client.itemStatistics` 用 `assessmentStatisticsDto`（期望 `item_statistics`）或
  `questionStatisticsDto` 解码 `GET /batches/{id}/statistics`，但后端该路由实际返回
  `Statistics`（`dimensions` + `matrix`）。
- `questionImport` / `itemEvidence` / `patchQuestionImport` 无后端路由；对应组件
  `AssessmentItemPage` / `QuestionImportPreview` / `QuestionEvidenceDrawer` 当前未被路由引用
  （死代码）。若后续接入 question 导入需先补齐后端 question 契约。
- **本轮处理**：仅记录为潜在问题，不做临时重构；当前主流程（评价五维）不经过这些路径。

## 三、各检查项结论

| 检查项 | 结论 |
| --- | --- |
| 1 前端 DTO ↔ 后端请求/响应 | 已接通路径一致；发现上述 P2 潜在不一致 |
| 2 任务创建/轮询/失败/取消/重入/刷新恢复 | 覆盖良好（幂等键、快照校验、sessionStorage 恢复、单次轮询无死循环） |
| 3 群体画像维度 ↔ 评分依据查询 | `profileDimensionDto` 与 `present()`/`ProfileScoreResult` 对齐 |
| 4 零画像/无权限/撤权/超时/旧快照/跨批次 | 错误码与状态机齐全（`AI_PURPOSE_REVOKED` 等） |
| 5 教师待核查/报告保存/MD/XLSX 导出重读 | 契约一致；xlsx 为真实 openpyxl 产物（PK 魔数通过） |
| 6 重复点击/重复请求/错误处理/死循环 | `useRemote`、`AiInvestigationPanel` 无死循环；`operate` 防重复提交 |
| 7 40s 代理 vs 后端耗时 | 见 P1-2（阻断）；补丁交互见下节 |
| 8 AI 来源不得冒充真实模型 | `execution_origin=http_mock`、`real_qwen=not_run`、`production_enabled=false` 均强制，前端 fail-closed |

## 四、40s 代理与 60s 补丁的交互（补丁是否可能超过 40s 代理上限）

- 前端代理对上游固定 **40s** 超时（`route.ts` 的 `setTimeout(..., 40_000)`），客户端 45s。
  补丁把发现计算与解析默认上限放宽到 60s，因此在 **40–60s 这一窄区间**，后端仍可能在计算，
  但代理已先 504 返回。
- 影响面：
  - **发现/洞察查询**：`DiscoveryReader.calculation` 的 `disconnected()` 每 0.02s 轮询，
    代理 40s 中止会经上游连接断开被感知 → 抛 499 `DISCOVERY_CANCELLED` 并杀掉子进程，
    **不会留下 60s 孤儿计算**。15–40s 区间由「失败」转为「成功」，属净改进。
  - **Excel 解析导入**：`parse_isolated` 在线程池内无显式断连检查，40–60s 的解析可能
    「代理已 504、解析线程仍跑至完成或 60s」，但有 `SLOTS=2` 限流且有上界，属低概率。
  - **`insights.py` 的 `MAX_SECONDS=15.0` 本次未改**，洞察仍 15s 上限（远低于 40s）。
- 结论：补丁**不会系统性**让常规请求超过 40s；仅在 40–60s 窄带存在「代理先 504」的
  理论窗口。这是 Windows 超时缓解的固有边界，彻底的端到端修复需在 40s 代理之外处理
  长耗时路径（尤其 `review-submissions`，见 P1-2，彭仲楷负责）。

## 五、交接说明（最小复现 / 耗时 / 原始错误 / 504 风险）

- **最小复现**：在 Windows 本机并发跑 `tests/durable_teacher_agent`（或启动完整演示宿主
  后提交 AI 待核查并重新下载报告），原始基线会稳定复现
  `test_tasks.py::test_persist_reload_evidence_review_and_report` 失败。
- **原始错误**：`{"error":{"code":"DISCOVERY_TIMEOUT","message":"发现计算超时"}}`（HTTP 503）。
  根因是发现重算子进程 spawn 超过 15s 预算。
- **测试耗时**：原始 15s 基线整套 `durable_teacher_agent` 约 15:36（936s）；修复后仅
  定向跑 `test_tasks.py` 约 4:00（240s，5 项全绿）。后端其他定向：
  `test_analysis_api + test_export_check` 74 项 144s；`test_worker + test_discovery_worker`
  8 项 46s；前端 vitest 211 项约 6s。
- **前端 504 风险**：`review-submissions` 同步重放证明 >40s（负载下 >240s）会被代理 504
  截断，是当前唯一确定的「前端 504」路径；本次补丁不改动该路径。

## 六、测试结果（本机，未占用 3000 端口 / 不抢占共享会话）

- 前端单测：`211 passed`（vitest，`src/**/*.test.ts`；生成模板后全绿）。
- 后端定向：`apps/api/tests/edu/test_analysis_api.py + test_export_check.py` = `74 passed`。
- 后端定向：`apps/api/tests/edu/test_worker.py + test_discovery_worker.py` = `8 passed`
  （验证 15→60 默认超时改动无回归）。
- 后端 `tests/durable_teacher_agent`（原始 15s 基线）：`1 failed / 48 passed`，失败为
  `test_tasks.py::test_persist_reload_evidence_review_and_report` → 报告下载时
  `DISCOVERY_TIMEOUT`(503)，即 P1-1 的复现。
- 后端 `tests/durable_teacher_agent/test_tasks.py`（修复后 60s）：`5 passed`（240s），
  上述失败转绿，验证 15→60 修复有效。
- 浏览器实跑 E2E（需共享服务）待 DS-1 释放后执行，见下。

## 七、交给最终集成的 E2E 命令

前置（仓库外 DB、受控凭证见 `docs/DATABASE_BATCH_AUTH.md`）：

```powershell
# 完整演示宿主（真实 HTTP + SQLite 合成数据；Identity=first 空教师 / sample 样例教师）
powershell -ExecutionPolicy Bypass -File scripts\local\start-demo.ps1 -Identity sample
```

前端（`apps/web`）：

```powershell
corepack pnpm test        # 生成模板 + vitest + tests/smoke.mjs
corepack pnpm typecheck
corepack pnpm lint
```

后端（`apps/api`，需先 `uv sync --locked --group dev`）：

```powershell
cd apps\api
uv run --locked pytest -q tests/edu tests/durable_teacher_agent
```

浏览器实跑（DS-1 释放 3000 端口后）：

```powershell
cd apps\web
$env:EDU_TEST_BROWSER='C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
node tests/ai-task-e2e.mjs
node tests/teacher-adaptive-e2e.mjs
```
