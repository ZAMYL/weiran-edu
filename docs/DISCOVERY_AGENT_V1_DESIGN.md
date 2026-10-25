# Discovery Agent v1 — 审查与内部契约草案

状态：设计评审，尚未实现。2026-10-25 核验 PR #41 仍为 OPEN、mergedAt=null；main=a7a6c2812354b2775394879cb019476d77864d5c，#41 审查头提交=50ea3719e7417e3e86cb0999f0801bf1b4dbf861。本 PR 只新增设计与评测计划，不改 #41、AI 运行代码或公共接口。合并 #41 后，重新 fetch 并从当时最新 main 新建 `feat/edu-discovery-agent-v1`，不得直接沿用本设计分支充当实现基线。

目标是模型根据已返回聚合事实选择下一项调查，而不是一次性总结 discover()。教师确认、统计事实与因果证明互不等同。本阶段不生成聊天、练习或教学建议。

## 1. 代码审查结论

### R1 / P2：群体去重先于资格判断，可压制合法候选

位置：[engine.py L335–349](https://github.com/ZAMYL/weiran-edu/blob/50ea3719e7417e3e86cb0999f0801bf1b4dbf861/apps/api/app/analysis/edu/discovery/engine.py#L335-L349)。`seen.add(partition)` 发生在非条件维度效果/样本检查之前；分区键也不区分筛选维度与结果维度。

已实际运行的完全合成复现：20人，前10人算法=1、实现=2.90，后10人算法=5、实现=3.10；其余三维均3，ScorePolicy(step=1/100)，使用默认发现阈值。

| 调用/规则 | 独立可核算结果 | 实际行为 |
| --- | --- | --- |
| 算法≤1，结果维度为实现 | 两组10/10；实现均值差−0.20，不达0.5 | insufficient_support_or_effect，但分区已经记入 seen |
| 实现≤2.90，结果维度为算法 | 两组10/10；算法均值差−4，达到0.5 | duplicate_partition，未做资格判断 |
| compare_groups 单独检查实现≤2.90 | 算法均值1−5=−4 | 返回−4、10/10 |
| discover 的群体卡 | 可能存在合格结果 | 实际0张；仍有相关性发现，不能说全部发现均漏检 |

建议由 #41 维护者评估：去重应在合格候选之间进行，并保留筛选/结果维度语义，避免先前不合格规则阻塞后续规则；添加以上回归。单纯把 seen 移后不能解决所有“相同分区、不同结果维度”的去重口径。此设计 PR 不修核心。若暂不修，A/B 都必须保留此已知漏检记录；不能把 B 绕开它宣称为模型自主探索带来的增量。

### R2：教师内部投影不能原样成为远程模型输入（接入差异）

[presentation.py L98–103](https://github.com/ZAMYL/weiran-edu/blob/50ea3719e7417e3e86cb0999f0801bf1b4dbf861/apps/api/app/analysis/edu/discovery/presentation.py#L98-L103) 的关联图按精确 `(x,y,count)` 汇总；count=1 的点会暴露一条二元评分组合。它没有身份字段，适用于已授权内部使用，但不足以满足本轮远程最小化要求。不能直接把 `present()` 或内部 dataclass/asdict 发给模型。

新模型投影只白名单提供 rho、成对有效/排除数量、两组均值/差异、有效分母和汇总检查状态；不发送散点、逐人数据、精确评分频数表、来源坐标、RecordRef、evidence_id 或任何原文。展示投影和模型投影分别受限，不能共用“删掉姓名即安全”的假设。

### R3：现有两阶段协议不能直接冒充分步 Agent（接入差异）

当前 `run_insights` 一次 plan → 一批执行 → 一次 compose；`Plan.calls` 至少1条；`Proposal.observed_facts` 至少1条。`QwenClient.plan` 覆盖历史，compose 按该次 native tool_calls 配对，`QwenSettings.max_requests` 最大2。这些是现有工作流的限制，不是已支持多轮探索。

后续增加独立的分步决策类型，并复用现有 Qwen Function Calling 的 HTTP 发送、超时/取消、端点白名单、错误脱敏、Token 计量、JSON 重复键检查。预算需由整个 run 统一持有。不得循环创建新 Client、重置计数、反复调用旧 plan 假装保留历史，也不得为新流程放宽旧入口的2次上限。可将受限 HTTP 发送提取成内部共享组件供两个流程使用；保持旧 run_insights 行为、Schema和所有回归兼容，不引入 Agent 框架或另一套统计公式。

### R4：Fact/证据契约需显式适配（接入差异）

现有 Fact 是单维 Fraction 与单个 denominator；相关系数为 stdlib float，群体对照有目标/余组各自有效分母，不能硬塞到单个“样本数”。`validate_proposal` 的解释基底白名单也不含 rho/差异。新阶段不输出解释模板，不能假装旧 Proposal 已验证新发现。

保留原 `Fact.source_refs` 的实际参与者原则；新增内部 DiscoveryFact 与对照来源绑定。评分依据、成对参与者、余组、边界新增成员是不同指标集合。不能用同批/同群体/同名指标代替实际来源；不把 Fraction 数值分母当作学生数。既有 EvidenceDocument 与 RubricDocument 的分离检查保持不变。本阶段不提供模型可调用的个人证据或评语工具。

## 2. 拟议入口与安全上下文（非公共 API）

离线入口：`run_discovery(source, client, limits=..., mode=OFFLINE)`，必须是明确的合成测试源和离线 Client。在线入口只接受受审计的内置 `case_id`，由服务端工厂重建题目、1–5/0.01 Dataset、批次、快照和访问范围，逐项核对注册内容；不接受调用方任意 Dataset/question 加 synthetic_only 布尔值自我证明。

沿用 ExecutionMode：Offline 拒绝网络 Client；Synthetic Live 需要显式模式与付费 opt-in，且只接受内置合成场景；Production 一律在构建 Client/读取 Provider/发请求前拒绝。既有历史6次调用授权已经用完，不是本次评测授权；设计 PR 不读 Key、不发真实请求。将来另列总场景/重复次数/请求与输出Token上限，取得该轮明确批准后才执行。

本轮不接正式教师认证；SyntheticAccess 不是生产凭证。批次/快照/获准群体由不可变服务端 RunContext 绑定，工具参数里不开放 batch_id、snapshot_id、student_ids、SQL、文件路径、Provider地址、评分行、个人证据 ID。若带这些字段，严格拒绝；不能静默忽略。

RunContext 内部包含：run_id、batch_id、snapshot_id、scope fingerprint、规则版本/配置、合成工厂版本和受控来源句柄。模型只看到本 run 的不透明 scope token、允许维度、非敏感样本支持状态与可调用工具；不读取磁盘 Excel、数据库或教师原文。

## 3. 分步决策、停止和预算

拟议严格 tagged union（Pydantic extra=forbid、严格类型、JSON 重复键/NaN 拒绝）：

```json
{"kind":"investigate","tool":{"name":"query_dimension_relation","arguments":{"left":"ALGORITHMS","right":"IMPLEMENTATION"}},"basis_fact_ids":["run-local-summary-fact"],"purpose":"check_relation"}
```

```json
{"kind":"finish","selected_fact_ids":["run-local-validated-discovery-fact"],"reason":"sufficient_evidence"}
```

- Function Calling 在线请求每轮只允许1个 native action（parallel_tool_calls=false，服务端仍拒绝多于1条）；模型可选择任意合法首工具，服务端不规定“统计→相关→群体”的顺序。
- 后续每轮只接收此前成功发布的聚合事实与预算/状态，保留 native tool_call_id 的合法配对；下一次调查是否执行取决于下一次模型 action。伪造/过期引用、格式错误、重复键、多工具混合、非法数值等不作自动修补。
- basis_fact_ids 必须属于当前 run、同上下文且与请求维度/条件相关；首步可以为空。存在 ID 只是最低要求，不证明该方向有价值。只记录目的枚举，不要求或保存模型隐藏思维过程。
- finish 只可选择已通过资格/来源校验的发现事实，不能选“检查完成”记录或仅有总体均值就称新发现。最多3项；空集合合法，不自动填卡。无效 finish 失败，不返回正常报告。
- 停止原因：sufficient_evidence/no_valid_findings/insufficient_data/privacy_limited；程序强制终止另标 budget_exhausted/timeout/cancelled/validation_error。模型主动无发现不等同于程序错误；强制终止不伪装成模型主动完成。
- 默认最多6次模型请求（含 finish）、5次分析工具尝试、最多1次无新增事实的重复请求，第二次重复即停止。去重前、失败、缓存命中也消耗调用尝试预算。不能自动重试、回退到另一模型、扩张范围或调用完整发现作为失败兜底。
- 每请求最多800输出Token，默认全 run 输出上限4800；响应16KiB、单工具8KiB、聚合工具累计32KiB、实际请求64KiB、发布事实24个；全 run 90秒，每网络请求15秒。硬上限为6次请求/5次工具，不向模型开放修改预算。上线前按 Mock 与真实 Schema 大小验证这些草案值，超限必须 fail closed。
- Token 总数来自真实 API usage；缺失为 null。字节限制是请求体保护，不伪称精确输入Token预算。计量错误或上限检查不能再次调用模型“解释失败”。
- 根样本为空/不足聚合披露门槛时可由程序直接安全停止（0次模型请求），这属于安全约束，不是固定工具顺序。

重复 action 的规范键 = 工具名+规范化合法参数+上下文；引用顺序或十进制尾零变化不能绕过去重。不同 outcome/条件含义不能仅因成员分区相同而视作完全相同发现。最终重复发现另按评测定义去重，并记录模型原始重复尝试次数。

## 4. 工具白名单与计算复用

下表是拟议内部工具，不会同时向 Agent 暴露旧的个人证据工具。工具注册表由服务端静态创建，模型不能新增工具。

| 工具 | 合法参数 | 程序复用与返回范围 |
| --- | --- | --- |
| get_dimension_statistics | 空对象 | 委托既有 summarize/ToolExecutor 受限统计投影；只给每维有效/缺失/无效数、均值/中位数及固定粗分箱，不发逐分值频数/极值行。粗分箱仅为显示聚合，不改变底层筛选与0.01精度 |
| query_dimension_relation | 不同的 left/right 维度枚举 | 复用 spearman_correlations，只投影所选 pair 的 rho、状态、n_pair、excluded；内部计算10对的成本也记录，不能谎称只算1对 |
| compare_student_groups | 1–2个不同维度的合法 AND 条件；1–3个非条件结果维度 | 复用 #41 compare_groups。返回 M、R、U、N/E、各结果维度的两组有效n/均值/差异；目标与余组互斥。条件差异不作为新发现 |
| inspect_rare_combination | 不同的 left/right 维度枚举 | #41 没有独立公开 rarity 函数。暂可在本 run 本地缓存 discover(search_budget=0, maximum_findings=50) 的结果，仅返回指定 pair 的少见组合诊断；全部结果不外发。记录内部相关/围栏/删组工作量；qualified_count 超过保留数时显式 truncation，不把未保留项说成不存在。是否后续拆公共小函数由核心维护者另审，本 PR 不动核心 |
| verify_threshold_sensitivity | 已登记边界 fact_id 与一个现有条件查询引用 | 复用 #41 compare_boundary。边界必须是本 run 的实际评分/分位点白名单值；按精确 cents/Fraction 区分 <、≤，不对展示舍入值筛选。返回 N/E/U、两次M与成员变化的聚合数；不返回成员列表 |
| inspect_discovery_support | 本 run 发现 fact_id | 复用已有发现 ranking.robustness、真实 SourceSet/sources 和样本/缺失诊断，只汇总来源覆盖、两组分母、检查次数和限制。普通 compare_groups 不包含稳健性结果，必须明确 not_computed；不得把请求成功或引用存在等同于通过稳健性检查 |

阈值仅为参数，不是模型提交统计值。用严格十进制字符串、1–5且最多2位小数转 Fraction；未知维度、重复维度、不支持运算符、阈值越界、非网格值等拒绝，不取整。新边界工具只做 < 与 ≤；不能从模型文本抽数字“修复”参数。原工具支持5个条件，新群体工具沿用 #41 1–2个维度限制，不能偷偷提高。

### 定向群体结果与稳健性缺口

`compare_groups` 本身不生成 Discovery/ranking/删组稳健性。定向工具可发布“计算事实，稳健性未检查”，不能伪造 Discovery 或宣称已通过 #41 全部资格。inspect_discovery_support 对已有 #41 candidate 可读取原检查；对仅定向比较的结果必须返回 not_computed。若要复用同一删组检查发布为发现，需 #41 合并后由维护者明确抽取共享资格/稳健性辅助函数，或同一 discover 搜索确实命中相同条件且结果一致；不能另抄公式、拿别的候选替代、强行把它标为可展示。

这是后续实现前必须决策的接口缝隙，不用临时平行算法填平。Agent 可以选择未完成验证的事实继续调查，也可以因证据不足停止；评测不能把这些当合格发现。此处和 R1 都需要核心/后端负责人审查。

## 5. 事实、来源和展示契约

DiscoveryFact（拟议冻结内部对象）最少字段：

- fact_id、run_id、batch_id、snapshot_id、scope_digest、rule_id/version、kind、metric。
- value 类型：exact_rational 或 spearman_float（明确方法及浮点容差）；缺失不可计算时 value=null + reason。
- population_count、eligible_count、target_count、remainder_count、unevaluable_count、paired_count按 kind 分型；每个结果维度有 target_valid/remainder_valid，禁止用一个 denominator 混装。
- 维度、规范条件、实际记录集合的受控来源句柄/摘要；source_record_count只是来源条数，不替代学生数。个人 RecordRef/原文不在模型视图或最终卡中。
- sample_support、qualification、robustness（method/planned/evaluated/passed/not_computed）、missing/invalid、limitations。
- 事实账本仅在工具结果合法、来源匹配、输出/隐私预算通过后原子发布。失败不能留下可引用 ID。ID 仅本 run 可引用，跨run/batch/snapshot相同字符串仍拒绝。

验证顺序：执行上下文/模式 → 工具名/参数 → 预算与引用 → 确定性计算 → N/E/U及两组/配对分母不变量 → 每指标实际参与者/来源维度/快照 → 聚合披露检查 → 原子发布。finish 再校验所选事实、资格、重复与本次来源；程序生成标题、事实文字、值、分母、状态和限制，模型不能提供这些字段。现有 validate_proposal 的未知引用/实际来源原则复用为共享校验，不把当前仅支持 mean 等指标的函数强行用于新类型。

### 远程披露检查

即使完全合成，也不发送个人评分行。远程聚合视图默认 k=5，每个输出维度/组必须有效n≥5；不输出逐分值点、稀少个体组合或新增成员评分。累计已披露集合也要检查：新旧群体对称差非空且小于k时，拒绝继续披露其精确对照统计，防止重复查询直接隔离单条记录；边界变更人数不足k时只给 privacy_limited 状态，不发送4→5/具体新增点。固定粗分箱的小格抑制及总数补算风险同样检查，不能只把小格置空但让其可由其他格算出。

这是合成评测的防护约定，不是已证明的生产匿名化或差分隐私。它不能授权真实数据外发；Production继续关闭。53人4/5的例子可在可信本地工具层/离线流程验证，不因此要求远程返回单成员变化。A/B必须经过相同发布门槛；不能因A被抑制而B看到更多明细。

内部教师展示的未来最小投影：discovery_id/type、batch/snapshot token、程序标题/摘要、typed metrics及明确分母、conditions/comparison、support/robustness、status/limitations、受控query token。禁止原始身份/定位/文本；public_display_allowed=false。此文不是正式 API/DTO，前端页面和报告不改；证据抽屉和正式授权下载由后端另行设计。

## 6. Trace 和指标

分离四类事件，不把模型意图记作执行成功：

1. planner_decision：round、已校验action类型/工具枚举/规范参数、basis_fact_ids、purpose枚举；未知名称/参数只记固定错误码，不记录攻击文本。
2. tool_execution：run/round/server_call_id、状态、实际work_units、cache_hit、计算耗时、输出字节与发布fact_ids。
3. fact_validation：接受/拒绝ID、固定原因、引用/来源检查状态；不记录 RecordRef、评语、Key、端点或原始异常。
4. final_selection：模型选了哪些ID、验证器接受哪些、程序最终展示哪些、停止来源(model/program)、stop_reason。

网络计量使用既有 CallMetric 口径，增加round与run：耗时、请求次数、实际usage/cached tokens或null。不保存自由文本理由、隐藏思维过程、HTTP Authorization、原始失败响应。归一化Trace可重放检查执行顺序和引用；真实模型的规划、耗时和Token不保证逐次相同，不能称完全确定性。固定Fake决策的纯工具部分必须重复一致。

## 7. 实现与审核门槛

待 #41 合并并重新审查 R1/稳健性接口缝隙后：

- 在 ai/edu 内增量实现分步协议、受限子注册表、共享HTTP发送和run级账本，不引入统计依赖或第二套工作流框架。
- 不改变旧run_insights的既有Plan/Proposal语义与测试；不能以所有HTTP200替代事实验收。
- 先做Fake条件分支和Mock native多轮回归，证明同样入口在不同聚合结果下选择不同合法后续步骤，并覆盖提前finish、空选择、跨run引用、错误参数、重复调用、输出限额、超时、取消、伪造数值、模式越权、合成注册不匹配和提示注入。
- A/B协议见 [独立评测计划](../evals/DISCOVERY_AGENT_V1_EVALUATION_PLAN.md)。在真实Qwen对照完成前，不宣称Agent提升召回、效率或智能性。Fake只验执行机制。
- 请 Peng-Kai789 审核：工具范围绑定、事实/来源分母校验、远程投影、模式/预算和正式后端未来接线边界；ZAMYL确认探索资格/稳健性与独立预期，AooMing后续仅评审展示字段。当前不向页面或正式鉴权系统增加任何入口。
