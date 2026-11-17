# 合成授权调查任务闭环（候选契约）

本切片叠加在冻结的 #70 `fa49f6ef10160f103cd15a7c2f66d3a2d8767b63`，不修改该 PR 分支。#70 尚无正式 Review；Peng-Kai789 和 AooMing 的审核请求仍在。

**可运行范围：注册合成工作簿 → 真实导入/SQLite/教师凭证 → HTTP Mock 调查 → 持久回读 → 原评分依据 → 正式待核查结果 → 原教师确认 → 原 MD/XLSX 报告。**

Production、真实教师 AI 用途授权和远程 Qwen 均关闭。不是已上线的教师 AI 功能，也不证明模型优于固定分析。固定 5/9、Mock 6/9 及冻结 Oracle 未改。

## 启动与复现

在本分支根目录，Python 3.12、已有 `apps/api` 锁定依赖：

```powershell
$env:PYTHONPATH='.;apps/api'
$env:PYTHONUTF8='1'
uv run --project apps/api --locked python -m prototypes.durable_teacher_agent.demo --output D:/Temp/edu-durable-http.json
```

该命令生成完全合成工作簿、临时 SQLite 和一个小时的测试凭证，使用真实回环 TCP。第一个 Uvicorn **进程退出后**，第二个进程重新创建服务/Provider，读取原任务，再核查证据、提交候选、教师确认、重新打开 MD/XLSX。最后撤销凭证、停止进程并删除临时数据库。不会读取环境中的模型 Key，也不会访问模型网络。

API 只能由测试宿主显式 `install()`。`app.main` 未挂载新路由，正式教师应用行为不变。`build_host(..., install_bridge=False)` 防止旧内存接口绕过本切片。不要把 `environment=test` 或文件 SHA 当作正式用途许可。

## 状态和持久化

宿主显式初始化三个 `ai_synthetic_*` 表，与原批次/评分/分析结果在**同一 SQLite**，不是第二个报告数据库：

- grant：教师、唯一 batch/snapshot、原始来源经核验的 manifest、固定用途 `synthetic_mock`、到期及撤销。只能本机服务端 operator 签发，HTTP 请求不接受授权布尔值。重复签发不续期、不恢复撤销。
- task：教师及发起凭证摘要、grant、campaign、受控目标、幂等键、状态、deadline/expiry、模型预算、工具次数、披露历史和核验结果。任意问题文本只映射成受控目标及项目后持久化。
- event：按事务内序号记录真实工具尝试、验收、模型预算预留/结算、失败和报告承接。没有 Prompt、原评语、姓名、原始文件或 Key。

`queued → running → completed/failed/cancelled/timed_out`；用途撤销后为 `revoked` 并删除可读结果。到期返回 410。completed 表示本次执行返回，不代表穷尽调查；还须查看 `result.stop_reason`、缺证和未覆盖方向。异常执行不发布部分事实。

`BEGIN IMMEDIATE` + 教师幂等键唯一 + grant/campaign 永久唯一：同键同目标重读同一任务；同键不同目标 409；更换请求 ID、进程或 journal 目录也不能启动第二次 campaign。取消、失败、超时不退款。

复用原 `CampaignBudget` 的独占 fsync journal，并在每次模型预留/结算完成前同步 SQLite；任何一侧失败均停止。默认上限仍为 **6 模型请求、5 工具尝试**。HTTP Mock 的 Token/价目账本仅验证预算机制，**实际远程请求为 0，实际 Token、账单均为空**。

### 重启与并发的明确限制

- 已完成结果可在新进程中读取；证据查询/报告准入会重放整个有序事实与累计披露集合，不是相信持久 JSON 或把旧 Fact 塞入新调查。
- 在途任务不自动恢复、不重发模型、不重置预算；崩溃后等待 deadline 后变为 timed_out。没有分布式队列、跨机恢复或续查协议。
- 取消/撤权由每个模型/工具/来源校验边界执行；取消立即关闭结果发布，计算在安全检查点退出，不声称可以抢占任意 Python 指令。
- 发起凭证撤销后，即使同一教师换新凭证，也不能沿用旧 campaign。正式可迁移凭证与用途授权仍待后端设计。
- 报告承接在正式保存事务内再次检查用途及任务，并原子保存 task→result 关联。崩溃发生在“承接占位”与正式保存之间时保留 preparing、拒绝自动重试，需 operator 核对；不会静默复制分析结果。
- 这里只对一个注册合成批次/快照的 campaign 累计披露。跨真实批次、跨会话身份关联与撤销传播没有获批实现。

## 给林明的接口

公共前缀：`/api/v1/edu/batches/{batch_id}/ai-investigation-tasks`，继续使用原 Bearer 凭证、X-Request-ID、固定错误结构与 no-store。**仅测试宿主可见**。

| 操作 | 方法与后缀 | 输入 / 结果 |
|---|---|---|
| 发起 | POST 空后缀 | `{snapshot_id, question, idempotency_key, mode:"http_mock", strategy:"adaptive"}`；202 TaskView，立即返回，后台本机执行 |
| 状态/事实 | GET `/{task_id}?snapshot_id=...` | 状态、停止原因、实际预算、已核验聚合 facts、Trace；真实模型恒为 not_run |
| 取消 | POST `/{task_id}/cancel?snapshot_id=...` | 幂等状态；不会恢复预算 |
| 指标依据 | POST `/{task_id}/records/query` | 原 SourceRequest；先核验全部实际参与记录，再分页返回匿名 ID/原 evidence_path |
| 提交教师核查 | POST `/{task_id}/review-submissions` | `{snapshot_id,fact_ids:[...]}`；只选本任务事实，不接受统计/候选/confirmed |
| 继续画像探索 | POST 原 `/groups/query` | 使用 Fact 的 `exploration_query`，保留正式 batch/snapshot/条件 |
| 教师确认/不采纳 | PATCH 原 `/analysis-results/{result_id}` | 使用原 GET 返回的当前 expected_version 和 receipt.candidate_ids；三态机制不变 |
| 报告 | GET 原 result `/export` | 原 snapshot_id/expected_version/format；只导出教师 confirmed，不含个人逐人明细 |

`ReviewReceipt.version=1` 是初次提交版本；刷新原结果获取最新版本，不能在教师确认后继续把 1 当作当前版本。前端无需理解 campaign/hash，它们不是授权凭据。`result.facts[].source_refs[].participant_count` 是该指标实际参与人数，不是均值 Fraction 的分母，也不是全群体人数。

## 事实进入报告的资格

1. 当前凭证、owner、用途、batch、snapshot、来源 manifest 仍有效；重放原任务的有序工具、规则版本、数值、来源与披露历史。
2. 调用原 `DiscoveryReader.calculation`，执行正式发现规则和资格/稳健性；**没有新统计公式**。
3. 关联：项目对、rho、成对有效/排除人数及两项每条评分证据必须完全一致。
4. 群体：条件维度/运算符/精确阈值、结果维度、两组/不可判定/有效人数、均值、差值、M/N、M/E 及每项实际评分证据全部一致。正式搜索预算没有返回同一合格结果时拒绝；不能用成员相同但条件不同的卡替代。
5. 复用原 `DiscoveryResultService.create_discovery` 和原 analysis_results/version 表，初态只能 pending。task 关联在同一保存事务中提交，失败全部回滚。
6. 原教师 PATCH 确认或不采纳；原 `ResultExporter` 重核 snapshot、版本和证据，只将 confirmed 投影到 MD/XLSX。

统计摘要、质量观察、无量规的解释及不匹配的群体继续保留为观察，不擅自造正式候选。教师确认不是因果证明。正式结果一旦独立重算并保存，其后续访问按原教师统计/报告权限执行；撤销 AI 用途禁止继续读调查，不替代原统计结果删除/撤销政策。

## 固定错误与界限

- 401：原凭证过期/撤销；404：其他教师/不可访问批次或任务。
- 403 `AI_PURPOSE_REQUIRED/AI_PURPOSE_REVOKED`：没有本机用途准入或已撤销。
- 409 `SNAPSHOT_MISMATCH`、`AI_IDEMPOTENCY_CONFLICT`、`AI_CAMPAIGN_ALREADY_USED`、`AI_TASK_CANCELLED/TIMEOUT`、`AI_REVIEW_CONFLICT`。
- 410 `AI_TASK_EXPIRED`；422 非法字段、未知/不合格 Fact、正式规则未产生对应候选或适配未支持。
- 503 来源、数值、披露或规则不一致。失败不返回截断文件、伪成功事实或私密异常。

## 审核与尚未开放

请 Peng-Kai789 重点审核 SQLite 事务、用途/旧凭证撤销、不可重置预算、完整来源与正式结果承接；请 AooMing 审核实际 OpenAPI 与 DTO。新 PR 叠加 #70，不能将其 CI 当作 #70 独立批准，也不能把未审依赖合入 main。

真实教师用途授权、持久队列的故障恢复、跨 campaign 披露账本、正式动态 Provider、真实 Qwen 尚未开放。本轮没有读取教师原件、修改正式前端/路由/迁移、调用外部模型、部署或合并。#66/#67 独立审核、北京有效配置和单独费用批准仍是未来真实合成实验的门禁。
