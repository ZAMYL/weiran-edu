# DS-03 · 代码特征与错误模式分析内核

本内核提供可复用的本地代码结构特征提取，并与判题结论、测试点证据关联，形成可供 C-01
Agent 继续调查的模式候选。它不执行学生代码、不搭沙箱、不预置固定结论、不推断学生性格/是否
使用 AI/五维能力分。

代码位置：`apps/api/app/analysis/code/`（纯标准库，无新增依赖）。测试：`apps/api/tests/code/`。

## 一、复用边界（与 B-01 / C-01）

- B-01 PR #105 的 `AssessmentReader.problem_patterns()` 已提供：每题结论分布、满分/零分人数、
  平均分、失败测试点计数（`worst_test_points`）。**本内核不重复这些基础计数。**
- 本内核消费 B-01 的逐提交原始证据（`submissions.code_text` / `verdict` /
  `test_observations`），补上 `problem_patterns` 没有的**代码结构特征**及其与结论/测试点的关联。
- C-01 PR #107 的 `programming_evidence.py` 定义了 `FeatureCode` 白名单与 `FeatureAssociation`
  形态；本内核通过 `interop.py` 向其投影，**不修改 C-01 的 Agent 核心**。

## 二、代码特征

8 个可验证结构特征（`features.py` 的 `FeatureId`）：

| 特征 | 含义 | 二值化阈值 |
|---|---|---|
| `branches` | 条件分支点（if / 三元 / match / switch） | ≥1 |
| `loops` | 循环结构（for / while / do） | ≥1 |
| `max_loop_nesting` | 循环最大嵌套深度 | ≥2（=有嵌套循环） |
| `functions` | 函数/方法/lambda 定义数 | ≥1 |
| `direct_self_recursion` | 直接自调用（递归） | 布尔 |
| `index_accesses` | 下标/索引访问 | ≥1 |
| `container_uses` | 容器字面量/类型构造 | ≥1 |
| `io_operations` | 输入输出调用点 | ≥1 |

解析方法与置信度：

- **Python**：标准库 `ast` 全量解析，`reliable`；注释/字符串/docstring 天然不干扰。
- **C++**：手写词法器（跳过 `//`、`/* */`、字符串/字符字面量、预处理行）+ 花括号作用域分析，
  `tentative`。
- 无法可靠判断一律 `unknown`（缺源码 / 语法错 / 不支持语言），不猜。

## 三、关联计算

`engine.analyze(batch_id, problem_id, submissions, *, minimum_samples=2) -> Analysis`：

1. 校验（重复提交 / 身份不一致 / 重复测试点 / 越界记录 → 拒绝，不静默修复）。
2. 逐提交提取特征。
3. 结论分组（success / failure / other），对每个特征计算 2×2 列联表
   （特征有无 × 通过/失败），用精确 `Fraction` 比例与差值，保留 `excluded_count` 与分母。
4. 对 `COMPUTABLE` 且差值非零的特征产出 `PatternCandidate`，带 `evidence`（失败且含该特征的
   提交来源引用）。

三个解释层次在 `explanation.py` 结构上分离：`ObservedFact`（事实）/ `StatisticalAssociation`
（统计关联）/ `Hypothesis`（唯一状态 `UNVERIFIED`）。候选的 `hypothesis` 槽默认留空，关联
**不是**根因。

## 四、输入 / 输出契约

输入（`contract.py`）：`SubmissionRecord(batch_id, problem_id, submission_id, student_id,
language, source_code, verdict, test_points, source, code_state, code_truncated)`；其中
`language` 可为 `None`（语言无法确定），`code_state` 为 `PRESENT`/`UNANSWERED`/`MISSING`，
`code_truncated` 标记代码是否被截断。`TestPointResult(testpoint_id, outcome)`；
`SourceRef(source_id, location)`。

**代码不完整时不产生「完整代码」特征**：`UNANSWERED`/`MISSING`/截断/语言未知的提交，其全部
特征标为 `unknown` 并从需要完整代码的关联中排除（`Analysis.source_statuses` 保留排除原因与
数量），绝不当作完整、可靠的代码事实。

输出（`results.py`）：`Analysis` 含 `submissions`（`SubmissionFeatures`）、`outcomes`、
`associations`、`test_points`、`candidates`、`source_statuses`、`limits`；查询方法
`matching_submissions`、`submissions_failing_testpoint`、`failed_verdict_submissions`、
`association_for`、`candidate_for`。

## 五、与 B-01 / C-01 的接口差异（最小要求）

`interop.py` 已桥接三处差异，均不 import 未合并 PR 的模块：

1. **B-01 中文状态词 → 类型化判题**：`b01_verdict("答案正确") -> Verdict.ACCEPTED` 等；
   测试点 `b01_test_point_outcome("答案正确") -> PASSED`。
2. **B-01 编译器标签 → 语言**：`b01_language("GXX") -> Language.CPP`；`GCC` 无法区分 C/C++，
   返回 `None`（请 B-01 若需支持 C，提供更明确的语言标签）。
3. **C-01 `FeatureCode` 白名单投影**：`c01_feature_code` 把 5 个特征映射到
   `boundary_check / loop / indexing / recursion / input_parsing`；`max_loop_nesting`、
   `functions`、`container_uses` 是本内核额外的富特征，不进入 C-01 白名单。
4. **C-01 `FeatureAssociation` 形态**：`c01_feature_associations(analysis, status=..., point=...)`
   产出 `(problem, point, status, code, checked, present)`；`present` 为
   `(参与者, 来源引用)`，由 C-01 Provider 包装成 `Witness`。

需要 B-01 / C-01 配合的最小差异：

- **B-01**：请随提交提供 `code_state`（`PRESENT`/`UNANSWERED`/`MISSING`）与 `code_truncated`
  两个字段（`answersheet.py` 已产出，需在逐题读取接口透出）。本内核据此把 `UNANSWERED`/
  `MISSING`/截断的代码特征标为 `unknown` 并排除出关联。`language` 是自由文本编译器标签，
  建议固化取值，至少区分 C++（`GXX`/`G++`）与 Python，避免 `GCC` 歧义（歧义时语言标为未知）。
- **C-01**：`FeatureAssociation.present` 的 `Witness` 引用需由 C-01 Provider 根据本内核的
  `(participant, source.location)` 建立；DS-03 不生成 Witness。特征值未知（代码缺失/截断/
  语言未知）的参与者不进入 `checked`/`present`。

## 六、验证

```bash
cd apps/api
uv run --locked pytest -q tests/code          # 72 passed
uv run --locked ruff check .                    # 通过
uv run --locked ruff format --check .           # 通过
```

测试覆盖：控制流/函数/递归识别、循环嵌套、容器/索引、I/O、注释干扰、缺源码/语法错、测试点
分布、极少样本、重复提交与身份不一致、B-01/C-01 映射。当前为合成小代码片段；真实资料联合验收
待 B-01 验收版落地后执行。

## 七、已知限制

- C++ 特征为词法级 `tentative`；不识别 mutual 递归（仅直接自调用）。
- 关联是共现非因果；候选假设槽留空，由后续 Agent 在第 3 层填写并保持 `UNVERIFIED`。
- 按单题范围分析，不跨题池化；不支持的语言返回 `UNKNOWN`。
