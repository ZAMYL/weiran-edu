# Research Agent V2.2：Live预检与Provider交接

隔离分支基于 #66 f3799a1；#66原工作区和#59不改。本轮是可运行的合成验证准备，正式教师前端、路由、数据库和权限未接入。没有新的Agent循环，复用ResearchPlanner/QwenAdaptivePlanner、原_run_session、工具、来源重放与CampaignBudget。

## 运行

在本工作区根目录，Python3.12、原锁文件：

```powershell
$env:PYTHONPATH='.;apps/api'
uv run --project apps/api --locked python -m prototypes.research_agent_v22.evaluate --preflight-only --output D:\Temp\v22-preflight-new.json
uv run --project apps/api --locked python -m prototypes.research_agent_v22.evaluate --output D:\Temp\v22-comparison-new.json
uv run --project apps/api --locked python -m prototypes.research_agent_v22.profile --output D:\Temp\v22-profile-new.json
uv run --project apps/api --locked python -m prototypes.research_agent_v22.provider_demo --output D:\Temp\v22-provider-new.json
```

所有输出拒绝覆盖。默认不读Key、不创建真实网络Client、不执行付费请求。A/B通过真正HTTP协议的MockTransport执行既有模型发送器；C明确not_run。评测默认输出JSON、简短Markdown、完整技术Trace；profile和Provider分别输出可复核JSON。

## 固定实验和独立判分

固定一例：research-v21-review-decimal，40人、4道题、0～10、0.5步长，输入指纹见preflight.plan。来自V2.1隔离审核者；该审核者未参与策略调试，但曾看到统计Kernel，不是完全盲测。案例已经做过离线评分，本轮不再按其结果改策略，不把它宣传为全新未见泛化测试。

A为原AdaptiveFake有限策略，B为ResearchMock，C为同一ResearchPlanner的真实Qwen发送路径。三组同输入、受控问题、初始概况、工具、6模型/5工具尝试、800输出Token及同一价格/预算配置。5工具含服务端初始概况。固定策略不是穷尽搜索。

冻结的9个目标保持不变，未执行也留在分母。新增scoring适配合法其他阈值/比较符，不用Kernel或模型输出生成答案。数值用独立Fraction/秩矩参考核对，来源按输入行和值校验，群体对照还核对实际成员和互斥余组；条件语义与结果项目共同去重。四等宽分布按既有工具声明独立检查，未映射来源记错误。重复包装summary已有缺分计数不算新事实。

实际离线：A为5/9、B为6/9，B新增一个合格联合条件对照，无基线遗漏，双方错误/来源错误为0；仍有3个目标未覆盖。C not_run，真实模型增量价值未证明。停止判分分开记录预算/覆盖陈述是否一致、仍可续查时主动停止及目标遗漏；冻结答案没有唯一的教学停止策略，因此独立语义停止准确率为null，不能把服务端自述当标准答案。JSON保留真实提议、执行、事实验收、未覆盖范围、停止状态、计量和全部请求。HTTP Mock Token/费用不作真实账单。

## 官方价格、上界和批准

2026-11-04只读核验：[模型说明](https://help.aliyun.com/zh/model-studio/qwen3-8-flash)、[统一价格](https://help.aliyun.com/zh/model-studio/model-pricing)。北京、同步非思考文本，qwen3.8-flash：输入0.8、输出2.7、缓存命中0.1元/百万Token；最大输入991808。不使用Batch、显式缓存创建或折扣假设。

新增RESEARCH_PRICE_20261104，旧PRICE及旧授权保持原指纹/原过期日。新快照的2026-11-11是项目7日复核窗口，**不是官方价格保证有效期**。授权按指纹解析受控注册表，预算检查/预留/结算/剩余量同读budget.price；未知价格拒绝。授权失效不能只换日期继续。

|项目|建议首轮上限|
|---|---:|
|真实模型请求|6，不自动重试|
|工具尝试|5，含初始概况|
|每请求输出|800 Token|
|每请求最坏输入+输出|992608 Token|
|整轮最坏Token|5955648|
|每请求目录价最坏上界|0.7956064元|
|整轮目录价最坏上界|4.7736384元|
|拟批准人民币硬上限|4.78元|

第一次请求仍按模型最大输入保守预留，至少需要992608 Token及0.7956064元容量；不能按实际短问题猜小输入上限。实际用量缺失保留全额预留并停止。有效usage对应目录价上界与实际账单分开记录；没有账单时invoice_cost_cny=null，不能造真实费用。

本轮没有获得付费授权。预检输出只是待批计划，不是授权对象。当前进程未配置Base URL；也实际预检了负责人此前提供的北京业务空间端点，仅保存哈希，不证明当前账户/Key可用。实际执行时服务端Base URL必须与批准哈希一致。

后续操作员提供PilotApproval（本机服务端文件，不含Key）：plan_sha256、已审核code_revision、authorization。其中authorization沿用原SyntheticLiveAuthorization，绑定campaign、负责人批准引用、模型、端点哈希、唯一合成来源指纹、新价格指纹、有效期和上表全部上限。不能复用旧campaign；账本独占，不因重启或更换报告路径恢复预算。人类批准引用由受信本机操作员维护，不是密码学授权证明，不可交给前端自由填入；本机管理员主动删除账本/更改用户配置不属于防篡改账单服务的保证。

只有负责人另行批准、#66和本后续PR均由Peng-Kai789在准确提交独立APPROVED、工作树干净后，才能运行：

```powershell
# 本轮禁止执行；authorization由另行批准后的操作员创建。
python -m prototypes.research_agent_v22.evaluate --execute --authorization LOCAL_APPROVAL.json --review-pr REVIEWED_PR_NUMBER --output NEW_LIVE_RESULT.json
```

入口从GitHub读取实际审核状态（不是客户端布尔值/审核JSON），验证计划/提交/授权后才让原Planner读取Key。Key仅来自DASHSCOPE_API_KEY，Base URL来自DASHSCOPE_BASE_URL，模型WEIRAN_QWEN_MODEL必须匹配。模型/同步/非思考/输出Token等保留字段不得被payload覆盖。Live账本固定在本机受控research-v22-live-ledgers目录，与输出路径无关；相同campaign换输出路径仍被拒绝。取消/异常保留按campaign和本次execution_id绑定的failure.json与共享预算，不返回伪成功文件；最终状态失败则命令非零退出。辅助失败记录写入失败不会丢掉本次已尝试状态；仅预算预留而无传输记录时标为request_reserved_transport_unconfirmed，不冒称已请求或未请求。审核只是运行前检查，不能冒充正式教师撤权服务。

## 52次重放与性能判断

实测48人3次工具尝试对应20次ResearchExecutor._verify调用。每次都会重放当前整个Fact账本，而非只检查传入的一个Fact：

|调用位置|verify调用|重放工具执行|
|---|---:|---:|
|规划前decide|6|14|
|目标反思reflect|9|23|
|最终选择finish|2|6|
|报告投影fact_record|3|9|
|合计|20|52|

profile同时观测到118次AdmittedSynthetic.validate、372次Kernel.statistics、44次relation、36次group。这些包括目录准备和多层复核，不是新增调查动作；不能把52重放说成全部计算量。逐项计时及上下文留在JSON。sys.setprofile会增加开销，不能把该用时与无profile延迟直接比较。

存在同账本重复验证，但Fact.view/query等为可变对象，累计披露/字节计量也会变化；未来Provider还需每步查撤销状态。没有证明不可变快照、Fact账本epoch、披露epoch和授权revision的完整失效机制前，不做缓存或减少重放。checks_cached_or_removed=0。下一步性能优化应独立证明所有篡改/撤销都会使缓存失效，而非按Fact ID缓存。

## V1.1最小Provider契约与实际样例

provider.py提供AssessmentProvider Protocol：acquire、assert_current、reserve_tool、commit_disclosure、replay_witness；SyntheticProvider只接受注册的封闭输入，禁止任意客户端JSON或文件准入。AssessmentSnapshot Protocol声明fingerprint、Canonical Envelope及validate，不强迫未来来源伪装为recipe。当前Probe仍仅准入确切AdmittedSynthetic类型，其他实现明确拒绝，未来正式Provider必须另行审核。Lease只由受控Provider签发，构造Probe时再次校验source/batch/snapshot/scope；每次工具先查修订/撤销及共享工具次数、实际来源，重放后提交累计披露，失败回滚新事实并阻止继续。

Binding具体包含batch_id、snapshot_id、source_sha256、scope_sha256、authorization_revision、campaign_id、disclosure_sha256、purpose、rule_version。内部actor不是登录凭证，不应直接作为公共DTO。单批次/完整合成范围，不支持任意学生子集或跨会话继承。

provider_demo是真实可执行的服务端接线示例：获取Lease → 读取实际项目/量表和能力 → 受控Goal → 统计 → 两条件群体对照 → 按指标Witness核对来源 → 撤销后拒绝新工具。示例输出40人、Q01/Q02<=0时目标10/其余30，Q03均值2.5/7.5、差-5。输出只含聚合、受控查询与来源数量，不含逐人评分、Witness原文或内部定位。样例也包含按当前权限重放后的internal_card_projection（实际数字、限制、Fact来源查询）与真实tool_execution_ledger，供前端对齐字段，不能当作已上线公共DTO或AI任务进展。完整可交接JSON见evals/results/research_v22-provider-example-20261104.json。

对照当前main的CANONICAL_ASSESSMENT_MODEL/ANALYSIS_READINESS与app/edu/analysis.py：正式服务仍为固定五维读取，未见已合并的动态项目HTTP DTO。因此本契约对齐已有Canonical对象，**尚未取得仲楷对正式动态接口的确认**。请Peng-Kai789审核以下边界：

- 后端在既有授权/撤销和完整性事务之后提供不可变Canonical快照，保留实际AssessmentItem/Scale、精确Observation及可回放Witness；withheld正文不能伪装为空。
- 真实项目ID若不符合旧工具IQ编号约束，需稳定、可逆且获确认的别名映射；当前没有偷偷改正式ID或截断为五维。源哈希、项目/量表版本和实际授权成员范围需一起绑定。
- 正式Provider必须服务端验证教师/用途/项目/群体范围，提供原子披露与共享预算上下文；真实来源Provider、跨会话披露承接和多来源清单的正式适配尚未实现。
- 前端将来只消费获授权聚合结果、真实任务状态和已验收发现引用；不能上传授权布尔值、Fact JSON或旧ID来签发新事实。真实教师资料外发仍关闭。

本轮测试与完整结果在PR与evals/results，含成功、拒绝、预算/来源边界。独立静态审核不代表Peng已批准；没有真实Qwen结果或教学效果证明，不部署、不合并。


## 本轮实际验证记录

最终V2.2专项20 passed（59.67秒）；更早的共享Research/adaptive回归180 passed（432.98秒），随后Live安全子集68 passed（124.30秒），上述中间回归与最终源码版本的区别由PR说明；完整CI以精确最终SHA为准，不把历史CI替代本轮。

独立只读审核者逐项复核并关闭了Lease异源替换、Live账本随报告目录重置、旧失败串入本次运行、辅助失败日志写入错误掩盖已调用状态等问题；对应安全回归已纳入专项。它未执行模型或代表Peng批准。人工审核与负责人付费授权仍待完成。

待批文件结构见evals/research_v22_approval.example.json，含故意无效的占位值，不能直接执行或当作批准。本轮官方资料核验、所有对照、Provider例子和重放分析均未读取老师原件、数据库或API Key。
