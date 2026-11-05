# Research Agent 安全依赖收口（隔离研究，不是放行）

检查基线：#66 `445f3e2086c90fc0ba8c7271a15d433ffa0c2689`；#67 `c64a614fd79f3dc826c08e4c09fbd377fd6789bd`；#70 `fa49f6ef10160f103cd15a7c2f66d3a2d8767b63`；#72 `bda5d37955199f392154b370d335eda630099e75`。

## 当前结论

|版本|安全状态与操作|
|---|---|
|#66 445f3e2|Live 全封禁，准确提交 CI 已成功；Peng-Kai789 对新 SHA 尚无正式 APPROVED，代码冻结，不代批。|
|#67 c64a614 原分支|仍带旧 Research/adaptive Live 路径，尚未同步安全补丁，不得放行 Live。|
|本隔离候选（基于 c64a614）|已选择性同步封禁，更新依赖审核目标为445f3e2；封禁专项通过，但原测试两处正例与新政策冲突，**不是可直接合并候选**。|
|#70 fa49f6e|仅合成 DB+HTTP Mock；仍需后续选择性同步，不能把源 SHA/CI 当成独立授权。原分支不改。|
|#72 bda5d37|持久任务/报告闭环冻结；仍需同步上游封禁与处理 Windows CI。临时副本验证可保持32项闭环通过，不代表冻结分支已同步。|

真实模型请求、真实模型 Key 读取均为0。不合并、不部署、不改正式前端/API/数据库/统计/Oracle。

## #67 最小同步与原能力保留

用三方补丁而非整文件覆盖；实际只有两处 import 冲突：

- `live/budget.py`：保留 #67 的 `reviewed_price`、绑定价格及原全部预留/结算逻辑，只加入全局 Live 封禁。
- `research_agent_v2/runtime.py`：保留按 `budget.price` 计算容量的改进，不重新引入旧全局 PRICE，只加入封禁。
- 原 Research CLI、adaptive CLI、Planner、预算、runtime 均拒绝 Live；固定错误 `synthetic_live_disabled_pending_independent_review`，在 Key/Live ledger 之前停止。
- `review.DEPENDENCY_SHA` 从 f3799a1 更新到445f3e2。真实最新 APPROVED、准确 head、干净工作树检查逻辑保留；没有自动批准或伪造批准回执。
- `PilotApproval`、Pilot固定账本根、价格快照、PLAN及哈希、全部原测试文件逐字节不改。Live入口数仍为0；不是新建了一个可用的Live服务。

### 实际离线结果

- 同步前两个原 Live 正例：2 passed（7.29s），仅构造0请求账本，不读真实Key。
- 同步后联合检查：**75 passed / 2 failed（90.42s）**。两个失败原样保留：
  - `test_live_budget_cannot_reset_by_changing_report_path`：原本允许打开0请求 Live 账本；现在构造器应立即拒绝。
  - `test_real_network_requires_explicit_optin_and_environment_only_settings`：同样预期先构造 Live 账本，和新封禁政策冲突。
- 新封禁专项（含Pilot名义授权、准确依赖目标、复制授权、五层旁路、失败/取消）：**10 passed（2.90s）**。
- 原三组离线入口实际执行：固定 **5/9**、HTTP Mock **6/9**、真实Qwen **not_run**；原量规/Oracle未改。
- 原测试的价格、预算未知usage、精确审核状态、Provider权限/来源/撤权、52次重放等检查通过。没有新增xfail/skip、裁剪评分分母或放宽断言。

待仲楷确认的是两个测试的安全政策迁移，不能为了让它们通过重新允许Live构造。建议保留历史结果，新增“固定根参数不受报告路径影响”的无消费检查及“即使名义批准也不得打开Live”检查；获准后再更新原正例，不在本轮擅改。

## #70/#72 具体接点与兼容证明

- #70 `teacher_agent_bridge/api.py`：`open_budget` 固定 `origin=http_mock`；不改业务事实投影。
- `investigate_database`：精确 DatabaseSource/MockTransport/OfflineSnapshotPermit 身份必须保留。
- 共享 `_investigate`：封禁应放在实际执行入口；不得退回只接受recipe的旧runtime、覆盖DB分派或丢失durable_audit。
- `live/budget.py`：保留 OfflineSnapshotPermit 的 `origin!=http_mock → invalid_live_authorization` 原契约，再封禁所有其他Live构造；否则会破坏既有拒绝用例的错误码。
- #72 `DurableBudget` 的同库预算镜像、TaskStore占用/披露历史、worker领取权、AuthorizedEnvelope读取前后用途复查全部保持不变。
- 不改待教师核查承接、原分析结果版本、MD/XLSX生成器。

在 **bda5d37 的一次性副本**上只加入这些六文件改动：`tests/teacher_agent_bridge + tests/durable_teacher_agent` **32 passed / 1 warning（248.29s）**。包括持久回读、证据、教师确认、MD/XLSX、撤权/并发/预算/失败及重启；该副本的两个 bridge目录、正式app、全部原测试和CI配置均无差异。32项是闭环兼容证明，**不是1675项全量通过**；将来同步后，全量仍会要求解决相同的旧Live正例政策迁移，不能在AI分片中排除这些测试。

复审补丁：`docs/patches/teacher-live-containment-selective.patch`。它是供后续同步的零上下文代码补丁，只能在准确bda5d37隔离副本上先用 `git apply --check --unidiff-zero` 校验再应用；不能对未知版本直接套用。不代表已修改 #70/#72，也不代替新SHA下的独立审核。

## #72 Windows 实际超时与分片方案

准确运行：[41866645380](https://github.com/ZAMYL/weiran-edu/actions/runs/41866645380)。

- Windows job126703209867：北京时间11月5日00:24:22～00:39:31；pytest步骤00:24:43～00:39:25，**1497 passed / 1 warning / 879.57s** 后 KeyboardInterrupt。
- check annotation 明确 `exceeded ... 15m0s`；中断栈 `openpyxl/descriptors/serialisable.py:160`，不是已证明的测试断言失败。
- 同一SHA Ubuntu完整 **1675 passed / 1 warning / 627.04s**，Frontend成功。Windows还有178项未完成，不能计为178项失败或全量成功。
- `-q`日志没有当前nodeid。按同源1675项收集顺序，1497之后邻近 `tests/reporting/test_exports.py::test_scale_reports_come_from_actual_statistics[53]`；仅是定位线索，**不能认定它就是故障函数**，更不能归到旧Worker异常。

只收集不执行原测试，得到 **1675项/79文件**；按整文件保留fixture分组：

|Windows作业|项目数|目录|
|---|---:|---|
|core|300|根测试、analysis/comments/discovery/insights|
|ai_research|517|adaptive_assessment/ai_edu/research_v2/research_v22|
|edu_import|723|edu/importing|
|reporting|103|reporting|
|teacher_bridge|32|teacher_agent_bridge/durable_teacher_agent|

`1675=300+517+723+103+32`，覆盖完整且互斥。`scripts/plan_windows_pytest_shards.py` 从受控收集清单生成参数，拒绝重复nodeid、未知目录、路径越界或不完整分配；没有写死总人数/统计结果，也不执行测试。文件级参数见 `docs/ci/windows-shards-bda5d37.plan.json`。

后续经后端/CI负责人审核再应用：保留Ubuntu全量与Frontend已选配置，将Windows单作业拆成五作业，每个仍15分钟、同SHA/Python3.12/锁依赖/原环境；静态检查不删。各片从apps/api运行原pytest，增加`--durations=30`与JUnit采集，不使用缩范围的-k、排除、skip或xfail。最终聚合门禁须核对收集与实际执行nodeid并集完整、无重复，全部片通过才算Windows通过；缺片/取消/断言失败均阻断。

**分片仅完成清单与方案验证，未执行分片CI、未证明每片耗时；没有放宽15分钟，也未重复运行已知超时的全量任务。** 新增测试后的正式候选需重新收集清单，1675仅是bda5d37基线，不是永久配置。

## 复现与复审顺序

本机清单：`D:/Temp/edu-72-collection-bda5d37.json`（collect-only，Key getter已禁止）；分片生成命令：

```powershell
python scripts/plan_windows_pytest_shards.py --manifest D:/Temp/edu-72-collection-bda5d37.json --output D:/Temp/edu-72-windows-shard-plan.json
```

本机证据：`D:/Temp/edu-67-safety-sync-original-tests.xml`、`edu67-live-sync-denials.xml`、`edu67-contained-ab.json`、`edu-72-containment-compat.xml`。只含合成测试与代码结果，不含教师文件、凭证或真实学生数据。

先等Peng-Kai789对 #66准确445f3e2正式复审；再审本候选的最小冲突解决、原测试政策迁移和Windows分片方案。未批准前不回写 #67/#70/#72，不打开Live，不把本地通过写成正式批准。
