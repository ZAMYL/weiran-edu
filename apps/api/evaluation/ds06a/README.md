# DS-06A｜编程错误模式分析的可复现评测基准

本目录是独立研究评测代码，不是生产 API。目标是用人工可核验的合成提交比较：

- **A：基础统计**：判题分布、失败数量、测试点分布及基础来源引用。
- **B：A + DS-03**：真实代码结构特征、2×2 统计关联和证据位置。
- **C：B + C-01**：真实多步调查会话、证据选择、Witness 解析与来源核验。

本基准不预设“AI 优于传统统计”，也不把统计关联当作根因。

## 1. 固定代码基线与实际 PR 核对

本工作区基于以下不可变 SHA 建立独立合并快照；没有修改三个 PR 的核心实现：

| 来源 | SHA | 实际核对的接口与行为 |
|---|---|---|
| `main` | `d2ccd7f79d355a73d28861224602fff2934c31fe` | 评测分支基线。 |
| PR #105 | `a0d14221f7e12f4d376047b84fbc135a5c84ebc7` | `_convert_rich` / `_convert_cell` / `_normalize_record` 输出 `ParsedRow`；`_code_blocks` 抽取代码但显式说明不能证明源码类型。已检查 missing_id、重复题号、unsupported、repair、原题题号优先等测试。 |
| PR #108 | `b929787284b32162f522c1fbec17ef1891e3ab4f` | `analyze(batch_id, problem_id, tuple[SubmissionRecord]) -> Analysis`；Python AST 与 C++ 词法/括号特征；`c01_feature_associations` 输出 checked/present。缺失、截断、未知语言和语法不完整为 unknown 并排除关联；候选只声明非因果。 |
| PR #107 | `ce8fb6acecddeeb80d759b912dcb367da5f9f9cc` | `investigate_programming(...) -> (Run, executor)`；`EvidenceSnapshot` / `SubmissionEvidence` / `Witness` / `FeatureAssociation`；模型只接收聚合事实，源码值不发送；`executor.resolve` 返回 Witness，`verify_all` 核验来源。 |

三个 PR 描述仅作为导航，上述结论来自实际函数和测试。当前合并快照只用于可执行评测，**不代表这些 PR 已合并 main**。

### 尚未完成的正式集成

- PR #105 的 `parsed_row.code*` 不是当前答题卡 StudentRow 的持久化字段，B-03 仍需映射。
- PR #108 的 C-01 interop 已由本评测适配器真实调用，但尚未接入生产 B-01/页面。
- PR #107 的正式证据 Provider 仍需生产批快照；本评测使用内存只读 Provider。
- 本评测不触达数据库、前端或生产作业。

## 2. 数据与答案隔离

- `data/cases/*.json`：静态合成输入，只含匿名编号、合成代码、判题与测试点、来源定位。
- `data/oracles/*.json`：独立编写的人工标准，含人工理由、统计、现象、特征、关联、弃答和拒绝预期。
- `data/generate_cases.py`：只重建输入，**不生成或修改人工标准**。
- 加载器拒绝输入中的 `expected*`、`oracle*`、`human_*`、`root_cause*` 字段。
- 每个流水线先返回结果，评分器随后才读取 oracle；oracle 不会进入 DS-03、C-01、Provider 或离线选择器。

重新生成输入：

```bash
cd apps/api
uv run --locked python evaluation/ds06a/data/generate_cases.py
```

### 十一组案例

| 案例 | 核验重点 |
|---|---|
| `same_verdict_different_features` | 同题相同判题表现，不同循环/下标结构。 |
| `same_structure_different_points` | 代码结构相似，失败测试点不同，不得合并现象。 |
| `manual_boundary_index_input` | 人工可检查的边界/下标风险与输入解析风险；无运行轨迹仍不定根因。 |
| `association_not_causation` | 完全共现可形成关联，但必须保留 `cause_unconfirmed`。 |
| `incomplete_code` | 缺源码、截断、语言未知、语法不完整与仅一份可检查失败代码。 |
| `insufficient_test_evidence` | 编译错误且没有源码/测试点，不得虚构测试证据。 |
| `small_sample_privacy` | A/B 可统计，C 在 `minimum_cell=5` 下应无卡片。 |
| `varied_distribution` | 两题、10/15 条记录、不同失败比例与未作答，禁止跨题池化。 |
| `duplicate_submission` | 重复提交键必须拒绝，不得静默去重或双计。 |
| `missing_witness` | 判题行引用的 Witness 缺失，C 当前以 `source_scope_mismatch` 拒绝。 |
| `witness_mismatch` | Witness 值与判题事实冲突，C 必须 `source_scope_mismatch`。 |

所有人员标识均为 `anon-*`，没有真实学生信息。代码只被静态解析，**从不执行**。

## 3. 三类真实执行路径

### A｜独立统计

`baseline.py` 直接遍历合成记录，计算 verdict/test-point 计数和失败现象，并保留基础证据引用。它是本评测的对照实现，不冒充 B-01 数据库接口。

### B｜真实 DS-03

`pipelines.py` 将输入转换为 `SubmissionRecord`，逐题调用真实：

```python
app.analysis.code.analyze(...)
app.analysis.code.c01_feature_associations(...)
```

特征、候选、unknown 状态和证据均来自返回对象，没有硬编码模块输出。

### C｜真实 C-01 离线会话

`c01.py` 使用真实 B 结果构造授权 Witness/FeatureAssociation，逐题调用：

```python
prototypes.research_agent_v2.programming.investigate_programming(...)
executor.resolve(...)
```

传输层为 `httpx.MockTransport`。离线选择器只读取实际 `overview` / `inspect` 返回，依次调查方向并完成卡片；它看不到人工标准。预算、隐私门限、来源重放和 Witness 校验均走真实实现。

**未调用真实 Qwen**。Mock 响应中的形式化 usage 不是实测数据，报告中的真实 Token、费用和模型推理质量均为 `null` / 未验证。

## 4. 指标口径

| 指标 | 定义 |
|---|---|
| 基础统计正确率 | 每案 verdict 与测试点统计和人工标准完全一致，再宏平均。 |
| 错误现象发现正确率 | `(problem, point, status)` 集合的 Jaccard；额外现象会扣分。按案例宏平均。 |
| 有效证据定位成功率 | 对每个现象的 affected/covered 人群及候选证据逐项核验，微平均；同时校验定位。 |
| 特征提取正确率 | 对人工指定的循环、下标、I/O 等值逐项比较；A 无此能力，记 N/A。 |
| 关联/卡片正确率 | B/C 的 checked/present 群体；C 另核验卡片特征计数。无适用项记 N/A。 |
| 无依据根因断言次数 | 非空 DS-03 hypothesis、非 unconfirmed 的根因字段计数；不把 `next_check` 当根因。 |
| 不完整证据正确弃答率 | unknown 特征、无候选、无卡片、必需 limitation、正确拒绝等人工检查项的微平均。 |
| 来源与事实一致性 | 评分器独立重算 source/locator/hash/kind/value；C 另核对 overview verdict 和 `verify_all`。 |
| 分布稳定性 | 报告每种分布的现象准确率、最低值和最大值减最小值；另做重复运行语义一致性。 |
| 实际运行耗时 | `perf_counter` 实测。C 包含 DS-03 与离线会话；不同机器不可直接横比。 |

隐私抑制的小样本 C 以“无现象、无卡片”为正确标准，同时计入弃答指标；这不表示它发现了小样本现象。

## 5. 运行命令

在仓库根目录：

```bash
cd apps/api
uv run --locked python -m evaluation.ds06a.run_eval --repeat 2
```

单案调试：

```bash
uv run --locked python -m evaluation.ds06a.run_eval \
  --case association_not_causation --repeat 2 --output-dir /tmp/ds06a-check
```

必要回归与静态检查：

```bash
uv run --locked pytest -q tests/code/test_ds06a_evaluation.py
uv run --locked ruff check evaluation/ds06a tests/code/test_ds06a_evaluation.py
```

相关 PR 定向回归：

```bash
uv run --locked pytest -q \
  tests/code tests/research_v2/test_programming.py \
  tests/importing/test_answersheet.py \
  tests/importing/test_answersheet_repairs.py \
  tests/importing/test_answersheet_shapes.py \
  tests/importing/test_answersheet_structures.py
```

Windows PowerShell 使用相同 `uv run --locked ...` 命令；单案输出目录可改为 `$env:TEMP\ds06a-check`。

## 6. 交付文件

- `run_eval.py`：CLI、重复运行与结果写出。
- `baseline.py`、`pipelines.py`、`c01.py`：A/B/C 真实执行适配。
- `metrics.py`：独立评分与来源重算。
- `data/cases/`、`data/oracles/`：合成数据与人工标准。
- `results/metrics.json`：完整实际输出、逐案指标、重复耗时及未验证能力声明。
- `results/summary.csv`、`results/case_results.csv`：汇总与逐案结果表。
- `results/report.md`：自动生成实验报告。
- `tests/code/test_ds06a_evaluation.py`：接口调用、答案隔离、负例扣分、重复一致性和拒绝回归。

## 7. 局限性：已测到与尚未验证

### 已测到

- 三类分析可以在固定合成快照上实际运行并比较。
- DS-03 的真实结构特征、unknown 排除、统计关联和证据位置符合本集人工标准。
- C-01 的离线多步调查、隐私抑制、Witness 重放和来源冲突拒绝符合本集人工标准。
- 重复运行的语义结果一致；反例会被评分器扣分。

### 尚未验证

- 真实学生数据上的泛化、统计显著性、班级/题目规模扩展和真实故障分布。
- 真实 Qwen 的方向选择、推理质量、Token、延迟和费用。
- 正式 EvidenceProvider、B-03 持久化、B-01 页面、B-01 导出和端到端验收。
- 根因诊断准确率；本集没有向模块提供足以普遍确认根因的运行轨迹。
- C++ 的真实评测覆盖（实现已核对，但本批合成案例以 Python 和未知语言为主）。
- AI 相对传统统计的优势；本结果不支持此类结论。

`results` 中较高分数只表示这些确定性模块通过当前小型合成合同，不是论文效果声明。下一阶段应在授权真实数据、预先登记的指标和真实模型预算下重新实验。
