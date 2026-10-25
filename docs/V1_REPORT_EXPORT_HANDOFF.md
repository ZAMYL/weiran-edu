# V1 学情报告导出核心交接

对应 Issue #20。工作分支 feat/edu-report-export-core，基于 main
409d531（含已合并的 PR #19 / #22），独立 worktree。
**核心与后端正式下载已接线；网页下载入口本轮未开发。**
本文件保留 #28/#33 核心阶段的契约记录；最新正式四格式/预检/鉴权接线以
[FORMAL_REPORT_DOWNLOAD_API.md](FORMAL_REPORT_DOWNLOAD_API.md) 为准，不阻塞 Issue #26 P0。

## 入口与输出

代码：apps/api/app/reporting/edu/。复用已合并的 Statistics、GroupResult、
Rubric DTO 与现有 openpyxl 3.1.5；没有新增依赖、HTTP、迁移或前端代码。
没有修改 app/edu、routers、storage、analysis/edu/insights 或任务 #8 原型/文档。
基础核心在 PR #28 合并。本次已核验洞察适配基于 main d96d428（含 #23/#25/#28/#30）；
该核心阶段开始时 #32 未合并；正式下载阶段现已基于合并后的 main。新增只读依赖 AnalysisResult / InsightCard，不调用 Qwen。

```python
from app.reporting.edu import from_statistics, generate_report

report = from_statistics(
    statistics,                 # 完整获授权范围的 Statistics，不能传某一页学生作为全体
    generated_at=aware_time,     # 显式、带时区的生成时间；不是测评日期
    rubric=rubric,               # 可选；不提供时标明评分标准/版本未提供
    group_pages=(tuple(all_pages_of_one_group),),
)
files = generate_report(report)  # 默认仅汇总，不输出任何匿名个人明细
# files.xlsx 是 bytes，files.markdown 是 str；核心不读写任意路径。
```

生成一对相同输入快照的文件；任一校验、布局或大小限制失败均抛 ReportError，
不返回半份“成功报告”。异常仅含固定原因码，不回显原始值。
XLSX 为可编辑、无公式的静态快照，四页分别为报告说明、维度统计、群体结果、洞察事实。
Markdown 为精简文字版，含全批统计/分布、群体条件与完整获授权明细（如启用）；
可选群体统计在文字版概述有效数、均值和中位数，完整项保留在 XLSX。
编辑导出文件不会重算平台数据，编辑后的文件不自动继承原始一致性检查结论。

## 准确性与动态项目

- 所有数值来自输入统计或合成演示中真实运行的 summarize/select_students，
  不写死人数、比例、评分或“异常”。生成器只做投影和一致性核验。
- 内部 ReportInput/DimensionReport 允许实际项目数 0—32，按行迭代，
  非 V1 项目使用安全的通用代码标签；这不代表当前五维上传格式已经改变。
  正式 DTO 适配仍只接受已合并的 V1 维度编码，未知映射拒绝。
- 五维展示名来自受控代码字典，不复制 Rubric 的自由 display_name、
  original_name、依据正文或其他原始字段。动态自定义项目正式名称需后续审核适配，
  不在此提前冻结任务 #8 数据协议。
- 有效+缺失+无效=实际范围 N；均值/中位数/极值与频数分布须一致。
  缺失不当零分，无有效数据时统计值为 None，显示“暂无有效数据”。
- Fraction 为精确来源；数值/展示值/精确值/样本分母分列。
  展示 HALF_UP 两位，评分单元格按量表步长显示；有理数分母不是学生人数。
  API StatValue.value 作为有限浮点提示核对，display 自由文本丢弃并重算，防止私密文本混入。
- 评分范围、步长、rubric_version 只在输入提供时记录；不同量表不自动比较，
  不因报告具备通用渲染就宣称新增上传格式或新的评分规则。
- 群体同时保留 M/N、M/E 和不可判定人数；零命中写“没有匹配记录”，
  未选择群体与零命中区分。群体可能重叠，不把人数直接相加。
- 每项统计、群体、成员的 batch_id/snapshot_id 必须相同，群体条件及量表映射一致。
  这些检查核验输入的内部一致性，不能证明源文件真实性或证据在数据库中的归属。

## 完整群体与权限

group_pages 是“每个群体全部页”的元组；适配器验证每页来源、条件、规则、
人数、统计、offset/returned/has_more，拒绝缺页、重叠、重复 ID 或计数漂移。
不能把 limit=20 的第一页冒充全体。即使默认只导出汇总，传入 DTO 分页也必须完整。
独立的已校验 ReportInput 可明确省略 members，仅用于汇总；请求明细时必须拒绝缺少成员的输入。

匿名明细另需显式 include_details=True 和上层提供的 DetailAuthorization：
- 同批次/快照，purpose 只能 teacher_internal；
- 每个群体必须在授权集合中，授权匿名 ID 集合与完整结果完全相等；
- 成员评分应满足条件、量表和已有分布，重复证据引用拒绝；
- 默认不导出成员 ID 或 evidence_id；显式授权后也不输出显示姓名/学号、评语、
  身份映射、定位符、内部路径、密钥或未审核依据原文。

**DetailAuthorization 是受信后端的调用声明，不是登录凭证或真实鉴权。**
不能把前端 checkbox、用户提交的成员集合或 synthetic 标志直接当授权。
上层须先完成教师有效期/撤销、来源成员、批次和选中群体访问、当前快照及证据归属检查，
并在最终下载前再确认权限；核心不认识真实身份、不连接数据库。
受控标识仅做格式/明显凭据前缀拒绝，不是通用 PII 识别或匿名化算法。
文件始终标“教师内部使用/非公开”，没有公开个人报告模式或 DRM。

## XLSX 与内容安全

- 所有字符串显式写为文本 cell.data_type=s，无公式对象、外部超链接或外部工作簿引用。
  =、+、-、@、空白/制表符/换行开头均不执行；CRLF/CR 统一为 LF。
- Markdown 转义 HTML、表格和 Markdown 元字符；不接受任意教学解释/原始评语文本。
- 整数、评分、比例和时间为原生类型；精确有理数单列文本；不把数字全转成格式化字符串。
- 上限：32统计项目、20群体、每组5000匿名成员；每表25000行、总250000单元格、
  每种文件8 MiB；可显式调低预算。超限报错，不截断名单或频数。
  适配前限制 DTO 项目、分页与成员数，单元格文本最多1024字符。
- 时间与来源由调用方显式注入，排序固定；相同输入/同一依赖环境的内容与文件可重复。
  ZIP 条目时间固定，核心属性保留传入的生成时点，不隐式取当前时间。
- 源数据及中间对象不写日志。测试解压检查 XML/关系文件，并重新打开读取；
  合成样例四页经过渲染检查。尚未做 Microsoft Excel/LibreOffice 原生交互验收。

## 合成演示与测试

在 apps/api：

```powershell
uv run --locked python -m app.reporting.edu --output-dir D:/Temp/edu-report-example-new --students 17
# 如需演示匿名清单，再加 --include-synthetic-details。
# 该开关仅接受内置、完全相同的合成数据，不是真实教师授权。

uv run --locked pytest tests/reporting -q -ra --tb=short
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -q -ra --tb=short
```

output-dir 必须为不存在的绝对目录，不覆盖现有文件；演示不读取教师文件或配置数据库。
17人样例实际计算命中9/17=52.94%，不是报告默认人数或默认筛选。
生成时间为固定合成演示时点；生产调用方应注入真实生成时间。

测试覆盖 0/1/17/53/105/320/2000 人、0/1/3/7 个项目、2.75、半步中位数、
空统计、缺失/无效、条件边界、零命中、完整/不完整分页、快照/批次混用、
授权与隐私字段过滤、恶意公式、资源边界、确定性及可编辑往返读取。
真实测试数量和 CI 以本 PR 当前提交的实际结果为准。
文档中的 [Markdown 示例](examples/synthetic-analysis-report.md) 完全合成；
XLSX 由上述命令生成，不将二进制样例或原始教师文件加入仓库。

## 后续接入职责

- **ZAMYL**：with_reviewed_findings 已接入保存结果的 confirmed 事实，见下节；
  未传入时仍显示“洞察卡未提供”，传入但零 confirmed 显示“暂无教师已确认发现”。
  未接入评语复核或自由模型解释，不输出内部 RecordRef 和扣留原文。
- **彭仲楷**：正式授权下载接口、同快照完整统计与完整群体读取、证据 ID 归属检查、
  服务端 DetailAuthorization、撤销/限流/并发及存储策略；响应使用 no-store、
  正确 MIME、安全 Content-Disposition 文件名，不使用原始文件名或路径。
  当前没有新增 URL/正式下载 DTO，也没有修改数据库结构。
- **林明**：在正式接口完成后加下载入口，传递当前批次/快照/条件和用户意图，
  提示内部用途，处理加载、零命中、权限撤销和快照冲突；前端本轮未改。
- 浏览器一键导出、完整真实授权联调、原文审核/发布、加密/签名/生产性能仍未交付。
  不通过报告导出绕过 withheld，不开放 Qwen Production，不改变产品与五步流程。


## 已保存、教师已确认洞察的受控适配

这是报告内容的 P1 增量，不是网页一键下载，也不是林明 P0 浏览器联调的前置条件。
不新增 HTTP、认证、数据库、迁移、正式 DTO 或下载接线。

```python
from app.reporting.edu import with_reviewed_findings, generate_report

# 服务端先通过 ResultService.get 校验当前教师凭证/撤销、批次访问权、
# payload 校验和及所选快照，读取完整 AnalysisResult；此处不接受请求体 JSON。
# report 由同一授权范围、同快照的完整 Statistics/GroupResult 创建。
report = with_reviewed_findings(
    report,
    saved_result,                       # 真正由服务端加载的 AnalysisResult
    expected_result_id=selected_result_id,
    expected_version=selected_version,  # 必须与当前教师核验版本一致，bool 不可代替
)
files = generate_report(report)         # 默认仍不输出匿名个人明细
```

内部新增契约（不属于公共 DTO）：

| 对象 | 白名单内容 |
| --- | --- |
| ReportInput.reviewed | 可选 ReviewedFindings；旧调用无需传入 |
| ReviewedFindings | source、result_id、result_version、规范排序的 conditions、findings、projection_version |
| ReviewedFinding | candidate_id、candidate_type、dimension_code、denominator、rule_id/version、数据状态、metrics |
| FindingMetric | key、Fraction value、unit、指标 denominator、source_record_count、可选 at_score |

projection_version 固定 edu.report.reviewed.v1；当前仅接受现有
edu-insight-candidates-0.1.0-draft 的评分分布/组合群体规则，未知版本拒绝，不降级猜测。
保存结果 version 是教师判断版本，不是评分 snapshot、候选规则或报告适配版本。

检查和口径：

- 结果 ID/预期版本、audience、batch/snapshot 与报告一致；全批统计、可选群体统计及规则一致。
- 当前一次报告适配一个保存结果；报告无群体时查询也无条件；有群体时只能一个且条件相同。
  多群体报告仍可用旧入口生成，本适配不混用多个结果。AND 次序规范化，< 与 <= 严格区分。
- AnalysisSnapshot.members 必须完整、无重复，并与群体评分/证据或全批频数对应；
  有条件时须提供群体统计。当前正式保存 DTO 为完整有效评分；不猜补缺少成员评分或证据。
- 每条 finding 都须与 snapshot.candidates 中的完整候选相等，决策 candidate_id 逐一对应，
  不允许未知、重复或缺少的关联；pending/dismissed 不导出为 confirmed。
- 即使未确认卡片也校验其保存元数据/指标，保存载荷损坏不悄悄忽略。
- 分布卡始终使用完整批次统计，即使该保存查询同时选了群体；组合卡使用匹配群体。
  均值/中位数、频数、人数、比例、精确值、指标分母和来源记录数均与已有统计核对。
- source_record_count 是该指标的实际参与记录数：例如群体命中9/17时，
  matched_ratio 来源9条，eligible_count 来源可为17条；不能把17误写为命中人数。
  维度均值来源有效记录，单分值频数仅来源该分值记录；有理数分母仍不代表人数。
- XLSX/Markdown 保存结果与候选 ID、核验版本、规则与适配版本；各指标保留精确值及口径。
  标题和解释标签由程序生成，不读取结果 title/note、教师标识/逐卡备注、候选 title/summary/
  limitations/suggested_next_action、公示限制自由文本、display_label、原评语或依据正文。
  数值 display 继续丢弃重算；未开放任何自由文本通道，即使原字段已经过教师审核也不直接接入。
- 系统候选、教师 confirmed 与客观统计分别标记。教师确认不证明因果、能力等级或教学效果；
  未运行评语分析，withheld 不当作空评语或零异常。零 confirmed 明确提示，不制造发现。

信任边界与待彭仲楷审核项：

1. **Python 类型、expected_version 和内部白名单不是真实认证或持久化证明。** 任意进程都能
   构造 DTO，本核心无法单独验证签名、数据库来源或当前最新版本；不能把客户端 JSON、布尔值、
   自建 AnalysisResult 或前端 confirmed 声明直接接到本函数。
2. 正式后端应使用已鉴权 ResultService 返回值，并在同一一致读取范围内取得统计/群体，检查
   当前核验版本、payload 完整性、证据归属、教师凭证撤销和下载前权限；本轮没有改动这些服务。
3. 本模块的来源检查是 DTO/指标/匿名评分引用的一致性核对，不能替代数据库级证据归属核验。
   保存结果中的个人引用不自动进入文件；匿名明细仍需单独 DetailAuthorization，不能因 confirmed 绕过。
4. 请 Peng-Kai789 重点审核当前版本与快照的装配方式、完整群体来源、下载前撤销检查以及未来接线位置。
   原有正式下载现已增加 XLSX/MD 接线，见最新契约；前端按钮、公开传播授权均未交付。

专项入口：uv run --locked pytest tests/reporting -q -ra。测试仅合成 DTO/确定性计算，
含 confirmed/pending/dismissed、零条确认、批次/快照/版本/条件/候选漂移、数值和参与记录数冲突、
自由文本过滤、公式与外链禁止、稳定输出；原有统计/完整分页/精确值测试继续运行。


[已确认事实的 Markdown 合成样例](examples/reviewed-findings.synthetic.md)：
17条测评、所选群体命中9/17，教师核验版本2；仅算法分布和组合群体两张卡 confirmed，
其余 pending 不作为确认导出。算法均值49/17≈2.88、中位数11/4=2.75；
群体 M=9、E=N=17；eligible_count 来源17条，matched_ratio 来源9条。
XLSX 默认不含匿名明细，已做重新打开和第四页渲染检查；未做原生 Excel 交互验收。
