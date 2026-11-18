# 固定模板 Excel 解析服务

> ⚠️ 本文部分内容已被 V1.4 取代，以 docs/V1.4_BASELINE.md 为准。

后续数据库、预览暂存、批次确认和教师权限已在独立实现中接入，运行方式见 [数据库与权限说明](DATABASE_BATCH_AUTH.md)。本页下文描述PR #12解析阶段的独立服务边界。

关联 Issue #10、协议草案 PR #2、实现 PR #12。此阶段实现文件解析、质量校验、来源保留、学生及逐行脱敏预览、完整质量报告与JSON/Markdown导出，可本地运行；尚未实现上传 HTTP 路由、预览暂存、教师权限、数据库和确认事务。`can_commit` 及报告的 `validation_passed` 仅表示通过解析质量门槛，不能替代后续权限、身份冲突和事务校验。

## 运行

需要 Python 3.12 和 uv。在仓库 `apps/api` 目录运行：

```powershell
uv sync --locked --group dev
uv run --locked python -m app.importing.edu '本地文件路径.xlsx' --summary-only
uv run --locked python -m app.importing.edu '本地文件路径.xlsx'
uv run --locked python -m app.importing.edu '本地文件路径.xlsx' --student-offset 0 --student-limit 20 --row-offset 0 --row-limit 25 --issue-offset 0 --issue-limit 50
uv run --locked python -m app.importing.edu '本地文件路径.xlsx' --quality-report --output 'quality.json'
uv run --locked python -m app.importing.edu '本地文件路径.xlsx' --quality-report --format markdown --output 'quality.md'
```

第一条解析命令只输出人数、评分数和问题数；第二条输出分页脱敏预览 JSON（默认20名完整学生、25条评分、50条问题）。完整质量报告不受预览分页大小影响。成功退出码为0；有阻断错误、文件不可读或格式不支持时为2。有错误的可读文件仍能导出质量报告，并保留失败退出码。仅有警告时退出码为0，但后续入库必须人工确认警告。

默认只读并输出到终端；指定 `--output` 后写入新的脱敏结果文件，终端仅返回写入状态。拒绝覆盖原始Excel或已有输出文件，输出目录须已存在。不可读或不支持的文件不虚构零错误报告，只返回固定失败代码。`--format markdown` 仅用于 `--quality-report`。

程序调用：

```python
from app.importing.edu import parse_excel
from app.importing.edu.preview import build_preview

private_result = parse_excel(uploaded_bytes)
public_preview = build_preview(private_result, row_offset=0, row_limit=25)
payload = public_preview.model_dump(mode="json")
```

也可一次解析同时获得学生预览和完整质量报告，两者共享临时匿名ID及问题ID：

```python
from app.importing.edu import inspect_excel
from app.importing.edu.report import render_quality_report

inspection = inspect_excel(uploaded_bytes, student_limit=20, row_limit=25, issue_limit=50)
preview_payload = inspection.preview.model_dump(mode="json")
report_payload = inspection.quality_report.model_dump(mode="json")
markdown_report = render_quality_report(inspection.quality_report)
```

调用方须在累计上传字节前限制流大小。禁止序列化或记录 `private_result`、学生对象、原始身份、评语或评分依据；公开返回值只能从 `build_preview` 取得。解析器不记录业务日志，也不访问网络、文件存储或模型。

## 支持的模板

- 仅接受 OOXML `.xlsx` 内容，且恰好有两个可见工作表 `Sheet1`、`Sheet2`。命令行另检查扩展名；未来 HTTP 层还需校验上传类型。
- Sheet1：B2:F2 对应五维完整标题；评分依据保存在 `L3:P6`、`L8:P10`、`L12:P14`、`L16:P18`、`L20:P22` 的合并区域。保留标题、依据原文及来源位置；缺失、标题映射错误或合并区域改变均报错。
- Sheet2：第一行留空，或仅A列为标题；第二行开始识别学生数据块。A列为12位学号、空格/横线和姓名；B-D表头为 `维度`、`评分（1~5）`、`评语`；随后五个维度分别占一行。学生块和块内维度允许重排，空白行允许存在；不按每六行机械切割，不硬编码学生人数。
- 使用12位学号字符串识别学生，保留前导零、原始身份和姓名。无效身份、重复学号、孤立评分行、多余数据列、未知/重复/缺失维度阻断整批确认。
- 英文编码固定为 `algorithm_data_structure`、`code_implementation`、`problem_modeling`、`efficiency_awareness`、`solution_strategy`。仅映射当前模板短标题，不模糊猜测维度。
- 评分必须是数值单元格，范围 `[1,5]`，精确到百分之一分。使用原始 XML 数值文本和 Decimal 有理数转换生成 `score_units`，不先经 float 舍入；例如2.75为275，2.751报错。文本数字、布尔值、日期、缺分和非法数值均不接受。
- 空评语及完全相同的非空评语为警告，保留原文。重复警告按受影响记录计数，不等于重复分组数，也不推断评语真实性。

## 输出与追溯

私有解析对象包含学生身份、每行评分原值、整数单位、原始评语、评分依据快照及 `file_sha256`、工作表、行号和B/C/D单元格。无效内容也不被自动修正或部分确认；调用方保留原始文件的受控引用后，才能覆盖完整原文件审计需要。

公开 `ParsePreview` 是解析阶段的 DTO，尚不是 PR #2 中带 `import_id`、范围、版本和有效期的完整 HTTP `ImportPreview`。`student_samples` 仅包含身份、表头及五维评分完整合法的学生，按固定五维顺序提供评分；完整总数为 `summary.complete_student_count`。逐行 `rows` 仍保留不合格学生的数据供定位，块级错误会传递到该学生行的 `issue_codes`。三组分页独立，offset为非负整数、limit为1～100的整数，不能用布尔值代替整数。分页不改变全量汇总。

每条问题提供稳定错误代码、中文提示、工作表及单元格。能够映射到评分行时，返回 `row_key` 和全量评分行列表中的零基 `row_offset`，可据此取回目标页；问题位于学生块表头或Sheet1时，不编造评分行位置。`preview_student_id`、匿名标签、`dimension_code` 在可确定时返回，否则为空。缺失/重复维度问题会明确对应维度编码。同一私有解析结果重复分页、生成质量报告时，问题ID保持一致；重新解析则生成新的临时ID。

学生只显示本次解析产生的随机预览 ID 和“学生001”类标签。有效评分可以显示原始数字文本；无效评分原值一律不回显。评语全部暂缓公开：非空为 `comment=null, comment_status=withheld`，空值为 `empty`；本阶段不声称已完成评语脱敏审核。私有对象禁用字段 repr，不能因此把它当成可公开对象。预览ID在同一解析结果分页中保持一致，重新解析会更换；正式学生和证据ID须在后续确认事务中分配。

`parsed_evaluation_count` 统计检测到的评分行，包含非法行；`complete_student_count` 统计身份合法、无重复学号、表头正确且五维评分完整合法的学生块。存在其他全局问题时仍可展示这些计数，但 `status=invalid`、`can_commit=False`。

## 完整质量报告

`build_quality_report(private_result)` 返回公开 `QualityReport`，版本为 `quality.v1`，可用于后续接口组装或直接导出：

| 字段 | 含义 |
| --- | --- |
| `status` | `passed` 无问题；`warning` 仅有警告；`blocked` 有阻断错误 |
| `validation_passed` | 是否通过解析质量门槛；不是权限或确认入库结果 |
| `requires_warning_acknowledgement` | 存在警告时为true，后续确认入库需处理 |
| `summary` | 完整人数、行数、错误/警告、合法/缺失/非法评分数、未知维度行数、定位学生数和工作簿级问题数 |
| `dimensions` | 固定五维分别统计评分行、合法/缺失/非法数值、缺少该维度的学生、维度重复的学生 |
| `issue_groups` | 按级别与问题码分组，列出提示、次数、定位学生数、定位行数及最多5个定位示例 |
| `issues` | 完整问题清单；不受预览默认50条或任何预览分页参数截断 |

合法/缺失/非法评分数针对实际评分行的数值，三者之和等于评分行总数；未知维度的行仍计入数值统计，但不能因此通过维度校验。“没有该维度整行”与“有该维度但评分为空”分别统计，不能把未出现的评分行伪造成已解析的空值。定位学生数按有直接问题来源的临时学生ID去重；Sheet1评分依据等全局错误单独计入工作簿级问题，不编造受影响的具体学生。

重复评语的次数为受影响记录数，不是重复文本组数。报告中每组最多展示5个示例，但 `occurrence_count` 和完整 `issues` 始终来自全部问题。Markdown报告包含结果、统计表、五维质量表、问题汇总和全部定位明细。JSON/Markdown均不输出姓名、学号、原始评语、非法评分或非法维度原值。

这里的质量问题码属于解析器内部服务，目前尚未实现HTTP协议草案中的问题码映射、教师身份冲突检测或预览暂存生命周期，不应直接宣称完整 `/imports/preview` 已上线。

文件摘要是原始字节SHA-256。无阻断错误时才生成 `content.v1` 内容摘要：UTF-8 JSON、`ensure_ascii=False`、`sort_keys=True`、分隔符 `(',', ':')`，无结尾换行；结构为 `{"version":"content.v1","students":[[学号,[[维度编码,整数分,原始评语],...]],...],"rubrics":[[维度编码,完整标题,[依据原文,...]],...]}`。学生按学号排序，维度及评分依据按上述固定五维顺序排列。姓名、样式、文件名、坐标和行顺序不参与内容摘要，原始评语/依据文本变化参与；后续确认必须在防重判断前单独检查学号与姓名冲突。内容摘要不等于已实现数据库防重。

## 输入边界

默认限制：压缩10MiB、解压总量50MiB、256个ZIP条目、每表10000行/32列/100000个存储单元格、每XML部件300000个元素、单文本节点32767字符。数值词法最多80字符。检查实际行/单元格，忽略可能伪造的 worksheet dimension；重复单元格、错位行号或无序坐标拒绝处理。

openpyxl以只读方式打开，安装 defusedxml，XML预检查禁用DTD/实体。拒绝公式（即使存在缓存值）、外部关系、宏/二进制嵌入部件、重复ZIP条目和路径穿越；不会计算公式、解压到文件系统或访问外部链接。含图片等当前模板以外的二进制部件也明确拒绝，不静默忽略。
新增的唯一二进制例外是通过专用内容类型、worksheet/pageSetup关系及有界 DEVMODEW 包络联合校验的
打印设置部件；只作为惰性数据保留，不交给打印驱动或 openpyxl 读取，不改变原始字节或评分。
具体支持子集、固定拒绝码、风险和原文件溯源见 [打印设置兼容评估](PRINTER_SETTINGS_COMPATIBILITY.md)。
这不是任意 .bin 准入，也不代表所有平台/厂商格式已支持或完成恶意软件认证。

10秒是协作式超时检查，并非能中断第三方库调用的进程硬超时。后续上线上传接口时，应在受限工作进程中执行解析，并设置硬超时/内存上限、请求体限制和身份鉴权；当前CLI/私有服务不直接暴露为公开上传接口。不可读/不支持的文件抛出仅含固定代码的 `ImportFileError`；可读但内容不合格的文件返回 `invalid` 预览，不能入库。

依赖新增 `openpyxl==3.1.5`、`defusedxml==0.7.1`，锁文件只增加它们及 `et-xmlfile`，不升级已有包。安全与只读行为参考 [openpyxl 官方说明](https://openpyxl.readthedocs.io/en/stable/) 和 [只读模式文档](https://openpyxl.readthedocs.io/en/stable/optimized.html)。

## 检查

```powershell
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -q
```

`tests/importing` 全部使用程序生成的合成数据，覆盖当前模板、小数及XML词法精度、质量错误、来源、内容摘要、脱敏分页、命令行退出码和恶意/损坏输入。真实学生文件、解析结果及数据库不得提交到仓库。

下一阶段：受控预览暂存和教师权限 → 确认事务及SQLite批次/身份隔离 → 正式批次查询和证据API → 现有分析引擎适配。只有这些完成后，前端才可以通过正式业务API使用已入库数据。
