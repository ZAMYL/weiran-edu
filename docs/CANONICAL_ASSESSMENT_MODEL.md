# 通用标准测评数据层（未来设计）

状态：任务 #8 的设计与完全合成离线验证，初次交付的验证基线为 main `23f876d`（从 `12dd7f9` 开始开发）。不修改产品定位和教师五步流程，不是新 HTTP DTO、数据库迁移或已上线能力。与任务 #7 / [Issue #18](https://github.com/ZAMYL/weiran-edu/issues/18) 独立并行，其正式洞察 API 不由本文实现或覆盖。

**五维只是内部可选参考 Profile，不是教师上传的质量评分标准。标准测评数据层以实际存在的项目和事实为边界，可以保留不同数量、不同种类、不同来源的有效测评事实。教师可以不选五维方案，AI 也不能补造事实。**

依据 [PR #23 产品负责人反馈](https://github.com/ZAMYL/weiran-edu/pull/23#issuecomment-6392744109)，未来教师端先展示已识别项目和当前可做的分析，可选资料建议默认折叠；禁止用五维分母、覆盖百分比或完成度进度条评价上传。具体优先级及四种教师文案见 [展示契约](ANALYSIS_READINESS_DESIGN.md#4-界面展示契约与主次信息优先级未来体验目标)。本轮仅调整文档，V1 固定模板仍不支持三维、八维或逐题数据直接上传。

## 1. 对象、版本与关系

以下是概念字段，不承诺物理表结构。每个引用都必须在当前来源范围和数据快照内校验。ID 不含姓名、学号或可直接反推身份的编码，也不构成授权凭据。

| 对象 | 核心字段与关系 | 约束 |
| --- | --- | --- |
| Dataset | `dataset_id, version, source_scope_ids, batch_ids, provenance` | 数据集合及来源范围；可收纳多个测评批次。不同来源不自动合并参与者 |
| Batch | `batch_id, dataset_id, version, snapshot_id, source_ids, assessment_date, scheme_version, revision_of_batch_id` | 一次测评/修订的不可变快照；未知日期为 null；上传时间不是测评日期；修订不是另一次成长测评 |
| Participant | `participant_id, source_scope_id`；通过批次成员关系限定参与范围 | 受控匿名 ID；真实身份映射独立受控。跨来源、跨批匹配须另有授权和确认，不能按姓名猜测 |
| AssessmentItem | `item_id, version, original_name, display_name, kind, scale, rubric_refs`；方案成员关系关联 Batch | 动态项目，`kind=competency/question/task`，未来可受控扩展。身份与显示名分离；同名不等于同项目、同口径。只有方案中实际定义的项目才进入预期观测集合 |
| Rubric | `rubric_id, version, applicable_item_ids, course/task_scope, scale, criteria_evidence_ids` | 评分范围、单位、精度、等级含义、权重均以明确资料为准；未知部分为 unknown，不套用 1–5 默认值。数值量表与评价标准正文分开记录 |
| Observation | `observation_id, batch_id, participant_id, item_id, attempt_id, raw_value_ref, exact_value, quality_state, evidence_ids` | 一条实际测评事实；不把 AI 解释、建议、假设写回。重做/多次作答需独立 attempt；同一事实的多个来源不能重复计数；矛盾来源不得随意取末条或平均 |
| Evidence | `evidence_id, source_id, source_version/hash, kind, observation/item/rubric_refs, protected_locator, provenance, authorization` | 评分、评语、答卷、标准等来源；来源可为受控文件、考试系统或教师确认录入。定位符及原文按权限投影，不直接给模型。证据不等于证据已获授权 |
| AnalysisCapability | `analysis, status, scope, prerequisites, eligible_count, denominator, exclusions, limitations, rule_version` | 在具体批次版本、用途与权限快照上推导；available/partial/unavailable 表示准备度，不表示分析已执行或结论成立 |

关系：Dataset → Batch → 方案项目成员、参与者成员及 Observation；Observation → Participant + AssessmentItem + 一个或多个 Evidence；AssessmentItem → 适用 Rubric → 标准 Evidence。一个证据可支持多个事实，但每条关联都要验证范围；多来源事实保留来源差异，不以 Profile 映射代替证据关系。

来源属性保留 `user_confirmed / retrieved / historical_analogy / assumption / unknown` 的区别；历史类比、假设和未知不能充当真实评分证据。原型只接受前两者作为可信观测来源，并未验证现实世界真实性。

### 精确值和量表

- 评分保留原始词法、原单位与量表版本，数值以十进制/有理数表达。示例：2.75 → numerator=11、denominator=4；展示舍入不得参与比较。非法精度不截断，缺失不补零。
- 每项目保留其范围、步长、单位及测量水平（名义/顺序/区间等）。通用设计只对支持的数值观测开放数值分析；顺序量表的均值只能作为受限描述，不能声称分差等距。
- 0–100 分题目与 1–5 能力等级可以同时保存，但不能直接比较高低、相加求总分或自动标准化。经教师确认的换算也须有适用范围、公式、版本、来源及限制，保留原值。
- 题目得分不会自动变成能力评分。题目→能力需另有教师确认的测量映射及聚合口径；仅凭题名或 AI 推测不建立关系。重复或冲突事实先核查，不暗中合并。

## 2. 四种必须分开的状态

| 情况 | 表达 | 可做与不可做 |
| --- | --- | --- |
| 方案没有某维度 | 方案成员集合不存在该项目；内部可标 `not_in_scheme`，不是缺分 | 不创建缺分行或教师端“尚未提供”占位；不显示方案外缺失项。教师主动探索新内容时才轻提示可选采集价值 |
| 方案有维度，某学生没评分 | 项目存在，预期 Observation 缺失或 `quality_state=missing`，原因可为未提交/缺考/未知 | 不移除项目，不当零分；该观测不参与数值统计，报告完整分母及有效子集 |
| 评分非法 | 原值受控保留，`invalid_numeric/out_of_range/off_step`；引用错误和重复属于结构错误 | 不改分、不舍入、不冒充完整数据。非法观测阻断其数值处理；结构歧义整份拒绝。正式 V1 仍按原规则整批阻止入库 |
| 数据存在但未授权展示或外发 | `presence=present`，另有 `authorization[display/external_model]=withheld/denied/revoked` | 不当作 absent，不要求 AI 重新编造，不读取/外发受限内容；可继续获授权的评分/汇总分析 |

授权与质量是两个轴，不用 `null` 一个字段代替所有情况。缺标准时可以描述可信评分，但不能编造等级含义、评价依据或判定评分合理。没有量表范围时，明确标 `range_unknown`，只能在原单位下描述已获授权数值，不能声称已通过范围校验。

## 3. 默认五维 Profile（内部可选参考）

Profile 标识建议为 `weiran.edu.programming.default.v1`，推荐项目如下。Profile 的推荐身份独立于 Schema；教师可以不选择，适用时才记录其选择及映射确认版本。它不生成上传完成度，也不构成开始分析的条件。

| 推荐代码 | 推荐名称 |
| --- | --- |
| algorithm_data_structure | 算法与数据结构 |
| code_implementation | 代码实现能力 |
| problem_modeling | 问题建模能力 |
| efficiency_awareness | 效率意识 |
| solution_strategy | 解题策略 |

| 实际输入 | 保留规则 | 建议行为 |
| --- | --- | --- |
| 三维评价 | 保留三项及其评分规则 | 先显示三项已识别及可用分析，主 CTA“开始探索”；不显示另外两项的占位或完成度。主动探索新内容时可选提示增益 |
| 五维评价 | 按实际评分和证据正常分析 | 不因人数、缺评语或无答卷虚构完整复核能力 |
| 八维评价 | 保留全部八项 | 按原项目直接分析；不做与五维的完成度比较，不描述为“超标”或要求映射 |
| 十三题成绩 | 保留十三个 question 项目 | 逐题准备度；无能力评分就不输出五维能力 |
| 自定义名称/数量/量表 | 保留稳定项目 ID、原名、类型、量表 | 按原方案直接分析；不可靠映射保持 unconfirmed，仅在教师主动选择相关参考分析时可请其确认，不强制对应 |

只有固定 V1 模板中已核验的明确映射，或教师明确确认，才能标记 `confirmed_profile_code`。名称相同也不自动证明口径相同。推荐建议不生成 Observation、不复制分数、不修改用户方案；忽略/拒绝建议后继续已有数据分析。

## 4. 与当前 V1 的兼容路线（未来接入，非本轮改动）

核对依据：[实现状态](IMPLEMENTATION_STATUS.md)、[冻结规则](BACKEND_CONTRACT_V0.1.md)、[统计接口](STATISTICS_GROUP_EVIDENCE_API.md)，以及 main 基线实际代码。

最终已只读复核 main 新合并的 [快照完整性修复](API_INTEGRITY_REVIEW.md)：匿名学生引用被冻结，读取在授权后的同一事务校验完整批次事实。未来 Canonical 投影也必须经过这些服务边界，不能绕过校验直接读表。该修复来自上游，不属于本轮补丁。

| 当前边界 | 已实现事实 | 未来兼容办法 |
| --- | --- | --- |
| 固定 Excel 解析器 | 双表模板、五维映射、1–5、0.01；缺维度/缺分/非法分阻断确认 | 保留 `legacy_five_dimension_v1` 适配器及原验证规则；未来另行增加通用来源适配器。三维/八维/十三题目前仍不能直接导入正式 API |
| SQLite `001_edu.sql` | `dimension_code` CHECK 固定；`evaluation_count=student_count*5`；`score_units` 为 100–500 | 只读投影到 Canonical：batches→Batch，students.public_id→Participant，dimension_code→competency item，evaluations→Observation，rubric_criteria→Rubric/Evidence。未来存储扩展另立 Issue、迁移与团队审核，不能解除约束冒充通用化 |
| 精度与来源 | `score_units/100` 经 Fraction 计算；评分 evidence_id、文件摘要、Sheet1/2 位置可回查 | 无损保留来源和精确值；不把内部 RecordRef/locator 当公共授权 ID；原始身份不进入通用分析投影 |
| 标准/评语原文 | 正式读取时 `criteria_text=null, criteria_status=withheld`；评语区分 empty 与 withheld | 有来源不等于可读正文。Canonical 保存受控状态；评分分析可用，正文复核按用途不可用，不能给出“零异常” |
| `app.analysis.edu` | `Dimension` 枚举固定五维；`prepare_dataset` 为每人生成五维状态；统计、筛选、相关/候选内核存在 | 先仅允许已确认且满足原规则的五维投影进入旧引擎。未来动态项目引擎另行设计测试；不得将其他项目截断或假装是枚举维度。原型不重写统计算法 |
| 正式 HTTP / Issue #18 | 初始基线已有批次统计、群体、证据接口；本次产品反馈时正式五维洞察已通过 PR #19 合并 | 本轮不修改或耦合 DTO、路由或 #18 文件。未来准备度作为内部适配结果先验证；如需 HTTP 契约另开 PR 由受影响负责人确认 |
| AI | 有离线/受控合成 Qwen 路径；不代表生产可用 | 只消费当前授权、版本绑定的事实投影；不替模型补来源，不把叙述写回事实。此次无 Qwen 调用 |

后续分阶段：只读五维影子投影并做精确一致性核对 → 通用来源解析与人工映射确认 → 单独评审存储/API 扩展 → 动态项目分析的回归与权限验证。每阶段独立开关及可回退适配器；旧接口保持旧语义。历史数据不重标、不就地改分，未来映射变动产生新版本。

## 5. 本轮实际交付边界

[准备度设计](ANALYSIS_READINESS_DESIGN.md) 给出能力前置条件、阻断范围和补全建议。[独立原型](canonical_assessment/README.md) 仅使用内存 dataclass、Decimal/Fraction、合成证据声明和 pytest，不导入正式应用，不接 SQLite，不解析真实文件，不调用 Qwen。

原型为单 Dataset/单 Batch 的数值准备度子集；多批次实体关系、通用导入、真实权限、量表测量水平、比较等价性、非数值观测、持久化和分析执行仍是未来设计。不能把原型的 available 状态宣传成正式平台已支持八维/十三题导入或已完成评价复核。
