# V1 学情报告导出核心交接

对应 Issue #20。工作分支 feat/edu-report-export-core，基于 main
409d531（含已合并的 PR #19 / #22），独立 worktree。
**当前交付是离线报告生成核心，不是已完成的网页一键导出。**

## 入口与输出

代码：apps/api/app/reporting/edu/。复用已合并的 Statistics、GroupResult、
Rubric DTO 与现有 openpyxl 3.1.5；没有新增依赖、HTTP、迁移或前端代码。
没有修改 app/edu、routers、storage、analysis/edu/insights 或任务 #8 原型/文档。
PR #19 已在本轮期间合并，本轮不直接接入其新增公共 DTO，也不调用 Qwen。

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

- **ZAMYL，后续洞察卡适配（#19 已合并）**：新增明确适配器，仅从已审查卡/指标构造可信数值事实，
  检查同批同快照、规则和每指标实际来源；继续丢弃自由模型解释、内部 RecordRef 和扣留原文。
  当前洞察页明确“洞察卡未提供”，不伪造候选、重复评语或评分冲突。
- **彭仲楷**：正式授权下载接口、同快照完整统计与完整群体读取、证据 ID 归属检查、
  服务端 DetailAuthorization、撤销/限流/并发及存储策略；响应使用 no-store、
  正确 MIME、安全 Content-Disposition 文件名，不使用原始文件名或路径。
  当前没有新增 URL/正式下载 DTO，也没有修改数据库结构。
- **林明**：在正式接口完成后加下载入口，传递当前批次/快照/条件和用户意图，
  提示内部用途，处理加载、零命中、权限撤销和快照冲突；前端本轮未改。
- 浏览器一键导出、完整真实授权联调、原文审核/发布、加密/签名/生产性能仍未交付。
  不通过报告导出绕过 withheld，不开放 Qwen Production，不改变产品与五步流程。
