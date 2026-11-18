# 导出前校验与本人请求诊断

> ⚠️ 本文部分内容已被 V1.4 取代，以 docs/V1.4_BASELINE.md 为准。

对应 Issue #31；基于 PR #25 的已保存分析结果，作为后续 PR 单独审阅。
本次新增 2 个 API，含 PR #25 为 27 个；同时组合 PR #30 的前三个诊断接口后为 30 个（含 health）。
不增加业务数据库迁移或 Python 依赖，不修改前端页面。请求记录采用单独的运维数据库。

## 导出前校验

`POST /api/v1/edu/batches/{batch_id}/analysis-results/{result_id}/export-check`

请求使用 Bearer 凭证、`Content-Type: application/json`：

```json
{"snapshot_id":"当前批次快照 ID","expected_version":2,"format":"zip"}
```

`snapshot_id`、正整数 `expected_version` 必填，format 为 json/zip/xlsx/md，默认 zip；禁止额外字段。
先鉴权，再读取有大小限制的正文；在只读事务中重新核验当前教师凭证、来源范围和批次读取权。
批次所有者和被授予读取权的教师都可以预检已有结果。

与真实下载共用检查：批次完整性、快照、结果版本、保存载荷校验和、人数、完整五维评分、
证据 ID 唯一性、证据对应的学生/维度/分值/快照，以及当前已发布评语和评分依据。
JSON 载荷上限沿用 8 MiB；json/zip 预检在内存组装 JSON 测量 UTF-8 字节数，不生成 CSV/ZIP。
xlsx/md 另复用正式报告适配与完整内存生成，测量所选文件，新增 report_payload_bytes/
max_report_payload_bytes（每文件8 MiB），并在生成后重查当前授权/核验版本；不落盘或锁定状态。
旧格式响应不新增 null 字段。member_count 是实际匹配人数，不是新汇总报告的逐人输出行数。
细节与限制见 [正式报告下载契约](FORMAL_REPORT_DOWNLOAD_API.md)。

成功 200 返回：batch_id、snapshot_id、result_id、result_version、format、exportable=true、
member_count、confirmed_finding_count、json_payload_bytes、max_json_payload_bytes、warnings 和说明。
`json_payload_bytes` 是检查当时 JSON 的字节数，**不是 ZIP 大小或最终下载大小承诺**。

| warnings.code | count 含义 |
|---|---|
| PENDING_FINDINGS_EXCLUDED | 不进入已确认发现列表的待核验发现数 |
| DISMISSED_FINDINGS_EXCLUDED | 不进入已确认发现列表的已否决发现数 |
| WITHHELD_COMMENTS_EXCLUDED | 匹配群体中未发布/已撤回的评语数（按证据计） |
| WITHHELD_RUBRIC_EXCLUDED | 未发布/已撤回的评分依据数（按维度计） |

只返回非零警告。警告不阻止按既有文本策略导出，不自动确认发现、不读取或返回原始私密正文。
失败保留真实 HTTP 状态：401 凭证失效；404 不存在或没有读取权；409 快照/版本不一致；
413 EXPORT_TOO_LARGE；415/422 输入不符；503 DATA_INTEGRITY_ERROR 或存储不可用。
没有额外的 `exportable=false` 成功响应；客户端应依据统一错误结构展示阻断原因和 request_id。

预检不锁定状态。教师修改结果、撤回文本或权限变化后，实际 export 接口仍重新检查。

## 本人请求诊断

`GET /api/v1/edu/diagnostics/requests/{request_id}`

需要当前有效教师凭证。只查询该教师本人完成鉴权的请求元数据，不是运维日志搜索接口。
request_id 必须为服务端产生的 `req_` 加 32 位小写十六进制字符。
成功返回以下固定字段：

```json
{
  "request_id":"req_00000000000000000000000000000000",
  "route_template":"/api/v1/edu/batches/{batch_id}/analysis-results/{result_id}/export-check",
  "method":"POST",
  "status_code":409,
  "error_code":"ANALYSIS_RESULT_VERSION_MISMATCH",
  "stage":"conflict",
  "duration_ms":12,
  "created_at":"2026-10-25T00:00:00.000000Z",
  "expires_at":"2026-11-01T00:00:00.000000Z"
}
```

stage 为状态码映射的粗粒度分类：completed、authentication、authorization_or_resource、
conflict、validation、limit、service、server、request，不代表异常栈或精确内部执行位置。
耗时记录到服务端取得响应、准备写诊断记录时，不含网络传输时间，最大显示 3,600,000 ms。

不存在、其他教师、已过期、被容量淘汰和格式不符的 ID 均为相同安全 404；凭证失效为 401；
未启用或诊断存储不可用为 503 REQUEST_TRACE_UNAVAILABLE。
记录仅包含上述响应字段和内部访问隔离用的 teacher_id，不包含原始 URL/查询串、请求正文、
响应正文、请求头、凭证、姓名学号、路径参数、原始错误信息或异常栈。
路由只从服务端已注册的模板获取。所有 EDU 响应均保留 Cache-Control: no-store。

只记录完成鉴权且命中已注册路由的请求，包括其后的业务失败；未鉴权、鉴权失败、健康检查、
未知路由和诊断查询本身不记录。因此不能通过此 API 排查最初的 401 或未路由的 404/405。
成功记录的原业务响应带 `X-Request-Trace: recorded`；未启用为 disabled，写入失败为 unavailable。
诊断查询自身没有该头。原业务 HTTP 状态和响应内容不因诊断写入失败而改变。
每次请求独立事务写入，跨进程可查询，重启不丢失未过期记录；不自动重试业务请求。

## 服务端启用与保留期限

默认关闭。运维必须先显式初始化**仓库外、绝对路径**的专用 SQLite 文件：

```powershell
python -m app.edu.trace_admin --database 'D:\weiran-runtime\request-traces.sqlite3' init
$env:WEIRAN_EDU_REQUEST_TRACE_DATABASE='D:\weiran-runtime\request-traces.sqlite3'
```

随后重启后端。也可通过 Settings.request_trace_database_path 显式传入。
禁止与 WEIRAN_EDU_DATABASE 使用同一文件；初始化命令拒绝含业务表的数据库。
HTTP 请求不创建目录/文件、不自动初始化或迁移。诊断记录写入不改写业务数据库。
客户端跨域如需读取新响应头，部署层 CORS expose_headers 应增加 X-Request-Trace。

保留期限为 7 天，每教师最多 1,000 条、全库最多 20,000 条，超限时优先淘汰旧记录。
查询严格排除过期记录；新记录写入时会删除过期记录。低流量环境需定期显式执行：

```powershell
python -m app.edu.trace_admin --database 'D:\weiran-runtime\request-traces.sqlite3' cleanup
```

物理删除依赖写入或 cleanup，不承诺 SQLite 文件内的安全擦除；数据库文件应置于仅服务端账户
可访问的目录，不进入仓库、前端交付包或学生数据导出。本实现不是永久审计日志。
诊断写锁等待上限 100 ms，失败按 unavailable 返回，不阻断已完成的业务操作。

## 验证范围

合成数据测试覆盖预检及真实导出的共同守卫、严格正文、权限撤销、空群体、警告计数、
评分/证据篡改、超限，及本人隔离、过期和容量、记录脱敏、持久化、显式初始化、诊断库损坏/
锁定/未配置/误配业务库时的故障隔离。与 PR #30 组合后还需运行其只读诊断回归。
