# 就绪、权限与完整性诊断 API

对应 Issue #27。独立于待审 PR #25，无新迁移、依赖、自动修复或前端改动。
在当前 main 的 18 个 API 操作上新增 3 个，共 21 个；与 PR #25 组合后为 28 个。以运行时 OpenAPI 为准。

## 1. 服务就绪检查

`GET /api/v1/health/ready` 不要求教师凭证，避免数据库故障时无法通过数据库认证来检查状态。

- `200 {"status":"ready"}`：数据库可以打开，代码随附迁移与已应用版本/校验和完全匹配，并存在迁移声明的表、索引、触发器。
- `503`：统一错误结构，`error.code=SERVICE_NOT_READY`，固定安全提示及服务端 request_id；不返回路径、版本清单、校验和、SQL 或异常栈。
- 固定内部原因码只写服务端日志：数据库未配置/不可用、迁移不匹配、必要结构缺失。日志不包含路径、凭证或异常原文。

不创建数据库/目录、不执行迁移、不写任何业务记录，也不扫描原始文件或整库数据。
未配置数据库、尚未初始化、缺失迁移、校验和错误、未知版本或必要结构缺失时均不就绪。
就绪只代表结构可服务，不代表每个批次的数据都健康；批次事实由完整性接口检查。
原有 `/api/v1/health` 继续用于进程存活检查，即使数据库未就绪也可返回 200。

## 2. 当前教师操作权限

`GET /api/v1/edu/batches/{batch_id}/capabilities?snapshot_id=...`

需要有效 Bearer 凭证及当前批次读取权；snapshot_id 可选，提供时必须匹配。
返回 batch_id、snapshot_id、role=owner/reader 与 operations。每项含 action、available、allowed、reason：

| action | 规则 |
| --- | --- |
| read_batch | 当前拥有者或被显式授权且仍是来源成员的读者 |
| import_data | 当前来源成员的 can_import，与批次拥有者身份分开 |
| diagnose_integrity | 仅批次拥有者 |
| review_text | 对应审核路由已注册且为拥有者 |
| save_analysis / edit_analysis | 对应保存/修改路由已注册且为拥有者 |
| export_analysis | 导出路由已注册且具有批次读取权 |

reason 为固定枚举：allowed、owner_required、feature_unavailable、import_permission_required。
当前 main 尚未合并 PR #25 时，审核/保存/导出明确标记 feature_unavailable；组合注册其实际路由后自动反映权限，不在诊断分支伪装成已交付。
不允许客户端通过参数指定教师身份或功能开关。

这里回答“有没有权限及功能入口”，不证明数据健康、已有可导出结果、文本已发布或预算足够。后续操作仍独立核验，不能把此结果当作长期授权缓存。
能力查询不运行事实校验，因此损坏批次仍可先区分权限问题；无权批次始终返回通用 404。

## 3. 拥有者完整性诊断

`POST /api/v1/edu/batches/{batch_id}/diagnostics/integrity`

```json
{"snapshot_id":"来自当前正式批次的快照"}
```

仅批次拥有者可调用。使用现有严格 JSON 解码，限制 64 KiB，拒绝重复键、未知字段、非法快照及任何 repair 参数。
在同一只读事务中依次复核当前凭证、批次访问权、拥有者资格、快照，然后读取完整批次的事实元数据。
不调用普通查询的提前失败入口，故损坏批次也可得到逐项报告；普通统计/学生/证据等查询继续返回 `503 DATA_INTEGRITY_ERROR`。

成功执行诊断返回 200；检查失败通过 `passed=false` 表达，不能把“HTTP 成功”当成“数据健康”。

返回包含 batch_id、snapshot_id、checked_at、passed、repaired=false、scope=stored_fact_metadata，以及预期/实际学生数、评分数和十项检查：

| code | 检查 |
| --- | --- |
| STUDENT_COUNT | 成员数与批次学生数 |
| MEMBER_SCOPE | 成员存在且与批次来源范围一致 |
| RUBRIC_DIMENSIONS | 评分标准五维完整且唯一 |
| RUBRIC_SOURCE | 标准摘要、Sheet1 与固定范围 |
| EVALUATION_COUNT | 评分数、批次缓存与每人五维数量 |
| EVALUATION_MEMBERSHIP | 评分归属学生及评分标准 |
| STUDENT_DIMENSIONS | 每人五维完整且唯一 |
| SCORE_RANGE | 整数评分单位在 100—500 |
| EVIDENCE_SOURCE_HASH | 评分证据与批次来源摘要 |
| EVIDENCE_COORDINATES | Sheet2、行号、B/C/D 坐标及来源行唯一性 |

每项含固定 code/message、passed、violation_count。计数表示各规则的失败行/项，同一损坏可能触发多项，不能相加当作独立根因数。报告不回显数据库中的任意字段值、内部主键、学生身份、原文、文件路径或凭证。
检查规则与普通事实读取共用 `inspect_snapshot`，不维护另一套口径。

范围是存储元数据关系，不重新解析 Excel，不校验 SQLite 文件的每个磁盘页，不声称能抵抗持有数据库管理权限者的一致性伪造；不修改评分或补齐缺失记录。

## 错误与联调

- 401：凭证失效、撤销或教师停用；403：有批次读取权但不是诊断拥有者；404：无权或资源不存在。
- 409：有相应权限后发现 snapshot_id 不匹配；拥有者资格检查先于诊断快照比较，读者不能绕过 403。
- 413/415/422：请求超限、媒体类型或输入错误；503：数据库/结构无法读取，不伪装为完整检查结果。
- 所有成功及失败包含服务端 X-Request-ID、Cache-Control: no-store。

建议前端先检查 ready，再读取 capabilities，拥有者按需执行完整性诊断。ready 失败可用 request_id 查看服务端固定原因码；本轮不增加请求追踪查询接口。

测试仅使用合成工作簿和临时数据库，覆盖正常/损坏/缺库、迁移校验、结构缺失、拥有者/读者/未授权者、撤销竞态、只读同事务、私有列禁读与原有事实查询失败行为。当前 PR 的实际检查和 CI 以其最新提交为准。
