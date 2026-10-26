# Discovery Agent v1 运行层

本 PR 是可运行的离线运行层，不是设计占位或真实 Qwen 质量报告。
基线 main `761d209` 已合并 #41；沿用 `edu-discovery-0.1.1-draft`。
只读参考 #43 `d4bde918`、#47 `91463121`，未修改它们的文件、标准答案或 #44/#40。

## 运行

在仓库根目录：

```powershell
uv run --directory apps/api --locked python -m app.ai.edu.discovery.demo
uv run --directory apps/api --locked pytest tests/ai_edu/test_discovery_runtime.py tests/ai_edu/test_discovery_adapter.py -q
uv run --directory apps/api --locked pytest tests/ai_edu tests/discovery -q
```

异步调用：`await run_agent("coupled53", AdaptiveFake())`。
HTTP 协议演示使用 `MockQwenPlanner(adaptive_transport())`，最后 `await planner.aclose()`。
输入只接受内置合成工厂名称，不接收任意 Dataset、教师问题、Excel、数据库或个人记录。
`synthetic_context` 仅接受指定 synthetic 命名空间与 20 位十六进制摘要，用于离线评测来源绑定；不是教师授权。
Production、Synthetic Live 及 `offline_only=False` 客户端在调用前拒绝。本轮没有远程入口。

## 真实分步行为

每轮先请求一个模型决策，运行层再执行对应工具；返回的已核验聚合结果决定下一轮选择。
从不调用完整 `discover()`，不预生成候选池，不执行模型代码、SQL 或文件访问。
Fake 决策器只读模型可见聚合视图，不读 case 名、原始行或标准答案。

| 合成输入 | HTTP Mock 实际工具顺序 | 请求/尝试 | 最终结果 |
| --- | --- | --- | --- |
| 53 人：前 26 人 A=1/B=5，后 27 人 A=5/B=1，其余维度 3 | 维度统计 → A/B 相关性 → A≤1、结果 B 的群体对照 → 依据检查 → finish | 5/4 | 目标 26、余组 27；B 均值 5 与 1，差 4；一张群体卡 |
| 17 人：所有维度 3 | 维度统计 → finish | 2/1 | `no_valid_findings`，零卡 |

两条路径及脚本化安全探针保存在 `evals/discovery_agent_v1/offline-demo.json`。
其中 HTTP 请求是真正通过 httpx 和既有受限 Qwen sender 执行的 Mock，不是远程 Qwen。
探针保留跨批次参数拒绝和伪造最终引用失败，不把失败删除或转成正常卡。

## 工具与共用资格

| 工具 | 主要参数 | 结果用途 |
| --- | --- | --- |
| get_dimension_statistics | 空对象 | 程序五维统计及允许探索的观察阈值；仅支持调查 |
| query_dimension_relation | left/right 枚举 | 定向 Spearman、有效成对分母、排除数及原三次删组检查 |
| compare_student_groups | 1–2 个 conditions；1–3 个非条件 outcomes | 互斥目标/余组的原始对照；通过共享资格和稳健性评估的每个结果维度才可选卡 |
| inspect_rare_combination | left/right 枚举 | 原差值 IQR 规则；不足披露门槛的少见个体不发布 |
| verify_threshold_sensitivity | conditions、varying_dimension | 真实观察边界上的 < / ≤；不改变分数 |
| inspect_discovery_support | 本 run fact_id | 从实际发现重新检查投影/来源并返回排序与稳健性依据；本身不是新发现 |

阈值为 1～5、0.01 网格的十进制字符串，运算符仅 `< <= == >= >`。未知字段、维度、重复维度和条件维度充当结果均拒绝。
边界工具目前直接接收严格类型的条件；与 #43/#47 草案的 boundary/query fact 引用形式尚未接线，不能把两者宣称为最终公共协议。
`TargetedDiscovery` 和自动 `discover` 共用 `_relation/_groups/_rare/_boundary`、来源构造及 ID 生成；没有第二套资格公式。
单个相关性只算选定的一对及其三次删组，而不是整张相关矩阵。原自动发现的规则、排序、搜索预算和数值精度保持不变。
原 #41 四类合成 Demo 全 JSON（含发现 ID）比较相等；规范化摘要 `3a52d1a1aec7f2ab1ac737bc2a47463711532d66dd7759cfba4eacbf293fed8e`。

## 事实、Trace 与披露

- `Fact` 保存本次不可跨 run 引用的 fact_id、程序视图、内部 Discovery 和逐指标实际 `metric_sources`；来源不进入 repr 或模型投影。
- 每次工具尝试有 `ToolEntry`；独立 `Event` Trace 区分模型选择、工具执行、事实校验、最终选择。拒绝参数不回显原文。
- 只有通过资格、来源、分母、披露和大小检查的 Discovery 可以进入最终卡。调查事实可继续用于选择方向，不能冒充发现。
- finish 只允许已核验 fact_id。数字、标题、图表、条件、规则和限制由程序投影；不接受模型自由事实文本、分数或因果解释。
- `proposed_ids` 保留模型原提议，`selected_ids` 仅为最终认可集合；失败结果无卡，不把错误提议悄悄改成正确引用。
- 来源核验复用 `present/sources`；成员归属不能代替每个均值实际参与的记录。群体去重区分筛选维度和结果维度。
- 不给模型发送散点、逐分值频数表、个人行、评语、身份、证据 ID、内部定位或凭据；图表仅均值、人数及相关系数聚合。
- 默认 k=5，抑制小组、缺失小格和累计集合对称差 1～4 的精确披露。53 人边界的 4→5 仍由核心精确算对，但本模型通道拒绝发布。
- 这是保守的合成试验披露规则，不是差分隐私证明，也不是已实现正式教师权限。未来真实服务仍须重新授权和独立隐私评审。

## 预算与失败

单次运行硬上限 6 次模型请求、5 次工具尝试；失败尝试也计数，finish 消耗一次模型请求。
预算归 run 持有，测试覆盖每轮重建 HTTP Client 仍无法突破。Mock Client 自身另有跨调用 6 次上限。
每次最多 800 输出 Token、16 KiB 响应；单工具 8 KiB、累计 32 KiB、实际请求 64 KiB。
默认每轮 5 秒、总等待预算 90 秒；不自动重试。同步分析受 5000 人规模及有限工具限制，总时间检查在异步轮次边界执行，不是同步代码抢占。
重复工具（含顺序反转的同一维度对）、重复语义发现、输出超限、非法最终引用显式失败；允许模型主动结束或返回零发现。
超时无伪成功卡，取消按 asyncio 语义向上传递。执行轨迹只含固定错误码和已验证参数，不含异常正文。
旧 `run_insights` 保持原两阶段和两次请求限制；仅抽取相同受限 HTTP sender，未放宽旧行为。

## #47 评测接入

`evals/discovery_agent_v1/adapter.py` 提供 `AgentCandidate(contracts_module).run(CandidateInput, "C_agent") -> CandidateRun`。
通过显式注入 #47 contracts 工厂避免运行时代码依赖未合并模块。无需复制或修改标准答案。
仅匹配本 PR 已注册的内置合成行；其他输入与 foreign distractors 显式 `not_run`，不假装已评测。
此同步包装只用于 CLI，异步服务直接 await 运行层；不能在运行中的事件循环嵌套 asyncio.run。

已只读使用 #47 `91463121` 的 contracts/cases/grade_claim 实测：4 个匹配案例、15 个 not_run；实际得到的 4 个 Discovery Claim 均通过独立数值与来源核对。
记录在 `pr47-contract-probe.json`。这是契约/数值探针，**不是完整 evaluate 或 Qwen 质量/覆盖率结果**。
候选 ledger 目前仅投影其已有四种 Discovery Claim；摘要、未合格调查和 support 事实仍完整留在伴随 `AgentRun.facts/trace`，没有删改原 Trace 来骗过判分。
正式 `evaluate` 待 #47 合并后补充这些事实类型与边界参数验证；保留原始 selected 与实际 presented、错误和 not_run 状态。
`operation_counts` 是 cProfile 实测统计函数调用；`engine_invocations=0` 表示未调用 discover。
`compute_seconds` 为工具执行实测时间，完整 wall time 另在 AgentRun 保存。Token/费用均为 null，Mock usage 不能计成真实消费。

冻结历史结论保持：#47 原基线 A_default（预算20）11/12，B_full（预算50）12/12；默认漏掉 AND 交互目标的失败保留。
本 PR 未重新宣称这些历史数值为当前 Agent 的结果，也不以更改标准答案、缩小目标分母制造增量。

## 后端/前端与下一步

当前没有修改或接入教师 API、认证、数据库、报告和页面；#44/#40 独立继续。
前端未来最小展示字段：discovery_id/type、batch_id/snapshot_id、title/summary、status、metrics、chart、conditions、ranking、limitations。
最终卡不含内部 RecordRef；依据下钻须后端另行校验当前教师、快照、群体与实际来源，再签发受控查询，不能把 fact_id 当权限凭证。
请 Peng-Kai789 审核来源/分母、共享资格、工具范围、披露、预算与失败边界。

真实 Qwen 合成评测的启动条件：明确授权场景/请求数/Token/费用预算；审核仅限内置合成工厂的 Live 专用客户端和模式；
保持 run 预算及投影不变、Production 关闭；先补齐 #47 摘要/support/Trace 判分和相同披露政策的 A/B/C 对照。
本轮没有真实 Qwen 调用，没有 Agent 优于固定流程的结论，也不提供教师真实数据的远程通道。

## 本次实际验收

- Ruff check：通过；Ruff format check：174 files already formatted（含本 PR 评测适配目录）。
- 运行层与适配专项：76 passed。
- 早一轮 AI/发现专项：347 passed；新增适配测试后的最终专项将在 PR 中记录。
- 后端全量：1223 passed、1 skipped、1 warning，533.54 秒；跳过是 Windows 无符号链接权限，警告是既有 Starlette/httpx 弃用提示。
- 原 #41 四类 Demo 兼容比较：完整 JSON 相等。不是用旧输出替代独立数值标准答案。
- 独立标准答案只读探针：4 个已运行案例、15 个 not_run、4 个 Claim 的数据和引用均正确。
- 真实远程模型：未运行；Token/费用不填模拟数字。最终 GitHub CI 结果见本 PR。
