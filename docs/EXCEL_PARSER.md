# 固定模板 Excel 解析服务

关联 Issue #10、协议草案 PR #2。此阶段实现文件解析、质量校验、来源保留和脱敏预览，可本地运行；尚未实现上传 HTTP 路由、预览暂存、教师权限、数据库和确认事务。`can_commit` 仅表示通过解析质量门槛，不能替代后续权限、身份冲突和事务校验。

## 运行

需要 Python 3.12 和 uv。在仓库 `apps/api` 目录运行：

```powershell
uv sync --locked --group dev
uv run --locked python -m app.importing.edu '本地文件路径.xlsx' --summary-only
uv run --locked python -m app.importing.edu '本地文件路径.xlsx'
```

第一条解析命令只输出人数、评分数和问题数；第二条输出分页脱敏预览 JSON（默认25条评分、50条问题）。成功退出码为0；有阻断错误、文件不可读或格式不支持时为2。不会改写源文件或创建数据文件。

程序调用：

```python
from app.importing.edu import parse_excel
from app.importing.edu.preview import build_preview

private_result = parse_excel(uploaded_bytes)
public_preview = build_preview(private_result, row_offset=0, row_limit=25)
payload = public_preview.model_dump(mode="json")
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

公开 `ParsePreview` 是解析阶段的 DTO，尚不是 PR #2 中带 `import_id`、范围、版本、有效期及学生样本的完整 HTTP `ImportPreview`。`rows`、`summary`、来源及分页字段沿用草案口径；每条问题提供稳定错误代码、中文提示和来源位置。

学生只显示本次解析产生的随机预览 ID 和“学生001”类标签。有效评分可以显示原始数字文本；无效评分原值一律不回显。评语全部暂缓公开：非空为 `comment=null, comment_status=withheld`，空值为 `empty`；本阶段不声称已完成评语脱敏审核。私有对象禁用字段 repr，不能因此把它当成可公开对象。预览ID在同一解析结果分页中保持一致，重新解析会更换；正式学生和证据ID须在后续确认事务中分配。

`parsed_evaluation_count` 统计检测到的评分行，包含非法行；`complete_student_count` 统计身份合法、无重复学号、表头正确且五维评分完整合法的学生块。存在其他全局问题时仍可展示这些计数，但 `status=invalid`、`can_commit=False`。

文件摘要是原始字节SHA-256。无阻断错误时才生成 `content.v1` 内容摘要：UTF-8 JSON、`ensure_ascii=False`、`sort_keys=True`、分隔符 `(',', ':')`，无结尾换行；结构为 `{"version":"content.v1","students":[[学号,[[维度编码,整数分,原始评语],...]],...],"rubrics":[[维度编码,完整标题,[依据原文,...]],...]}`。学生按学号排序，维度及评分依据按上述固定五维顺序排列。姓名、样式、文件名、坐标和行顺序不参与内容摘要，原始评语/依据文本变化参与；后续确认必须在防重判断前单独检查学号与姓名冲突。内容摘要不等于已实现数据库防重。

## 输入边界

默认限制：压缩10MiB、解压总量50MiB、256个ZIP条目、每表10000行/32列/100000个存储单元格、每XML部件300000个元素、单文本节点32767字符。数值词法最多80字符。检查实际行/单元格，忽略可能伪造的 worksheet dimension；重复单元格、错位行号或无序坐标拒绝处理。

openpyxl以只读方式打开，安装 defusedxml，XML预检查禁用DTD/实体。拒绝公式（即使存在缓存值）、外部关系、宏/二进制嵌入部件、重复ZIP条目和路径穿越；不会计算公式、解压到文件系统或访问外部链接。含图片等当前模板以外的二进制部件也明确拒绝，不静默忽略。

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
