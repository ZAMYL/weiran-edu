# 正式五维发现：保存、教师核验与报告契约

> ⚠️ 本文部分内容已被 V1.4 取代，以 docs/V1.4_BASELINE.md 为准。

对应 #60。只使用现有 SQLite 中当前教师有权访问的五维测评批次。正式发现完全本地确定性计算，不调用 #49/#56/#58 的合成 Agent 或 Qwen；不新增认证、数据库迁移、评分规则或前端上传格式。

## 教师流程与两个入口

学情洞察页与测评数据页导入后的「查看学情」进入同一当前授权批次。先展示少量可解释的候选，再查看完整图表、发现原条件下的群体及对应评分证据，最后保存、核验、下载。前端首屏建议请求 `limit: 3`，不把 API 分页或保留上限当作人数分母。

已有 #50 四个 POST 契约保持不变：`discoveries/query`、`discoveries/{id}/query`、`discoveries/{id}/chart/query`、`discoveries/{id}/records/query`，均位于 `/api/v1/edu/batches/{batch_id}`。证据继续通过当前快照的既有 evidence 接口读取。详情沿用内核 `chart`，分析范围 `scope_conditions` 与发现内部 `conditions` 始终分开。

本次后端新增保存桥接；页面由 #40 的教师核验/下载与 #55 的图表/群体/证据能力共同接入。后端 HTTP 验收不能代替尚未完成的两个页面浏览器验收。

## 保存教师实际选中的发现

复用 `POST /api/v1/edu/batches/{batch_id}/analysis-results`。原有请求、旧候选和旧结果保持原语义。新增可选 `discovery`；使用新发现时必须 `include_dimensions: false`，不能混入旧 `candidate_ids`。例子中的引用必须取自实际授权响应：

```json
{
  "snapshot_id": "来自当前批次",
  "title": "本次测评分析",
  "note": "教师备注，与系统计算分开",
  "conditions": [],
  "include_dimensions": false,
  "discovery": {
    "input_key": "来自发现列表scope.input_key的64位摘要",
    "discovery_ids": ["来自列表的24位发现引用"]
  }
}
```

`conditions` 必须使用发现查询的分析范围条件，而不是该发现的内部筛选条件。`input_key` 绑定批次、快照、源摘要、查询配置和规范化范围；错配返回 409。最多显式选择 3 个互不重复的发现 ID，可选择空列表保存概况；不默认保存全部候选，不接收客户端统计、图表、学生或证据作为事实。

保存会从完整正式快照重新执行同一受限发现计算。每项发现逐指标先经过内核来源校验，再检查数据库评分归属，最后在新写事务中重新核对权限和完整输入后落库。学生分页不会参与重算；完整批次统计、范围统计与所有匹配成员分别保存。

响应复用 `AnalysisResult`：

- `analysis.query/statistics/group_statistics/total_count/matched_count/ratio/members` 沿用现有含义。
- 新增 `analysis.discovery`：保存 `spec`、完整 `scope` 与所选 `findings`，均为服务端派生。
- 新增 `discovery_findings[]`：`discovery` 是原有完整 `DiscoveryDetail`；`teacher_review` 继续采用 `{candidate_id, status, note}`，其中 `candidate_id` 等于该项 `discovery.discovery_id`。
- 新发现模式的旧 `analysis.candidates` 和 `findings` 为空。旧模式结果省略新增空字段，不把新发现伪装成旧分布或群体候选。

前端须显式解码新字段；只使用 #40 的旧 `findings` 会漏掉本轮新发现。`analysis.discovery` 标识新发现保存模式；空的 `discovery_findings` 可能省略，按空列表读取。不要将空数组误报为「已完成核验」。

## 保存、核验和下载沿用同一结果版本

列表和读取继续用 `GET .../analysis-results` 与 `GET .../analysis-results/{result_id}`。核验继续用 `PATCH .../analysis-results/{result_id}`，携带当前 `snapshot_id`、`expected_version` 和 `decisions`；状态为 `pending/confirmed/dismissed`。批次所有者可写，获授权只读教师可读和导出；不是教师认证替代方案。

预检是 **POST** `.../analysis-results/{result_id}/export-check`，JSON 为 `{snapshot_id, expected_version, format}`。下载是 GET `.../analysis-results/{result_id}/export`，同名查询参数。XLSX/Markdown 的 MIME、附件名、字节预算、实际文件签名检查沿用现有前后端下载契约。冲突仍返回 409，不自动覆盖核验或替换快照；前端有未保存修改时继续暂停下载。

每次新发现导出/预检均重开授权快照，通过原有隔离子进程重新核对发现详情、图表与逐指标来源；完成计算/排版后再次检查当前权限、版本和快照，并在同一最终事务刷新评语和依据的最新发布文本。计算期间撤回的正文不会沿用早先读到的内容进入私有 JSON/ZIP。过时、损坏或撤权不能沿用预检结果下载。

XLSX/Markdown 只呈现确认的发现，用中文固定字段和受控指标生成说明，不复制教师备注、原始评语或模型自由文本。教师核验判断和系统证据状态分别呈现；小样本、稳定性检查未完成/未全部通过及当前内核的必要限制继续保留，教师确认不将证据状态升级。报告仅接受已核验的固定限制说明，未知自由文本限制明确拒绝，不静默省略。人数为整数，评分均值默认两位小数，仅在排版时格式化；内部 Fraction 不变。技术引用和精确值在明确附录。没有确认发现时不输出空发现章节。报告不包含匿名逐人明细。

原有 JSON/ZIP 行为保留。新发现结果的私有导出新增 `discovery_spec` 与 `confirmed_discoveries`；未确认/否决的详情不会通过 `analysis.discovery` 或 `result.discovery_findings` 混入。核验状态计数和导出预检计数同时覆盖新发现。ZIP 原有文件名和公式注入防护保持不变。

## 预算与失败

复用 #50 的每进程 2 个计算槽、最多 1666 条学生记录、15 秒发现计算上限及可终止子进程。保存请求仍采用既有有界 JSON reader，保存快照不超过 4 MiB，报告/私有导出不超过 8 MiB。保存、GET 下载、POST 预检均监听浏览器断连与请求任务取消；GET 下载无需等待读取请求体才启动监听。跨线程取消信号传至既有 Worker、来源核验和报告排版检查点，回收子进程、工作簿与计算槽；不增加生产超时、不遗弃后台线程。单次正在进行的 SQLite 调用或压缩 I/O 仍须返回到检查点，不能宣称所有同步 I/O 都可立刻强制中止。已经提交的写事务无法由浏览器撤销，重新进入已保存分析读取实际结果。

无效发现 ID 的授权/快照校验仍保留，但在线程池执行，避免阻塞事件循环。不存在的分析结果在只读事务中核验当前权限与归属后返回 404，不抢占 SQLite 写锁；只读教师仍返回 403。实际核验写入仍在写事务内重新检查权限、快照和 expected_version，读写之间撤权或版本变化不能沿用预检结果。

保留统一错误结构和服务端请求编号：401 凭证失效；403 只读尝试写入；404 未授权批次/结果/未知发现；409 过时快照、版本冲突或 `DISCOVERY_INPUT_MISMATCH`；413 请求/快照/导出超预算；422 重复选择、混合旧新模式或非法输入；499 发现计算断连；503 计算忙、超时、损坏数据或来源校验失败。错误不返回原始学生数据、输入正文或凭据。

## 验收边界

后端使用合成 Excel、真实 FastAPI、临时 SQLite 和真实隔离计算：分页不影响 53 人及均值 619/212；筛选范围 40 人和边界新增 13 人；四类发现报告；空发现；未知引用；错误快照；只读授权；撤权；来源越界；重新计算摘要仍被发现的存储篡改；硬超时与 ASGI 断连不落库；教师三态核验与版本冲突；四种导出和 XLSX 重开。

本轮修复回归另覆盖：真实 SQLite 锁下有效/无效发现 ID 均不阻塞健康请求；不存在分析结果不申请写锁；只读预检后撤权/范围撤销/版本竞争；四种下载及预检计算中断连；来源核验、工作簿构建中的取消；服务端任务取消；请求体未读完即断连；子进程终止、工作簿关闭、计算槽复用；5 人谨慎解释及 100 人正常证据状态的两种报告，确认后保持原事实、否决后仍排除。

完整页面验收还需确认：从两个入口进入同一批次；候选 → 原图表 → 原条件群体 → 评分证据 → 显式选择保存 → 新发现核验 → 两种报告下载，并验证只读、撤权、冲突和空发现。不得将后端测试或合成 Qwen 原型的 CI 当作此浏览器流程已经通过。
