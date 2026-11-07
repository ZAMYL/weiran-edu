# 动态评分画像：合成原型与交接

基线 #76 dfce7cd8d0fefb5c722e77d0093b9ec7d889ee94。父链 #66/#67/#72/#76 仍是各自独立审核对象；本分支不更新它们。未继续 Issue #77。

## 真正新增什么

现有 Discovery 已能计算五维差异，但同一批成员会按不同结果维度分别出卡；Research 通用卡主要显示一次调查的单个结果维度。本原型把合格比较整理为“共同条件 + 完整五维摘要 + 互斥余组 + 实际来源”的群体画像，再让现有 Planner 选择核验。允许群体交叠，不给每个人强制分配类别，不生成能力等级。

只组合原 `discover`、`TargetedDiscovery.group`、`present`、`Kernel`、`ResearchExecutor/Planner`、共享会话、披露门禁和 CampaignBudget。没有新统计公式、KMeans、工具执行体系或模型框架。唯一原文件增量是合成注册表登记三个新输入及其受控项目名称（旧案例默认名称不变）；原 runtime、正式源码、API、数据库、预算、Oracle、前端不改。

## 规则和可信边界

- 仅注册的合成五维能力评分：I01～I05对应算法/实现/建模/效率/策略，原1～5、0.01量表；逐题资料明确拒绝，不推断能力维度。
- 候选预扫描复用原内核，条件搜索预算50、最多接收50条原发现（不是全部计算量）。模型工具预算仍最多6请求/5尝试，含1次服务端统计种子；预扫描、三次删组与验证重放另计，不把三次工具宣传成总共只做三次计算。
- 只提升已经合格的非筛选维度差异；沿用原最少有效样本、至少0.5分差、三次删组检查。目标组至少占范围1/5且不超过9/10，是本原型可版本化探索门槛，不是教学标准。
- 相同成员分区的多个合格语义合并为一张画像，余组在同卡展示，内部保留等价发现ID；不会让不合格结果占去重位置。相似成员集合Jaccard≥4/5过滤，最多六候选、最多三张选择结果；保留未调查数量，不宣称覆盖完整。
- 各画像所有维度的有效参与者集合执行原累计披露门禁，默认最小单元5；模型只见通过审查的聚合事实和候选查询，不见成员、Witness、来源定位、评语或候选预计算数值。候选方向不是本轮Fact。
- 模型只能提交已有白名单工具及合法参数/当前Fact ID；查询必须属于服务端候选。数值来自原工具；最终画像再次核对批次/快照、精确均值/人数/分母与实际评分Witness集合，不能仅比较均值或群体成员身份。
- 五维完整摘要来自确定性Discovery重放；Agent Fact只覆盖所调查的主要非筛选结果维度，二者明确分开，不冒称一条Fact已经签发所有指标。单项均值不是每位参评者都相同；筛选维度差异是条件定义结果。
- 原Live禁令、独占持久campaign和错误/取消不退款机制原样保留。新入口只接受HTTP Mock；CLI --execute明确拒绝。真实Qwen not_run，真实Token/费用为null，Mock usage/目录价预留不是实际账单。

## 评语为何未接入

正式后端已有 `published_text` 与 published/withheld 及审核版本；#72来源桥接只保留评分，不提供本用途可读的评语Provider。本原型不读取数据库或评语，不把withheld当作空文本，统一返回 `comment_state=unavailable_not_authorized`。

后续最小文本Provider必须由服务端提供当前教师/用途授权、batch/snapshot、对应记录/维度、证据ID、原文指纹、最新published审核版本、撤回复查及最小化片段。正文不能直接进模型，也不能根据未审核文本生成画像原因。本轮未实现该Provider。

## 运行与真实对照

仓库根目录（Python3.12、已有锁定环境），PowerShell：

```powershell
$env:PYTHONPATH='.;apps/api'
$env:PYTHONUTF8='1'
python -m prototypes.capability_profiles.demo --output-dir D:/Temp/edu-profile-demo-new
```

需全新输出目录。产生四份教师Markdown、四份技术JSON、原Research对照JSON与comparison.json（含源码SHA）；state中只有Mock合成预算记录，不得把它当正式授权。技术JSON记录原始协议请求、工具参数、执行/校验Trace、实际Fact与来源查询、模型/工具计数、预算、未调查数量、候选扫描和投影重放；教师版不显示工程字段。没有新Web页面、HTTP路由或真实文件读取入口。

|合成输入|独立设计/预期|固定Discovery默认20|原Research HTTP Mock|画像HTTP Mock|
|---|---|---|---|---|
|原53人|26/27；实现均值5/1、差4|2张群体卡，同一分区|1关联+1群体；3请求/3工具|1张整群五维卡；2/2|
|A：72人，seed811|三个24人家族各有±0.25变化，五维总体均值都为3|9群体卡，2成员分区|1关联+1群体；3/3|2画像；3/3|
|B：96人，seed821|独立二元条件，两组各48人，交集24；其他维度±0.10|8群体卡，7成员分区|1群体；3/3|3画像；4/4；仍有3候选未调查|
|C：81人，seed823|3^4平衡设计，第五项取模组合；评分2.75/3/3.25，每对9种组合各9人|0卡|0卡；3/3|0画像；概况后结束，1/1|

这是实际开发期四组运行，不是模型效果评测。B中一个所选语义未在默认20返回卡中，是候选搜索50与截断/排序差异，不是Qwen增量。原内核本身已提供全部使用的计算；未证明新增统计规律或真实AI优于旧策略。完整候选在A/B还受最多50原发现截断影响，不隐藏遗漏，不把筛选门槛外的群体称为不存在。

独立标准不调用待测discover：合成行用Decimal/Fraction直接比较条件、逐成员核对、逐维求均值/频数，另以48/48/24、26/27/4与平衡设计的人工可复核值校验。冻结#47 Oracle不变。开发期首轮出现普通子串`five`误报成场景泄露的测试失败，已修正为检查真实recipe字段/完整注册名，原失败JUnit保留本机；未删除安全断言。当前47项画像/原问句/Live定向回归已通过，CI以本PR精确head为准。

教师样例：算法评分≤2.25分的24人群体，实现均值4分，其他48人2.5分，差+1.5；共同条件只说明算法评分范围，其他维度是组均值。下一步核对实际评分依据与对应任务，不能推断具体代码错误、心理、AI依赖或因果。

## 交给现有产品的轻量内部契约

`ProfileCandidate`/`ProfileBook`仅服务端内部；`teacher_view`是只读投影，**不是正式公共DTO**。例如：

```json
{
  "profile_id": "profile-<server-derived>",
  "batch_id": "<server-bound>",
  "snapshot_id": "<source-fingerprint>",
  "title": "算法与数据结构≤2.25分的参评群体",
  "metrics": {"target_count": 24, "remainder_count": 48, "student_denominator": 72, "eligible_denominator": 72},
  "conditions": [{"dimension_code": "algorithm_data_structure", "operator": "<=", "value": "2.25"}],
  "dimensions": "五维：dimension_code/label、target/remainder的valid_count、mean、median、distribution及精确difference",
  "source_queries": [{"profile_id": "<same>", "metric_key": "target.IMPLEMENTATION"}],
  "comment_state": "unavailable_not_authorized",
  "production_enabled": false
}
```

上例只摘取字段，完整输出由演示生成。人数分母N/可判定E/维度有效n分开，Fraction分母不是人数；显示两位不改变精确筛选。内部score引用只由`resolve(book,id,metric)`重放后读取，不能作为已存在正式evidence_id或权限凭据。

- LiveDiscoveries：复用卡片/详情行为承载title、共同条件、why_show、范围、五维摘要、限制；profile_id不是当前Discovery ID，禁止直接拼现有详情URL。
- LiveExplorerPage：用exploration_query保持原batch/snapshot和条件；正式Provider重算后才能转换为已有groups/query请求。小数按现有阈值契约传递，不能先舍入。
- StudentPortrait：继续只展示授权单人原始评分。本模块是群体摘要，不能把组均值冒充某人画像，也不在卡中公开成员ID。
- AiInvestigationPanel：已只读核对#74准确364e648的ai-task-types：Task→Fact→source_refs/exploration_query及pending回执。当前schema没有profiles，本输出不能直接塞进Task.facts；需后端审查后增量投影。主结果Fact可经原正式重算匹配后成为pending，整张新画像尚不能保存为confirmed或导出。本轮无接口改动，林明无需立即改页面。

后端最小接线仍需获授权的五维评分Snapshot Provider：Principal与AI用途、batch/snapshot/source绑定、真实量表/维度映射、逐评分Witness重放、当前撤权、共享持久预算和跨请求累计披露。当前只接受AdmittedSynthetic；不能把recipe换成客户端JSON或真实教师原件。正式来源/用途与核验承接未审前入口保持关闭，等待非作者独立审核，不合并、不部署。
