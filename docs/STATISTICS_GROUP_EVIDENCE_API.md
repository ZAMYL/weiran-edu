# 正式批次统计、群体筛选与证据查询 API

> ⚠️ 本文部分内容已被 V1.4 取代，以 docs/V1.4_BASELINE.md 为准。

本轮实现位于 `apps/api/app/edu/analysis.py`、`analysis_models.py`、`query.py`、`reads.py` 与 `routers/edu.py`。依赖已实现的 Excel 导入确认、SQLite 数据库、教师凭证及批次授权；无新增依赖或数据库迁移。分析计算复用现有 `app.analysis.edu`，没有修改其算法，也没有接入模型或生成能力标签。

## 运行与入口

按 [数据库与教师权限运行说明](DATABASE_BATCH_AUTH.md) 完成迁移、管理员预置凭证及导入确认，启动后访问 `/docs`。设置 `WEIRAN_EDU_DATABASE` 后这些路由才注册；未配置时仍仅开放原有基础接口。使用教师 Bearer 凭证，不能用教师 ID 请求头代替认证。运行时 `/openapi.json` 是实际接口依据，仓库早期 v0.1 文档仍是草案。

所有下列路径均以 `/api/v1/edu/batches/{batch_id}` 开头：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/statistics` | 全批次五维统计；可附二维评分矩阵 |
| POST | `/groups/query` | 1–2 个不同维度 AND 条件筛选、分页及可选群体统计 |
| GET | `/students` | 沿用已有匿名学生、五维评分及证据 ID 列表 |
| GET | `/students/{student_id}/evidence` | 五维评分证据，新增快照校验与响应快照 |
| GET | `/evidence/{evidence_id}` | 单条评分证据，新增快照校验与响应快照 |
| GET | `/rubric` | 五项评分依据 ID、固定名称、评分范围及 Excel 来源 |

GET 接口可传 `snapshot_id`；POST 放在 JSON 请求体中。建议前端从选定批次保存该值，并在统计、筛选及证据回查中始终携带。快照不匹配返回 `409 SNAPSHOT_MISMATCH`；快照 ID 不授予任何读取权限。没有传值时读取 URL 中指定的不可变批次，不会自动替换成修订后的新批次。

## 五维编码与计算口径

| 接口维度编码 | 内部引擎枚举 | 展示名称 |
| --- | --- | --- |
| `algorithm_data_structure` | `Dimension.ALGORITHMS` | 算法与数据结构 |
| `code_implementation` | `Dimension.IMPLEMENTATION` | 代码实现能力 |
| `problem_modeling` | `Dimension.MODELING` | 问题建模能力 |
| `efficiency_awareness` | `Dimension.EFFICIENCY` | 效率意识 |
| `solution_strategy` | `Dimension.STRATEGY` | 解题策略 |

数据库整数 `score_units` 转为 `Fraction(score_units, 100)` 送入引擎，评分为 1–5，精度为 0.01。规则版本固定为 `edu.statistics.v1`。均值、中位数、最小值、最大值及分布占比返回 `StatValue`：

```json
{"value":2.675,"exact":{"numerator":107,"denominator":40},"display":"2.68"}
```

`value` 用于图表，`exact` 是约分后的精确结果，`display` 使用整数运算实现 HALF_UP 两位小数。筛选比较使用精确数值，展示舍入不参与计算。空群体的统计量为 `{"value":null,"exact":null,"display":"暂无有效数据"}`，分布为空数组，不用 0 代替缺失统计。

全批统计的 `scope={kind:"batch",record_count:N}`；每维返回有效/缺失/无效人数、四项统计量和按分数升序排列的分布。正式批次必须每位学生五维完整，因此有效人数都是 N，缺失和无效都是 0。读取时检查学生数、评分总数、维度、评分范围、依据关联和来源摘要；如正式事实损坏，返回 `503 DATA_INTEGRITY_ERROR`，不会悄悄缩小分母或补分。

可选矩阵请求：

```text
GET .../statistics?snapshot_id=服务端快照&x_dimension=solution_strategy&y_dimension=algorithm_data_structure
```

两个维度必须同时给出且不同；否则 422。矩阵 `scope="batch"`，`x_values/y_values` 是实际出现的分值升序集合，`cells` 只含非零 `{x,y,count}`，未返回的格子视为 0。每格人数之和等于 `paired_count=N`，`excluded_count=0`。不传维度时 `matrix=null`。不会把 2.75 归并到半分刻度。

## 群体筛选

```json
{
  "conditions": [
    {"dimension_code":"solution_strategy","operator":"gte","value":4},
    {"dimension_code":"algorithm_data_structure","operator":"lt","value":2.75}
  ],
  "offset":0,
  "limit":20,
  "snapshot_id":"替换为服务端返回的快照",
  "include_statistics":true
}
```

操作符只允许 `lt/lte/eq/gte/gt`，对应 `< / <= / == / >= / >`。条件数 1–2，维度不得重复，固定 AND；不支持任意表达式、SQL、身份筛选或客户端指定学生名单。阈值必须是 JSON 数字，范围 1–5，最多两位有效小数；允许 2.75、2.750、275e-2，拒绝 `"2.75"`、布尔值、非有限数值及 2.751。原始 JSON 按 Decimal 解码后校验，因此 `2.750000000000000000000000000001` 不会先变成浮点 2.75 而通过校验。拒绝重复对象键、未知字段、无效 JSON；请求体实际流式大小最多 64 KiB，不信任 Content-Length。数值型 offset/limit 不接受布尔值或字符串。

返回结果的口径：

| 字段 | 含义 |
| --- | --- |
| `total_count` | 当前授权批次全体学生数 N |
| `matched_count` | 完整筛选结果数 M，独立于分页 |
| `ratio` | M / N，JSON 数字 |
| `eligible_count` / `unevaluable_count` | 可评价人数 N / 不可评价人数 0 |
| `matched_among_eligible_ratio` | M / N |
| `items` | 当前页匿名学生，每人包含五维 `dimension_code/score/evidence_id` |
| `page` | offset、limit、返回数和 has_more，按完整 M 计算 |
| `statistics` | 默认 null；显式启用时统计全部 M 人，`scope.kind="matched_group"` |

分页默认 0/20，limit 为 1–100。按批次内原始显示序稳定排列，不代表能力排名。偏移超出末尾时返回空页，M 与统计不变。零命中是 200，`ratio=0`、空列表，启用的群体统计保留五维结构和空值。群体统计中分布的 denominator 为 M，matrix 为 null。组查询本身不创建持久化群体，也不修改测评数据。

前端可按下面顺序使用（`apiRoot` 为 API 服务地址，凭证应按现有教师会话方式取得）：

```javascript
async function explore(apiRoot, token, batchId, snapshotId) {
  const base = `${apiRoot}/api/v1/edu/batches/${encodeURIComponent(batchId)}`;
  const headers = {Authorization: `Bearer ${token}`};
  async function read(url, options = {}) {
    const response = await fetch(url, {...options, headers: {...headers, ...options.headers}});
    const body = await response.json();
    if (!response.ok) throw new Error(body.error.code);
    return body;
  }
  const overview = await read(`${base}/statistics?snapshot_id=${encodeURIComponent(snapshotId)}`);
  const group = await read(`${base}/groups/query`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      conditions: [{dimension_code: "algorithm_data_structure", operator: "lt", value: 2.75}],
      snapshot_id: snapshotId, include_statistics: true, offset: 0, limit: 20
    })
  });
  const selected = group.items[0];
  const evidence = selected ? await read(
    `${base}/evidence/${encodeURIComponent(selected.scores[0].evidence_id)}` +
    `?snapshot_id=${encodeURIComponent(group.snapshot_id)}`
  ) : null;
  return {overview, group, evidence};
}
```

## 证据、权限与展示边界

证据 ID 直接引用确认入库时生成的 `evaluations.evidence_id`；引擎内部 `RecordRef`、来源或记录 ID 不作为 HTTP 授权标识。群体学生的 `scores[].evidence_id` 可回查同批证据，返回匿名学生 ID、维度、数值及原分数词法、Sheet2 行号及 B/C/D 单元格、文件 SHA-256、`rubric_criterion_id` 和快照。通过 `/rubric` 可以找到对应依据的 Sheet1 范围及同一文件摘要。不同批次的证据 ID 不能互换。

每次请求先验证教师凭证，读取事务内再次检查有效期、撤销和教师状态，并要求来源成员资格和批次拥有权或显式读取授权。授权与事实从同一只读 SQLite 事务读取，不使用权限缓存。没有权限和资源不存在都返回 404，先检查授权再比较快照，避免越权探测快照。401/404/409/413/415/422/503 使用既有统一错误结构、服务端 X-Request-ID 和 no-store；成功响应同样 no-store。

现有评语策略保持：非空 `comment_status="withheld"`、空值 `"empty"`，`comment=null`。依据原文也可能包含身份信息，本轮 `/rubric` 返回 `criteria_text=null, criteria_status="withheld"`，只提供固定维度名称及真实来源定位；`original_name` 为解析器核验通过的固定标准标题。**这与旧草案中必返依据正文的定义不同**。学生页不得把 withheld 当成没有评语/没有依据，也不能宣称已经完成脱敏或内容审核。未提供身份、原始文件或原文下载接口；完整评语和依据正文展示仍待单独审核流程实现。

本轮不包含 AI 洞察、持久化群体、导出、真实教师试用、学校 SSO 或前端页面联调。当前分析适配在内存读取一个批次，规模受已冻结 Excel 导入行数限制；跨批次大规模聚合需要另行设计。

## 验证

```powershell
cd apps/api
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -q
```

新增 `tests/edu/test_analysis_api.py` 使用合成 Excel 与临时数据库，覆盖精确均值/中位数、HALF_UP、五维映射、五种比较符与阈值边界、稀疏矩阵计数、群体分母、全命中/零命中/越界分页、可选统计、证据与依据关联、跨批修订、快照、实时撤销、未授权先于读取请求体、异常 JSON/超长请求及正式数据损坏拒绝。未使用真实学生记录或真实学生数据库。
