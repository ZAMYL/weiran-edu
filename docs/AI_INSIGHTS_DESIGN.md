# AI 教学解释引擎 v0.1（离线草案）

本模块位于 `apps/api/app/ai/edu/`，只实现合成数据、Fake LLM 的内部工作流。
没有业务 HTTP 路由、数据库、真实教师认证、模型网络适配器或付费调用。
统计与筛选复用 `app.analysis.edu.summarize/select_students`，不另写计算公式。
详细分析与主题规则仍分别以 [ANALYSIS_RULES.md](ANALYSIS_RULES.md)、
[COMMENT_ANALYSIS_RULES.md](COMMENT_ANALYSIS_RULES.md) 的草案状态为准。

开发基线：PR #3、#4 已合并，main 为 `e37add282bbe6b48de775854953613ad084592a8`。
已阅读 [PR #2](https://github.com/ZAMYL/weiran-edu/pull/2) 固定头
`b71d1d26bef7e89980d22672a592bb42ff014d7b` 的 API 草案与
`BACKEND_ANALYSIS_HANDOFF_V0.1.md`。该 PR 当时仍开放；本模块没有复制、修改
或冻结其 DTO、认证方案和数据库结构。

## 工作流与信任边界

合成教师问题 → LLMClient.plan 返回工具计划 JSON → 白名单顺序执行 →
本次事实/证据台账 → LLMClient.compose 返回结构化模板选择 →
程序校验引用、依据及范围 → 程序渲染报告。

只允许一次规划、一次组合，不进行无限工具循环或失败重试。每次
`run_insights` 都创建新的 ToolExecutor；不同请求之间不共享台账。
`LLMClient` 是可替换的异步 Protocol，当前只用测试中的 FakeLLM。
`offline_only=True` 和 SyntheticAccess 是受信调用方的声明，不是网络沙箱、
真实鉴权或自动数据脱敏检测。不能把真实材料改个标志就用于本流程。

Provider 是受信服务端代码；文档正文是非可信数据，不作为指令执行。
工具返回完整的 EvidenceDocument/RubricDocument 供受控的服务端调用方查看，
但模型投影仅包含汇总事实、白名单 ID 和证据种类/维度元数据：
**不包含学生 ID、个人评分列表、评语正文、量表正文、原始来源或定位符。**
本轮因此只验证证据可读取和绑定关系，不宣称已理解评语/量表正文。
批次和证据 ID 也必须由后端保证是不含身份信息的不透明标识。

## 四类受控工具

以下均为内部 Python/模型工具参数，不是仲楷的 HTTP 协议。

| 工具 | 参数 | 结果和约束 |
| --- | --- | --- |
| get_dimension_statistics | batch_id；可选 student_ids | 复用 summarize；五维有效/缺失/无效数、均值、中位数、极值、观测分值频数 |
| filter_student_group | 同上；conditions | 复用 select_students；AND，人数、M/N、M/E、可判定与不可判定人数，保留条件 |
| get_evidence_records | batch_id、evidence_ids | 仅可读取明确授权且绑定本群体记录的评分/评语引用；缺失、扣留或绑定不符报错 |
| get_rubric_criteria | batch_id | 仅从授权标准 Provider 逐项读取本批五维依据；缺失、维度不完整或绑定不符报错 |

统计默认使用整个授权群体；student_ids=[] 是空群体。
传入其他批次或群体外 ID 直接拒绝，不静默取交集。
条件维度用 ALGORITHMS / IMPLEMENTATION / MODELING / EFFICIENCY / STRATEGY；
运算符为 <、<=、==、>=、>。阈值为明确的小数字符串，例如 "2.50"，
以 Fraction 精确解释，避免浮点中转；最多三位整数、四位小数。
这些是工具输入边界，不是重新定义评分精度或正式 API 的编码、条件条数。

SyntheticAccess 包含显式学生白名单、EvidenceGrant 和 RubricGrant。
评分/评语 grant 必须精确关联授权 Dataset 的 RecordRef 及其 EvidenceRef；
标准 grant 关联批次、维度、BASIS 来源及版本。Provider 回读结果必须与 grant 完全一致。
引用获授权不等于原文件真实性已得到本模块证明，Provider 仍须重新校验资源归属。

默认限制：8 次工具请求、1000 条授权记录；每次工具输出 64000 字节、
累计 128000 字节；单个模型响应 32000 字节；单份正文 4000 字符。
正文 UTF-8 字节也计入工具预算，即使不发给模型。
规划和组合提示另受累计字节上限约束。超限明确失败，不悄悄截断证据。
模型每阶段默认 5 秒超时，可配置；没有自动重试。
异步模型实现必须支持取消，不能在事件循环中阻塞。
Provider 当前是同步、合成内存实现；未来数据库/远程适配须另设超时、快照一致性
及资源限制。本模块没有提供可中断任意同步代码的沙箱。

工具不接受 SQL、表达式、URL、任意文件路径或调用方教师 ID。
未知工具名和多余参数拒绝。若一个工具失败，整个工作流返回 error，
已有部分结果不被包装成成功报告；失败工具也不能留下可引用的新 ID。

## 事实对象和数值口径

Fact 保留 fact_id、batch_id、rule_id/rule_version、metric、维度、精确 Fraction、
denominator、Scope（含 ScorePolicy 和群体 RecordRef）、参与记录来源、
筛选条件、缺失/无效/不可判定状态与限制。
投影规则版本为 `edu-analysis-v0.1-draft/ai-projection-v0.1`，是本适配器的草案版本，
不宣称原引擎具有已冻结的业务版本。行为变化需随代码审查更新版本与样例。

- 统计 n 为该维有效记录数；均值/中位数/极值的 denominator=n。
- 有效、缺失、无效人数的 denominator=N，即所选授权群体记录数。
- frequency_at_分值 的 value 是该分值人数，denominator=n；不预设优劣区间。
- 筛选保留引擎的 M/N 与 M/E。未知记录单列 unevaluable_count；
  不推测未知是缺失还是无效，因此其 missing_count/invalid_count 为 None。
- Fraction 的 numerator/denominator 是精确数值编码；Fact.denominator 是统计样本/
  人数口径，两者不可混淆。人数不由比例反算，空分母比例为 None。
- 同批唯一学生、重复拒绝、缺失不补零、严格 < 与 <= 均沿用现有分析引擎。
- source_refs 用于本地追溯，Scope 保留整个分母范围；引擎不验证源文件内容。
  当前不提供相关性工具，不生成新的 Spearman 或因果结论。

手算合成例：算法 [2,2.5,3,4] 的均值=23/8、中位数=11/4、n=4。
(算法,效率,策略) 为 (2,2,4)、(2.5,1.5,5)、(3,3,3)、(4,2,4.5)：
策略>=4 AND 算法<3 AND 效率<=2 命中前两条，M/N=2/4=1/2，E=4。
同样数据重复运行产生相同事实 ID、报告和 Trace；ID 仅在当前运行内使用。

## 结构化解释与程序保证

模型 Proposal 包含 observed_facts、hypotheses、teaching_actions、
verification_steps、limitations、cited_fact_ids/cited_evidence_ids。
它是最终 InsightReport 的选择草案，不直接信任模型报告文本。

- observed_facts 只允许 fact_id，文本和数值全部从台账由程序生成。
- hypothesis 必须标记 status="hypothesis"，仅允许 task_alignment /
  practice_opportunity 两种保守模板，最终明确显示“待验证假设”。
- action 仅允许 guided_practice / review_rubric；
  verification 仅允许 collect_work_samples / repeat_comparable_assessment。
- 每个假设、行动、验证步骤必须绑定已展示事实，并至少具有非空、非零分母的
  均值/中位数/极值/筛选比例依据。仅有人数、空值不能支撑教学解释。
  真实零命中且分母非零仍是有效观察，不等于数据缺失。
- evidence_id 必须在本次实际成功读取的集合中；不能只凭授权白名单引用。
  个人证据的记录与所有依据事实的群体必须对应，限定维度也须对应；
  rubric 引用同样校验维度。关联有效不证明该文本支持某种语义解释。
- 汇总引用集合必须恰好等于实际使用集合；重复引用、未知引用拒绝。
- 禁止模型增加自由 text、value、日期、课程、数值或其他字段；非法 JSON、
  重复 JSON key、NaN、超长结果和不符合 Schema 的内容都不能返回有效报告。
  不是用“出现数字但碰巧等于某个均值”的正则来伪装事实验证。
- limitations 强制添加：假设未确认、没有因果/教学效果证据、需教师复核、
  仅合成离线、来源真实性未验证、未解释证据正文；根据实际情况补充缺失和
  未读取标准/个人证据。模型不能删去这些限制。

示例程序事实文本（来自上述合成样例）：

> 算法与数据结构：mean=23/8；计算分母/样本数=4；授权群体记录数=4；缺失=0，无效=0。

示例假设：“待验证假设：练习机会的差异可能影响当前表现，现有评分不能确认此解释。”
这是可供教师检验的候选，不是对学生、课程或评价者的诊断。

**validated 只表示结构、引用、范围和程序数值校验通过，不代表教育解释被证实。**
模型选择了哪个模板、选择哪些真实观察、建议是否适切仍可能有选择偏差或误导；
模板本身也需教师审查。本轮主动不接受自由自然语言事实或任意因果叙事。
如后续开放自由文本，必须重新设计数值、引用和内容校验，明确标记无法自动验证的部分，
不能把此次保证直接套到自由生成文本上。

无有效均值或非空分母筛选比例时返回 insufficient_data：程序列出已知事实/限制，
hypotheses/actions/verification 为空，且不调用 compose。模型/工具失败则返回 error，
report=None 和固定原因码；不以降级掩盖权限冲突、错误或模型失败。

## Trace 和隐私

TraceStep 只记录顺序、白名单工具名（未知名统一 unknown）、状态、固定错误码、
事实 ID、证据数及输出字节数；拒绝轨迹也有长度上限。
不记录问题、模型原始响应、参数、学生/来源/证据标识、正文或异常细节。
模块不主动写日志、文件或网络。文档对象的 repr 隐藏正文与绑定引用，
Outcome 的 repr 隐藏本地事实台账。
这不保证调用方主动 asdict、打印内存对象或上传 traceback/local variables 的安全；
后端禁止将这些对象直接序列化到日志或公开 DTO。

评语中的“忽略规则、访问其他批次、执行 SQL”等内容不进入模型提示，更不能改写
工具注册、授权快照或预算。即使 Fake LLM 主动请求越权工具，执行器仍拒绝。
仓库没有真实个人数据、原始 Excel、数据库、凭据或外部模型配置。

## 仲楷后端接入与后续确认

当前入口为 `await run_insights(question, SyntheticInput(dataset, access), client,
evidence_provider, rubric_provider, limits=...)`。本轮可直接通过完整离线测试运行：

```text
cd apps/api
uv run --locked pytest tests/ai_edu/test_workflow.py::test_complete_flow_repeatable_result_and_trace -q
uv run --locked pytest tests/ai_edu -q -ra
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -q -ra
```

合成 Dataset、内存 Provider、FakeLLM 及组装示例见 tests/ai_edu/conftest.py；
工作流、权限、模型失败和可复现边界见同目录测试。测试禁用外部连接；
测试数量和 CI 以当前 PR 实际运行结果为准，不代表真实模型或语料质量。

未来接入顺序：

1. 后端先验证真实教师身份、批次、群体与个人证据权限，再构造只包含获授权
   数据的 Dataset；使用 PR #2 明确映射 Dimension / Operator 和
   Fraction(score_units,100)，显式 ScorePolicy，不直接序列化草案 DTO。
2. 实现 EvidenceProvider.get / RubricProvider.get，在每次回查重新核对批次、
   学生、record/source、kind、dimension、locator、标准版本和脱敏/扣留状态；
   内容缺失返回 None，内部故障由工作流显式失败。所有读取使用一致的授权快照，
   需要支持权限撤销，不把客户端输入直接做 grant。
3. 与 AI 负责人共同增加经过评审的生产授权上下文；当前 SyntheticAccess
   **不能替代真实认证**，不能仅修改 synthetic_only/offline_only 就开放生产。
4. 先确认教师主动触发、最小化数据、外部模型授权和隐私审查、小群体披露限制，
   再单独实现真实 LLM Client。默认继续不发送个人评分、评语、定位和身份。
   本轮没实现密钥读取或外部传输，不能声称已经完成这类集成安全验收。
5. 协调正式响应 DTO、展示精度、前端解释标识、Provider 超时和重检策略；
   经过教师审核扩充解释/行动/验证模板，再评估是否允许更自由的文本。
   统计意义、跨批次可比性与教学效果另需研究设计，不能从本轮测试推出。

本轮没有修改公共契约、数据库、前端、认证、CI 或依赖，也没有自动合并/部署。
