# 智能学情发现候选 v0.1（草案）

这是已有确定性结果的内部产品适配，不增加分析公式，不代表教学标准已冻结。
入口：`app.analysis.edu.insights`。不引入网络、LLM、HTTP、数据库或新依赖。
字段对照与待协调差异见 [ANALYSIS_FRONTEND_HANDOFF.md](ANALYSIS_FRONTEND_HANDOFF.md)。

## 接入与计算口径

受信后端先取得**完整的获授权群体**、不可变输入版本及本批 ScorePolicy，
用已有 prepare_dataset 构造 Dataset，再调用：

```python
from fractions import Fraction
from app.analysis.edu import Condition, Dimension, Operator
from app.analysis.edu.insights import (
    DiscoveryPolicy, DistributionFocus, GroupRule, discover_candidates, teacher_cards,
)

candidates = discover_candidates(
    dataset,  # prepare_dataset(..., policy=本批标准)，不是分页的一页记录
    batch_id=batch_id,
    snapshot_id=immutable_revision,
    student_ids=authorized_student_ids,
    groups=(GroupRule("explore.strategy_algorithm", (
        Condition(Dimension.STRATEGY, Operator.GE, 4),
        Condition(Dimension.ALGORITHMS, Operator.LT, 3),
    )),),
    focuses=(DistributionFocus(
        Condition(Dimension.ALGORITHMS, Operator.LT, 3), Fraction(1, 2),
    ),),
    comments=authorized_comments,
    policy=DiscoveryPolicy(),  # 公开阈值为可配置草案，不是匿名性保证
)
# 仅在调用方完成真实批次/群体授权之后使用；这个布尔值不实现鉴权。
cards = teacher_cards(candidates, authorization_checked=True, digits=2)
```

None 的 student_ids 表示本 Dataset 中该批全部记录，不能传入超出授权的数据集。
核心不认识教师身份、租户、数据库或未来 DTO；生产授权仍未实现。
匿名学生 ID 去重、重复记录拒绝与无效分数隔离沿用现有引擎。
小数必须遵循本批明确的标准；合成演示显式使用 1/100 步长。

| 类型 | 复用入口 | 生成内容与分母 |
| --- | --- | --- |
| dimension_distribution | summarize；可选 select_students 区间探索 | 每维有效 n/总 N、缺失/无效数、均值、中位数、实际分值频数（分母 n）；区间 M/n |
| ability_group | select_students | AND 条件、命中 M/N、可判定 E、M/E、不可判定 N-E；零命中不生成群体卡 |
| evaluation_review | review_comments | 重复文本单元数与涉及唯一测评记录数、严格数值冲突候选、需补充证据或人工判断的记录集合 |

默认输出非空群体的五维分布，不预设低分阈值或永久能力等级。
可关闭 include_dimensions，以便全部无命中时允许返回空结果。
可配置 DistributionFocus 的分数条件与 minimum_share；只有 M>0 且 M/n 达到所设比例，
标题才标“在所设评分区间内较集中”。这是相对于调用者设置的区间占比，
**不是**相对于其他维度、其他学校或往年基准，更不是低能力诊断。
空输入不造卡；有记录但某维全无效时，该维卡标 insufficient_data，均值/中位数为 None。

重复评语按现有规则归一化，不生成或回显原文。相同文本可以对应不同合法评分，
重复不等于错误；只有原模块能严格确认的完整文本数值断言冲突才标冲突候选。
comments=None 表示未接入评语提供器，跳过复核；
显式传空集合表示本次未提供任何对应评语，产生缺少输入的人工核查线索，
不能据此断言教师原文件中的评语为空。此语义需后端保留。
同一记录可涉及多类复核，不得把跨卡人数相加。所有复核都不回写分数或评语。

## 内部对象、来源与展示

InsightCandidate 包含 candidate_id、batch_id、candidate_type、程序标题与摘要、
metrics、conditions、denominator、rule_id/version、source_refs、evidence_refs、
query_ref、status、limitations、suggested_next_action、public_restrictions。
评语候选额外保留源规则 fingerprint；适配器版本为
edu-insight-candidates-0.1.0-draft。内部版本与规则不代表已批准的公共协议。

Metric.value 使用 int/Fraction/None；Metric.denominator 是统计样本数，
与 Fraction 编码中的 denominator 完全不同。频数附 at_score，
不预先将合法 2.75 归进整数档。显示 half-up，允许 0—6 位；
display 只用于阅读，不能回写分数、过滤数据或反推人数。
摘要也由程序依据精确结果生成，可能含分数形式；界面可优先展示 metrics.display。

Metric.source_refs / metric_sources(candidate, key) 是**该指标参与记录**：
- 均值、中位数只用本维有效记录，某档频数只用该分值记录；
- 缺失、无效计数各关联对应状态；区间命中只关联实际命中者；
- M/N、M/E 的直接支持来源为 matches；E、N-E 各自关联可判定/不可判定者；
- 复核来源为对应 Finding/重复组实际涉及的记录；评语单元数与学生数分开。

candidate.source_refs 是候选的主来源（统计为有效记录，群体为 matches，
复核为涉及记录），不是每个指标的来源并集。
query_ref.scope 是整个分母范围，不能拿群体成员资格替代直接证据参与资格。
evidence_refs 是内部 RecordRef + EvidenceRef 配对，只带相应维度评分、
群体命中维度评分或对应复核证据。无维度的评分引用只表示记录级定位，
不能声称已验证某个评分单元格；未来 Provider 须再验证种类、维度、原文和归属。
没有绑定评分证据时仍可计算统计，但不能谎称原文已可追溯。

candidate_id 由快照、范围、条件/发现配置、评分策略及版本确定；
不依赖 Python hash 或输入集合遍历顺序。它是逻辑查询引用，
**不是内容校验和、匿名化凭证、fact_id、evidence_id 或授权令牌**。
调用者必须让 immutable_revision 覆盖评分、评语、证据及标准的变更，
不能用同一版本重算不同数据后继续使用旧审批。哈希内部标识不代表数据已匿名化。

teacher_cards 为显式白名单投影，不序列化内部 dataclass：
没有学生 ID、RecordRef、来源定位、原文或原始姓名/学号。
它提供汇总范围、维度映射、精确/显示指标、条件、版本、状态和受控查询意图。
controlled_query 仅有候选 ID、快照 ID、种类和可选 metric_keys，
未来调用方须按获授权的缓存对象/重算结果解析，不能按客户端参数自由读取记录。
批次、快照、规则 ID 必须由受信服务提供不含个人信息的不透明值。

## 公开边界与局限

public_cards 默认返回空集合；每张卡必须显式列入人工审核白名单，
且没有任何 public_restrictions 才可投影。缺省 minimum_public_count=5：
- 所选范围小于阈值；
- 任一公开非零计数或其补数小于阈值（含分布档位、缺失/无效及筛选可判定数）；
- 范围与后端提供的敏感匿名 ID 集合相交；
- 所有个人评价复核候选；
以上均禁止公开，即使白名单批准也不放行。禁止卡整个省略，不泄露占位数量。
公开投影不提供受控详情查询。教师内部预览仍标明这些限制。

这些检查只是一层保守提示，**不是**差分隐私、真实脱敏检测或发布许可系统；
不能自动识别敏感身份，跨卡交集/差集、跨批次差分和连续查询仍可能泄露。
正式公开须由后端确认实际身份权限、披露策略、查询组合和审批生命周期；
不能将 public_display_allowed 当作已完成真实隐私审查。

## 与现有 Qwen 的关系

本轮不修改 ToolExecutor、Fact、Proposal、validate_proposal 或运行模式。
不注册新模型工具，不调用真实 Qwen，不读取 Key。核心候选也能脱离模型使用。
下一阶段可在既有工具白名单及预算内，增加候选选择适配：
1. 后端先按真实授权生成候选与不可变快照；
2. 逐个 metric 建立现有 Fact，source_refs 必须来自 metric_sources，
   不得使用 query_ref.scope.records 或整个 candidate.source_refs 替代；
3. 证据和标准继续经既有 Provider/grant、批次/维度和实际来源校验；
4. 模型只选合法候选/事实，程序渲染数字与事实文本，解释继续标待验证；
5. 未获得真实数据授权、脱敏审查和 Production 实现前，不把真实数据传入现有合成在线模式。

自然语言的教学解释、来源文件真实性和评价标准的教学有效性均不能由这些统计证明。

## 离线演示与验证

```powershell
cd D:\AI\weiran-edu\apps\api
uv run --locked python -m app.analysis.edu.insights.demo
uv run --locked pytest tests/insights -q -ra
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -q -ra
```

演示仅内置四条合成记录，合成授权声明，不读取教师文件或调用模型。
三张完整结果见 [insight_candidates_synthetic.json](../evals/insight_candidates_synthetic.json)：
- 算法 [2,2.75,3,4]：均值 47/16=2.9375，中位数 23/8=2.875，
  <3 命中 2/4 达到所设 1/2 占比；四个实际分值各 1/4。
- 策略 [4,5,3,4] 且算法 <3：前两条命中，M/N=2/4，E=4，M/E=1/2。
- 前两条合成评语相同：2 个文本单元、2 条测评记录；仅复核提示。
四人样本小于默认公开阈值，三张均不允许公开；JSON 只用于合成示例。

自动测试覆盖正常/小数/缺失/无效、AND 与 < / <=、空/零命中、
批次与范围隔离、顺序/快照稳定、来源绑定、重复与冲突、公开限制、
投影敏感信息及展示精度独立性。真实测试数量与 CI 记录见本 PR 的实际运行结果。
