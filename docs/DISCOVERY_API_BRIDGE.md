# 正式数据发现：第一阶段装配与第二阶段 API 契约

负责人 Peng-Kai789；承接已原则认可的 #44 / Issue #42，第二阶段 Issue #48。
基于 #41 已合并的最新 main `761d209e84fc35a4c69b47e0bf902819e15f539e`，复用第一阶段装配层，接线到 `edu-discovery-0.1.1-draft`。
**本分支已注册四条真实 FastAPI 发现路由，带正式授权和真实子进程内核；提交独立 PR，不自动合并。**
本分支生产 OpenAPI 为34个操作；主线在本PR合并前仍为30个，#44单独交付不等于正式发现API完成。
不修改 apps/web、AI 发现核心、数据库结构、认证和Qwen模式。

## 正式数据库如何装配

`app/edu/discovery_bridge.py:DiscoveryInputReader.snapshot(actor,batch_id,spec)`
复用 BatchReader._batch → 当前凭证/教师/来源成员/批次 owner或显式read授权 →
validate_snapshot 完整性 → AnalysisReader._load，均在同一只读事务内。

| 内部字段 | 正式来源与规则 |
| --- | --- |
| Dataset.policy | 1—5，step=Fraction(1,100) |
| record.batch_id | 当前授权批次，客户端不得声明另一个来源 |
| student_id / record_id | students.public_id，当前一批一人一记录；未来多记录必须重审 |
| source_id | batches.file_sha256，内部使用，不暴露给发现列表或模型 |
| score.value | evaluations.score_units/100 精确 Fraction，不从展示浮点重建 |
| EvidenceRef.location | evaluations.evidence_id，kind=SCORE，带实际维度及来源摘要 |
| scope_conditions | 最多两维 AND，复用既有 FilterCondition，英文编码/操作符与0.01验证 |
| student_ids | 服务端从完整 Dataset 按 scope_conditions 求出的分析范围；空 tuple确实为空 |

完整批次 Dataset 先装配，再选择范围；班级/范围统计均调用既有确定性统计程序。
不从 GET /students 的分页 items 重建 Dataset。学号、姓名、评语、依据正文、Excel BLOB不读取。
正式数据完整性要求每人完整合法五维；缺失/无效的损坏正式记录返回503，不降级为内核质量提示。
内核本身支持缺失值不等于正式数据库允许破坏入库约束。

input_key=SHA256(批次、快照、原文件摘要、服务端profile、按维度排序且阈值按整数百分位规范化的范围条件)。
2.75与2.750、AND条件次序不改变key。key不是授权或持久保存ID；内核 discovery_id也不是权限凭证。
数据对象 repr隐藏，不返回 Dataset/RecordRef 给前端或日志。

计算前关闭读取事务；正常退出 snapshot 时开启新事务，再核验当前授权、完整性及完整数据对象相等。
期间撤销Token/停用教师→401；撤销批次read或来源成员→通用404；不返回已算好的旧结果。
不存在缓存授权、SQLite结构变更、独立认证或新管理员HTTP入口。

## 最小调用契约（本分支已接线）

公共前缀 `/api/v1/edu/batches/{batch_id}`，继续使用现有 Actor/Backend依赖。
所有输入严格 JSON、拒绝未知字段；认证先于解码；请求体64KiB，no-store及服务端X-Request-ID。

| 请求 | 请求体 | 成功结果 |
| --- | --- | --- |
| POST /discoveries/query | DiscoveryQuery | DiscoveryList：scope、rule_version、qualified_count、retained_count、truncated、items、page |
| POST /discoveries/{discovery_id}/query | DiscoverySpec | 同一原始范围重算、精确ID查找的完整详情，未知ID404 |
| POST /discoveries/{discovery_id}/chart/query | DiscoverySpec | 完整聚合图表，无学生ID/证据定位，不能截断成单页图 |
| POST /discoveries/{discovery_id}/records/query | DiscoveryRecordsQuery | 来源全量核验后分页，total_count=该指标实际参与人数，items为匿名学生及对应维度evidence_id |

```json
{"snapshot_id":"当前快照","profile":"edu.discovery.profile.v1","scope_conditions":[],"offset":0,"limit":20}
```

详情/图表/来源必须携带**列表当时同一 DiscoverySpec**，在完整范围重新运行同一服务端profile、
rule_version和排序/保留策略后解析ID；不允许只拿ID访问，不接受客户端的统计、成员、RecordRef、评分或权限声明。
服务端核验当前内核rule_version、策略、完整scope引用及每条发现的context，再作分型白名单投影。
最终返回前保留snapshot的新事务权限与完整性复核。每次查询均重新计算，不缓存权限或旧发现。

范围条件与发现内部条件分开：scope_conditions决定N，finding.conditions决定该范围内目标群体。
scope.batch_record_count是原班级人数；scope.scope_record_count是内核本次范围N。
scope.eligible_count/unevaluable_count仅描述外层scope_conditions过滤的可判定/不可判定人数，
不是某张卡片内部的E/U；卡片自己的目标/余组/可判定人数必须从该卡metrics读取。
进入已有 groups/query 时，需要服务端将原范围与发现条件合并后重放；既有群体接口最多2维，
合并后超过2维或同维有不同边界，**不能覆盖、丢条件或直接发给旧接口**。
此时records/query是权威名单；第二阶段另行设计受控原条件重放，未完成前不显示错误“同群体”跳转。
少见差值围栏不能用两个单维阈值等价表达，也必须使用发现来源查询，不能伪造 AND 条件。

## 与 #41 的公开投影适配

列表仍使用已认可的 DiscoverySummary；详情为扁平 DiscoveryDetail，顶层包含scope、metrics、chart、conditions、parameters、ranking、limitations和controlled_queries。
图表接口顶层保留同一chart、metrics、conditions和scope，不另造charts字段。四类metrics与chart由严格DTO校验并核对type，不透传内核对象。
证据返回DiscoveryRecords，补充discovery_id、rule_version、conditions及source_dimensions，参与人数与分页仍沿用第一阶段契约。
保留 title/summary/status/rule_version/parameters/ranking/limitations/public_display_allowed=false；
`input_key` 补足范围绑定。内核使用 `chart`（单数），公共详情也采用 chart，不另造charts同义字段。
摘要列表不包含图表逐点数据或来源定位；详情和图表按分型白名单映射，禁止任意dict透传。

| 类型 | 必须保留的完整图表/分母 |
| --- | --- |
| dimension_relation | 两维完整稀疏散点(x,y,count)、成对有效/排除人数、N，rho保留stdlib_float方法 |
| group_difference | 目标/互斥余组各维完整分布与均值精确值、有效/缺失/无效分母；condition_defined显式标识；M+R+U=N，M/N与M/E分开 |
| rare_combination | 全成对差值分布、围栏精确值、成对分母与命中人数；rare_members和pair_reference不可混用 |
| boundary_sensitivity | 原始strict/inclusive条件、完整命中数与added数、N/E/U；不能把added人数当班级人数 |

Fraction序列化为 numerator/denominator/display；学生人数分母单独命名。
浮点Spearman没有伪造exact Fraction，使用 `{value,method:"stdlib_float"}`。
内部 Dimension.ALGORITHMS等映射为既有英文维度码；操作符<、<=、==、>=、>映射lt/lte/eq/gte/gt。
受控metric_key作为不透明服务端键保持原值，例如 paired_scores、target.IMPLEMENTATION、
rare_members、pair_reference、strict_members、inclusive_members、added_members，不允许任意键猜出来源。

`resolve_sources`只接受**服务端内核 sources(finding,metric_key) 的真实参与者及维度**。
阶段一已验证每个 RecordRef 都等于该正式范围的规范引用，且评分维度、来源摘要、匿名学生、
evidence_id与授权DB一致；随后才分页并返回匿名评分链接。再次GET既有证据接口仍重新鉴权。
正式接线先调用内核sources；同批或同组但未参与该指标的记录不能替换来源。
随后装配层对完整参与者集合核正式DB归属，再分页；不是只验证当前页。此顺序有实际HTTP反例验证。

## 分页、预算、超时与错误

- 列表 offset>=0、1<=limit<=50；在完整计算/排序/保留后分页。page.has_more基于retained_count，
  不基于裁剪前 qualified_count；truncated显式表示内核最大保留数造成的截断。空发现200+items=[]。
- 来源分页同样先验证完整来源集合；total_count不等于单页items.length，也不等于Fraction.denominator。
- 复用装配预算：最多1666名正式学生、每ASGI worker并发2、公开JSON最多1MiB、请求体64KiB。
- 固定profile：搜索20条规则、最多保留50项、最少有效样本5人、支持目标20人，其余阈值复用内核默认值。客户端不能调整这些预算。
- 整体snapshot仍保留15秒复核上限；CPU发现由spawn子进程执行，父进程按剩余时间硬终止。15秒预算包含子进程启动和传输等待；终止/kill/join及管道清理最多另需约2.1秒。超时不返回部分发现或空列表。
- 接收线程只负责读取可信本机子进程传输，不执行发现算法；超时/取消时先终止真正的CPU子进程，再关闭管道。内部传输上限32MiB，不接收客户端pickle。
- 断连观察器位于BaseHTTPMiddleware外层，仅在既有认证+大小校验读取完最后一块请求体后监听http.disconnect；不预读、不缓冲请求体。断连返回内部499取消结果，避免留下等待响应的请求；真正任务取消仍传播并在shield内回收进程和计算槽。
- 每worker的2个计算槽不等于整个多worker部署的全局并发；部署方仍须控制worker数量。没有新增队列、缓存表、独立服务或认证。
- 本机完全合成预算实测：53人、1666人五维两极样例分别约0.902秒、3.430秒，均得到45项事实。该测量不是生产延迟承诺；CI仍验证确定性错误/预算而不依赖机器速度阈值。
- 当前不提供用户自定策略、任意student_ids、公开分享、远程模型模式或自动持久化；无新增数据库表。

| HTTP/代码 | 规则 |
| --- | --- |
| 401 UNAUTHORIZED | 缺少/过期/撤销凭证或停用教师 |
| 404 RESOURCE_NOT_FOUND | 未授权批次、未知发现/metric_key、跨批次资源统一拒绝 |
| 409 SNAPSHOT_MISMATCH | 当前批次快照不符；不同范围/profile导致ID找不到则404，不回退默认范围 |
| 413 DISCOVERY_RESPONSE_TOO_LARGE | 返回载荷超过1MiB |
| 413 REQUEST_TOO_LARGE | 请求体超过64KiB（第二阶段路由预算） |
| 422 VALIDATION_ERROR | 参数/维度/操作符/精度/未知字段/分页非法 |
| 499 DISCOVERY_CANCELLED | 客户端断连，计算已取消，不返回发现；已断开的客户端通常收不到此响应 |
| 503 DISCOVERY_UNAVAILABLE | 子进程启动、传输或异常退出失败，不伪装空发现 |
| 503 DISCOVERY_BUSY | 计算槽繁忙 |
| 503 DISCOVERY_LIMIT_EXCEEDED | 输入或内部保留数超过服务端预算 |
| 503 DISCOVERY_TIMEOUT | 超时不返回半成品或空发现 |
| 503 DATA_INTEGRITY_ERROR / DATABASE_UNAVAILABLE | 数据损坏、来源不一致或DB不可用 |
| 500 INTERNAL_ERROR | 意外错误统一脱敏，不返回原始异常 |

私有分页工具对超出50的内部调用返回413；公开DTO在路由解码处对此返回422。
401/404/409/503与空发现200不能互相替代，不把异常转换成“未发现问题”。

## 前端调用与原条件重放

1. POST discoveries/query，保存返回的scope_conditions、snapshot_id、profile及发现ID。
2. 用同一个DiscoverySpec请求详情或chart。详情顶层chart与图表接口顶层chart完全相同；分母来自metrics，不能用单页items数量替代。
3. 从controlled_queries选择内核返回的metric_key，带同一个DiscoverySpec进入records/query。群体用target_members，少见组合用rare_members；pair_reference是完整成对参考集，不能当少见人数。边界新增用added_members。
4. 每项scores中的evidence_id可继续调用既有GET证据接口；该GET仍独立重新鉴权。

scope_conditions与conditions均保留，不能把发现内部条件覆盖原分析范围。records/query直接重放同一范围的内核原条件和来源，是正确群体名单的入口；超出旧groups/query两维限制、同维不同边界或差值围栏时，不拼出有损的旧接口请求。

边界发现由固定profile自动为外层scope_conditions中已有lt/lte条件生成BoundaryRequest，在同一授权范围内比较严格/包含边界两组；没有客户端任意规则入口，也不修改分数。

林明前端现有同源代理需要由前端负责人加入四条POST白名单，本PR没有修改apps/web。正式发现目前为重算查询，不自动写入旧analysis-results、教师核验或报告流程。页面接线和统一联调在PR交付后进行。

## AI隔离与验收

正式数据只在本机确定性统计与spawn子进程中分析。装配与执行均不读姓名、学号、评语、依据正文或原文件BLOB；不包含Qwen、网络Client或远程模型调用。现有Qwen合成模式完全不改动，真实数据模型调用必须另行评审。

第一阶段test_discovery_bridge.py保留明确的测试harness；它只验证已认可装配行为。
第二阶段test_discovery_api.py使用真实生产FastAPI路由、临时SQLite及合成Excel，通过真实spawn内核覆盖四类发现、精确分母、分页、owner/read授权、撤权、错快照、未知发现和metric、数据损坏、敏感列禁止读取、完整来源先验证后分页。
test_discovery_worker.py运行真实子进程超时/异常退出/任务取消，并在完整FastAPI ASGI调用链模拟客户端断开，核对请求退出、进程回收及全部计算槽归还。

```powershell
uv run --locked pytest -q tests/edu/test_discovery_api.py tests/edu/test_discovery_worker.py tests/edu/test_discovery_bridge.py
```

核查实例包括：53人均值619/212；20人原漏检样例得到一项算法差-4的群体事实；17人少见组合的rare_members人数1与pair_reference人数17分开；边界严格/包含及added集合使用相同授权范围。所有样例完全合成，不读取教师原始Excel。
