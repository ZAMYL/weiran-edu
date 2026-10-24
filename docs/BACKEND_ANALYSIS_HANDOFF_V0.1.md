# 后端与分析引擎对接草案 v0.1

状态：保留的历史对接设计。2026-10-25 审核更新：五维映射、定点评分到 Fraction、证据 ID 关联已由 `apps/api/app/edu/analysis.py` 与 `reads.py` 实现，未修改分析引擎。当前行为见 [统计与证据接口](STATISTICS_GROUP_EVIDENCE_API.md) 和 [实现差异清单](IMPLEMENTATION_STATUS.md)；下文原核验基线与未实现表述仅记录设计时状态。

核验基线：main 提交 `05c20e824e1f1166682756f7e01fbcd086521244`。实际对象见 `apps/api/app/analysis/edu/models.py`、`validation.py`、`engine.py`，计算规则见 [ANALYSIS_RULES.md](ANALYSIS_RULES.md)。该提交已有离线统计、AND 筛选、个体画像和 Spearman；不等于六类分析、HTTP 接口或后端适配已经完成。

## 1. 维度与运算符映射

API/数据库继续使用稳定英文编码，适配器按下表建立双向映射，不按中文、模糊名称或枚举位置猜测。

| API / 数据库编码 | 引擎枚举成员 | 当前枚举中文 value |
| --- | --- | --- |
| `algorithm_data_structure` | `Dimension.ALGORITHMS` | 算法与数据结构 |
| `code_implementation` | `Dimension.IMPLEMENTATION` | 代码实现能力 |
| `problem_modeling` | `Dimension.MODELING` | 问题建模能力 |
| `efficiency_awareness` | `Dimension.EFFICIENCY` | 效率意识 |
| `solution_strategy` | `Dimension.STRATEGY` | 解题策略 |

当前 `Dimension("algorithm_data_structure")` 不成立，因为枚举 value 是中文。返回 DTO 时通过逆映射恢复英文编码；未知编码返回 `422 VALIDATION_ERROR`。引擎新增维度时先升级适配，不静默忽略。

| API 运算符 | 引擎枚举成员 | 含义 |
| --- | --- | --- |
| `lt` | `Operator.LT` | `<` |
| `lte` | `Operator.LE` | `<=` |
| `eq` | `Operator.EQ` | `==` |
| `gte` | `Operator.GE` | `>=` |
| `gt` | `Operator.GT` | `>` |

引擎可接收更多条件；公共 API 仍只开放 1～2 个不同维度条件、固定 AND，不自动扩大接口能力。

## 2. 评分精度与 Fraction

### 输入、存储与计算

- 后端口径为闭区间 `[1,5]`、百分之一分精度。先精确解析、检查范围和粒度，再存储；不能先 round、quantize 或 int 截断后声称原值合法。
- JSON 只接受数字，拒绝布尔值和文本数字；Excel 原单元格由固定模板解析器识别，原文另存 `score_raw`，不直接把这个字符串传入引擎。
- 检查精确值乘 100 是否为整数，再写入 `score_units`。`2.5`、`2.50`、`2.75` 对应 `250`、`250`、`275`；`2.751` 拒绝，不改为 `2.75`。
- SQLite 读出后使用 `Fraction(score_units,100)` 构造 scores，阈值同样精确转换；不将 275 当成原始分数，不使用 `Fraction(float_value)` 或 `Decimal(float_value)`。
- 兼容 Python float 时先检查有限性，再以十进制字符串解释；原始十进制词法应在读取文件/JSON 时保留，不能依赖浮点恢复文件原始精度。

适配器显式调用：

```python
prepare_dataset(
    records,
    policy=ScorePolicy(
        minimum=Fraction(1), maximum=Fraction(5), step=Fraction(1, 100)
    ),
)
```

引擎默认不限制步长，不能直接以默认策略替代后端精度校验；分析文档的 `step=Fraction(1,2)` 是半分示例，不能用来拒绝合法的 `2.75`。

正式批次每名学生五维完整。引擎可逐维隔离 missing/invalid，是更通用的内部能力，不改变后端“缺分阻止整批确认”的规则。正式数据出现 Dataset.issues 时应按完整性失败处理，不偷偷缩小样本。

### 输出边界

- 筛选、计数和原始比例保留 Fraction，不中途舍入。API 单项评分从整数单位恢复，最多两位小数。
- 均值、中位数和比例可能不是两位小数，不能套用评分精度。统计成功 DTO 的展示位数和舍入方式仍需分析负责人确认，本次不擅自冻结。
- 群体接口 `ratio` 接 `selected.matched_share.proportion`，即 M/N；JSON number 可能近似，精确口径以 `matched_count / total_count` 为准。不能根据近似比例反算人数或重新筛选。
- 不直接 JSON 序列化 Fraction、Enum、dataclass、raw_value 或整个 Dataset；通过白名单 DTO 投影。分母为 0 时内部 None 不替换为 0；正式批次禁止为空，此时先核对数据完整性。
- Spearman 当前是 float，仅可输出有限值，不可计算状态对应 null；正式统计成功响应另行定义。

## 3. 匿名证据 ID 与引擎引用

一个 AssessmentRecord 是“一批次内一名学生的五维”，不是一条评分；因此 record_id 不取任意单条 evidence_id。

| 引擎字段 | 服务端映射 |
| --- | --- |
| `batch_id` | 获授权的正式 batch_id |
| `student_id` | students.public_id，绝不使用真实学号、姓名或 student_pk |
| `record_id` | `record/{batch_id}/{student_id}`，由两个不透明 ID 组成，批内唯一 |
| `source_id` | batches.source_import_id，对应正式批次原始文件，不随重复上传改变 |
| `scores` | 英文编码映射 Dimension 后，五个 Fraction(score_units,100) |
| `evidence_refs` | 从持久化且获授权的评分、评语及标准逐项构造 |

| EvidenceKind | location | dimension | 回查目标 |
| --- | --- | --- | --- |
| `SCORE` | `evidence/{evidence_id}/score` | 对应 Dimension | 本批评分与来源 |
| `COMMENT` | `evidence/{evidence_id}/comment` | 对应 Dimension | 已检查或遮蔽且允许展示的评语 |
| `BASIS` | `criterion/{rubric_criterion_id}/basis` | 对应 Dimension | 本批标准该维依据与来源 |

每条 EvidenceRef.source_id 使用实际所属文件的稳定来源导入 ID；当前模板三类证据来自同一文件。未来不同文件来源须分别建真实来源关系，不能全套本批 source_id。location 是内部定位符，不是 URL、磁盘路径或可执行表达式。

构造与回查顺序：

1. 先核验教师、批次和学生权限，再从数据库读取五维及本批标准；核对每条 evidence_id 所属批次、学生和维度。
2. 只有真实来源与持久化 ID 才构造引用。未确认预览没有正式证据 ID，不送入正式分析。空评语或 withheld 评语不构造可展示的 COMMENT 引用；不编造缺失证据。
3. 构造记录并调用 prepare_dataset；引擎返回的 RecordRef/EvidenceRef 作为内部线索保留，不把整个引用对象发送前端。
4. 回查时重新核对 `(batch_id,student_id,record_id,source_id,kind,dimension,location)` 与本次授权快照及数据库事实。禁止信任模型或调用方自填的定位符；跨批次、跨学生、维度不符均拒绝，内部完整性失败不伪装成正常无证据。
5. 仅将核验后的公开 evidence_id 写入 DTO，评分/评语仍经本批 `/evidence/{evidence_id}` 读取；依据经本批 `/rubric` 按 rubric_criterion_id 匹配。解析 ID 不能绕过权限或脱敏。
6. 修正版生成新批次与新证据，旧引用不改指向；重复上传返回原批次、原 source_import_id 和原 evidence_id。

引擎不验证文件真实性、不读取 Excel 或评语正文，也不承担教师鉴权。引用只用于定位，不是授权；页面允许读取不代表已取得向外部模型发送个人证据的授权。

## 4. 人数、分页与验收

- 先取本批全部获授权记录调用引擎，筛选完成后再分页；不能把一页当整批。matched_count 来自 matches，total_count 来自 scope.student_count。
- API 使用 M/N，不接 matched_among_eligible 的 M/E。正式完整批次应有 E=N、unevaluable_records 为空；不满足时报告完整性失败，不改分母。
- 合成核对：五维和五个运算符双向映射；`2.75 → 275 → Fraction(11,4)`；非法精度、布尔值、NaN、文本拒绝；筛选阈值边界、人数比例和分页证据一致。
- 核对 score/comment/basis 引用维度、空/扣留评语处理、修订和重复上传的来源稳定性，以及伪造跨批次/跨学生引用的拒绝行为。

本次用独立合成探针验证实际引擎的映射、精确数值与引用保留。真正的适配器、数据库授权和 HTTP 联调尚未实现；评审后再由后端与分析负责人共同实现，不在此 PR 修改分析模块。

## 5. Demo 联调补充（2026-10-24）

PR #9 已合并。新增[Demo/API 对齐草案](FRONTEND_API_ALIGNMENT_V0.1.md)和同版 OpenAPI 采用 HALF_UP 两位展示建议，与现有 teacher_cards 保持一致；保留 exact numerator/denominator，不改变筛选或人数，尚待相关负责人确认。

候选回查使用 metric_sources 获取每项指标实际参与者；candidate.source_refs 只表示主来源，不能代替每项指标来源，更不能用整个 scope 充当证据。数据与脱敏快照需绑定并逐次重检权限。当前没有实现 HTTP/数据库适配。
