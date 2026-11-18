# 林明接入契约：上传、质量检查、确认后自动诊断

> ⚠️ 本文部分内容已被 V1.4 取代，以 docs/V1.4_BASELINE.md 为准。

日期2026-10-27；随PR #65交接补丁提供，合并部署前不能假设旧main已有新增字段。前端及路由仍由林明实现，本PR不改apps/web。完整前缀为 `/api/v1/edu`，继续使用现有教师会话/受控代理，不引入新认证系统。

## 1. 请求顺序

| 操作 | 接口 | 服务端约定 |
| --- | --- | --- |
| 上传 | POST `/imports/preview`，multipart | 仅file必填；title默认安全处理的文件名；课程/日期选填；不选模板/维度数/类型/映射/规则 |
| 读取/翻页 | GET `/imports/{import_id}` | ImportView：名称、状态、全文件recognized_items/summary及分页rows/issues/student_samples |
| 完整质量 | GET `/imports/{import_id}/quality-report` | 默认JSON；完整计数、问题类别、定位、阻断和确认要求 |
| 可选改名 | PATCH `/imports/{import_id}` | 当前preview_version及title；用返回的新版本替换本地版本并清除旧确认状态 |
| 确认 | POST `/imports/{import_id}/commit` | 当前preview_version及布尔acknowledge_warnings；成功返回batch_id/snapshot_id |
| 自动诊断 | 用确认返回的引用加载诊断接口 | 直接进入学情洞察，不再让教师选批次 |

页面有当前教学空间时传data_scope_id；省略时仅能自动使用唯一可导入范围。多范围无上下文为409 IMPORT_SCOPE_REQUIRED，不随意选第一个。详细默认值见[V1_FILE_INTAKE.md](V1_FILE_INTAKE.md)。

## 2. 文件及解析状态

| HTTP / 状态 | 展示与行为 |
| --- | --- |
| 上传请求未结束 | 前端本地显示“正在处理”；目前没有异步任务ID、parsing状态或百分比进度 |
| 201 / import.status=preview_ready | 真实解析后的预览，可查看质量报告；还未正式入库 |
| 201 / import.status=invalid | 已识别部分内容但有阻断问题，显示缺失项及原文件定位；修改文件后重传 |
| 200 / committed，preview_available=false | 暂存已清理，recognized_items、rows、issues、student_samples为null，改用正式批次数据 |
| 410 / IMPORT_EXPIRED | 未确认预览过期，提示重新上传 |
| 422 / IMPORT_FORMAT_UNSUPPORTED | 当前不支持此工作表结构，不创建假预览/假批次 |
| 415 / UNSUPPORTED_MEDIA_TYPE | zip/html/xls等未支持容器或不符媒体类型 |
| 503 | 解析繁忙、超时或服务/数据库故障，展示真实失败并允许重试，不切回Demo冒充成功 |

201只代表预览建立，invalid不可提交。已有提交合法重放不因暂存清理而重新解析。

## 3. 名称、已识别项目和参评记录数量

| 字段 | 含义 |
| --- | --- |
| import.title | 测评名称，默认文件名，可修改；文件名不证明测评类型或评分语义 |
| recognized_items | 新增：后端从完整质量结果生成的实际已识别评分项目列表，所有预览页一致 |
| recognized_items[].item_id | 当前适配器等于原dimension_code；交互按服务端值传递，不是授权凭据 |
| display_name / kind | 中文名称及种类，当前仅competency；没有宣称question/task通用上传已经实现 |
| scale.minimum / maximum / step | 当前已验证适配器提供1–5/0.01；不由样本最大值推断 |
| score_row_count | 全文件该项目评分行数，含缺失和非法评分，不是有效人数 |
| summary.detected_student_count | 已识别参评记录数；当前按学生数据块计数，含重复/无效块，不冒充去重人数 |
| summary.complete_student_count | 结构完整的参评记录数，不是能力分数 |
| summary.parsed_evaluation_count | 已解析评分行数，含缺失、非法和未知维度行，不显示为合法评分数 |
| quality.summary.valid_score_count / missing_score_count / invalid_score_count | 合法、缺失、非法评分的后端全量计数 |

一个项目对象的真实结构：

```json
{
  "item_id": "algorithm_data_structure",
  "display_name": "算法与数据结构",
  "kind": "competency",
  "scale": {"minimum": 1, "maximum": 5, "step": 0.01},
  "score_row_count": 2
}
```

项目没有出现时不补占位；非法旧模板识别到四项不代表已支持四维导入。十三题不转成五维，原项目和量表扩展需单独验证。

## 4. 质量类别、全量数量和定位

| 字段 | 使用规则 |
| --- | --- |
| quality.status | passed / warning / blocked，由后端判定 |
| validation_passed | 当前文件验证通过，不代表已入库或始终有权限 |
| requires_warning_acknowledgement | 有提醒需明确确认；blocked即使为true也不能导入 |
| issue_groups[].code / message | 结构化问题类别及受控中文；显示message，code供诊断 |
| severity | error阻断，warning在验证通过时可确认，不按中文文案猜测 |
| occurrence_count | 此类问题完整出现次数，不等于受影响人数 |
| affected_student_count / affected_row_count | 此类别受影响参评记录数/行数，不跨类别相加作为总人数 |
| examples | 每类最多5个示例，非全部问题 |
| issues[].sheet_name / row_number / cell | 原文件实际工作表、行和单元格，帮助修改后重传 |
| issue_id / row_key / row_offset | 问题标识、预览行引用及全局偏移；row_offset有值时GET相应行页定位，null时不伪造跳转 |
| preview_student_id / display_label | 暂存内匿名记录引用，不当作正式student_id |

当前实际问题码为：STUDENT_IDENTITY_CONFLICT、STUDENT_IDENTITY_INVALID、STUDENT_DUPLICATE、BLOCK_HEADER_INVALID、DIMENSION_UNKNOWN、DIMENSION_MISSING、DIMENSION_DUPLICATE、SCORE_REQUIRED、SCORE_NOT_NUMERIC、SCORE_INVALID、SCORE_OUT_OF_RANGE、SCORE_PRECISION、COMMENT_TYPE_INVALID、COMMENT_EMPTY、COMMENT_REPEATED、UNEXPECTED_DATA_COLUMN、ORPHAN_ROW、NO_STUDENTS、TITLE_ROW_INVALID、RUBRIC_MAPPING_INVALID、RUBRIC_REQUIRED。以响应severity为准；目前COMMENT_EMPTY/COMMENT_REPEATED是warning，其余为error。

预览分页参数为row_offset/row_limit、student_offset/student_limit、issue_offset/issue_limit，limit最大100。summary、recognized_items和质量计数按全文件；rows.length、当前页issues.length、examples.length均不得作为总体数量。质量JSON返回完整issues及分组，当前没有新增类别分页参数。

前端可依据服务端状态控制提交按钮，最终合法性以commit为准：未确认提醒为409 WARNINGS_NOT_ACKNOWLEDGED；存在error即使acknowledge_warnings=true仍为409 IMPORT_NOT_CONFIRMABLE；旧preview_version为409 PREVIEW_VERSION_MISMATCH。缺必要信息只提示修改后重传，不增加在线映射/评分规则表单，不补零、不丢弃错误记录。

## 5. 确认成功后自动打开诊断

三种成功响应均含import_id、batch_id、**snapshot_id（新增）**、result。snapshot_id从当前授权的持久化批次读取，与确认/重放处于同一事务；不能用文件哈希、preview_version或前端UUID代替。

| HTTP / result | 自动打开 |
| --- | --- |
| 201 / created | 新批次的batch_id/snapshot_id |
| 200 / duplicate | 已有批次的batch_id/snapshot_id |
| 200 / replayed | 原确认的同一批次/快照 |

前端清理旧页和旧发现状态，将返回引用设为当前批次，直接进入本批次「学情洞察」；具体页面路由由前端决定，后端不新增页面URL字段：

```text
commit成功 → currentBatch={batch_id,snapshot_id} → 打开学情洞察
  GET /batches/{batch_id}                    名称、计数、is_current
  GET /batches/{batch_id}/capabilities       当前允许的操作
  GET /batches/{batch_id}/statistics?snapshot_id=确认返回值
  POST /batches/{batch_id}/discoveries/query
       {"snapshot_id":"确认返回值","scope_conditions":[]}
```

不再查询批次列表让教师选择，不用列表第一个批次。重复命中历史修订时打开实际返回的历史批次，按is_current标明，不能偷偷切到另一份最新修订。共享重复批次仍按原读取授权展示，不因导入而获得拥有者权限。

后续读取失败时保留当前引用并显示真实加载失败，可重试；发现200且items为空是正常结果，不是导入失败。切批次时取消/忽略旧请求，不把旧发现配到新数据。撤权401/404或快照不符409时停止沿用旧结果，不回退Demo；确认引用不构成授权凭据，诊断仍校验权限、快照、完整性及来源。

确认响应丢失时按同一import_id和preview_version重试，接受replayed，不重新上传制造批次。任何非2xx不能当作成功跳页。

## 6. 未支持文件的明确错误

```json
{
  "error": {
    "code": "IMPORT_FORMAT_UNSUPPORTED",
    "message": "当前暂不支持此文件结构，未创建测评批次",
    "request_id": "req_由服务端生成"
  }
}
```

HTTP422；请求ID也在X-Request-ID，不回显原文、身份、异常栈或凭证。保留打印设置、未知二进制、公式、宏、外链、损坏和大小检查。历史ZIP/HTML未经正式验证不宣称兼容。

## 7. 合成样例与验收

[实际HTTP合成响应](contracts/edu-import-handoff.synthetic.json)包括预览、质量通过/提醒/阻断、created/duplicate/replayed、版本冲突和未支持结构。包装层http_status/cases/synthetic_only只是样例说明，不是API字段；示例UUID/哈希不能引用当前服务，必须读取真实响应。

后端验收使用真实FastAPI、隔离解析子进程和发现Worker，不靠前端Mock推算。数值、完整分母及合法性由后端决定；前端负责状态、中文、分页、图表和导航，不重新统计评分、推断满分、补造缺失或用AI解释改写事实。浏览器接线验收仍由林明完成。
