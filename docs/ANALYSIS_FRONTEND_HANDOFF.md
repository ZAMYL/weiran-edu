# 分析结果与前端交接 v0.1（草案）

核验 main：0eaac70fa800e4e3e9b6354b45e3c057c56f2e2b。已阅读 model.ts、
statistics.ts、InsightsPage / ExplorerPage / DataPage 及导入、演示和证据抽屉代码。
PR #2 仍开放，参考其固定头 b71d1d26bef7e89980d22672a592bb42ff014d7b 的
API_CONTRACT_V0.1.md / BACKEND_ANALYSIS_HANDOFF_V0.1.md。以下是适配建议，
不修改林明页面、仲楷正式 DTO 或数据库，不表示双方协议已经冻结。

## 三方维度与条件

| 前端当前 DimensionId | 引擎 Dimension | 后端草案 dimension_code |
| --- | --- | --- |
| algorithm | ALGORITHMS（算法与数据结构） | algorithm_data_structure |
| coding | IMPLEMENTATION（代码实现能力） | code_implementation |
| modeling | MODELING（问题建模能力） | problem_modeling |
| efficiency | EFFICIENCY（效率意识） | efficiency_awareness |
| strategy | STRATEGY（解题策略） | solution_strategy |

必须显式映射，不能依赖枚举位置、中文 value 或模糊字符串。
未知维度拒绝，不默认退回算法维度。

| 前端当前 | 引擎 Operator | 后端草案 |
| --- | --- | --- |
| < | LT / < | lt |
| <= | LE / <= | lte |
| = | EQ / == | eq |
| >= | GE / >= | gte |
| > | GT / > | gt |

前端显示 ≥/≤ 是排版，不是新运算符；不得把 <3 改成 <=3。
前端无条件表示全部学生，而 select_students 空条件会拒绝：无条件读取应走群体统计，
不能向引擎偷偷塞默认条件。前端和后端草案只开放最多两条件，后端要求不同维度；
引擎内部更通用。不能丢弃额外条件来“适配成功”，超出契约时需共同确认。

## 分数、精度与分母

- 当前 DEMO_STANDARD.step=1，DataPage 固定使用它。2.75 在正式后端草案合法，
  在当前演示导入中会被拒绝；不由分析模块悄悄放宽前端标准。
- 后端草案 [1,5]、百分之一分：score_units=275 → Fraction(275,100)=11/4。
  应显式 ScorePolicy(step=Fraction(1,100))，不能套用半分示例。
- 引擎保留有效/missing/invalid 状态；后端正式导入要求完整五维、错误阻止整批提交，
  两者职责不同。内部可分析不代表非法导入可放行。
- 前端 Rating.value 是非空 number，当前演示不表达部分有效数据；
  正式适配须补状态、有效样本数与不可判定人数。缺失不补零。
- N 是本次有记录的唯一学生数，不是应测花名册人数。有效 n 按维度分别计算，
  均值/中位数/分布以该维有效值为依据；缺失、无效独立计数。
- 筛选 M/N 是命中占所选群体全部记录；M/E 仅针对查询维度均有效者。
  N=0 或 E=0 的比例为 None，不等于 0%。未知者不能默认归入已判定不命中。
- 当前 percentage 空分母返回 0%，mean 空集返回0且均值提前保留一位；
  正式展示应区分无数据。distribution/heatmap 按离散网格相等计数；
  不能用整数格子漏掉2.75，正式分布应消费实际分值频数或另行确认分箱。
- Fraction 精确值与展示值分开。本轮内部投影保留 numerator/denominator 和
  display 字符串，使用固定 half-up 舍入，不修改条件、分数、人数或来源。
  不同 digits 只改变显示，不得用显示分数再次筛选或按比例反算人数。
  这些内部字段不是最终 JSON 数字契约。

## 实际产品差异与待协调项

1. InsightsPage 的发现使用固定 DEFAULT_CONDITIONS，主题用 includes 检索，
   不是已接入后端的候选或正式评语规则；VerifiedTag 不能解释为教学原因已证实。
2. ExplorerPage 的热力图是全批，右侧是筛选群体。候选需要同时带分析范围与条件，
   不把全批图的分母误标成筛选人数。
3. DataPage 现在是浏览器长表、内存会话；后端草案是固定模板、服务端预览/确认。
   前端空评语作为阻止确认的问题，后端草案空评语为可确认警告，此差异需共同确认。
4. 浏览器演示 S001 等 ID 只在会话/文件内生成，sourceId 可能保留输入标识。
   它们不等于后端稳定匿名 ID，更不等于已经完成隐私审查。
5. 前端 Source 直接包含文件名/工作表/行列，后端草案不返回原始文件名和磁盘路径。
   现有抽屉能展示本地原文，不等于生产端有授权回查。
6. 现有引擎有统计、筛选、个体画像、Spearman，加评语分类/复核；
   后端 statistics 成功 DTO 及所谓六类分析的完整对外形状仍待联调。

## 标识与证据

- student_id：后端授予的匿名学生标识，需要批次/来源范围与真实权限。
- record_id：一批一人的五维测评记录，不是某条评分。
- evidence_id：服务端持久化的单条评分/评语证据；criterion_id 属于本批标准。
- RecordRef / EvidenceRef：分析内部定位结构，不是公开 DTO，也不是访问凭证。
  不能将 source_id/location 拼成 URL、磁盘路径或凭空当作 evidence_id。
- 候选保留实际参与记录和绑定证据；候选 ID 只是本次快照的逻辑引用，
  后端必须在当前权限下重新解析/重算与验证实际来源，不能按 ID 绕过授权。
- 不返回真实姓名、学号、完整评语、来源定位或学生名单到候选卡。
  详情由未来正式授权查询提供。

## 林明展示候选需要的字段

建议展示 candidate_id、candidate_type、title、summary、batch_id、status、
metrics（精确值/展示值/单位/各自分母）、conditions、candidate.denominator、
规则版本、limitations、suggested_next_action 和受控查询种类。
状态区分观察事实、待复核、数据不足及禁止公开。
布局、配色、动画和最终字段命名由前端与后端共同决定，本轮不修改 UI。

内部展示投影不含 RecordRef 或学生名单。公开展示默认空白名单，
还要通过小样本、敏感范围与补数泄露检查；教师预览入口只是受信调用约定，
并未实现真实教师鉴权。不能把“可预览”当成“可公开”。

## 仲楷后续需提供的职责（不是本轮新增接口）

| 职责 | PR #2 已有草案可对接点 / 待确认 |
| --- | --- |
| 获授权批次与不可变数据版本、全量群体输入 | batches；不得只取一页分析 |
| 五维统计与候选列表投影 | statistics，成功 DTO/候选承载形状仍待确认 |
| 条件群体与匿名名单 | groups/query，回查同条件同快照，M/N 不按页计算 |
| 学生评分、已检查评语、证据来源 | students/{student_id}/evidence / evidence/{evidence_id}，重检来源归属 |
| 本批标准及版本 | rubric |
| 候选查询引用/复核集合回查、公开审查 | 尚无正式契约，需定义授权、快照失效、分页及披露策略 |

本轮完成候选计算与展示适配草案；真实导入、数据库/权限、证据 ID 解析、
真实语料规则质量、公开发布审查与前端联调仍未实现。
未来 Qwen 可选择这些程序候选，再经现有事实/引用机制解释；本轮不改模型工具，
不打开 Production，也不调用真实 Qwen。

内部调用、口径、公开限制和三张合成样例见 [INSIGHT_CANDIDATES.md](INSIGHT_CANDIDATES.md)。
