# Demo 与后端接口对齐 v0.1

> ⚠️ 本文部分内容已被 V1.4 取代，以 docs/V1.4_BASELINE.md 为准。

日期：2026-10-24。状态：保留的 Demo 联调设计草案，未完成前端真实 API 接入。

2026-10-25 审核更新：后端已实现导入、数据库、教师试点授权、统计/筛选和证据来源查询。实际接口以运行时 `/openapi.json` 和 [实现差异清单](IMPLEMENTATION_STATUS.md) 为准；下表中的洞察查询与指标参与记录 HTTP 回查仍未实现。本文原字段方案与静态 OpenAPI 不覆盖实际契约，尤其注意快照错误码、筛选 items 和依据原文暂不展示的差异。

核验 main `20e923241fd576b8023608525a6d09639865eca4`（含已合并 PR #7、#9）。依据：`apps/web/src/domain/model.ts`、`statistics.ts`、三页 features、GroupDrawers 和分析候选投影。机器契约及 Mock 示例见 [OpenAPI](contracts/edu-api.v0.1.openapi.json)，业务约束见 [API 草案](API_CONTRACT_V0.1.md)。本文件落实 [分析/前端交接](ANALYSIS_FRONTEND_HANDOFF.md)，不改页面布局和组件所有权。

## 1. 三页调用与数据来源

| 已完成的 Demo 交互 | 服务端模式调用 | 返回数据 / 约束 |
| --- | --- | --- |
| 批次切换、历史记录 | GET `/batches`，GET `/batches/{batch_id}` | 分页批次、真实计数、snapshot_id；不通过本页学生数组长度计算整批人数 |
| 数据中心上传 | POST `/imports/preview` | 原教师块式 Excel 的服务端预览；不再要求教师转换为演示长表 |
| 预览表与问题定位 | GET `/imports/{import_id}` | rows/row_page、问题级别、row_key；支持跳到问题对应页 |
| 预览中修改测评名称 | PATCH `/imports/{import_id}` | 修改元数据并返回新 preview_version，原 24 小时有效期不延长 |
| 确认导入 | POST `/imports/{import_id}/commit` | 以最新版本确认；成功后读取批次，不再调用本地 crypto.randomUUID 创建正式批次 |
| 五维雷达和分布 | GET `/batches/{batch_id}/statistics` | 五维均值、中位数、有效样本数与实际分值频数；图表标签用 display |
| 全批两维矩阵 | 同一 statistics 加 x_dimension/y_dimension | 全批精确分值的稀疏矩阵，含 2.75 等小数；不受右侧筛选影响 |
| 应用 1～2 条条件 | POST `/batches/{batch_id}/groups/query` | M/N、M/E、不可判定数、分页名单，可附群体五维统计 |
| 清空条件 | GET students + GET statistics | 全批名单和统计；不向内部 select_students 传空 conditions |
| 点击矩阵单元格 | groups/query 中两条 eq 条件 | 坐标原分数直接作为阈值，不从舍入标签反推数值 |
| 发现卡 | POST `/batches/{batch_id}/insights/query` | 复用 PR #9，返回程序事实、指标分母、条件、版本及受控查询 |
| 查看某卡/某指标依据 | POST `/batches/{batch_id}/insights/{candidate_id}/records/query` | 同批同快照实际参与记录及核验后证据 ID，不把全部 scope 当指标来源 |
| 学生详情与评语 | 现有 students/{student_id}/evidence、evidence/{evidence_id}、rubric | 五维评分、已检查评语及实际位置；不从分页摘要伪造完整来源 |

以上新增读取/修改形状属于本次草案，不表示已获团队共同冻结。完整调用前缀为 `/api/v1/edu`。不会新增公开发布接口、真实 AI 入口、评语自由文本搜索或导出服务。

## 2. 领域模型的明确转换

| 当前 Demo | 后端字段 | 转换决定 |
| --- | --- | --- |
| Batch.id / name / fingerprint | batch_id / title / file_sha256 | 只做显式命名转换，sha 只代表文件防重，不代表数据快照 |
| Batch.students | 独立分页 StudentList | 不把分页结果塞成完整批次，移除生产路径对 batch.students 的全量假设 |
| Student.id | student_id | 使用服务端稳定匿名 ID，不保留 S001 会话 ID 或真实输入标识 |
| Student.sourceId | 无公开身份字段 | 不能填真实学号。service/视图模型应取消该生产字段，不用假来源填满旧类型 |
| Standard | rubric_id、rubric_name、rubric_version、score_policy、criteria | 由当前批次标准生成维度，不继续写死 DEMO_STANDARD |
| Rating.value / raw | score / score_raw | 原值文本与规范数值分开，不能用 raw_value 任意对象直接渲染 |
| Rating.comment | comment + comment_status | empty 与 withheld 不合并为空串；分别显示“无评语”和“评语暂不可展示” |
| Source.file | 无原始文件名 | 浏览器选中文件名仅在本地上传区显示；服务端证据用“已授权来源”及安全摘要标签 |
| Source.sheet / row / column | sheet_name / row_number / score_cell 或 comment_cell | 按评分和评语分别选真实坐标，不能用评分列替代评语列 |
| ImportIssue.rowKey | QualityIssue.row_key | 配合 row_offset 定位；issue_id 是问题标识，不是 evidence_id |

前端不需强行维持包含所有学生、整段评语和本地来源的旧 Batch 类型。建议在 service 边界设置 BatchSummary、PagedStudent、StudentEvidence、Statistics 和 CandidateCard 视图模型，页面样式保留；这些前端代码本轮不修改。

### 五维和条件

| Demo | API / 数据库 | 引擎 |
| --- | --- | --- |
| algorithm | algorithm_data_structure | ALGORITHMS |
| coding | code_implementation | IMPLEMENTATION |
| modeling | problem_modeling | MODELING |
| efficiency | efficiency_awareness | EFFICIENCY |
| strategy | solution_strategy | STRATEGY |

`< / <= / = / >= / >` 映射到 `lt / lte / eq / gte / gt`，再到 `LT / LE / EQ / GE / GT`。API 不接受 Unicode ≥/≤、字符串 == 或第三条条件。未知项明确报错，不回退默认维度。空条件由前端路由到全批读取，不能偷偷补 DEFAULT_CONDITIONS。

## 3. 导入交互的决策

- 正式支持老师现有 Sheet1 依据＋Sheet2 学生记录块模板。演示长表仅留在 Demo 模式，不能作为正式上传的隐含替代模板。
- 正式分数 1～5、步长 0.01；原始单个评分保持不变。演示整数标准不作为实际导入校验依据。
- 空评语、重复评语是 warning；有 error 才阻止确认。前端 blocked 改为 summary.error_count > 0，而非 issues.length > 0；有 warning 时提供显式确认。
- 新预览 rows 可包含合法/缺失/非法评分行。非法文本可能含身份信息，score_raw 只返回安全数字词法或 null；原始内容仍在受控暂存，不通过错误消息回显。来源坐标足够定位回原文件纠正。
- V1入口为「上传测评文件」，不预选模板/维度/类型。上传可仅传file，后端默认安全处理的文件名作为title，预览后仍可PATCH改名。页面有当前教学空间时传data_scope_id；省略时后端仅自动使用唯一可导入范围，多范围为409 IMPORT_SCOPE_REQUIRED，不任意选择。见[V1_FILE_INTAKE.md](V1_FILE_INTAKE.md)。
- row_key 在该导入内稳定。问题可定位时同时返回 row_key 和 row_offset；定位跨分页时请求对应 row_offset 所在页，再高亮。row_offset 不存在时不显示假的跳转按钮。
- PATCH 只允许 title/course/assessment_date，并要求当前 preview_version。修改后生成新版本，保留 rows 和校验事实，确认必须使用新版本；不允许借此换文件、来源范围、学生、分数、标准或修订目标。
- PATCH/commit 在 committed、expired、版本失配时返回明确冲突/过期；重复上传命中已有批次时不覆盖其名称。需要不同评分的修正版仍走重新上传。
- 原上传文件名/路径不单独持久化或用于证据展示；默认生成的title是正式、获授权可读的元数据，非匿名字段。浏览器仍用本地File显示上传名，教师可在预览后改名。此处按新产品约定替代此前“仅本地显示文件名”方案。

## 4. 统计、热力图和精确值

Statistics 包含 batch_id、snapshot_id、rule_version、scope、display_policy、五维 dimensions 和可选 matrix。scope 明确是 batch 或 matched_group。每维有 valid_count/missing_count/invalid_count、mean/median/minimum/maximum 及 distribution。

统计量使用 `{value, exact, display}`：value 是绘图近似 number/null，exact 是 `{numerator,denominator}`/null，display 按 HALF_UP 两位小数生成。与 PR #9 的固定 half-up 保持一致：2.875 显示 2.88。人数和筛选不使用 display；超出 JSON 安全整数范围的 exact 暂拒绝序列化，另行升级协议，不静默损精度。

**exact.denominator 是有理数分母，不是统计样本量。** 每项分布另有 count、denominator（有效样本 n）和 ratio。无有效值时统计量 value/exact 为 null、display 为“暂无有效数据”；空群体是正常状态，不用 0 分均值代替。

热力图使用原分数实际出现的 x_values/y_values 和非零 cells，每格含 x/y/count，分母为本批配对有效记录数；当前正式批次五维完整，paired_count=N。未出现的组合仅在这两个已声明坐标轴内视为零，不能省略真实的小数分数。前端可反转 y 轴显示，不改变坐标值。step=0.01 不意味着必须画 401×401 的整数步长网格。

group 查询通过 include_statistics=true 获得匹配群体概况；矩阵仍调用全批 statistics。M=0 时 ratio=0、items=[]，附带群体统计的 scope.record_count=0，五维均值 null、分布为空。全批 N>0，不把“零匹配”误报为数据不存在。

## 5. 候选卡与证据回查

- insights/query 接收同快照下最多两条群体条件；空数组表示不生成能力组合卡，仍可返回分布/评价复核卡，不作为空条件传入筛选引擎。include_dimensions 默认 true；显式 false 可只请求群体/复核卡，均无结果时返回空列表。条件来自教师操作，不固化永久“高低能力”标准。
- 复用 discover_candidates/teacher_cards 和 metric_sources，不另写一套统计。服务端只使用来源范围和当前教师已获授权的完整批次，生成前重检权限；authorization_checked=true 本身不构成鉴权。
- 公开卡保留 candidate_type、status、title/summary、metrics、各指标分母、条件、scope、snapshot_id、规则/适配版本、limitations 和 controlled_query。内部枚举转英文 API 编码，不直接透传 frontend_dimension、RecordRef、source_id 或 location。
- 本轮仅教师内部展示，public_display_allowed 恒为 false。内部公开限制可作为解释提示，不能作为真实公开许可。候选响应不返回学生名单、完整评语或个人来源坐标。
- controlled_query.kind 只允许 dimension_records/matched_group_records/review_records，绑定服务端候选和快照，不接受任意 SQL、原始 locator 或额外学生列表。
- records/query 中 metric_key 非空时使用 metric_sources(candidate,key)；null 时使用 candidate.source_refs。所有记录按学生去重，证据还需匹配指标维度、种类与本次快照。不能把分母 scope.records 或其他指标的证据拼入该指标来源。
- 每个参与记录只返回匿名标识、display_label、核验后的 evidence_ids/criterion_ids 与 evidence_status。名单存在但证据缺失时显示 missing，不编造 ID；已扣留评语不返回其可展示引用。评分/评语正文继续走受控证据接口。
- comment_analysis_status 区分 ready/not_configured/withheld。未接评语提供器不返回“0 个异常”冒充已复核。发现卡的 observed 是统计观察，review 是待人工核查，不等于教学原因或评价错误已经证实。

## 6. 快照、分页和失效

Batch、新统计、群体、候选和证据响应携带 snapshot_id。它覆盖评分、标准、评语、来源绑定和脱敏投影的版本，不能复用文件哈希充当；权限始终逐请求重检，不靠快照 ID 授权。

新查询中 snapshot_id 可省略以取得当前快照；同一操作后续页、详情和候选回查必须传回已有快照。过期或不匹配返回 409 SNAPSHOT_STALE，候选缓存过期且无法安全重建返回 409 CANDIDATE_STALE；清除旧页和旧抽屉后重新获取，不能将旧名单配新人数。

切换批次时取消/忽略旧请求。当前学生页不是完整批次，不能交给浏览器 mean/filterStudents/heatmap 再算服务器事实。批次列表总数、质量总数、匹配总数都使用相应响应字段。

## 7. 保留设计与功能状态

- service 明确区分 Demo 与服务端模式。真实 API 请求失败时显示真实失败状态，不自动退回 DEMO_BATCH 或本地算式冒充服务端成功。
- 三个业务页、图表和抽屉布局由林明保留；只替换 service、状态和数据来源。
- 首页固定 S008、固定群体和 includes 主题检索仅用于 Demo。服务端模式以真实候选和受控查询替代，不伪造主题分类结果。
- 个体分数表格按需加载证据，加载中、空数据、withheld、无权限、预览过期、快照失效和服务器失败分别显示，不复用同一“0 条数据”。
- VerifiedTag 只能表达按当前输入核算的统计状态，不能把 observed、结构/引用校验通过渲染成能力诊断或教学成因已经证实。
- 现有 AI 面板仍是明确标记的规则模板；真实上传不能接现有 synthetic-live 模式，不改变 Production 保护。
- 导出和教师补充判断是后续任务，不在本次接口对齐里虚构已可用的按钮行为。

## 8. 交接验收与待确认

使用 OpenAPI 的合成示例，不依赖开发者讲解即可演示：预览警告可确认、非法评分可定位、名称修改更新版本、同批小数分布/矩阵完整、重置条件显示全批、两维筛选人数与名单一致、零匹配、按指标取证、快照失效，以及 withheld 与空评语有区别。

本轮是后端提出的可审阅适配方案，不声称林明或 ZAMYL 已确认。最终需三方确认：新增三项操作（元数据 PATCH、候选查询、指标参与记录回查）、统计展示 HALF_UP 两位与图表近似值、前端分页视图模型、名称修改及问题定位交互。认证签发、上传限额和保留期限另行确认；这些事项不通过本文件自动变成已实现功能。
