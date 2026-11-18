# 受控文本、教师核验与导出 API

> ⚠️ 本文部分内容已被 V1.4 取代，以 docs/V1.4_BASELINE.md 为准。

关联 Issue #24；建立在已合并的 PR #19、#22 上。仅开发后端，不表示前端已完成接入。
新增 7 个接口，配置数据库后共 25 个操作（24 个业务操作和 1 个健康检查）。以运行时 `/openapi.json` 为准。

## 权限、事实与展示版本

所有请求均需有效 Bearer 教师凭证，复核教师状态、来源成员和批次读取权。
批次拥有者可查看脱敏待审文本、发布/撤回展示、保存分析和修改核验；显式授权读者只能读取已发布文本、已保存结果与内部导出。外部用户/未授权批次返回 404；有读取权但无写入权返回 403。

评分、原始文本及 Excel 来源不改写。`snapshot_id` 标识评分事实；文本展示由 `comment_review_version`/`criteria_review_version` 独立标识；教师核验结果由 `version` 标识。
撤回展示与修改教师判断均追加新历史记录，不更新旧版本。新批次修订不能复用旧批次的证据、标准或分析结果 ID。

所有成功和失败返回 `Cache-Control: no-store`、服务端 `X-Request-ID`。写入请求使用有 64 KiB 实际流量限制的严格 JSON 解码，拒绝重复键、未知字段及伪造的统计、学生名单或证据。

## 1. 审核评语和依据

路径前缀：`/api/v1/edu/batches/{batch_id}`。

| 方法与路径 | 作用 |
| --- | --- |
| `GET /text-reviews/{kind}/{target_id}?snapshot_id=...` | 仅拥有者读取脱敏待审文本、当前发布状态和版本 |
| `PUT /text-reviews/{kind}/{target_id}` | 发布审核展示文本或撤回为 withheld，追加新版本 |

`kind=comment` 使用 `evidence_id`；`kind=rubric` 使用 `rubric_criterion_id`。
GET 返回 `preview_text`、`preview_truncated`、当前 `version/status/display_text` 及最近审核人、时间与 review_id；初始 version=0。
不返回原始学号、姓名或未经脱敏的正文。已知来源范围内身份、简单空格拆分、常见电话/邮箱/URL/长数字被处理；不调用模型，也不执行正文内指令。

```json
{
  "snapshot_id": "来自正式批次",
  "expected_version": 0,
  "status": "published",
  "display_text": "教师复核并脱敏后的展示文本"
}
```

发布文本 1—4000 字符，可见全角标点先规范化后校验，隐藏字符和敏感信息拒绝。空原评语不能被补写为来源评语。撤回提交 `status=withheld` 并省略 `display_text`；不接受同时夹带正文。每次请求必须带最新 `expected_version`，冲突为 `409 TEXT_REVIEW_VERSION_MISMATCH`。
发布文本是教师审核后的展示投影，不应标成逐字原文；原始文件来源保持可追溯。

原有证据/学生证据接口随后返回 `comment_status=published`、审核文本及 `comment_review_version`；依据接口同理返回 `criteria_status=published`、`criteria_text` 和 `criteria_review_version`。
初始/撤回状态仍为 withheld；原始空评语初始仍是 empty。两种状态都不能被解释为零异常。

自动脱敏是保守规则，不能保证识别任意未登记姓名或全部敏感表达，发布前仍需教师人工检查。来源范围超过 5000 个身份时文本预览/新文字校验明确失败，不截断身份名单继续发布。正文按普通文本渲染，不作为 HTML、Markdown 指令或模型指令执行。
评语分类与 AI 评语分析 Provider 本轮仍未接入，洞察响应继续明确报告该能力不可用。

## 2. 保存与核验分析结果

| 方法与路径 | 作用 |
| --- | --- |
| `POST /analysis-results` | 服务端按快照与筛选条件重算并保存，返回 201 |
| `GET /analysis-results` | 分页列出当前批次的结果摘要 |
| `GET /analysis-results/{result_id}` | 读取已保存的评分统计、群体、候选及教师判断 |
| `PATCH /analysis-results/{result_id}` | 修改标题/备注或逐卡核验状态，追加版本 |

POST 示例：

```json
{
  "snapshot_id": "来自正式批次",
  "title": "本次测评学情核验",
  "conditions": [
    {"dimension_code": "solution_strategy", "operator": "gte", "value": 4},
    {"dimension_code": "algorithm_data_structure", "operator": "lt", "value": 3}
  ],
  "include_dimensions": true,
  "candidate_ids": ["来自同一查询上下文的候选ID"],
  "note": "教师补充判断"
}
```

评分精度、0—2 个不同维度 AND 条件及维度分布含义与正式洞察相同。
`candidate_ids` 省略/null 表示保存本次全部候选，空数组表示不选卡片，最多 6 个且不能重复。
服务端不接受客户端提供统计值、学生名单或卡片正文；候选须由相同快照与条件重算得到。
保存完整匹配群体，分页不缩小人数和分母；无筛选条件时保存全批。零命中保存空群体，群体均值 null，而全批统计仍保留实际人数。

评分统计、精确分数、候选规则版本和成员证据保存为不可变分析载荷，并保存 SHA-256 校验。
CPU 计算在写锁前完成；取得写锁后再次复核凭证、批次和快照。撤销凭证不能在计算后绕过写入检查。
标题、备注及逐卡备注也校验已知身份/联系方式，不把学生敏感资料写入分享结果。

PATCH 示例：

```json
{
  "snapshot_id": "与结果相同",
  "expected_version": 1,
  "note": "已结合课堂情况核对",
  "decisions": [
    {"candidate_id": "已保存的候选ID", "status": "confirmed", "note": "已检查对应评分"}
  ]
}
```

逐卡状态：`pending` 待核验、`confirmed` 教师确认、`dismissed` 不采纳。没有提交的卡片保持原状态。
PATCH 不能修改评分、筛选条件或候选正文；要改分析口径，创建新结果。标题可修改，清空备注用空字符串，不用 null。
冲突返回 `409 ANALYSIS_RESULT_VERSION_MISMATCH`。并发更新同一预期版本只允许一个成功。
GET 默认当前教师核验版本；旧版本以不可变审计记录保留，本轮未提供历史版本列表接口。

## 3. 导出已保存结果

`GET /analysis-results/{result_id}/export?snapshot_id=...&expected_version=...&format=zip`

仅导出已保存结果，格式为 `zip`（默认）、`json`、`xlsx` 或 `md`，不接受自定义路径、文件名或待执行模板。文件名由服务端结果 ID 和版本生成，返回 attachment、nosniff 和 no-store。
必须提交所选结果的快照及当前核验版本，避免把旧页面的判断当成最新结果下载。

ZIP 包含：

- `analysis.json`：结果 ID/版本、快照、筛选条件、评分统计、精确值与规则版本；完整匿名匹配群体、教师备注、已确认发现及文本审核版本。
- `students.csv`：一位学生一行，匿名 ID、显示序号、五维评分和对应证据 ID。
- `statistics.csv`：全批及有筛选时的群体统计，保留有效人数、均值精确分数与显示值。
- `confirmed-findings.csv`：只包含教师确认的发现和备注；待核验、不采纳分别计数，不作为已核验结论导出。
- `evidence.csv`：群体评分证据与当前已发布评语，注明审核状态、版本及来源单元格。
- `README.txt`：使用范围和数据口径。

每次导出在同一只读事务中重新核验权限、评分事实和当前文本发布状态；保存结果不缓存评语或依据正文，已撤回文字不会在下一次下载中重现。
导出不读取身份、原评语、原标准正文或 Excel BLOB；只读取审核后的展示字段。CSV 为 UTF-8 BOM，公式起始字符加单引号，作为文本导出。

导出始终为 `authorized_teacher_only`、`public_display_allowed=false`。教师确认不等于公开发布许可或因果证明；已下载到本机的文件不具备远程撤回机制。
已扩展 XLSX/Markdown 汇总报告下载；PDF 与模型仍不接入。保存载荷上限 4 MiB，导出 JSON 上限 8 MiB，沿用洞察计算预算；超过返回 413，不截断学生或正文伪装成功。

XLSX/MD 仅汇总与教师 confirmed 白名单事实，不含个人逐人明细或任何自由评语/备注。
旧 JSON/ZIP 内容保持兼容；导出预检增加同样格式并共用报告生成、限制和生成后权限/版本复查。
完整契约与第五步 P1 浏览器交接见 [正式报告下载](FORMAL_REPORT_DOWNLOAD_API.md)。

## 迁移、联调与验证

先备份数据库，再在更新后的 `apps/api` 执行：

```powershell
uv run --locked python -m app.edu --database 'D:\private-runtime\edu.sqlite3' init
```

`004_reviewed_analysis.sql` 仅追加三张版本表及批次/快照唯一索引，不更新评分、身份和原始文本。旧迁移文件及校验和不变。HTTP 不自动迁移，完成后重启后端。

浏览器跨域需允许新增 `PUT`，并暴露 `Content-Disposition` 供文件下载命名；本机包装服务已单独配置，正式部署须配置相应来源。

新增合成测试覆盖发布/撤回、敏感内容拒绝、来源为空、服务端重算、防伪造、教师逐卡核验、两类并发版本竞争、权限撤销竞态、跨修订引用、CSV 公式、私有列禁止读取、载荷损坏、零群体与预算失败。运行结果见本 PR 的本地检查和当前提交 CI。
