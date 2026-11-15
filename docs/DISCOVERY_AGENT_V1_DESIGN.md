# Discovery Agent v1 — 审查与内部契约草案

状态：审核整合，Agent尚未实现。2026-10-26重新fetch：main=201929cca8921a9c016353e2abda5e2044004671。#41（1424655）、#43、#47（9146312）、#44（9b3f2b4）均OPEN，mergedAt=null；#41原CHANGES_REQUESTED尚无新的批准，不能把修复提交或CI绿色等同于审核通过。

本轮只更新本PR已有两份文档，不改#41/#47/#44或AI运行代码，不创建依赖未合并接口的运行分支。**#41、#43、#47经人工审核合并后**，再fetch并从届时最新main建立独立worktree/`feat/edu-discovery-agent-v1`；本设计分支不是实现基线。#44仍是独立正式后端准备工作，不是本轮合成Agent的数据入口。

目标是模型根据已返回聚合事实选择下一项调查，而不是一次性总结 discover()。教师确认、统计事实与因果证明互不等同。本阶段不生成聊天、练习或教学建议。

## 1. 代码审查结论

### R1 / P2：历史漏检已提交修复，仍待复审和合并

位置：[engine.py L335–349](https://github.com/ZAMYL/weiran-edu/blob/50ea3719e7417e3e86cb0999f0801bf1b4dbf861/apps/api/app/analysis/edu/discovery/engine.py#L335-L349)。`seen.add(partition)` 发生在非条件维度效果/样本检查之前；分区键也不区分筛选维度与结果维度。

已实际运行的完全合成复现：20人，前10人算法=1、实现=2.90，后10人算法=5、实现=3.10；其余三维均3，ScorePolicy(step=1/100)，使用默认发现阈值。

| 调用/规则 | 独立可核算结果 | 实际行为 |
| --- | --- | --- |
| 算法≤1，结果维度为实现 | 两组10/10；实现均值差−0.20，不达0.5 | insufficient_support_or_effect，但分区已经记入 seen |
| 实现≤2.90，结果维度为算法 | 两组10/10；算法均值差−4，达到0.5 | duplicate_partition，未做资格判断 |
| compare_groups 单独检查实现≤2.90 | 算法均值1−5=−4 | 返回−4、10/10 |
| discover 的群体卡 | 可能存在合格结果 | 实际0张；仍有相关性发现，不能说全部发现均漏检 |

上表是旧头50ea371的历史行为，不能当作当前1424655仍未修复。1424655已改为先逐非条件结果维度检查资格，发布后按“无方向分区＋筛选维度集合＋结果维度”去重，独立计算各结果的稳健性和排序；20人例现在有1项算法差−4、两组10/10。内部`GroupComparison.outcome_dimension`和展示`metrics.outcome_dimension`标明主结果，直接compare_groups仍默认None。规则版本为`edu-discovery-0.1.1-draft`，旧发现ID不可沿用。

本次只读审核再次核对该实现；修复不是单纯后移seen。#47已锁定1424655做独立离线基线，不能将修复基线漏检计作Agent增量。本PR不改核心，也不自行解决Peng-Kai789的CHANGES_REQUESTED线程或合并。

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
| inspect_rare_combination | 不同的 left/right 维度枚举 | 必须在合并后抽取共享的定向评分差/IQR助手，只计算已请求维度对；自动discover也调用同一助手。不再允许discover(search_budget=0)缓存所有候选作为定向工具，也不把完整发现池发给模型。单维为常数时仍可能存在少见差值，不能用Spearman不可计算直接排除。当前尚无该公开定向接口，本PR不改核心 |
| verify_threshold_sensitivity | 已登记边界 fact_id 与一个现有条件查询引用 | 复用 #41 compare_boundary。边界必须是本 run 的实际评分/分位点白名单值；按精确 cents/Fraction 区分 <、≤，不对展示舍入值筛选。返回 N/E/U、两次M与成员变化的聚合数；不返回成员列表 |
| inspect_discovery_support | 本 run 发现 fact_id | 复用已有发现 ranking.robustness、真实 SourceSet/sources 和样本/缺失诊断，只汇总来源覆盖、两组分母、检查次数和限制。普通 compare_groups 不包含稳健性结果，必须明确 not_computed；不得把请求成功或引用存在等同于通过稳健性检查 |

阈值仅为参数，不是模型提交统计值。用严格十进制字符串、1–5且最多2位小数转 Fraction；未知维度、重复维度、不支持运算符、阈值越界、非网格值等拒绝，不取整。新边界工具只做 < 与 ≤；不能从模型文本抽数字“修复”参数。原工具支持5个条件，新群体工具沿用 #41 1–2个维度限制，不能偷偷提高。

### 定向群体结果与稳健性缺口

`compare_groups`本身只提供对照，不生成Discovery/ranking/删组稳健性；outcome_dimension也不是“已经审核”的布尔凭证。合并后应从现有discover内抽取最小共享评估/构造助手，自动流程和定向工具共同调用：

1. 服务端以同一Dataset、批次/快照、范围、条件和规则profile构造比较，不接受客户端预计算GroupComparison或任何qualified=true。
2. 每个请求的非条件结果维度分别检查有效样本、差异门槛、原3次删组方法、实际来源和限制，生成程序化候选。保留#41的目标/互斥余组、不判定U和逐维有效分母语义。
3. 原始比较或未计算稳健性的诊断可作为继续调查的信息，必须明确not_computed；不能进入最终发现卡。完成同一评估后的cautious状态不等于失败，也不能升级为统计显著。
4. 自动discover继续按预算探索/语义去重，不能改变当前数值、来源、排序、阈值或ID生成口径来迁就Agent。共享助手提取须通过原回归和#47独立标准答案，必要的规则版本变更显式记录。

不接受“先运行完整discover，再找同条件候选给定向结果补资格”的兜底；缓存仅限已经实际执行过的具体规范请求，不能偷偷预取未请求群体或全部候选。相关性工具可先复用已有Spearman后仅投影请求pair并如实记内部10对计算，后续若提取定向pair助手，同样复用已有并列秩算法，不另写公式。

这些是合并后的必要内部共享重构，不在本轮未合并阶段实施。只有原始对照可调用，不代表全部工具接口已经就绪。

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

#41、#43、#47审核合并后，在实现PR内补齐下述内部接缝；不增加额外口头开工批准，也不替代既定测试：

- 在 ai/edu 内增量实现分步协议、受限子注册表、共享HTTP发送和run级账本，不引入统计依赖或第二套工作流框架。
- 不改变旧run_insights的既有Plan/Proposal语义与测试；不能以所有HTTP200替代事实验收。
- 先做Fake条件分支和Mock native多轮回归，证明同样入口在不同聚合结果下选择不同合法后续步骤，并覆盖提前finish、空选择、跨run引用、错误参数、重复调用、输出限额、超时、取消、伪造数值、模式越权、合成注册不匹配和提示注入。
- A/B协议见 [独立评测计划](../evals/DISCOVERY_AGENT_V1_EVALUATION_PLAN.md)。在真实Qwen对照完成前，不宣称Agent提升召回、效率或智能性。Fake只验执行机制。
- 请 Peng-Kai789 审核：工具范围绑定、事实/来源分母校验、远程投影、模式/预算和正式后端未来接线边界；ZAMYL确认探索资格/稳健性与独立预期，AooMing后续仅评审展示字段。当前不向页面或正式鉴权系统增加任何入口。

## 8. 与现有交付的审核整合（2026-10-26）

### 实际前置与本轮边界

| PR | 已有内容 | 当前仍缺什么 |
| --- | --- | --- |
| #41 / 1424655 | 四类确定性发现、逐指标来源、R1修复、结果维度语义 | Peng复审/合并；共享定向资格与rare助手尚未抽取 |
| #43 | 分步/安全/评测契约 | 评审/合并；没有Agent实现 |
| #47 / 9146312 | 19案例、独立标准答案、A_default/B_full实测、CandidateRun接口 | 评审/合并；C_agent未运行；支持事实与全Trace还需接入扩展 |
| #44 / 9b3f2b4 | 正式Dataset装配、范围/来源DTO及第二阶段契约 | Draft且未注册生产发现路由，硬执行器/正式接线未完成；不能视为真实Agent已获授权 |

#44的范围条件与发现条件分离、当前授权重验和真实来源分页保持由后端负责。正式source_id/evidence_id和完整Dataset即便匿名，也不成为模型可读数据；本轮不得从DiscoveryInputReader取正式评分外发。内核图表字段是chart（单数），primary outcome元数据需在未来正式适配时由Peng审核；此处不改公共DTO。

### #47的真实接口，不是模型输入

可信评测适配器实现同步`run(CandidateInput, profile) -> CandidateRun`，通过`evaluate(fixed_adapter, agent=agent_adapter)`接入。异步Agent工作流的同步封装仅用于无运行中事件循环的CLI；正式异步后端不得嵌套asyncio.run。Agent核心不依赖评测oracle；适配器留在evals侧。

- CandidateInput含本地合成rows、distractors、batch/snapshot/source token、一般问题和明确边界。没有targets/seed/recipe/分析标签；不得把整个对象发给模型，也不得读取case/oracle文件帮助规划。
- CandidateRun.ledger必须来自真实工具执行。selected_ids保存模型原始提议，presented_ids保存验证后实际展示；两者不能在适配器里悄悄同步，以掩盖被拒绝的错误。
- Query.outcome每个Claim只指一个结果维度，映射#41真实outcome_dimension。群体Claim.values和Binding需要完整本地五维对照及逐指标来源；模型视图可以只披露请求的维度，不能用oracle补齐未计算数据。Fraction、rho浮点、N/E/U与各组有效n按#47现有格式转换，不复制统计公式。
- measurement_origin必须明确scripted_fake/http_mock/qwen_live等来源；Fake/Mock的Token和费用不作为真实值。首轮离线C_agent是机制验证，不是千问质量或优于固定流程的证据。

### 必须先解决的评测接入缝隙

当前#47独立reference只支持relation/group/rare/boundary；trace_request_valid还要求被工具引用/发布的ID已在校验通过的Claim账本中。因此直接把摘要或查询定义伪装成发现、漏掉这些ID、或删除合法事件来获得绿色评测都不成立。

合并后的最小扩展应显式区分“支持事实/查询依据”和“可选发现”，为实际统计摘要、边界查询引用补独立参考/来源校验；支持事实永远不能被finish当发现。新增参考答案仍使用独立Decimal/Fraction，不调用待测内核或模型；适配器不得硬填rule_version来抹平真实来源差异。保持既有四类标准答案与R1/4↔5锚点，保留#47首份历史报告；新增结果绑定新的评测器提交/源码摘要，不手工修改历史报告的成绩或把历史摘要要求变成当前代码必须倒退。

CandidateRun.tool_trace当前只接受工具事件；Agent的planner_decision/fact_validation/final_selection保存在完整Agent Trace，不能当未知工具塞进此字段，也不能直接丢弃这些阶段。实施时需明确增加独立Trace投影/伴随记录并验证四阶段ID关联，工具预算只统计真正工具尝试，不把所有事件都当调用。

#47的停止状态目前是completed/no_valid_findings/insufficient_data；sufficient_evidence应映射为completed。privacy_limited、预算强制停止、超时和取消不能一律伪装completed或无发现：需显式保留原始reason及model/program来源，并在独立评测中单列，未支持状态不得篡改适配器骗过判分。

### 披露与评测公平

#47现有19案例是本地原始合成数学基准，尚未应用本设计的远程k/差分门槛。将来C_agent启用披露门槛时，固定两组也必须使用同一可披露视图/限制，另报数学覆盖、可披露目标覆盖和抑制原因；保留原始12项目标，不暗减分母来提升成绩。1/17个体组合和53人新增1人的边界案例可以在可信本地工具层验证，不意味着可以发给模型。此调整需要独立参考和共同策略验收后才能做真实三组质量比较。

### Fake、HTTP Mock与启动门槛

Fake只读取模型实际可见的聚合结果来决定下一步；测试必须展示相同入口在不同实际统计结果下走不同路径、主动finish和零发现，而非按case_id或预写列表顺序返回工具。HTTP Mock通过真正的消息序列与native call_id配对执行多轮，验证预算只持有一份；不能把真实网络Client的offline_only改成True。可分别测试受限HTTP组件与离线编排器，或用严格无网络的Mock专用客户端组合，真实Client在Offline仍必须拒绝。

90秒run期限和asyncio超时不会强行中止同步CPU统计；合成阶段须限定数据/计算量并在工具前后检查期限，不声称有硬CPU中止。#44规划的正式子进程硬超时是另一条生产接线门槛，不能以线程或async超时替代。

运行实现的开工条件仍是三项前置审核合并后重新fetch；届时执行原AI回归、#41发现回归、#47独立标准与新分步测试、全量及最终CI。真实Qwen合成评测另需明确case/重复次数/总请求与Token限额的费用授权；当前授权为0次真实请求。Production继续关闭。
