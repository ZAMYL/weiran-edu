# 正式批次评分洞察 API

对应 Issue #18。数据来自**已确认入库且当前教师有权读取的批次**，
复用 AnalysisReader._load、discover_candidates、metric_sources 与现有证据核验。
没有新算法、数据库迁移、原文审核、外部模型调用或前端改动。

配置数据库后以运行时 /openapi.json 为准。两个新增 POST 均需 Bearer 凭证、
application/json、必填 snapshot_id；成功/失败均 no-store，带服务端 X-Request-ID。
请求 DTO 在 app/edu/insight_models.py；错误沿用统一 ErrorResponse。
旧静态 OpenAPI 不代表本接口。

## 查询卡片

`POST /api/v1/edu/batches/{batch_id}/insights/query`

```json
{
  "snapshot_id": "从当前正式批次取得的快照",
  "conditions": [
    {"dimension_code": "solution_strategy", "operator": "gte", "value": 4},
    {"dimension_code": "algorithm_data_structure", "operator": "lt", "value": 3}
  ],
  "include_dimensions": false,
  "offset": 0,
  "limit": 20
}
```

- conditions 默认空数组，最多两个不同维度 AND，直接复用现有 FilterCondition；
  五维编码、lt/lte/eq/gte/gt、[1,5]/0.01、Decimal 原始 JSON 校验均不变。
  不接受学生名单、SQL、教师 ID、任意来源路径或多余字段。
- include_dimensions 默认 true，显示整个授权批次五维分布；
  非空条件额外产生匹配群体卡，不把分布卡偷偷改成群体统计。
  没有条件不创建默认“策略高/算法低”模板。
- include_dimensions=false 且无条件，或条件零命中：items=[]、total_count=0。
  开启分布卡时零命中仍有真实全批分布，不能误称筛选发现了学生。
- offset/limit 严格整数，默认 0/20，limit 1—100。仅分页卡片，不分页分析输入。

返回 InsightResult：
- batch_id、snapshot_id、audience=authorized_teacher_only；
- scope={kind:batch,record_count:N}；这里 N 是实际全批人数；
- conditions、include_dimensions 回显有效请求口径；
- total_count 是全部候选卡数，page 以卡片数计算，不是人数；
- rule_version=edu.insights.v1 是 HTTP 适配版本；每卡另有内核规则版本；
- comment_analysis_status=unavailable，
  comment_analysis_reason=reviewed_comment_provider_not_available；
- limitations 和 items。每卡含 candidate_id、dimension_code（组合卡为 null）、
  程序 title/summary、metrics、conditions、denominator、rule_id/version、
  status、limitations、suggested_next_action、public_restrictions。
  public_display_allowed 始终 false，教师读取权不代表公开发布许可。

每个 metric 有 key、unit、value:StatValue、denominator、at_score、source_record_count。
StatValue 沿用 value/exact/display 和两位 HALF_UP；
ratio 的 value/display 是 0—1 比例（例如 0.5 / "0.50"），并非百分数。
频数的 at_score 指实际分值，例如 2.75，不归并整数档。
不能混淆 exact.denominator（分数编码）与 metric.denominator（统计样本数）。
不要按数组位置推断维度，使用 dimension_code；每次相同快照与条件排序稳定。

## 指标参与记录下钻

`POST /api/v1/edu/batches/{batch_id}/insights/{candidate_id}/records/query`

重放生成卡片时的 snapshot_id、conditions、include_dimensions，
另加 metric_key、offset、limit。**卡片自身 conditions 可能为空；
前端应保存原始查询上下文，不用它替代整个请求。**

```json
{
  "snapshot_id": "与卡片相同的服务端快照",
  "conditions": [
    {"dimension_code": "solution_strategy", "operator": "gte", "value": 4},
    {"dimension_code": "algorithm_data_structure", "operator": "lt", "value": 3}
  ],
  "include_dimensions": false,
  "metric_key": "matched_count",
  "offset": 0,
  "limit": 20
}
```

服务端无客户端候选缓存或新增持久化表：按当前权限和输入快照重算，
只解析本次真实存在的候选和 metric_key。candidate_id 不是访问凭证；
伪造 ID、其他批次 ID、未生成的候选、未知指标均 404。
客户端不能指定 evidence_id 或个人 ID 来扩大参与范围。

返回 InsightRecordsResult：
- batch_id、snapshot_id、candidate_id、metric、conditions、rule_version；
- total_count 是该指标的实际参与记录数，不随分页变化；
- scope_record_count 是整卡分析范围 N，metric.denominator 仍保留指标分母；
- items=[{student_id,display_label,scores:[{dimension_code,score,evidence_id}]}]；
- page 以完整参与记录数计算，按正式批次显示序稳定排列，非能力排名。

均值仅取有效评分记录，某档 frequency:11/4 仅返回评分 2.75 的记录；
matched_count/matched_ratio 只取真实 matches，eligible_count 取可判定者，
unevaluable_count、missing_count 等零来源指标返回空页，不拿其他学生充数。
正式入库要求五维完整，读取发现事实损坏直接 503，不缩小分母或补零。
scores 只含支持该指标的维度：分布卡一维、组合卡对应条件维度，
不默认把每位参与者的其余评分也当作直接证据。

每个 evidence_id 都在同一只读事务中经现有证据 JOIN/投影核验：
批次、快照、匿名学生、维度、分数、文件摘要和内部记录引用必须一致。
可直接传给现有 /evidence/{evidence_id} 或与
/students/{student_id}/evidence 对照，再经 rubric_criterion_id 回查 /rubric。
新卡片和下钻响应不含原始身份、内部 RecordRef/location、文件路径或个人评语。
已有授权证据接口继续按原契约返回 Excel 来源单元格，不改变其权限及展示策略。

## 扣留原文与失败

当前不接入评语 Provider：不读取 comment_raw、身份列、评分依据原文或 Excel blob，
不把 withheld 转成空评语。即使原评语实际为空，也只报评语分析不可用，
不伪造重复评语、评分冲突或“零异常”。依据正文仍由现有接口标 withheld。
原文审核、导出、Qwen/Production 开放不在本轮范围。

401 凭证缺失/失效/撤销；404 无资源权限或未知候选/指标；
409 SNAPSHOT_MISMATCH；413 QUERY_TOO_LARGE / INSIGHT_RESULT_TOO_LARGE；
415 媒体类型；422 参数/JSON 不合法；
503 数据/预算/繁忙/阶段超时。均使用：
`{"error":{"code":"...","message":"固定安全提示","request_id":"req_..."}}`。
先重验教师/批次授权，再比较快照；撤销后的新请求不能沿用旧候选权限。
修订产生新批次和快照；历史批次仍需其自身权限，不能混用证据 ID。

## 成本与验收边界

- 复用 64 KiB 实际流式请求限制、重复键/非有限数值/未知字段拒绝；
- 每进程至多两个并行洞察计算，忙时 503，不排无限队列；
- 完整批次人数受现行 Excel 每表行预算推导的上限约束；
- 预估评分扫描工作量不超过 4,000,000，JSON 响应最多 1 MiB；
- 15 秒阶段检查超预算返回 INSIGHT_TIMEOUT。它在同步计算完成后检查，
  **不是可抢占中断或严格墙钟截止**；记录/工作量/并行限制共同约束成本。
  不悄悄截断数据、少算学生或伪装成功。不改核心算法或 Qwen 上限。
- 权限与事实采用同一只读 SQLite 事务视图；不声称可以撤回已在途返回的数据。

合成 HTTP 实际捕获样例与 1/4/17/53 人本机耗时见
[formal-insights.synthetic.json](examples/formal-insights.synthetic.json)。
样例来自临时已确认数据库，UUID 只用于文档，不是已部署可调用资源；耗时不是生产 SLA。
四人算法 [2,2.75,3,4] 均值47/16、中位数23/8，
上述组合命中2/4；分页一人不改变 M/N=1/2。

测试：生成 .xlsx → HTTP 上传/预览 → 确认 → 洞察 → 指标参与者 →
现有评分/学生证据与标准来源。涵盖未确认输入、1/17/53 人、小数、边界、
零命中、分页、过期快照、修订、跨教师/批次、撤销竞态、
伪造 ID、异常 JSON、预算、只读事务、损坏事实与私密列禁止读取。
真实 Ruff、pytest 和 CI 结果以本 PR 当前提交为准。

林明需要保存查询上下文和快照，以 candidate_id + metric.key 打开依据抽屉，
处理 unavailable、空页、401/404/409/503；本轮没有改页面。
仲楷需审核新增两个 POST/DTO、共享严格解码器泛化及只读证据适配；
现有统计/群体/证据响应不变，没有迁移。
