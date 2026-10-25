# 分析准备度与可选择的资料补全（未来设计）

状态：任务 #8，最终对齐 main `23f876d` 的独立设计和合成验证（从 `12dd7f9` 开始开发）。五维是推荐 Profile，项目集合来自实际测评方案。[通用模型](CANONICAL_ASSESSMENT_MODEL.md) 定义数据实体；本文不改变现有教师五步流程、V1 解析器、正式 API、数据库或任务 #7 / Issue #18。

## 1. 输入与输出

准备度输入为：受控来源中的方案项目、参与者成员、精确观测、证据/量表关联、批次/方案版本、当前主体权限及本次用途。先授权，再读事实和计算；无资源访问权时整个请求拒绝，不能通过准备度接口探测有无学生/批次。

`AnalysisReadiness` 建议包含以下字段。它是内部设计，不是新的公共成功响应。

| 字段 | 语义 |
| --- | --- |
| dataset_id / dataset_version | 数据集合及不可变版本 |
| batch_id / batch_version / rule_version / purpose | 分析范围、准备度规则版本、本次用途；生产接入还须绑定 snapshot_id 和实时权限版本 |
| recognized_items | 实际全部项目的 ID、原名、类型、原量表、确认的 Profile 映射，以及质量计数和证据可用状态；不补推荐项目 |
| available_fields | 当前用途下存在且可用的评分/评语/答卷/标准字段；不表示每条记录都完整 |
| missing_fields | 缺失或当前不可用的字段；须联查 absent/withheld/unconfirmed_source，不能统一说文件没有资料 |
| quality_issues | 单元格缺失、非法、范围未知、证据缺失/未确认/未授权及影响范围；不返回原文或凭据 |
| available_analyses | 全范围满足本能力必要条件；没有执行分析、没有生成结论 |
| partially_available_analyses | 有明确有效子集；列 eligible_count、完整 denominator、排除原因和项目/人群范围 |
| unavailable_analyses | 当前无合格记录，或缺少能力的全局前提；不返回伪造的零异常/零进步 |
| suggested_resources | 每条建议包含下表五项解释及影响范围 |
| limitations | 量表、范围、授权、模型与未实现能力的边界 |

字段存在性、质量和用途授权独立记录。原型 `recognized_items[].evidence_availability` 区分 `absent / available / withheld / unconfirmed_source`，`criteria_availability` 同理。`available_fields` 与 `missing_fields` 可以同时包含同一项目字段，表示部分记录有、部分记录缺，不能当作全体覆盖。具体缺失/非法数来自逐记录检查，分母不是有分人数，也不是页面条数。

## 2. 能力判定规则

| 能力 | 必要前提 | 缺资料时仍能做什么 |
| --- | --- | --- |
| 项目评分统计 | 至少一条可信、当前用途可用的数值评分；按项目/原量表分别统计，非法值排除并报告 | 不要求评语/答卷；量表未知只可描述原始数值并标 range_unknown，不能解释等级或跨量表比较 |
| 项目组合探索 | 至少两个实际数值项目；按同一参与者取各项目有效授权记录的交集，并报告配对数和排除数 | 可做原始分值联合分布；各轴保留各自量表，不把分差、均值或权重直接相加。相关需另校验样本数、常量输入及方法适用性，原型不宣称可用相关结论 |
| 评价复核准备 | 单条记录具有可信有效评分、适用版本的标准、对应评语和原始答卷，且均获本用途授权 | 仅有评分可继续统计；仅有评分+评语可提供受限文字线索，但不能宣称已核查答题表现或评分正确。原型的 evaluation_review 专指更深入的人工复核资料准备度 |
| 跨测评变化描述 | 至少两个真实测评时点、可靠匿名匹配、项目/量表/标准可比、任务难度和条件说明、授权和教师确认的比较范围 | 缺任何一项就不能自动宣称能力进步。修订版不是成长；即使可比也只能描述条件内变化，不证明教学因果。原型始终不执行此能力 |

准备度可随输入改变：补上已有学生的缺分可增加有效人数；补答卷+标准+评语可增加复核范围；撤销任一必要授权则立即缩小/取消对应能力。与“必须五维”“恰好某人数”或固定 Demo 的预先结果无关。

## 3. 阻断规则及建议的五项解释

每条 `ResourceSuggestion` 必须包含：`missing`（缺什么）、`why`（为什么）、`enables`（补后新增能力）、`can_continue`（当前是否可继续）、`requirement`（可选还是阻断）和 `scope`（阻断哪部分）。`can_continue` 必须结合范围理解，不能表示有错误也可正式入库。

| 情况 / 缺什么 | 为什么 | 补后新增能力 | 不补能否继续 | requirement / 阻断范围 |
| --- | --- | --- | --- | --- |
| 相对 Profile 少两项 | 推荐覆盖不足，不是评分非法 | 教师决定采集后可分析新增项目；历史分数不会补造 | 可以，原三维继续 | optional / profile |
| 无评语 | 缺少教师文字说明 | 获授权后可进行评语线索检查；深度复核还需标准和答卷 | 可以做评分统计 | optional / review |
| 无原始答卷 | 分数不能证明具体答题过程 | 获授权并补齐其他条件后可进行更深入复核 | 可以做评分统计与组合探索 | optional / review |
| 标准未知/未授权 | 无法核查等级含义、评分依据或适用范围 | 标准解释与受限人工复核 | 可以描述评分，不能编依据 | optional / rubric_based_analysis |
| 无明确范围/单位/步长 | 无法完成量表范围与精度检查 | 可校验范围/精度并限制合法解释 | 可以描述可信数值，标 range_unknown | optional / scale_validation；不允许声称已完成标准核验 |
| 方案已定义项目但缺分 | 部分预期观测不存在 | 真实补录后恢复受影响记录资格 | 其他有效项目/记录可分析，缺失不当零 | quality_blocker / affected_observations；完整分析受阻 |
| 非法数值、越界、超精度 | 无合法数值参与计算 | 教师提供可追溯更正版本后恢复资格 | 只允许明确标注的有效子集准备度；不能称全量成功 | quality_blocker / affected_observations；V1 正式导入整批受阻 |
| 重复 ID、悬空来源、跨学生证据、适用标准冲突 | 事实含义歧义，无法可靠关联 | 来源/引用修正后重新验证 | 不允许部分接受该数据集合 | structural_blocker / dataset；原型抛原因码 |
| 评分存在但未授权/来源未确认 | 当前用途不能使用这条事实 | 取得对应授权/来源确认后恢复能力 | 仅继续其他获授权、可信记录 | authorization_or_source_blocker / affected_observations；不代替教师放开授权 |
| 多次测评无可比性说明 | 时间顺序不等于同一测量口径 | 未来可比范围内描述变化 | 各次可单独分析 | optional / longitudinal_progress；“能力进步”结论当前不可用 |

**推荐覆盖差异不是质量错误。** 原型结构合法、三项合法评分时 quality_issues 为空；相反，已定义五维的某学生缺一分，recognized_items 仍为五项，缺分明确报告。用户可以接受建议、补真实资料、确认映射，或者选择继续已有分析。选择“继续”不会消除非法记录、授予权限或让不可用能力变可用。

## 4. 计算顺序与安全边界

1. 在可信服务端校验主体、来源和批次权限，再在一致快照内读取事实；每次请求重验撤销和用途。原型没有真实权限服务，授权只由合成夹具声明，不能用于保护真实数据。
2. 校验实体唯一性、方案项目集合、参与者成员、证据归属及标准适用范围。结构问题明确失败，不能静默丢行或选择有利来源。
3. 按当前用途检查评分证据；被扣留时不查看数值、不给出该值的质量细节。对可用评分精确校验；没有定义的项目不生成缺失行，有定义但缺观测才记 missing。
4. 分项目计算合格记录集合；组合用交集，复核逐记录检查所需资料。没有合法分母时为 unavailable，而不是假 0 分或假 0 异常。原型只输出计数/资格，不重复实现正式统计引擎。
5. 与教师选定 Profile 的**确认映射**比较，仅生成可忽略建议；未确认名称不由 AI 猜测。追加项目不会删除原项目。
6. 输出具体建议、限制、版本。缓存必须随数据/方案/规则/权限和用途改变而失效；能力声明和匿名 ID 都不是读取授权。真实接入还须成本上限、分页与小群体披露控制，不能把本原型直接注册成 HTTP。

## 5. 合成 A–H 与实际输出

[synthetic.py](canonical_assessment/synthetic.py) 构造所有事实，不读学生文件。以下为可执行结果的精简选段；完整输出由原型生成并保存于 [synthetic_outputs.json](canonical_assessment/synthetic_outputs.json)。准备度状态不是分析执行结果。

| 场景 | 合成输入 | 应观察到的准备度 |
| --- | --- | --- |
| A | 三维、3 人，无评语 | 3 项统计、3 对组合可用；建议补效率意识/解题策略；无质量阻断 |
| B | 五维、3 人，含评语/标准/答卷 | 5 项统计、10 对组合、5 项人工复核资料就绪；只评价准备度 |
| C | 八维自定义、3 人 | 保留 8 项，8 项统计可用，不裁剪为 5 项；无确认映射不猜五维 |
| D | 十三道编程题、3 人，0–100 分 | 13 项逐题统计就绪，所有类型仍为 question，能力 Profile 均不映射 |
| E | 五维、评分/评语/标准，无答卷 | 评分统计可用；深度复核不可用；建议补对应原始答卷及授权 |
| F | 定义五维，1 人第 1 项缺分 | 仍有 5 项；第 1 项 2/3 人可统计，其他项 3/3；不称四维方案 |
| G | 五维评分/评语/答卷，无明确标准 | 评分统计可用；复核不可用；不生成标准正文 |
| H | 当前批次及历史批次 ID，无可比说明 | 各项评分可分析；longitudinal_progress 不可用，建议补可比性资料 |

```json
{
  "scenario": "A",
  "item_statistics": {"available_items": 3, "eligible_count_per_item": 3, "denominator": 3},
  "quality_issues": [],
  "profile_suggestion": {"missing": ["效率意识", "解题策略"], "requirement": "optional", "can_continue": true}
}
```

```json
{
  "scenario": "D",
  "recognized_items": {"count": 13, "kind": "question", "original_range": ["0", "100"], "unit": "points"},
  "item_statistics": {"available_items": 13},
  "confirmed_competency_mappings": 0
}
```

```json
{
  "scenario": "F",
  "recognized_items_count": 5,
  "first_item_quality_counts": {"missing": 1, "valid": 2},
  "first_item_statistics": {"status": "partial", "eligible_count": 2, "denominator": 3},
  "profile_missing_items": [],
  "quality_suggestion": {"requirement": "quality_blocker", "scope": "affected_observations", "can_continue": true}
}
```

```json
{
  "scenario": "H",
  "longitudinal_progress": {"status": "unavailable"},
  "suggestion": {"missing": "跨批次量表/任务难度、时间、参与者匹配及可比性确认依据", "requirement": "optional", "can_continue": true}
}
```

以上精简选段是说明用投影，不是正式 API 返回。完整原型结构、复现命令及测试边界见 [README](canonical_assessment/README.md)。B 故意添加答卷以验证具备条件的深度复核；删去答卷即变成 E，不因有五维就自动宣称可复核。

## 6. 已实现、原型验证和后续设计

V1 已实现固定模板解析、五维精确统计、群体筛选、来源回查及试点授权；评语/标准原文仍 withheld。正式洞察 API 属于并行 Issue #18，本轮不承诺其状态。动态项目的准备度、资料依赖和可选建议只在此次合成原型验证。

通用持久化、通用文件识别、非数值资料处理、真实用途授权、动态分析执行、比较等价性验证与前端接入均为未来工作；须经独立 Issue、契约评审和迁移/隐私验收。本轮无真实学生资料、无真实 Qwen、无部署，不能将 available 等同于正式系统能力已上线。
