# 正式教师报告下载 API（P1）

基于 main 028493d（#25/#28/#30/#32/#33/#34 均已合并）。
本轮仅扩展后端下载与预检，不改前端、评分算法、导入解析、认证或数据库结构。
这是第五步的可选 P1 增量，不是 Issue #26 的 P0 浏览器联调前置条件。

## 原路径增加两种格式

```http
GET /api/v1/edu/batches/{batch_id}/analysis-results/{result_id}/export?snapshot_id={snapshot_id}&expected_version=2&format=xlsx
Authorization: Bearer <现有教师凭证>
```

format 支持 json、zip、xlsx、md，大小写严格；为保持兼容，省略时仍默认 zip。
JSON/ZIP 的内容与权限不变（原有内部格式仍含完整匿名群体及已发布展示文本）。
**新 XLSX/MD 格式始终仅汇总和教师 confirmed 发现，没有逐人明细开关。**
教师第五步新增报告选项时应显式选择 xlsx/md，不能省略 format 后假定收到汇总报告。
只接收保存结果 ID、快照、版本与格式，不接受客户端统计、候选、权限或 confirmed 声明。

| format | Content-Type | 扩展名 |
| --- | --- | --- |
| xlsx | application/vnd.openxmlformats-officedocument.spreadsheetml.sheet | .xlsx |
| md | text/markdown; charset=utf-8 | .md |
| json | application/json | .json |
| zip（默认） | application/zip | .zip |

成功响应 200，Content-Length 为实际完整文件字节数，带：

```http
Content-Disposition: attachment; filename="weiran-analysis-<服务端结果UUID>-v2.xlsx"
Cache-Control: no-store
X-Content-Type-Options: nosniff
X-Request-ID: req_<服务端生成的32位十六进制>
```

文件名仅由服务端结果 ID、版本和格式生成，不使用课程名、教师备注、上传文件名或路径。
文件在内存完整生成并通过大小、权限及版本复查后才构造下载响应，不流式返回半份文件。
错误仍为统一 application/json（error.code/message/request_id），没有附件头；前端不能把错误 JSON 当文件保存。

## 导出预检保持共用规则

```http
POST /api/v1/edu/batches/{batch_id}/analysis-results/{result_id}/export-check
Authorization: Bearer <现有教师凭证>
Content-Type: application/json

{"snapshot_id":"当前评分快照","expected_version":2,"format":"xlsx"}
```

请求仅扩展 format 枚举；正整数版本、鉴权先于解码、严格 JSON/未知字段拒绝等行为保持。
旧 json/zip 成功响应不新增 null 字段，字段及 note 含义保持。
新 xlsx/md 除原有字段外增加：

以下为17人完全合成回环 HTTP 验收的响应字段摘录，省略批次/结果 UUID、旧 JSON 字节数与 warnings：

```json
{
  "result_version": 2,
  "format": "xlsx",
  "exportable": true,
  "member_count": 9,
  "confirmed_finding_count": 2,
  "max_json_payload_bytes": 8388608,
  "report_payload_bytes": 15495,
  "max_report_payload_bytes": 8388608,
  "note": "预检在内存完整生成报告并测量所选格式，不保存文件或锁定状态；实际下载仍复核权限及核验版本。仅汇总，不导出匿名逐人明细。"
}
```

member_count 是保存结果的实际匹配群体人数，**不是新报告输出的个人明细行数**。
json_payload_bytes 继续表示共用检查时构造的 JSON 载荷字节数，不是 XLSX/MD 大小。
report_payload_bytes 表示本次预检所选格式的实际字节数，不是随后下载大小或权限的承诺。
新格式预检会在内存生成同一对报告以验证格式/布局/资源预算；不落盘、不缓存供后续下载复用。
生成时间发生变化时 XLSX 压缩字节数可能变化；实际下载必定重新检查。

原 warning.code 保持；报告进一步排除所有自由文本（包括已发布展示文本），并非只排除 withheld。
预检不会改分、自动确认、修改版本或写入业务表；原有可选安全请求诊断仍正常记录请求元数据。

## 可信来源与清晰适配

正式代码入口在 app/edu/exports.py、app/edu/report_exports.py，路由实际注册于
app/routers/edu_reviews.py（原路径不新增 HTTP 操作），预检仍在 edu_support.py。

1. 共用 _prepare/_current：按当前教师凭证、来源成员、批次 owner/read 权限、snapshot、
   expected_version 读取完整 AnalysisResult；检查保存载荷 SHA-256、当前批次完整性和保存成员证据归属。
2. 在同一个授权只读快照中，复用 AnalysisReader._load/_statistics 和 select_students，
   从正式完整批次核对保存的全批/群体统计、条件、成员分值和 evidence_id；不另写统计公式。
3. from_statistics 规范化完整统计与现有 V1 量表/依据版本。群体直接构造内部 GroupReport，
   数据来自本次完整匹配集、N/E/M 与真实统计；**不伪造 GroupResult 分页或 limit/has_more**。
4. with_reviewed_findings 校验同批同快照/条件、保存候选归属、当前教师判断版本、规则白名单、
   精确指标、分母与实际参与记录数；仅 confirmed 投影，0条时准确展示空状态。
5. generate_report 使用默认汇总模式；没有构造 DetailAuthorization 或开放个人详情参数。
   原有字符串写入、公式/外链禁止、Markdown 转义和精确值保留继续适用。
6. 渲染结束后开启新的只读事务，再用同一权限与版本检查复核撤销、过期和教师版本变化，
   并确认保存分析载荷未变。预检与下载均经过这一步，不沿用生成前的授权快照。

XLSX/MD 不包含姓名、学号、匿名学生逐人 ID/证据列表、上传文件、原始评语、
未审核依据、教师/候选自由描述、个人备注、Key 或内部路径。依据正文即使已发布也不接入；
保留受控维度名称、V1 1—5/0.01 量表、依据版本、结果/候选引用、快照和规则版本。
教师确认只是核验判断，不证明能力等级、因果或教学效果；不运行任何 Qwen/AI。

## 失败、预算与限制

| 状态/错误码 | 前端处理建议 |
| --- | --- |
| 401 UNAUTHORIZED | 停止下载，按现有凭证流程重新认证 |
| 404 RESOURCE_NOT_FOUND | 结果不存在、跨批次或已无读取权；刷新当前可访问列表 |
| 409 SNAPSHOT_MISMATCH | 刷新当前批次与所选保存结果 |
| 409 ANALYSIS_RESULT_VERSION_MISMATCH | 重新读取结果与核验版本，请用户确认后再下载 |
| 413 EXPORT_TOO_LARGE | 当前同步导出预算不足；不要把部分内容当成功文件 |
| 422 VALIDATION_ERROR | 校验格式/参数，不自动忽略失败 |
| 503 DATA_INTEGRITY_ERROR / DATABASE_UNAVAILABLE | 暂停导出并使用 request_id 联系后端核查 |
| 500 INTERNAL_ERROR | 展示失败状态，不回显或下载内部异常 |

共用 JSON 检查上限8 MiB（包括原有当前审核投影），新报告每种文件上限8 MiB，
现有报告行/单元格预算保持；内部完整匹配成员最多5000，不截断清单伪造通过。
生成器当前一次生成 XLSX/MD 两种表示再选择返回；任一种触发预算即整次失败。
新报告相较旧 JSON/ZIP 还要验证报告规则/指标与当前完整批次，因此旧版本或损坏的保存事实可能被拒绝，
不通过降级到未确认的“正常报告”绕过失败。无公开分享、签名证明、加密或远程撤回本地文件功能。
最终检查后与网络发送之间无法构造跨客户端事务；下载完成后的文件仍需教师妥善保管。

## AooMing 浏览器接入说明

- 保持 Issue #26 第一阶段和旧 json/zip 流程，P1 按可选的 xlsx/md 按钮接入，不把其作为前置门槛。
- 使用现有鉴权请求封装，传当前 batch_id、保存 result_id、snapshot_id、expected_version 与格式。
  不把 Token 放进 URL、DOM 下载链接、日志或文件名；不提交模型输出/客户端统计。
- 可先 POST export-check 提示 pending/dismissed 等状态，然后 GET 同格式。预检通过不代表后续一定成功。
- 检查 response.ok 后再读 blob；失败按统一 JSON 展示 code/request_id，不能对全部响应无条件下载。
- 成功使用 Content-Disposition 的安全文件名，创建 Blob URL 下载后 revokeObjectURL；提示教师内部用途。
  本轮没有修改或交付任何前端按钮。
- API 主应用当前未自行配置 CORS；使用现有同源代理/部署封装。若跨域，需要既有运维配置允许
  Authorization/POST，并暴露 Content-Disposition、X-Request-ID；本 PR 不扩张 CORS 或认证策略。

请 Peng-Kai789 重点审核：正式服务修改、同快照统计/证据装配、生成后权限撤销/版本复核，以及原 JSON/ZIP 兼容。
接口验收后由 AooMing 接入第五步；不表示其浏览器验收已经完成。

## 合成验收

测试入口（apps/api）：

```powershell
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest tests/edu/test_report_download.py tests/edu/test_export_check.py tests/edu/test_reviewed_results.py tests/reporting -q -ra
uv run --locked pytest -q -ra
```

新增测试全部通过正式 ASGI HTTP 路由使用合成上传与临时数据库；检查四格式一致性、1/17/53/105人、
2.75、空群体/零确认、错快照/版本、跨批次/教师、授权与凭证撤销/过期竞态、真实完整成员、
文件预算、生成异常、私有列禁止读取、报告重新打开及公式/外链禁止。
另已执行临时127.0.0.1 Uvicorn + httpx 真正 TCP HTTP：合成17人→上传/确认→保存→确认2张卡→
预检/下载；M=9、N=17、比例9/17=52.94%，算法均值49/17≈2.88。
XLSX 下载15496字节、MD 下载5368字节；当时 XLSX 预检测量15495字节，生成时点变化导致压缩差1字节，
再次验证实际下载独立测量而非信任预检大小。样例不代表真实班级、固定人数或固定结论。
实际 pytest/CI 数量以本 PR 最终提交结果为准；没有做前端浏览器下载或原生 Excel 人工验收。