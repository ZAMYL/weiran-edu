# 未然 EDU 后端 API 草案 v0.1

日期：2026-10-23；Demo 对齐更新：2026-10-24。状态：保留的历史设计草案，不能作为现行 API 契约。

2026-10-25 审核更新：导入、数据库、教师试点授权、统计、群体筛选和证据查询已另行实现；候选查询和指标参与记录 HTTP 回查仍未实现。实际请求、响应和错误码以运行时 `/openapi.json`、[实现状态与差异](IMPLEMENTATION_STATUS.md)、[数据库接口](DATABASE_BATCH_AUTH.md) 和 [统计/取证接口](STATISTICS_GROUP_EVIDENCE_API.md) 为准。下文及同名静态 OpenAPI 记录原设计，不能直接用于生成当前客户端。

依据：[已确认的后端冻结规则](BACKEND_CONTRACT_V0.1.md)。本草案细化 DTO 和错误语义，不修改冻结规则；新增的读取接口与字段属于本次设计建议，不表示团队已共同冻结。数据表见 [数据结构草案](DATA_MODEL_V0.1.md)，机器可读契约和合成示例见 [OpenAPI](contracts/edu-api.v0.1.openapi.json)。

维度编码、评分/Fraction 与匿名证据引用对接见 [后端与分析引擎对接](BACKEND_ANALYSIS_HANDOFF_V0.1.md)。已实现适配位于 `apps/api/app/edu/analysis.py`。

三页 Demo 的原字段转换、分页、快照与图表方案见 [Demo/API 对齐草案](FRONTEND_API_ALIGNMENT_V0.1.md)。本草案提出 14 项操作（13 个路径）；该数量不代表当前已实现路由数。

## 1. 通用约定

| 项目 | 草案约定 |
| --- | --- |
| 路径 | `/api/v1/edu`；下文使用相对路径 |
| 命名 | JSON 字段 `snake_case`，维度使用稳定编码 |
| 成功响应 | 直接返回 DTO，不额外套 `data`；使用对应 HTTP 状态码 |
| 错误响应 | `{"error":{"code":"...","message":"...","request_id":"..."}}` |
| 未知值 | 可空字段必须返回 `null`，不能用空串或零代替 |
| 时间 | RFC 3339 UTC，例如 `2026-10-23T10:00:00Z`；测评日期为 `YYYY-MM-DD` 或 `null` |
| ID | 服务端生成的不可猜测标识，不含学号、姓名；示例中的 `*_demo_*` 仅为合成占位 |
| 评分 | JSON number，1～5，最多两位小数；服务端十进制解析，库内整数百分之一分存储 |
| 分页 | `offset >= 0`，`limit` 默认 20、最大 100；稳定排序后分页 |
| 数据响应 | 包含 `X-Request-ID` 和 `Cache-Control: no-store` 响应头 |
| 未知输入字段 | 请求对象拒绝未知字段；客户端不能提交教师 ID、分数修正值或 SQL |

已合并到 main 的基础工程提供 `GET /api/v1/health`、`APIError`、统一错误信封和 `X-Request-ID`，本草案沿用这些机制。现有错误响应只有 `error.code`、`error.message`、`error.request_id`，不新增通用 `details` 字段；行级质量问题放在导入预览的 `issues` 中，保持基础错误处理不变。健康检查属于已有基础接口，不计入下列 11 个 EDU 设计接口。

认证草案采用 `Authorization: Bearer <teacher_access_token>`，只定义受验证教师身份的接入方式，不在本次设计登录、签发令牌或注册接口。令牌签发方及生命周期仍待确认。教师和来源范围均由服务端校验，客户端不能通过自填教师 ID 授权。所有接口都需身份；资源不存在和无访问权统一返回 `404 RESOURCE_NOT_FOUND`，无有效身份返回 401，已登录但无来源范围导入权返回 403。

分页对象统一为 `{"offset":0,"limit":20,"returned":1,"has_more":false}`。总数是完整查询结果数，不能用本页长度代替。所有响应均不包含真实身份、原始文件名、磁盘路径、下载链接或未检查的原始评语。

## 2. 接口总表

| 方法 | 相对路径 | 职责 | 来源 |
| --- | --- | --- | --- |
| POST | `/imports/preview` | 上传、解析、预览及质量报告 | 已确认 |
| GET | `/imports/{import_id}` | 回读预览及分页质量问题，支持页面刷新 | 本次补充 |
| PATCH | `/imports/{import_id}` | 修改预览标题、课程、日期并生成新版本 | Demo 对齐补充 |
| POST | `/imports/{import_id}/commit` | 确认预览、事务入库、幂等返回批次 | 已确认 |
| GET | `/batches` | 当前教师可访问批次列表 | 已确认 |
| GET | `/batches/{batch_id}` | 单批元数据与数量 | 本次补充 |
| GET | `/batches/{batch_id}/students` | 分页匿名学生及五维评分 | 本次补充 |
| POST | `/batches/{batch_id}/groups/query` | 1～2 条维度条件 AND 筛选 | 已确认 |
| GET | `/batches/{batch_id}/students/{student_id}/evidence` | 学生五维评分、脱敏评语和来源 | 已确认 |
| GET | `/batches/{batch_id}/evidence/{evidence_id}` | 按证据 ID 核对引用 | 本次补充 |
| GET | `/batches/{batch_id}/rubric` | 本批次评价依据及来源 | 已确认 |
| GET | `/batches/{batch_id}/statistics` | 五维成功统计及可选全批分值矩阵 | 已确认路径，成功 DTO 为本次草案 |
| POST | `/batches/{batch_id}/insights/query` | 教师内部候选卡查询 | Demo 对齐补充 |
| POST | `/batches/{batch_id}/insights/{candidate_id}/records/query` | 某卡或指标的实际参与记录 | Demo 对齐补充 |

本次没有评分编辑、批次删除、跨批次趋势、AI 生成或任意 SQL 接口。独立的数据访问服务可供未来 AI 工具复用；不让 AI 绕过服务端权限直接读库。

## 3. Excel 上传与预览

### POST `/imports/preview`

请求为 `multipart/form-data`，字段如下。元数据与文件一起作为表单字段上传，不混用 JSON 请求体。

V1 入口为「上传测评文件」，不预选模板；当前仅处理已有验证的五维 XLSX。默认值、权限范围及未支持格式见 [V1_FILE_INTAKE.md](V1_FILE_INTAKE.md)。

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `file` | binary | 是 | 当前固定模板 `.xlsx`，检查实际内容而非仅扩展名 |
| `data_scope_id` | string | 否 | 当前教学空间；省略时仅自动使用唯一可导入范围，修订可继承本人拥有的目标范围；歧义为409 IMPORT_SCOPE_REQUIRED |
| `title` | string | 否 | 显式提供时去除首尾空白后1～100字符；省略时取安全处理后的文件名，预览后可PATCH |
| `course` | string | 否 | 未传视为 `null`；若传入，1～100 字符 |
| `assessment_date` | date | 否 | 未传视为 `null`，不从上传日期推断 |
| `revision_of_batch_id` | string | 否 | 修正版指向同范围、可访问且可修订的原批次；未传为 `null` |

草案建议上传上限为压缩文件 10 MiB、ZIP 声明解压总量 50 MiB、每表有效行 10,000；实现前确认配置。还应限制 ZIP 条目数和处理耗时，不能只信任压缩头或在 Web 进程无限解压。原始文件不执行宏、公式或外部链接，不使用 Excel 自动化执行上传内容。

处理建议采用当前小文件的同步预览：完成解析后才返回；不新增队列、轮询任务或 `processing` 状态。超时和资源上限失败返回明确错误，不留下可确认的半份预览。

正常处理返回 **201 ImportPreview**。工作簿有可定位的数据错误时，也创建 `invalid` 暂存记录并返回 201，让页面展示质量报告；HTTP 201 仅表示预览记录已创建，不表示允许入库。文件过大、损坏到无法检查、媒体类型错误或请求字段非法分别返回 413、422、415、422，此时没有可用预览 ID。

`ImportPreview` 包含：

| 字段 | 含义 |
| --- | --- |
| `import` | 暂存 ID、状态、来源范围、标题与日期课程、版本、时间、重复提示、已入库批次 |
| `summary` | 识别学生数、完整有效学生数、成功解析评价数、错误数、警告数 |
| `student_samples` | 只展示完整有效记录的匿名预览及五维数据；预览 ID 不是真实学生 ID |
| `student_page` | 匿名预览分页，完整总数为 `summary.complete_student_count` |
| `issues` / `issue_page` | 安全的质量问题列表及分页，总数为错误数与警告数之和 |
| `rows` / `row_page` | 包含有效、缺失、非法评分的安全行级预览，支持表格及问题定位 |

评分预览包含 `dimension_code`、`score`、`score_raw`、`comment`、`comment_status` 和来源。`score_raw` 是单个原始评分的文本表示，不是原始整行；它经校验后才能返回。`preview_student_id` 仅在本次导入内稳定，确认前不创建正式学生或身份映射。预览不生成正式 `evidence_id`。

质量问题包含 `issue_id`、`severity`（`error` / `warning`）、`code`、安全的 `message`、`sheet_name`、`row_number`、`cell` 及可空的 `row_key`、`row_offset`。无法定位时为 `null`，不编造行号或跳转。row_offset 为行级预览中的零基位置，服务端和前端按同一稳定顺序分页。非法评分文本若不能安全显示则 score_raw=null，不回显身份或原始评语。

`summary.detected_student_count` 是识别到的学生块数，不是去重人数；`complete_student_count` 是身份唯一且五维合法完整的记录数；`parsed_evaluation_count` 为成功解析的合法维度评分数，异常文件不能据此宣称全部通过。正式批次必须满足评分数 = 学生数 × 5。

### GET `/imports/{import_id}`

查询参数 `student_offset`、`student_limit`、`issue_offset`、`issue_limit`，分别分页读取预览和质量问题；上限均为 100，默认分别 20 和 100。

新增 `row_offset`、`row_limit` 读取行级表格，默认 0、10，最大页大小 100。问题定位先请求包含 row_offset 的页，再以 row_key 高亮。已提交导入的 rows/row_page 同样为 null。

- 所有权仍属于上传教师，不因为有同范围访问权就能读取别人的暂存文件。
- 未提交且过期时返回 **410 IMPORT_EXPIRED**，不能再次确认。
- 已提交时继续返回可保留的非敏感元数据和 `committed_batch_id`；`student_samples`、`issues` 及其分页为 `null`，`preview_available=false`，前端转到批次读取。不得承诺长期保存预览中的个人材料。
- 未过期预览 `preview_available=true`，两组列表与分页非空；即使列表没有元素，也返回空数组。

## 4. 确认入库与批次

### POST `/imports/{import_id}/commit`

请求示例（合成）：

```json
{
  "preview_version": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "acknowledge_warnings": false
}
```

`preview_version` 是服务端对暂存数据、元数据、来源范围、解析器版本和评价依据版本的绑定摘要。它由预览返回，不能自行计算为授权凭证。`acknowledge_warnings` 必须是严格布尔值；有警告而未确认时返回 409。确认请求不能替换文件、修改元数据或传入修正分数；元数据经下述 PATCH 生成新预览版本，文件和评分改变需重新上传预览。

| 情况 | HTTP | `result` | 行为 |
| --- | --- | --- | --- |
| 首次成功 | 201 | `created` | 同一事务创建完整正式批次 |
| 文件或内容已入库且可访问 | 200 | `duplicate` | 关联已有批次，保留其原始来源和元数据 |
| 同一预览已确认 | 200 | `replayed` | 权限和版本核验后返回原批次 |

三种成功响应字段相同：`import_id`、`batch_id`、`result`。同一导入 ID 的重试幂等，不依赖另设 `Idempotency-Key`。数据库错误返回 503；事务回滚后预览仍可按有效期重试。状态冲突或摘要不匹配返回 409，过期返回 410，`invalid` 预览返回 409 `IMPORT_NOT_CONFIRMABLE`。

已经提交的请求，先核对身份、预览摘要与正式批次访问权，再返回原批次；不因暂存材料清理而重新解析，也不因 24 小时已过拒绝合法重放。

对无权访问的重复批次，只返回 409 `IMPORT_CONFLICT`，不返回其 ID、教师、数量或存在细节。导入权不自动授予所有同范围批次的读取权。

修正版的 `revision_of_batch_id` 必须属于同一来源范围，上传教师必须拥有该批次；确认时还需验证它仍是修订链末端。并发修订返回 409 `REVISION_CONFLICT`，不能形成隐式分叉。普通无关测评不需要设置此字段。内容完全相同仍优先按重复处理，不用修订绕过防重。

### PATCH `/imports/{import_id}`（新增草案）

请求必须有当前 preview_version，且至少有 title/course/assessment_date 中一项；标题非空，课程和日期可显式设为 null。返回 200 ImportPreview，保留原 rows、质量事实及 expires_at，生成新的 preview_version。旧版本返回 409 PREVIEW_VERSION_MISMATCH，已提交返回 409 IMPORT_NOT_CONFIRMABLE，过期返回 410。不能更换来源范围、文件、修订目标或评分。本操作只更新暂存元数据，不直接修改已入库批次。

### GET `/batches`、GET `/batches/{batch_id}`

列表参数：`data_scope_id`（可选）、`include_revisions`（默认 false）、`offset`、`limit`。默认只列修订链末端，设为 true 可列历史；按 `created_at DESC, batch_id DESC` 排序。无数据为 200、空列表和 `total_count=0`。

`Batch` 字段：

| 字段 | 类型 / 含义 |
| --- | --- |
| `batch_id`、`data_scope_id` | 不透明 ID |
| `snapshot_id` | 当前数据/来源/脱敏投影的快照，不是文件哈希或授权令牌 |
| `title` | 教师确认的测评标题 |
| `course`、`assessment_date` | 可空课程和测评日期 |
| `student_count`、`evaluation_count` | 从正式数据核对得到的正整数 |
| `rubric_version`、`parser_version` | 不可变标准和解析版本 |
| `file_sha256`、`content_sha256`、`content_hash_version` | 受权限保护的来源与防重元数据 |
| `revision_of_batch_id` | 可空的前一版 ID |
| `is_current` | 是否为该修订链末端，不代表最新一次考试 |
| `created_at` | 入库时间，不代表测评发生时间 |

未知日期不能排序成虚构日期。历史批次始终可按 ID 读取；修订只改变默认列表显示，不把旧证据指向新版。

## 5. 五维评分、评语与证据

### GET `/batches/{batch_id}/students`

参数 `offset`、`limit` 及可选 snapshot_id，返回 `batch_id`、snapshot_id、`total_count`、`items`、`page`。按批次内 `display_order`、匿名 ID 稳定排序；显示顺序不代表排名。

`StudentProfile` 为 `student_id`、`display_label`、`scores`。每名学生返回恰好五个不同维度，评分摘要为 `dimension_code`、`score`、`evidence_id`；列表不附整段评语，点击后读取证据。同一来源范围内匿名学生 ID 跨批次稳定；不同来源范围不自动合并。

### POST `/batches/{batch_id}/groups/query`

请求示例（合成条件，不代表当前样本结果）：

```json
{
  "conditions": [
    {"dimension_code":"solution_strategy","operator":"gte","value":4},
    {"dimension_code":"algorithm_data_structure","operator":"lt","value":3}
  ],
  "offset": 0,
  "limit": 20
}
```

限定 1～2 条不同维度条件，固定 AND。阈值同样接受 1～5、最多两位小数；字符串、布尔值、NaN、无限值、未知维度及未知字段均拒绝。首版同维度区间筛选暂不在契约内。

请求可含 snapshot_id 与 include_statistics（默认 false）。响应另有 eligible_count、unevaluable_count、matched_among_eligible_ratio 和可空 statistics。

响应包含 `batch_id`、规范化后的 `conditions`、`rule_version`、`matched_count`、`total_count`、`ratio`、`items`、`page`。`ratio` 为完整匹配人数除以本批全部学生数，不按页数计算。`rule_version=group_filter.v1` 只表示确定性筛选实现版本，不代表六类洞察算法已冻结。

### GET `/batches/{batch_id}/students/{student_id}/evidence`

返回 `batch_id`、`student_id`、`display_label`、`rubric_version`、`items`，其中 `items` 恰好为五条评分证据，按冻结维度顺序排列。不接受其他批次中的学生作为本批学生；不跨批搜索补齐。

`Evidence` 字段：

| 字段 | 类型 / 含义 |
| --- | --- |
| `evidence_id`、`batch_id`、`student_id` | 稳定匿名标识，不能使用前端分页行号 |
| `dimension_code` | 五维编码之一 |
| `score` | 规范化分数 |
| `score_raw` | 原始单个评分文本，不含整行身份数据 |
| `comment` | 可空、经过身份信息检查的展示文本 |
| `comment_status` | `screened`（已检查）、`masked`（已遮蔽）、`empty`（原文空）、`withheld`（未完成检查或不允许展示） |
| `source` | 文件哈希、工作表、行号、维度单元格、评分单元格、评语单元格 |
| `rubric_criterion_id` | 对应该批标准中的单项依据 ID |

`empty`、`withheld` 时 `comment=null`；`screened`、`masked` 时为非空文本。`withheld` 不代表原始评语丢失，教师页面需显示“评语暂不可展示”。检查无身份信息时展示原文，有遮蔽时明确展示状态；原始文本仅受控保存。本次不提供绕过脱敏的 `raw=true` 参数。

### GET `/batches/{batch_id}/evidence/{evidence_id}`

直接返回同一 `Evidence`。证据 ID 必须属于路径批次，同时检查教师权限；不存在、批次不匹配或无权访问统一 404。供洞察卡引用核对和将来的 `get_evidence_records` 工具使用，不让调用方按 ID 跨批次取证。

### GET `/batches/{batch_id}/rubric`

返回 `batch_id`、snapshot_id、rubric_id、rubric_name、score_policy、`rubric_version`、恰好五个 `criteria`。score_policy 为当前固定模板的 minimum=1、maximum=5、step=0.01，前端据此替换演示标准。每项包含 `rubric_criterion_id`、`dimension_code`、`display_name`、`original_name`、`criteria_text`、`source`；来源为文件哈希、工作表和实际区域，例如 `L3:P6`。正文是本批真实评价依据的结构化读取结果，不由模型补写。

### GET `/batches/{batch_id}/statistics`

本次补充 200 Statistics：batch_id、snapshot_id、rule_version、scope、display_policy、五项 dimensions 和可空 matrix。每维包含有效/缺失/无效人数、均值/中位数/最小最大值及实际观测分值频数，复用已存在的 summarize，不定义新的教学标准。

统计量为 `{value,exact,display}`，exact 包含整数 numerator/denominator，display 采用 HALF_UP 两位小数。value 为绘图近似数值，不作为筛选依据；无数据时 value/exact 为 null。频数的 denominator 是本维有效样本数，与 exact 中的有理数分母不同。

可选 x_dimension/y_dimension 须同时指定且不同，返回全批真实原分数的稀疏矩阵；轴坐标包含实际小数，不能用整数格漏算。群体查询 include_statistics=true 时另外返回同快照匹配群体的五维统计；矩阵仍是全批范围。零匹配的群体均值为 null、分布为空，不是零分。

本成功形状覆盖已有五维统计和必要交叉计数，不代表完整六类分析、四分位数或 Spearman 公共 DTO 已冻结；未配置仍在鉴权后返回 503 ANALYSIS_RULES_NOT_CONFIGURED，不伪造成功结果。

### POST `/batches/{batch_id}/insights/query`（新增草案）

请求 snapshot_id 可省略，conditions 默认空数组且最多两条不同维度条件，include_dimensions 默认 true，另有 offset/limit。空条件仍可发现分布/复核，不调用空条件筛选；显式关闭维度卡且没有群体或复核候选时可返回空列表。复用已合并 PR #9：返回 batch_id/snapshot_id、comment_analysis_status、total_count、items、page；卡内保留英文维度、程序标题摘要、metrics 的 exact/display/denominator、条件、scope、规则/适配版本、状态和局限。

候选只供教师内部访问，public_display_allowed 恒为 false，不能因内部投影布尔值通过就当作真实鉴权或允许公开。未配置/被扣留的评语分析独立标记，不用“0 个问题”代替。候选不返回名单、正文或内部 RecordRef/location。

### POST `/batches/{batch_id}/insights/{candidate_id}/records/query`（新增草案）

请求必须有 snapshot_id，metric_key 可空，另有 offset/limit；非空时通过 metric_sources 获取该指标实际来源，空值取候选主来源，不取分母 scope。返回匿名参与记录及核验后的 evidence_ids、criterion_ids、evidence_status，不回传正文或全部无关评分作为指标依据。source、批次、学生、维度和访问权须重新核验。

新查询可省略快照以取得当前数据，后续页、群体/候选与证据查询携带同一 snapshot_id。失配返回 409 SNAPSHOT_STALE；候选失效返回 409 CANDIDATE_STALE，需要刷新候选和关联抽屉，不能把旧名单配新统计。候选 ID 不是证据 ID 或授权凭证。

## 6. 错误码草案

| HTTP | `code` | 用途 |
| --- | --- | --- |
| 401 | `UNAUTHORIZED` | 教师身份不存在或失效 |
| 403 | `FORBIDDEN` | 当前身份不具备所选范围的导入权限 |
| 404 | `RESOURCE_NOT_FOUND` | 资源不存在或不可访问，含批次与证据不匹配 |
| 409 | `IMPORT_NOT_CONFIRMABLE` | `invalid` 暂存或状态不能确认 |
| 409 | `PREVIEW_VERSION_MISMATCH` | 提交摘要与服务器预览不一致 |
| 409 | `WARNINGS_NOT_ACKNOWLEDGED` | 存在警告但未确认 |
| 409 | `IMPORT_CONFLICT` | 导入冲突，不能安全返回已有资源 |
| 409 | `REVISION_CONFLICT` | 修订目标不是链末端或并发修订冲突 |
| 409 | `SNAPSHOT_STALE` | 数据、来源或脱敏投影快照不匹配 |
| 409 | `CANDIDATE_STALE` | 候选缓存失效，需按当前快照重新查询 |
| 410 | `IMPORT_EXPIRED` | 尚未入库预览超过有效期 |
| 413 | `UPLOAD_LIMIT_EXCEEDED` | 压缩包、解压量、条目数或有效行超过限额 |
| 415 | `UNSUPPORTED_FILE_TYPE` | 不支持的文件类型 |
| 422 | `VALIDATION_ERROR` | 字段、筛选条件、日期、分页或数值不合法 |
| 422 | `WORKBOOK_UNREADABLE` | 损坏或无法安全检查的工作簿 |
| 503 | `DATABASE_UNAVAILABLE` | 数据库忙、连接或写入异常，可提示稍后重试 |
| 503 | `IMPORT_PROCESSING_UNAVAILABLE` | 预览处理超时或依赖不可用，没有可确认预览 |
| 503 | `DATA_REDACTION_UNAVAILABLE` | 评价依据尚未完成必要检查，不能安全展示 |
| 503 | `ANALYSIS_RULES_NOT_CONFIGURED` | 统计算法尚未配置 |
| 500 | `INTERNAL_ERROR` | 未分类服务异常，返回请求 ID，不返回堆栈 |

质量报告使用单独的问题码：`EMPTY_DATA`、`STUDENT_ID_INVALID`、`STUDENT_IDENTITY_CONFLICT`、`DUPLICATE_STUDENT`、`DIMENSION_MISSING`、`DIMENSION_DUPLICATED`、`DIMENSION_UNKNOWN`、`SCORE_MISSING`、`SCORE_INVALID`、`SCORE_OUT_OF_RANGE`、`SCORE_PRECISION_EXCEEDED`、`RUBRIC_MISSING`、`BLOCK_INVALID`、`COMMENT_EMPTY`、`COMMENT_REPEATED`。同来源、同学号姓名与受控身份映射不一致时，使用阻止入库的 `STUDENT_IDENTITY_CONFLICT`，不回显冲突姓名。可解析的模板错误以 `invalid` 预览中的问题表达，不能混淆为 HTTP 调用本身成功入库。

## 7. 联调与验收边界

前端先使用 OpenAPI 内的合成示例搭建上传、问题展示、确认、批次列表、画像和证据流程。示例数字不代表真实教师数据；当前没有后端地址，也不能将契约文件导入成功当作接口测试通过。

实现时验证：重复上传及并发确认只产生一个批次；异常事务不产生半批；筛选全量人数与证据合计一致；半分和两位小数无损；空评语与暂不可展示评语有不同状态；无权限和跨批次证据请求不泄露数据；预览到期与已提交重放行为不同；修订保留旧证据。

仍需共同确认：本次三项操作、统计 HALF_UP 两位展示及精确值形状、前端分页视图模型和预览名称修改交互；另外确认教师认证集成、上传资源上限、文件/身份保留期限及未覆盖的分析 DTO。个人数据外发另行授权，不由本草案替代。

规范参考：[OpenAPI 3.1.1](https://spec.openapis.org/oas/v3.1.1.html)。本次采用该明确版本描述接口，不表示它是最新版本。
