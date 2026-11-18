# 教师产品 → Research Agent 最小接入切片

> ⚠️ 本文部分内容已被 V1.4 取代，以 docs/V1.4_BASELINE.md 为准。

本分支隔离组合 #69 `5c3012e4fdcd9af057220c3fc10442aa930fa145` 与 #67 `c64a614fd79f3dc826c08e4c09fbd377fd6789bd`（含#66）。不更新原PR分支，不作为V1发布批准。V2.3工作台暂停，未纳入本分支。

## 实际差距与已复用能力

|边界|#69已有|本切片|仍未实现|
|---|---|---|---|
|教师和快照|Actor认证、当前token/范围/owner、validate_snapshot|每次取数及重放调用AnalysisReader._batch，写入AI前只允许owner|正式AI用途授权策略|
|统计与发现|完整五维、0.01精度、discovery/input_key、metric_sources、逐评分evidence|正式入库数据转Canonical Observation/AssessmentItem/Scale及Witness，原Kernel/Research循环实际计算|动态题目接口尚未在#69存在，不猜DTO|
|模型|#66/#67原生Function Calling及严格工具/来源/预算|相同Planner/循环使用HTTP MockTransport，无网络Client|生产外发、真实Qwen资格与单独授权|
|预算/披露|CampaignBudget独占账本，单轮累计群体门禁|新增仅Mock可用OfflineSnapshotPermit，同一教师/批次/快照只允一次；目录不随客户端重置|正式多会话/跨批次累计披露与恢复|
|证据|匿名学生及score evidence、源文件SHA和坐标|完整实际参与者核验后分页；评分链接回原evidence接口|withheld量规/任务不能作为已核验解释输入|
|报告|正式analysis-results、教师核验、MD/XLSX|返回已有GroupQuery与AnalysisCreate白名单请求，由旧服务重新计算并导出群体统计|Agent Fact不是discovery_id，尚不能作为已确认发现直接入报告|

当前数据来自实际FastAPI确认入库后的SQLite，不是把SyntheticProvider.recipe换成客户端JSON。DatabaseProvider只接受本机服务生成并登记SHA的合成XLSX。任意其它上传仍可按V1处理，但不能进入该AI切片。数据库路径、授权集合及凭证均不接受请求参数。

适配内部I01～I05与正式英文dimension_code双向固定映射，名称/量表来自#69现有五维契约（1～5、0.01，ordinal）；不宣称支持动态题目或自动推定能力。正式batch/snapshot、源XLSX SHA、Canonical评分Evidence与Witness分别保存，内容清单SHA与snapshot UUID不混用。只读数据库评分数值及来源元数据，不查询身份映射、原始评语或量规正文。服务器自造的合成工作簿用原解析器建立仅含评分坐标/分值/数字原文的白名单；每次验证当前batch_sources字节SHA，再逐评分核对原坐标内容与实际学生顺序。仅复用不可变合成原件的解析结果，不缓存当前数据库/授权或Kernel核验，也不解析未知教师原件。

## 最小运行与接口

原应用app.main:create_app默认**不挂载**新路由；只有build_host生成合成资料的test宿主显式install。Production注册拒绝。没有需要林明新增的页面，也未接正式教师服务。

```powershell
$env:PYTHONPATH='.;apps/api'
uv run --project apps/api --locked python -m prototypes.teacher_agent_bridge.demo --output D:\Temp\NEW_AI_HTTP.json
```

该命令临时启动127.0.0.1 Uvicorn，真实TCP上传→预览→确认→SQLite→Agent→证据回查→原群体/保存/MD/XLSX，结束撤销凭证并关闭服务、删除临时库。不读取环境Key，不调用Qwen。离线Mock使用固定历史测试日期，只是原价格/预算协议的fixture，不能作为Live授权。

### 发起调查（同步、完成后返回）

`POST /api/v1/edu/batches/{batch_id}/ai-investigations`

Authorization使用原教师Bearer；由原依赖先认证，再读取限64KiB且拒绝重复键的JSON：

```json
{"snapshot_id":"本批正式快照UUID","question":"比较 算法与数据结构、代码实现能力 的组合与 问题建模能力 的群体差异","mode":"http_mock","strategy":"adaptive"}
```

当前仅整个授权批次（scope_kind=whole_authorized_batch），不静默继承前端当前筛选；其它scope字段会被拒绝，正式子群体授权上下文尚待联调。source_manifest_sha256是本次来源内容清单摘要，不能代替snapshot UUID或授权。

strategy可为adaptive或fixed，用于机制对照。question先经原understand映射受控目标；模型只接收受控目标、实际项目和审核后的聚合，不接收自由文本。含SQL/越批次/改分等目标固定422，不让自由文本决定授权或文件。

响应以models.InvestigationResult实际OpenAPI为准，包含：
- run_id、batch_id、snapshot_id；execution_origin=http_mock，real_qwen=not_run，production_enabled=false。
- status、stop_reason、controlled_goal、goal_items、missing_evidence；uncovered_direction_count，investigation_exhaustive=false。
- facts：fact_id、method、rule_version、qualified、source_verified、原始条件、metrics精确分量/展示值及source_refs实际参与数。
- selected_fact_ids与retained_fact_ids分别保留；trace是真实规划/执行/验证/最终选择事件，不是AI思考动画。
- budget：实际Mock协议请求/工具尝试、6/5硬上限、来源重放计数；真实远程请求0、实际Token/账单null。
- group事实附已有exploration_query和save_group_analysis_request。后者candidate_ids为空，不把未经教师核验的AI文字写入报告。

`GET /api/v1/edu/batches/{batch_id}/ai-investigations/{run_id}?snapshot_id=...` 可回读当前宿主内的完成结果，并再次检查原凭证/权限和来源。此版结果绑定发起凭证；凭证撤销后不能用缓存继续读。令牌轮换后的继续访问协议尚待后端确认，不宣称已实现。

### 实际参与记录与评分依据

每个source_refs提供metric_key、正式dimension_code、participant_count、witness_kind，以及受控records_path/records_query。

`POST /api/v1/edu/batches/{batch_id}/ai-investigations/{run_id}/records/query`

```json
{"snapshot_id":"同一快照","fact_id":"本轮已签发ID","metric_key":"target.valid","offset":0,"limit":20}
```

先按服务器账本查Fact，重放原计算和整个数据库来源；所有实际参与引用匹配后才分页。返回total_count（全部参与者）、offset、匿名student_id、已核验evidence_id/dimension_code和既有evidence_path。未知Fact/指标404，错批次/快照409；撤权/过期401或权限404/403。行状态Witness不冒充个人评分依据，422 AI_ROW_WITNESS_NOT_SCORE_EVIDENCE；行引用只提供聚合状态。

### 与现有教师页面接线

林明只需在“学情诊断 → 发现详情 → AI深入调查”调用上述受控请求；当前仅能在合成宿主演示，不能对正式教师数据打开按钮。
- 展示Mock来源和停止状态；needs_external_evidence不是失败分析，也不是因果结论。不要显示“Qwen已完成”。
- 查看依据使用source_refs返回的records引用，再请求原evidence_path；ID不是凭据，所有请求带当前教师授权和snapshot。
- 探索群体：把exploration_query原样交给既有POST /batches/{id}/groups/query。保留条件和快照，不用人数反推群体。
- 形成报告：原analysis-results使用save_group_analysis_request重新计算群体统计，然后沿用原核验/下载流程。报告不会包含本轮Agent Fact。若要纳入AI发现，必须由正式服务新增受审的持久化/Fact-Discovery映射和教师核验，不能前端布尔确认。

## 硬边界、取消及剩余阻断

- 沿用原断连中间件，只增加ai-investigations路径识别；取消信号进入每次Source验证，取消/撤权不会发布部分结果。保存完成结果时去掉旧请求的取消事件，保留不可变来源和当前凭证复核。
- 整批数据库内容相等复核前后，批量检查全部引用，不逐引用重读全库；原ResearchExecutor数值/来源重放和累计披露检查没有缓存或删减。
- HTTP请求不能提供Fact、分数、授权标志、API Key、模型、文件路径或预算覆盖。Mock Permit不是SyntheticLiveAuthorization；原Live注册配方校验、价格快照、准确SHA审核和负责人单独批准门禁原样保留，DB来源强制禁止网络模式。
- 每个教师/批次/快照独占持久化campaign；失败也不退还、不重入。当前只保留内存结果，进程重启后不能恢复结果或重置预算；会拒绝旧campaign。需正式原子任务存储、跨会话披露台账、并发容量/取消/结果TTL后才能上线。
- 原#66/#67尚待审核；本集成不替代审核。正式动态Provider、完整任务/量规Witness、独立用途授权、Qwen真实质量及外发授权均未完成。

实际测试/HTTP结果以本PR最终SHA与docs/contracts/ai-investigation.synthetic.json为准；开发中201预期、可选取消回调以及缓存旧请求取消事件的失败记录保留在交付说明，不把中间失败写成通过。

## 本轮运行记录

- 实际回环HTTP最终27/27预期响应通过（含原解析隔离Worker），正常53人组合得到26/27人、差-4；缺量规/任务停止，17人同分零发现，撤权401；实际MD/XLSX下载通过。完全合成响应见ai-investigation.synthetic.json，API OpenAPI从真实宿主生成，37个operation ID无重复。
- 共享Research/Qwen兼容专项117 passed/1既有warning（382.60秒；原件坐标Witness加强前）。加强后最终接入专项16 passed/1既有warning（82.49秒）。最终全量以本分支精确提交CI为准，不用中间测试冒充最终全量。
- Ruff/format通过：apps/api 213文件，原型8文件另行检查；没有新增依赖或CI配置变更。
- 开发失败如实保留：脚本最初误期望commit为200（实际正确201）；新增接口曾错误调用None取消回调；缓存旧请求取消事件导致后续证据499，现已分离；首次只读测试准备漏范围成员；原件Witness绑定误用了匿名ID排序，现使用正式batch学生顺序。各问题未通过改变正式权限、扩大超时、跳过断言或删除来源检查来掩盖。
- 保留既有固定5/9、Mock6/9及Qwen not_run文件和Oracle，未重调选择策略。新API fixed/adaptive执行样例只是接线验证，批次/匿名标识各自生成，不作为同一快照下的独立质量比较；本轮不宣称AI新增教学价值。
