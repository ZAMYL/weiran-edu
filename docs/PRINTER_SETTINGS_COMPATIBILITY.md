# V1 打印设置部件兼容与安全评估

基于 main 028493d；独立分支 codex/edu-printer-settings-compat。
PR #35 开工时仍 OPEN，已知限制为合法双 Sheet 文件包含 xl/printerSettings/*.bin 时
被 UNSUPPORTED_WORKBOOK_PART 拒绝；前端不删除部件。本轮不改前端、评分、数据库、权限或 AI。

## 已复现的原问题

未修改解析器前，用纯合成 Sheet1/Sheet2、现有五维数据构造：

- xl/printerSettings/printerSettings1.bin：合成 DEVMODEW 公共头与可选合成私有数据。
- [Content_Types].xml：bin Default（或该 PartName 的 Override）指向专用内容类型。
- xl/worksheets/_rels/sheet2.xml.rels：内部 printerSettings 关系。
- sheet2.xml 的直接 pageSetup 子节点以同一个 r:id 引用该关系。

该有效 OOXML 部件图在 main 的文件后缀检查中稳定抛 UNSUPPORTED_WORKBOOK_PART，
独立复现测试实际运行1项通过（断言旧拒绝）；尚未到评分校验就失败。
夹具见 tests/importing/printer_fixture.py，全部程序生成，不提交 Excel/二进制夹具。

## 标准依据与实现边界

[Office Open XML Part 1，Printer Settings Part](https://download.microsoft.com/download/e/1/4/e14fb96f-83b8-4a2a-84db-7fa8acbe061a/Office%20Open%20XML%20Part%201%20-%20Fundamentals.pdf)
定义专用内容类型/关系，内容布局由应用决定；Windows DEVMODE 只是一个实现示例，
不能把“合法 OOXML”直接等同“任意 .bin 都安全”或“所有平台打印设置均是同一种格式”。
[微软 Printer Settings 实现参考](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xlsb/1cdc4cb9-836d-41d6-a5b5-9ac0428f491c)
说明了每工作表关联打印部件、部件不再发出关系的约束；这是实现参考，不把 XLSB 格式规则冒充 XLSX 的全部规范。

[DEVMODEW 定义](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/ns-wingdi-devmodew)
区分 dmSize 公共区与 dmDriverExtra 私有区。
[IsValidDevmode 文档](https://learn.microsoft.com/en-us/windows/win32/printdocs/isvaliddevmode)
也明确其不检查驱动私有字段，且可能修复输入；本实现不调用它、任何驱动、打印 API 或 ctypes，
不修复文件，不加载厂商私有格式。

本轮仅支持一个保守的 Windows 子集，不宣称覆盖所有合法 OOXML 打印结构：

- 路径必须精确为 xl/printerSettings/printerSettings[1-9][0-9]{0,5}.bin。
- 最多2个打印部件，每张现有工作表至多1个，部件不跨表共用，必须完整一对一绑定。
- 每部件至多64 KiB，同时计入原压缩/展开总量和 ZIP 条目预算；只允许普通文件，检查 CRC。
- 二进制包络识别：DEVMODEW dmSpecVersion=0x0401、dmSize=220；
  dmDriverExtra 与实际剩余字节数完全一致；设备名称字段必须在64字节的UTF-16单元边界内有终止符。
  明显的 PE/ZIP/OLE/ELF 文件头、短数据、截断/多余尾部、其它版本/长度均拒绝。
- 驱动私有区可以存在，但只作为有界惰性字节，**不解码、不解释、不执行、不发送到打印子系统**。
  公共字段也仅读取版本/长度/终止符作为结构包络，不采用打印参数改变评分读取。

这不是反病毒或驱动语义校验：伪造正确包络不能证明私有数据或设备名称无恶意，CRC 和 SHA-256
也不是来源认证。这里成立的安全边界是“不把打印数据交给任何解释器/驱动”，而不是“已认证打印二进制安全”。
若部署方要求连有界惰性私有区也必须证明无害，则不能支持任意厂商私有数据；应保留拒绝，
或另行制定更窄的无私有区策略，不能通过宣称校验成功绕过该限制。

## 联合部件校验

实现位于 app/importing/edu/printer_settings.py，接在 archive.py 原 XML 预检内，
而非新增一套解析器或先改写上传压缩包。

1. 原 ZIP 规则实际是 XML/RELS 后缀准入，不是完整 OPC schema 验证。本轮只增加上述精确打印路径例外，
   其它 .bin、图片、嵌入对象和不支持的部件继续拒绝；非空目录伪装载荷及打印部件符号链接也拒绝；宏/ActiveX 内容类型与宏关系不放行。
2. 校验 Content Types / Relationships 的命名空间、直接节点形状、重复扩展/PartName/关系 ID。
   printer MIME 必须为 application/vnd.openxmlformats-officedocument.spreadsheetml.printerSettings；
   支持 bin Default 或精确 Override；按有效类型校验，Override 优先于未实际使用的通用 bin Default。
   错误有效类型、误标评分 XML、悬空打印 Override 均拒绝，不因通用 Default 放行其它二进制。
3. 含打印设置时，根 officeDocument 关系必须唯一指向当前 xl/workbook.xml；
   当前工作簿及两个活动评分表必须具有正确 main/worksheet 内容类型，不能误标成通用二进制。
   工作簿的两个现有 sheet 必须有标准 worksheet 类型关系；打印关系只能由这两张工作表发出，
   Type 必须为 http://schemas.openxmlformats.org/officeDocument/2006/relationships/printerSettings。
4. 只接受 ../printerSettings/<文件名> 或 /xl/printerSettings/<文件名> 两种规范目标；
   外部关系、URI/UNC、百分号别名、反斜线、查询/片段以及绕路 .. 别名均拒绝。
   打印文件夹不能含附属关系/额外载荷，不允许 orphan、双重引用、指向评分 XML 或反向关系。
5. 含打印部件时，worksheet 关系只准 printerSettings；r:id 只准直接 pageSetup 使用，且恰好一次。
   评分单元格、嵌套 pageSetup 或未知元素不能借用打印关系，未知评分引用也不静默忽略。
6. 所有评分 XML 仍完整通过原公式/DTD/实体/坐标/数值词法/资源检查。结构异常先拒绝，
   再有界读取打印字节检查 CRC/包络；没有把“打印设置忽略”扩展为忽略任何评分异常。

## 原始文件与只读保证

原始 uploaded_bytes 直接用于 SHA-256、原 openpyxl 只读加载和后续保存；
没有删除部件、重打包、清洗、转换或覆盖教师原文件。保留原始字节意味着另存为产生的文件摘要
仍会变化；既有 content.v1 评分/评语/依据内容摘要不含打印元数据，算法保持不变。
评分来源的工作表、行号及 B/C/D 坐标不变，source.file_sha256 始终对应包含打印设置的原上传字节。

读取依赖仍为 openpyxl 3.1.5、defusedxml，无新增依赖。检查了锁定版本源码：只读路径读取关系 XML，
随后使用 ReadOnlyWorksheet，不加载打印驱动数据。测试拦截 ZipFile.open：打印二进制恰好只由
预检守卫读取1次，后续 openpyxl 不打开它；同时断言 load_workbook 收到原始相同字节、read_only=True、keep_links=False。
隔离进程解析仍使用既有 parse_isolated 及硬超时，不变更权限、工作进程或网络策略。

## 固定错误码

| 代码 | 含义 | 上传/确认 HTTP |
| --- | --- | --- |
| INVALID_PRINTER_SETTINGS_PACKAGE | 内容类型、所有权、引用或 pageSetup 不一致 | 422，固定中文说明 |
| UNSUPPORTED_PRINTER_SETTINGS_FORMAT | 打印数据不是受支持包络 | 422，说明暂不支持，原文件未改 |
| PRINTER_SETTINGS_SIZE_LIMIT | 单部件64 KiB或最多2个的预算超限 | 413，固定限制说明 |
| INVALID_WORKBOOK_RELATIONSHIP | 非法命名空间/重复关系/类型或目标异常 | 422，固定来源异常说明 |

CLI/私有解析直接返回固定 ImportFileError.code；上传/确认只增加上述映射，不改鉴权和评分 API。
既有其它错误映射保持。代码/提示不含目标路径、设备名、厂商数据、原评分/评语或身份信息。

## 尚未覆盖与最小后续方案

- Mac 打印记录、ANSI DEVMODE、旧/新公共布局、非规范命名/URI和大于64 KiB的设置仍可能是合法 OOXML，
  但不在本安全子集；固定失败而非自动删除、转换或猜测。共享同一打印部件的跨表关系本轮也不支持。
- 未读取任何真实教师文件，不能声称 PR #35 提及的具体原件已经可入库；本轮仅证明合成结构和原始字节溯源。
- 若受控本机验收仍失败，后端先只核对不含身份/设备名的部件类型、尺寸与公共头部数字，
  用相同结构补充纯合成夹具后审查扩展；不要上传教师原件、截取驱动私有载荷或让教师改评分文件。
- 当前没有全 OOXML schema 验证或恶意软件扫描；不得将本兼容层宣传为可信文件认证。
  支持范围扩大仍必须保证二进制不交给任何执行/解释路径，未知格式不能靠文件后缀或 MIME 单独放行。

请 Peng-Kai789 审核精确准入、惰性数据边界、原始 SHA/来源坐标及 HTTP 固定错误映射。
PR #35 前端与 #36 报告下载均独立推进，不修改或合并它们。

## 验证入口

```powershell
# apps/api
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest tests/importing tests/edu/test_printer_import.py -q -ra
uv run --locked pytest -q -ra
```

全部夹具合成。覆盖正常/警告/阻断、双表、私有区、Default/Override、绝对/相对规范目标、
SHA/内容摘要/来源坐标、原始 BLOB 保存与证据回查、未知二进制/宏/外部/路径/重复引用/CRC/超限、
公式与百分之一精度、只读读取轨迹和隔离工作进程。实际数量/CI以本PR当前提交为准。