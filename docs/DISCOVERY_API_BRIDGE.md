# 正式数据发现：第一阶段装配与第二阶段 API 契约

负责人 Peng-Kai789；Issue #42；基于 main a7a6c281。与 ZAMYL 的 #41/林明前端衔接。
**本 PR 只交付装配层、请求/范围/摘要/来源 DTO、分页工具及测试，不注册生产发现路由。**
发现算法仍由未合并 #41 负责；不复制、不导入其 discovery 包，不更改 AI 核心。
下面四条 POST 是第二阶段设计，当前调用仍为404；生产 OpenAPI 仍30个操作。
测试路由是测试内显式挂载的合成卡片 harness，不能当作发现引擎真实接线验收。

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

## 最小调用契约（第二阶段，待实现）

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
阶段二需校验当前内核rule_version、规范化公开投影，并在最终返回前保留 snapshot 的新事务复核。

范围条件与发现内部条件分开：scope_conditions决定N，finding.conditions决定该范围内目标群体。
scope.batch_record_count是原班级人数；scope.scope_record_count是内核本次范围N。
scope.eligible_count/unevaluable_count仅描述外层scope_conditions过滤的可判定/不可判定人数，
不是某张卡片内部的E/U；卡片自己的目标/余组/可判定人数必须从该卡metrics读取。
进入已有 groups/query 时，需要服务端将原范围与发现条件合并后重放；既有群体接口最多2维，
合并后超过2维或同维有不同边界，**不能覆盖、丢条件或直接发给旧接口**。
此时records/query是权威名单；第二阶段另行设计受控原条件重放，未完成前不显示错误“同群体”跳转。
少见差值围栏不能用两个单维阈值等价表达，也必须使用发现来源查询，不能伪造 AND 条件。

## 与 #41 的公开投影适配

本阶段已实现 DiscoverySummary，完整分型详情/图表的序列化器在内核合并后实现。
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
这不替代内核的逐指标参与者校验：把同批同群体其他人冒充某指标参与者必须由sources拒绝，
第二阶段应接上并做端到端反例；当前不能宣称阶段一已验证未合并内核的来源语义。

## 分页、预算、超时与错误

- 列表 offset>=0、1<=limit<=50；在完整计算/排序/保留后分页。page.has_more基于retained_count，
  不基于裁剪前 qualified_count；truncated显式表示内核最大保留数造成的截断。空发现200+items=[]。
- 来源分页同样先验证完整来源集合；total_count不等于单页items.length，也不等于Fraction.denominator。
- 第一阶段装配预算：最多1666名正式学生（既有10000行模板/6）、并发2、返回JSON最多1MiB。
  当前 snapshot 实现计算结束后的15秒计时拒绝，**是软超时，不能终止尚未返回的回调**。
- 第二阶段执行器是接线门槛：独立子进程硬15秒、父进程终止/回收、请求取消清理、每worker并发2；
  服务端固定搜索20（硬不超过50），保留至多50，边界请求至多20，工作估算预算需接内核后实测。
  未实现硬执行器前不注册生产发现路由；不能用线程超时假装CPU已停止。生产多worker总并发由运维限制。
- 当前不提供用户自定策略、任意student_ids、公开分享、远程模型模式或自动持久化；无新增数据库表。

| HTTP/代码 | 规则 |
| --- | --- |
| 401 UNAUTHORIZED | 缺少/过期/撤销凭证或停用教师 |
| 404 RESOURCE_NOT_FOUND | 未授权批次、未知发现/metric_key、跨批次资源统一拒绝 |
| 409 SNAPSHOT_MISMATCH | 当前批次快照不符；不同范围/profile导致ID找不到则404，不回退默认范围 |
| 413 DISCOVERY_RESPONSE_TOO_LARGE | 返回载荷超过1MiB |
| 413 REQUEST_TOO_LARGE | 请求体超过64KiB（第二阶段路由预算） |
| 422 VALIDATION_ERROR | 参数/维度/操作符/精度/未知字段/分页非法 |
| 503 DISCOVERY_BUSY | 计算槽繁忙 |
| 503 DISCOVERY_LIMIT_EXCEEDED | 输入或内部保留数超过服务端预算 |
| 503 DISCOVERY_TIMEOUT | 超时不返回半成品或空发现 |
| 503 DATA_INTEGRITY_ERROR / DATABASE_UNAVAILABLE | 数据损坏、来源不一致或DB不可用 |
| 500 INTERNAL_ERROR | 意外错误统一脱敏，不返回原始异常 |

私有分页工具对超出50的内部调用返回413；公开DTO在路由解码处对此返回422。
401/404/409/503与空发现200不能互相替代，不把异常转换成“未发现问题”。

## AI隔离与对齐状态

正式数据只供本机确定性统计/发现。该层没有Qwen、模型Client或网络工具，也不读评语。
现有Qwen合成流程保持原状；不能用synthetic_only布尔参数把正式Dataset改名外发。
任何真实数据远程模型请求、精确散点外发、分享或Agent接线均另行评审。

已在 #41 向 ZAMYL/AooMing 发送 DTO/范围/来源对齐提议，尚未把发送提议视为对方确认。
当前 #41 存在已复核的分区提前去重漏检问题（见审核）；内核修复合并前不进入第二阶段。
最终PR会再次同步本文链接，供ZAMYL确认及林明接入设计。

验收：`pytest tests/edu/test_discovery_bridge.py -q` 使用完全合成Excel、真实FastAPI/Actor/SQLite，
测试专用卡片provider明确隔离。覆盖owner/read、撤权/过期、错快照、空范围/空列表、分页、
53人精确均值619/212、证据归属、损坏数据、私有列禁止访问、预算及未注册生产路由。
