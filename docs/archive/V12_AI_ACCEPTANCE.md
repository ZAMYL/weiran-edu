# V1.2 AI 统一验收：P0-C

> ⚠️ 本文部分内容已被 V1.4 取代，以 docs/V1.4_BASELINE.md 为准。

2026-11-07；这是Agent审核记录与候选门禁，不是发布批准。统一候选分支/SHA：**待彭仲楷交付，组合最终回归未运行，不能放行**。不合并原PR、不代替独立作者之外的审核。

## 固定版本与范围

|对象|准确SHA|本轮定位|
|---|---|---|
|#75|babd36e5b2f15fd42d0b5e714e3d06c73122a2a1|受审基线，Peng-Kai789已在该SHA APPROVED；不是本轮新增修复的批准|
|#72|757bfd86cdd890e47d9e91fdc52c0742a39c2795|SQLite持久任务、核验/报告；单一任务API|
|#70 / #67 / #66|25b9010 / f7fd316 / 445f3e2|DB来源桥接 / 安全同步 / Live全封禁，原分支不改|
|#65|24c442cf9b48e11886fdaf45ac9e644b23e7c2d6|只读审查上传/确认/来源契约|
|#61|a64e58b25f01d82aa1b2607a3aad1e0ef6b70445|只读审查发现保存/教师核验/导出|
|#71|82f64651eb8c157ae9a9da1c2664e32321a1c4c2|只读审查逐题Snapshot/Scale/Observation/Witness|

本分支只修正下面实际复现的目标准入缺口，并补缺失否定例；不改统计Kernel、Planner、Fact、Oracle、正式API、数据库或前端。

## 审核结果与实质缺陷

**P1：旧关键词可掩盖不支持意图。** 基线合成catalog八例实际 **7 failed / 1 passed**：身份索取、改分、`证明I01导致I02`、`优先帮我写一首诗`、`核查缺分并查看别的批次`等仍被映射到合法Goal。是语义准入缺陷；未发现这些请求实际取得姓名、修改评分或发往真实模型，工具白名单/只读计算仍在。前述一例原已拒绝，不冒称八例全部为新增修复。

局部修复：身份字段、改分/因果/额外批次请求先拒绝；旧priority从任意关键词命中收紧到现有受控命令（可带合法项目ID）及#75六个完整别名。保留`为什么I01和I02相关`为缺教学证据的观察目标，不输出因果解释。不是通用自然语言理解：词法过滤不等于鉴权；未知/多指令问句必须在统一候选继续负测，不能宣称任意文本都能安全理解。

新增仅12项解析边界及1项FastAPI/SQLite准入前拒绝回归；#75六别名、模糊请求和幂等正例直接复用。初轮中文修补组合35 passed；随后复核同一中英文入口，在0134aaed上追加英文反例实际 **3 failed**（causes、student names、modify scores可被合法关键词掩盖），据此最小补齐相同拒绝类别，未冒称初轮CI已经证明这些边界。完整修复组合 **38 passed / 1既有弃用warning（81.53s）**，无skip/xfail。失败证据：本机`D:/Temp/edu-v12-goal-boundaries-before.xml`、`edu-v12-english-boundaries-before.xml`；最新修复后`edu-v12-goal-boundaries-complete.xml`。HTTP否定例要求422 AI_GOAL_UNSUPPORTED、无task/event/账本、worker不能启动，不把失败伪装成功。

## 已有可信性接点（不重复造测试）

|必须核对的事实|实际实现与既有回归|
|---|---|
|数值不是模型填报|`AdaptiveExecutor.derive/_verify`重算Calculation、视图和实际来源；`ResearchExecutor._verify`全账本重放及披露一致性；`test_program_values_and_actual_metric_sources_are_replayed`、`test_tampered_math_rejected_before_fact_is_published`、`test_tampered_view_unknown_metric_and_cross_run_ids_rejected`|
|SQLite/Witness属于本批实际评分|`DatabaseProvider.capture`校验原XLSX SHA、源坐标/原评分、匿名归属；`DatabaseEnvelope.validate`每次重新捕获；Fact snapshot绑定来源指纹。分析物化与模型视图只纳入评分，不纳入withheld评语/标准原文；原字节仅本地校验哈希。见`teacher_agent_bridge/test_bridge.py`来源/跨教师/快照用例|
|用途/凭证/快照持续有效|`TaskStore.check`核对当前教师、发起凭证、用途、到期、batch/snapshot；`AuthorizedEnvelope`在昂贵来源读取前后复查。见`test_readback_rejects_purpose_revoked_during_witness_replay`等|
|共享6模型/5工具|CampaignBudget独占fsync预留+SQL单调镜像；attempt先计数，失败/取消/新key/进程重启不退款重置。验证重放另计，不冒充新模型调查或零计算。复用`test_six_requests_and_five_tool_attempts_are_actual_hard_limits`、`test_campaign_cannot_reset_by_client_or_process_restart`和durable生命周期用例|
|失败不发布伪正常结果|领取失败不能改变其他worker；取消/超时/撤权/错误状态显式返回，未完成无result。复用`test_lifecycle.py`及既有race/失败测试|
|教师确认有正式边界|Fact只能经`review.match_fact`重算精确值、条件、人数/分母及实际score证据集合，匹配合格正式发现才保存pending；事务绑定task/result。原PATCH才confirmed/dismissed，旧版本导出拒绝；`test_persist_reload_evidence_review_and_report`、`test_unknown_and_unqualified_fact_not_promoted`|
|没有Live旁路|预算、Research/adaptive CLI、Planner、公开/共享runtime均拒绝；DB许可只能HTTP Mock，错误契约保留。复用`research_v22/test_live_containment_sync.py`，真实Key/网络调用均0。批准文件、环境配置和CLI开关不能开启Live|

SHA、Fact ID、evidence_id和来源哈希都是绑定/定位信息，**不是授权凭据或数字签名**；模型只提交白名单工具、合法参数及已签发事实ID，不能提供分数或任意SQL/文件路径。

## 单一准确接口（测试宿主显式安装，正式app默认不挂载）

前缀 `B=/api/v1/edu/batches/{batch_id}`；实际schema仍用`docs/contracts/ai-investigation-tasks.openapi.synthetic.json`，不新增DTO。

|方法/路径|必需输入及边界|
|---|---|
|POST `B/ai-investigation-tasks`|snapshot_id、question、idempotency_key；仅http_mock，202后读取实际state；不接收前端Fact/统计/授权布尔|
|GET `B/ai-investigation-tasks/{task_id}`|query snapshot_id；核对state/error_code/stop_reason、执行来源、事实/范围/预算和未覆盖方向|
|POST `.../{task_id}/cancel`|query snapshot_id；不恢复额度|
|POST `.../{task_id}/records/query`|snapshot_id、fact_id、metric_key、offset/limit；只回放该指标实际参与评分，不凭群体成员身份替代来源|
|POST `.../{task_id}/review-submissions`|snapshot_id、fact_ids；仅返回pending回执与原正式路径|
|POST `B/groups/query`|沿用Fact.exploration_query，不改条件/批次/快照|
|PATCH `B/analysis-results/{result_id}`|snapshot_id、expected_version、decisions[{candidate_id,status}]；复用教师三态核验|
|GET `B/analysis-results/{result_id}/export`|snapshot_id、expected_version、format=md/xlsx；confirmed才进入正式发现，no-store，不能导出withheld原文|

人数分母须区分全范围N、条件可判定E、均值有效n及成对n；证据条数/score_record_count不得当学生人数。Fraction精确值保留，展示舍入不改变条件。分页只影响明细，不改变总量。教师confirmed不等于因果或教学效果证明。

## #65/#61/#71独立只读契约核对

- #65：确认绑定preview_version、警告确认、原字节SHA/content hash、parser/rubric版本与source坐标；上传/确认只授予既定批次权限，**不等于AI用途授权**。统一入口不代表任意格式已支持。
- #61：SavedResult绑定snapshot/conditions/版本，发现由当前授权数据重算并逐指标核验来源；confirmed经原核验机制，导出核查当前权限/版本。未发现本轮需要修改的正式服务文件，不据源码阅读代替作者之外的APPROVED。
- #71：QuestionAssessmentProvider重读原件/SQLite，lease绑定teacher/batch/snapshot/source/scope，Witness逐项可回放；但purpose仅local_deterministic_analysis、remote_model_allowed=false，历史为同服务实例内存。**不能包装成SyntheticAccess/AI grant，也不是持久跨进程模型披露/预算授权**。
- #71原输入仍是13题XLSX协议；保留实际题目和各自量表，不能映射成五维。没有确认测量等级时拒绝相关/群体差异，不能因数值量表存在自行宣称interval。逐题AI候选/教师报告承接尚无正式适配，必须unsupported，不近似替代五维。

2026-11-07远端审核：#65/#71无正式Review，#61仅作者旧提交COMMENTED，均未见准确head的独立APPROVED；源码核对不替代批准。

远端只见旧V1候选#68 f8e13f1，不能把它冒称含#71/Agent的V1.2新候选。

当前集成阻断：正式真实教师AI用途与Provider准入未实现，当前DB桥接只接受登记合成字节、五维1～5/0.01、5～100人。105人正式统计可用不代表本AI切片可用；不得裁剪为100或把教师原件临时登记为“合成”绕过。若统一候选声称开放真实/逐题AI，此项为P0放行阻断；若仅合成演示则保留关闭标志并明确限制。

## 合成执行证据与候选最终回归

- #75 [CI42018582584](https://github.com/ZAMYL/weiran-edu/actions/runs/42018582584)：Ubuntu/Windows各1718通过；这是历史准确SHA基线，不是本补丁或下一组合的CI。
- #75本机实际TCP：原问句422→补丁202，任务priority/completed，3 Mock请求/3工具、来源通过，bounded_scope_stop；真实Qwen not_run。#72准确757bfd8实际TCP85次预期响应，含两个独立Uvicorn进程重启、依据→pending→教师confirmed v2→MD/XLSX重开。均为临时SQLite和合成XLSX，不能冒充浏览器或真实教师验收。
- 新候选到达后先固定来源提交、工作区和runtime OpenAPI；缺本轮安全边界则记录阻断，不能临时改源码再称原候选通过。
- 定向运行原`tests/durable_teacher_agent`、`tests/teacher_agent_bridge`、#75 priority tests、本轮goal_request_boundaries、Fact篡改/来源重放、6/5预算及Live封禁测试；保留原Oracle和所有失败。再运行原TCP demo，核对身份/用途/快照/撤权/版本拒绝、零发现、缺证、同key/新key与服务重启预算。
- 对最终candidate再核对完整CI和实际前后端组合记录；浏览器由林明、FastAPI整合由彭仲楷负责，本轮未替他们完成组合验收。

本轮复用的事实篡改/实际来源重放、6/5共享预算/重启、全部Live入口否定专项：**16 passed（10.47s，最新补丁复测）**，本机`D:/Temp/edu-v12-existing-trust-gates-complete.xml`。这些是本隔离补丁的定向回归，不能写成尚未提供的统一候选结果。

**不可对评委宣称**：真实Qwen已自主调查、已证明优于固定5/9或Mock6/9、全部目标/调查方向已穷尽、任意文件或题目结构正式可用、真实教师数据已允许外发、统计相关代表因果、教师确认代表教学效果、哈希代表授权，或仅后端HTTP/CI通过就代表浏览器/产品验收通过。没有负责人单独授权，真实Qwen继续not_run。
