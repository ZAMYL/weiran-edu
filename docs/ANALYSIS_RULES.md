# 学情分析规则 v0.1（草案）

> ⚠️ 本文部分内容已被 V1.4 取代，以 docs/V1.4_BASELINE.md 为准。

状态：ZAMYL 的第一阶段确定性分析实现口径，尚未由教师或团队正式冻结。不定义 HTTP JSON、数据库 Schema、评分等级或教学结论。入口为 `apps/api/app/analysis/edu/`；无第三方统计库、网络、文件读取或模型调用。

## 五个维度

内部 `Dimension` 枚举按以下顺序保留教师给出的原始评分，不从评语推算或改写分数：

| 内部维度 | 标签 / 评分对象 |
| --- | --- |
| ALGORITHMS | 算法与数据结构相关能力 |
| IMPLEMENTATION | 代码实现能力 |
| MODELING | 问题建模能力 |
| EFFICIENCY | 效率意识 |
| STRATEGY | 解题策略 |

这些标签不是新的评分量表。1—5 各分值的含义、维度权重、是否可跨教师/课程比较仍需教师确认；本次不计算总分、综合等级或“差生/高风险”等标签。

## 输入、校验和来源

- `AssessmentRecord` 包含批次 ID、脱敏学生 ID、记录 ID、来源 ID、五维原始值及证据引用。只有内部 Python 数据结构，不是正式公共协议。
- `prepare_dataset(records, policy=...)` 必须显式提供 `ScorePolicy`，生成不可变的规范化快照。后续函数只接收这一快照，输入顺序不改变结果，输出按批次和脱敏学生 ID 排序。
- `ScorePolicy()` 的草案默认范围为闭区间 [1,5]，默认不限制步长，故 2.5、2.25 均可保留；可以显式修改范围及步长。例如 `step=Fraction(1,2)` 表示从 minimum 起每 0.5 一档，不符合步长的数值被标为 invalid，不四舍五入。
- 接受 int、有限 float、有限 Decimal、Fraction；float 按其十进制字符串精确解释。不会把布尔值视为 0/1，也不自动把文本 "3" 转成分数。不补零、不截断、不缩放。
- 缺少某维度、值为 None，或文本精确命中调用者配置的 `missing_tokens`，标为 missing。默认没有文本缺失标记；空字符串默认 invalid，不擅自认定为空单元格。
- 越界、非数值文本、布尔值、NaN、无穷或步长不符的标量按单元格隔离为 invalid，保留 raw_value、原因、记录引用，并进入 `Dataset.issues`。该学生其他有效维度仍可分析。
- 未知维度、结构化单元格（例如 list/dict）、缺少或带首尾空白的标识、无效证据结构是结构错误，抛出 `AnalysisInputError`，整批输入不被部分接受。异常消息使用原因码，不包含分数、学生 ID 或评语内容。
- 同一批次出现两个相同学生 ID，即使内容完全相同也拒绝；不默认取第一条、最后一条或求平均。相同学生可存在于不同批次。同批次同来源同记录 ID 不能指向两名学生。
- `RecordRef` 保留 batch/student/record/source 及原输入证据。证据引用包含来源、定位符、score/comment/basis 类型及可选关联维度；不保存评语正文，不编造引用。评语可以来自与评分不同的来源。
- 引擎不验证来源文件是否存在或引用内容是否真实；后端必须验证授权及定位关系。没有传入引用时返回空引用，不能声称已有证据。

本阶段评分、统计和比例使用标准库 Fraction 精确保留有理数，raw_value 单独保留；不依赖调用者的 Decimal 精度。Spearman 系数使用 float，允许浮点误差。最终 API 数字编码、展示舍入和精度由三人另行确认，不直接序列化这些内部对象或无效原始值。

## 批次、群体和分母

所有分析显式传入 batch_id，绝不合并不同批次。

`student_ids=None` 表示该批次所有记录；指定 ID 集合表示其子群体，集合中重复的 ID 只计一次。空集合表示空群体；指定未知 ID 则报错，不能凭空补学生记录。一个没有记录的批次按空群体返回，调用者负责核实批次存在性。

群体人数 N 只表示输入中有测评记录的唯一学生数，不代表班级花名册人数、应测人数或缺考人数。每个结果的 scope 保留批次、规则配置、请求群体和实际记录引用。后端应先过滤授权范围，再调用引擎。

## A. 五维统计和分布

入口：`summarize(dataset, batch_id=..., student_ids=...)`。

对每维分别统计有效 n、缺失 m、无效 k，满足 n+m+k=N。均值为有效分数之和/n；中位数为排序后的中间值，偶数时取中间两值均值；最小/最大值仅使用有效分数。n=0 时这四项均为 None，而不是 0。

分布是按实际观测到的有效分值升序列出频数及比例，比例分母明确为 n；没有预设“优秀/薄弱”区间。分布总人数为 n、总比例为 1；没有有效值时分布为空。结果保留每维参与计算的记录引用。

手算示例：算法分数 [2,2.5,3,4]，n=4、均值=23/8=2.875、中位数=11/4=2.75、最小=2、最大=4；四个分值各 1 人、各占 1/4。若为 [2.5,2.5,None,"invalid",4]，n=3、m=1、k=1、均值=3，分布为 2.5 占 2/3、4 占 1/3。

## B. 跨维度 AND 筛选

入口：`select_students(dataset, batch_id=..., conditions=..., student_ids=...)`。

条件由 `Condition(Dimension, Operator, threshold)` 表示，支持 <、<=、==、>=、>，多个条件全部满足才命中。阈值必须为有限数值，不隐式转换字符串。<3 不包含 3，<=3 包含 3；分值和阈值不提前舍入。空条件列表报错，互相矛盾的条件返回真实的 0 命中。

只有所有被查询维度都有效的学生才进入可判定群体 E；任一被查询维度缺失/无效，记录进入 unevaluable_records，采用完整数据判定，不把它当成零分或已判定不命中。未被查询的维度缺失不会排除学生。

返回原条件、命中学生 ID/记录引用、可判定人数、未知记录，并同时提供：

- `matched_share`：命中人数 M/N（全部所选群体，包含未知记录）。
- `matched_among_eligible`：M/E（仅被查询维度均有效者）。

分母为 0 时比例为 None；人数始终是真实计算的整数。匹配比例不能被解释成其余所有学生均“不符合”。

手算示例：测试中四人的 (算法,效率,策略) 为 (2,2,4)、(2.5,1.5,5)、(3,3,3)、(4,2,4.5)。策略>=4 AND 算法<3 AND 效率<=2 命中前两人，即 2/4。增加一人算法缺失后，同条件为 M=2、N=5、E=4，两种比例分别为 2/5 和 1/2。

不同群体可以交叉。示例“算法<=3”有 3 人，“策略>=4”有 3 人，交集 2 人，并集为 4 人，不能把 3+3 当作总人数。

## C. 个体五维画像

入口：`student_profile(dataset, batch_id=..., student_id=..., student_ids=...)`。

返回该学生的原始评分、规范化有效值、missing/invalid 状态、同批次所选群体的每维统计、与群体均值之差及证据引用。默认比较群体包含本人；自定义群体必须包含目标学生，否则报错。不是跨批次比较，也不是排除本人后的统计。

差值为“本人有效值−群体该维有效均值”；本人缺失/无效或群体无有效值时为 None。证据仅包含目标维度及未限定维度的原引用，完整记录引用仍保留全部证据。

手算示例：第二人算法原始值 2.5，群体均值 23/8，差值=-3/8=-0.375。没有综合能力等级、排名标签、诊断或教学结论。

## D. Spearman 等级相关

入口：`spearman_correlations(dataset, batch_id=..., student_ids=..., minimum_pairs=2)`。

五维返回 10 个不重复维度对。每一对只使用两维均有效的同批次、同群体学生，单独重新排序和计算秩，不要求其他三维完整。有效成对样本 n_pair 与被排除记录显式返回，不能把不同维度对的样本数视为相同。

从 1 开始排名，并列值取所占名次的平均数；在该对有效样本中计算平均秩的 Pearson 相关。不能在并列情况下套用无并列的 d² 简化公式。返回逐条原分数、平均秩和记录引用，方便复算。方法参考 [Python 3.12 statistics.correlation](https://docs.python.org/3.12/library/statistics.html#statistics.correlation)。

n_pair 小于 minimum_pairs 时状态为 insufficient_pairs；任一维恒定时为 constant_input，系数均为 None。minimum_pairs 可配置且至少为 2；默认 2 只是数学可计算下限，不是教学研究的充分样本标准。两种不可计算条件同时满足时，先报告样本不足。

手算例：X=[1,1,2,3]、Y=[1,2,2,3]，平均秩分别为 [1.5,1.5,3,4] 和 [1,2.5,2.5,4]。两者秩均值=2.5，中心化乘积和=3.75，两边平方和均为4.5，rho=3.75/sqrt(4.5×4.5)=5/6。单个样本或恒定维度不返回伪造的 0。

相关性不是因果关系，不提供 p 值、置信区间、显著性、学生分组或因果推断。不同对使用不同样本时，也不保证能当作同一完整样本的相关矩阵用于其他统计运算。

## 后端 Excel 解析服务如何对接

后端负责人负责读取 Excel、字段映射、解析空单元格与数值、脱敏、权限和证据定位。本模块不读文件、不接 HTTP、不连 SQLite。解析器将认可的缺失状态显式转换为 None 或双方约定的 missing_tokens；不要依赖引擎自动识别 Excel/pandas NaN。

```python
from fractions import Fraction
from app.analysis.edu import (
    AssessmentRecord, Dimension, ScorePolicy, Condition, Operator,
    prepare_dataset, summarize, select_students,
)

# 仅演示合成输入；实际调用先校验批次/学生授权，再构造有来源的记录。
rows = [
    AssessmentRecord(
        batch_id="synthetic-batch",
        student_id="synthetic-001",
        record_id="synthetic-row-1",
        source_id="synthetic-source",
        scores={dimension: 3 for dimension in Dimension},
    )
]
data = prepare_dataset(rows, policy=ScorePolicy(step=Fraction(1, 2)))
stats = summarize(data, batch_id="synthetic-batch")
selected = select_students(
    data, batch_id="synthetic-batch",
    conditions=[Condition(Dimension.ALGORITHMS, Operator.LE, Fraction(3))],
)
assert stats.scope.student_count == 1
assert selected.matched_share.proportion == 1
```

先处理结构错误或展示 issues，再使用统计结果；不能将部分无效数据伪装成全部有效。内部结果到正式 DTO、响应精度、证据读取和权限校验由后端与前端/AI 负责人协商，不直接暴露所有原始值或其他学生数据。不得绕过 prepare_dataset 手动拼装 Dataset 及其内部对象。

## 待教师与团队确认 / 下一阶段

确认每一分的量表含义、合法范围及最小步长、空值和无效值的业务解释、重复测评的解决规则、应测人数口径、筛选默认展示哪一种分母、群体比较是否包含本人、研究所需最小样本及跨教师/批次可比性。

本次 v0.1 的实现可测，但不等于统计口径正式冻结。后续另行设计解析适配、正式公共协议、权限、评语主题分类及 AI 教学解释；不从这些分数直接推出学生等级、教学效果、发展轨迹或因果关系。

## 验证

在 apps/api 下执行：

```text
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -q -ra
uv run --locked pytest tests/analysis -q
```

测试全部为合成记录；四类手算例及边界断言见 tests/analysis，实际执行数量与结果以本次 PR 报告和 CI 为准。
