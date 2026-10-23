# 评语主题与评价复核规则 v0.1（草案）

本阶段提供确定性、可追溯的辅助复核，不判断学生好坏、不修改成绩或原评语，也不宣称评价错误已经成立。模块为 `app.analysis.edu.comments`，与现有内部 Dataset/Dimension/EvidenceRef 兼容，不定义 HTTP、数据库或仲楷的 API 契约。

## 依据和确认状态

当前仓库可核实的依据是 [ANALYSIS_RULES.md 的五维标签](ANALYSIS_RULES.md#五个维度) 和已有 Dimension 枚举；其中明确说明细化量表尚待教师确认。仓库没有可据以冻结规则的教师详细评价依据。

以下五类主题按任务给定候选与五维标签对应，全部输出 `rule_status="draft"`。词典是工程草案，不是正式教学知识点，也不能证明学生已经存在某项问题。

| 维度 | 候选主题 | 默认字面关键词 |
| --- | --- | --- |
| ALGORITHMS | 算法与数据结构基础 | 算法、数据结构 |
| IMPLEMENTATION | 代码实现、调试与边界处理 | 代码、调试、边界条件、边界处理 |
| MODELING | 问题分析与建模 | 问题分析、问题建模、建模 |
| EFFICIENCY | 时间和空间复杂度 | 时间复杂度、空间复杂度、复杂度、效率意识 |
| STRATEGY | 解题策略与优化 | 解题策略、策略选择、优化思路、解题方法 |

一条评语可有多个主题，同一主题只返回一次。词语重叠可产生多个命中位置，不是多个人次或置信度投票。“算法的时间复杂度”可以命中两个相关主题，不能由此判断两项能力均有缺陷。

## 版本和配置

内置规则版本为 `edu-comment-0.1.0`，定义在 comments/rules.py。RuleSet 是冻结的内部 Python 配置，支持修改主题词、上下文短句、精确评分标签、抑制词、重复比较策略和长度限制；不接受任意正则表达式或可执行脚本。

配置包含依据引用；所有结果记录版本、draft 状态和 SHA-256 配置指纹。指纹只基于规则配置，不对学生原文生成可外传的哈希。即使调用者忘记改版本，词典变动也会改变配置指纹；正式维护仍必须随词典或算法行为变更递增版本，并补充反例测试。当前实现不提供将草案自动标成教师已确认的开关。

例如可使用 dataclasses.replace(DEFAULT_RULES, version="local-draft-v2", contexts=...) 创建新草案。配置拒绝空关键词、重复规则 ID、同一维度重复主题、不含本主题关键词的方向短句、歧义评分标签及无效选项。新增方向短句不得只写“薄弱”等无维度指向的词。

## 分类与肯否语境

classify_comment 返回：
- classified：命中至少一个主题。
- unclassified：没有命中，不猜测主题。
- empty：None、空串或全空白。

主题与方向分开。方向为 affirmative、negative、mixed、mention_only 或 uncertain；negative 只表示当前字面表达为否定性评价，不是学生标签，也不自动触发评分冲突。

| 维度 | 肯定短句 | 否定短句 |
| --- | --- | --- |
| ALGORITHMS | 算法基础扎实；数据结构运用正确 | 算法基础薄弱；算法基础不扎实；数据结构运用不当 |
| IMPLEMENTATION | 代码实现正确；边界处理完善；调试方法合理 | 代码实现有误；调试存在困难；未考虑边界条件 |
| MODELING | 建模准确；问题分析清晰 | 建模不准确；问题分析不清晰 |
| EFFICIENCY | 复杂度分析清晰；时间复杂度分析清晰；空间复杂度分析清晰；效率意识良好 | 对应的“分析不清晰”；效率意识不足 |
| STRATEGY | 解题策略合理；优化思路清晰 | 解题策略不合理；优化思路不清晰 |

方向只认可完整的已配置字面短句，允许句首为“本次、目前、但、但是、然而、不过”。以中英文逗号、中文句号、分号、问号、感叹号、换行作为短句边界；未知前后文包围方向短句时降为 uncertain。重叠短句被完整的同方向短句覆盖时不额外制造不确定。

出现否定转述、反例、条件、历史、引文、疑问、目标等抑制信号时，整条评语的方向保守降为 uncertain。默认例子包括“并非、不是、没有、不能、不一定、未必、例如、反例、如果、可能、曾经、上次、建议、目标、其他人、引用、满分”、引号和问号等；完整列表以配置为准。

这项全评语抑制有意牺牲召回率，避免局部肯否语境被跨句误用。“没有算法基础薄弱的问题”不会被当作基础薄弱；“排除算法基础薄弱”因周边语境不受支持而返回 uncertain，不自动翻转为肯定。只有关键词而没有明确短句时为 mention_only。

例子：
- “算法基础扎实，代码实现有误”→ 算法 affirmative、实现 negative。
- “讨论算法与数据结构”→ 算法 mention_only。
- “反例：算法基础薄弱”→ 算法 uncertain。
- “继续保持学习投入”→ unclassified。

## 重复评语

review_comments 在指定批次及群体内比较非空评语，默认只做原始文本完全相等比较，不去标点、不合并内部空白、不模糊匹配。

可以显式设置 trim_duplicate_whitespace=True，仅在相等比较时忽略两端空白；原始输入和证据偏移不变。None、空串及全空白不计为重复组。重复组返回：
- occurrences：原评语单元格出现次数。
- distinct_assessments：涉及的不同测评记录数。
- 原始引用、规则 ID、版本和指纹。

同一记录有两个独立评语单元格时，可以是出现 2 次、涉及 1 条测评记录。重复传入同一单元格或同批次相同 comment_id 则拒绝，不能人为抬高频次。多批次相同文字不跨批次计数。

重复候选规则为 review.duplicate，原因明确为“文本重复，供检查，不据此判错”。相同评语配不同评分也不自动判错；例如 3 人都写“算法基础扎实”、原分分别为 1/3/5，重复频次为 3，但不会据此生成评分冲突。

## 保守的评分一致性复核

本阶段不把“扎实、薄弱、合理”等描述映射为分数阈值，因为教师量表尚未确认。相关记录返回 manual_review，供教师判断。

只有整条评语的非空短句全部是以下精确评分断言，且没有抑制信号，才进行自动数值对照：

`[可选“本次”] + 完整五维标签 + (评分或得分) + (为、是、:、：) + 数值 + 分`

例如“算法与数据结构评分为5分”“本次代码实现能力得分为2.5分”。数值支持 ASCII 十进制，整数和小数部分各最多六位，可带正负号；不识别中文数字、分数表达式、指数、满分说明或一般教学描述。不支持的格式转人工，不擅自解析。

额外条件：
- 来自同一已校验 Dataset 记录，关联评语 EvidenceRef 必须属于该 RecordRef。
- 原评分有效，文本数值符合当前 ScorePolicy 的范围和步长。
- 存在该维度或未限定维度的 SCORE 证据引用。
- 评语来源若绑定具体维度，不能拿来对照另一个维度。
- 同一评语对同一维度有多个不同分数时，转人工，不任选一个分数。

符合这些条件且数值不等时，生成 review.numeric_mismatch 复核候选，携带两边分值、维度、原评分/评语引用、命中偏移、规则版本/指纹和固定中文原因。半分使用现有 Fraction 精确对比，不舍入。

无法可靠判定时生成固定原因的 manual_judgment，包括空/未分类/未提供评语、受抑制或非完整断言、文本分数冲突、原分无效、文本数值不符合规则、缺少评分引用或维度不对应。

RecordReview 状态：
- conflict_candidate：存在明确数值冲突候选，仍不是最终错误结论。
- manual_review：有重复提示或无法可靠判断的事项。
- no_explicit_conflict：受支持的数值断言未发现冲突；不表示全部五维或全部语义已验证一致。

合成例：原算法分为 2，评语是“算法与数据结构评分为5分”→ 候选，recorded_score=2、stated_score=5。若是“例如，算法与数据结构评分为5分”或“算法基础扎实”→ manual_review，不生成数值冲突。

## 后端接入和证据约束

后端负责授权、脱敏、解析、原文读取与引用真实性，先使用已有 prepare_dataset 校验评分，再将原评语文本作为短暂内存输入，并提供与该记录相连的 COMMENT 引用。入口返回内部 Python 对象，不是正式公共协议。

```python
from app.analysis.edu import (
    AssessmentRecord, Dimension, EvidenceKind, EvidenceRef, ScorePolicy, prepare_dataset,
)
from app.analysis.edu.comments import CommentInput, FindingKind, review_comments

score_ref = EvidenceRef(
    "synthetic-source", "row-1/algorithm-score", EvidenceKind.SCORE, Dimension.ALGORITHMS
)
comment_ref = EvidenceRef("synthetic-source", "row-1/comment", EvidenceKind.COMMENT)
row = AssessmentRecord(
    "synthetic-batch", "synthetic-001", "row-1", "synthetic-source",
    {dimension: 2 for dimension in Dimension}, (score_ref, comment_ref),
)
data = prepare_dataset([row], policy=ScorePolicy())
comment = CommentInput(
    data.records[0].reference, "synthetic-comment-1", comment_ref,
    "算法与数据结构评分为5分",
)
report = review_comments(data, [comment], batch_id="synthetic-batch")
finding = report.records[0].findings[0]
assert finding.kind is FindingKind.SCORE_CONFLICT
assert (finding.recorded_score, finding.stated_score) == (2, 5)
assert row.scores[Dimension.ALGORITHMS] == 2
```

群体选择与已有引擎一致。没有传入评语的群体记录仍返回“未提供评语，无法判断”；批次外和已知但未选入群体的评语不参与计数。当前批次未知学生、伪造或不对应的 RecordRef、重复输入、无效类型和超长文本抛出安全原因码，不部分吞掉错误。

## 隐私、适用范围和局限

- 测试和文档只含合成文本、脱敏样例标识；不含教师文件或真实评语。
- 模块不记录日志、不打印、不读写文件、不调用外网、模型或数据库；默认长度上限 4000 字符，可显式配置。
- CommentInput 的默认 repr 隐藏原文；结果仅保存引用和原文中以 Python 字符索引计的 [start,end) 命中位置，不复制正文或重复组文本。不对输入做归一化后重新计算偏移。
- 后端必须限制引用解析与输出权限，不把输入 dataclass 做 asdict/日志转储，不把内部对象直接当作公开 DTO。异常对象的 traceback/local variables 也不应外传。这里只保证本模块不主动输出原文，不保证调用方任意调试操作不会泄露。
- 字面词典不能完整理解中文否定、讽刺、指代、转述或隐含时间关系。未知表达可能漏分类，主题命中也可能有歧义；本规则不提供真实数据准确率或置信度。
- 精确断言限制和全评语抑制会造成较多人工项与漏报，这是当前保守范围。no_explicit_conflict 不能当作评价合格证。
- 人工复核结论、分数修订、审计记录和持久化属于未来业务模块，本阶段不实现。

## 教师和团队待确认

需核对真实五维评价依据、主题词与同义词、短句肯否定义、抑制范围、是否需要明确且经批准的描述—分数规则、重复比较标准、默认展示与优先级、长度限制，以及人工复核流程。

教师确认前保持 draft；下一阶段应先基于授权且完成隐私审查的材料评估误报/漏报，再协调正式 DTO 与流程。不得把合成测试通过率当作真实语料分类准确率。

在 apps/api 下运行 Ruff、pytest tests/comments 以及全部 pytest；实际数量和 CI 结果见当前 PR，测试不会调用真实模型。
