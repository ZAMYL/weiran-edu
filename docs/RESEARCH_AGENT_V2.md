# Evidence-Driven Research Agent V2（隔离合成原型）

> 安全收口：旧 `--execute` 和底层 Synthetic Live 现已全部封禁；下文历史 Live 说明不再是可执行授权。仅 HTTP Mock 可运行。见 [P1 修复与依赖说明](RESEARCH_LIVE_P1_CONTAINMENT.md)。

基于main 5218af4的#49/#56/#58；不依赖未合并#47/#59运行代码。没有HTTP路由、前端、数据库或正式教师权限改动。唯一共享变化是synthetic.materialize对封闭V2配方的分派，原配方不变。不是生产安全能力。

## 可运行链路

自然语言（有限中文/英文词法规则）→受控Goal→服务端统计概况→原生Function Calling→白名单工具→真实计算及来源/累计披露重放→服务端Reflect→模型继续或finish→程序投影JSON/Markdown。

支持关联、群体差异/条件组合、缺分/样本核查、优先调查；原因问题只能形成观察和缺证状态。未知项目、SQL/文件操作、跨批次企图及不支持问题拒绝。原问题不发送模型，只发送类型化目标及合法项目。不是通用自然语言理解，不靠黑名单替代工具校验。

复用原_run_session、AdaptiveExecutor、QwenAdaptivePlanner、CampaignBudget。没有新统计公式或第二套Agent循环。V2为summary/support也重放实际工具，核对数值、查询、来源、披露及计量。校验重放单独计数，有真实CPU成本。

一轮最多6个模型尝试、5个工具尝试，包含服务端初始概况；失败/拒绝也计数，重建客户端不能恢复campaign。初始事实同轮重算，不导入旧Fact ID，不实现#59跨会话承接。最多展示3卡，所有合法观察仍保留，未展示ID明列。完成比较不等于发现异常，更不等于证明原因。

## 两种动态结构与命令

PowerShell，仓库根目录，Python3.12和原锁定依赖：

    $env:PYTHONPATH='.;apps/api'
    uv run --project apps/api --locked --no-sync python -m prototypes.research_agent_v2.demo --output D:\Temp\research-demo-new.json
    uv run --project apps/api --locked --no-sync python -m prototypes.research_agent_v2.demo --recipe research-v2-questions --question '核查所有项目的缺分及有效样本' --output D:\Temp\question-demo-new.json
    uv run --project apps/api --locked --no-sync python -m evals.research_agent_v2 --output D:\Temp\research-eval-new.json

输出JSON和同名Markdown，拒绝覆盖旧输出。也可使用已有锁定解释器运行python -m命令。默认真实HTTP协议结构的MockTransport，无网络、不读Key、不付费。

- 能力项：48人、4项；问题“比较I01、I02的组合与I03的群体差异”。概况→选择I01/I03关联（结果0）→选择两条件比较→finish。12人与其余36人，均值1和11/3、差−8/3，由原工具计算。
- 逐题：40人、7题、0～100量表。全项目缺分目标由原概况满足：Q07有效30、缺失10；只finish，不把同样数字重包装为AI新增。
- 相同优先核查目标下，HTTP Mock依据真实聚合返回选择关联→两条件比较，或关联→停止/质量核查。响应器是公开的数据驱动确定性策略，不是Qwen推理证据。

没有固定五维或13题映射，不做跨量表综合等级。封闭配方覆盖3～11项目、32～80人，这些只是测试输入，不是系统人数配置。不是任意文件上传平台。

## 结果与来源

Goal、保留观察、新Fact、重点卡、缺证/不支持解释、下一步核查、停止原因，以及工具账本、Trace、逐轮Reflection、原生请求、预算。

每个观察绑定batch/snapshot、完整注册合成范围、实际查询/条件、精确指标/分母/有效与排除、规则版本、方法、逐指标来源数量和受控查询引用。executor.resolve可重放回查。模型只选择工具/参数/当前Fact ID，不创造数字或来源。个人行、评语、证据定位不进入模型视图。

## 独立对照与失败

evals/research_agent_v2_manifest.json在首次最终批次执行前随a050dba封存。与开发样例不同的64人8项、80人11题、36人4项为最终批次。标准答案用独立Fraction/并列排名参考和手算分区，不调用待测discover/Kernel/Planner生成。原#47 Oracle未改。

A为main已有AdaptiveFake有限策略，B为V2数据驱动HTTP Mock；同数据、同目标、同初始概况、同6/5预算。不是与所有固定算法的穷尽搜索比较，不声称新事实只能由AI算出。

|最终批次|正确新增语义事实|遗漏基线事实|目标命中|
|---|---:|---:|---|
|条件交互|1|0|是|
|负关联|0|1|是|
|常数评分|0|0|是（零发现）|

两侧数值/来源错误及重复事实均0；三个初始概况保留均1/1。关联案例提前停止却遗漏合法群体观察，不能隐藏或称整体更好。新Fact ID不等于语义增量，已有概况能给出的缺分人数不算新增知识。真实Qwen全部not_run，实际Token/费用null；Mock usage/价格是fixture。目标3/3不是模型质量指标。

测试覆盖动态路径、目标语义、恶意工具/参数/引用、重复调用、来源篡改、原事实保留、预算重入、超时、usage缺失、拒绝访问和封闭注册。原始测试日志、完整HTTP记录留本机；仓库结果只有明确合成的调查及技术记录。

## Synthetic Live：有入口，当前不得执行

demo的--execute --authorization PATH仅供未来单独批准的合成实验。授权绑定场景指纹、模型、地域URL指纹、有效期、请求/Token/人民币上限，Key仅从原服务端环境读取。不得提供真实教师资料或Production模式。

当前价格凭据到2026-11-02已过期，不改日期绕过；本轮无真实调用。首次最大输入保守预留、独占账本、未知usage保留额度、无自动重试均复用。估算目录价上界与实际账单区分，未知用量不记0。

先独立审核、更新经核验价格，再由负责人批准少量合成调用。建议首先一轮不超过6请求/5工具、每次输出不超过现有800；具体Token与人民币额度须按有效价格最坏输入预留核准。本PR没有获批实际额度。真实Qwen无增量则如实停止扩展，不调Oracle。

## V1.1最小接入条件（未实现）

后端提供获授权且仍有效的Canonical快照：动态AssessmentItem/Scale、Observation、审查状态和可回放Witness；绑定同一批次/快照/范围，不接受客户端统计或授权声明。提供当前权限/撤销/取消、用途绑定的历史披露和共享预算上下文。解释原因另需经审核量规/任务Provider。

适配层接到既有Kernel/Executor；正式Provider须经审核替代封闭合成准入，不能直接改为任意JSON。这里只列内部接口要求，不决定正式DTO或教师认证。前端将来展示真实进展，不显示假的思考。

已证明：代码执行、数值/来源、受控目标/预算机制、有限Mock策略对照。未证明：Qwen自主调查增量、教学效果、生产授权/跨会话安全、通用目标理解。没有LangGraph迁移或部署。


封存最终批次属于同一合成生成族的种子/规模留出，不是第三方盲测。结构多样性有限，不据此证明泛化；后续真实模型对照还需独立审查者提供未参与调试的场景。


## V2.1：调查完整性修复（同一 PR #66）

V2 历史表和 evals/results/research_agent_v2-20261103.json 保留不改。负关联遗漏的原因：任一合格观察使 goal_satisfied，旧 Mock 随即 finish；服务端没有单独描述调查覆盖。因此“目标已有对应观察”被误用成“后续合法方向都已调查”。

新增 completeness.assess 为服务端计算的有限、可解释调查前沿（最多4个方向），只读已验证统计、实际项目能力、阈值和本轮关系结果；不执行新工具、不签发事实、不读案例名或答案。强关联后的互斥组比较与弱关联后的条件组合是两个候选方向。所有数值、资格与累计披露仍经原 Executor/Kernel 检查。前沿只是建议，不预定发现或强制模型执行。

ResearchPlanner 在每轮提供独立的 goal reflection、coverage 与剩余预算。真实 Qwen 仍可自由选择合法方向或停止；离线 ResearchMock 明确只是选择首个待调查方向的协议 fixture，不能据此证明模型自主推理。goal_satisfied 不再意味着调查穷尽。提前停止、预算不足、证据限制各有独立状态；报告还列出未调查的合法项目对数量。最多3卡的展示上限不删除本轮全部已核验事实；模型选择为空也不会丢掉已签发合格观察。未引入跨会话历史事实。

新增独立评审案例由未参与策略调试的隔离上下文子代理提供，40/30/40人、4项，含多重关联、缺失来源和0.5步长逐题组合。其曾看到统计 Kernel，实现了独立 Fraction/秩矩参考但不是完全盲测，更不是 Peng-Kai789 的人工批准。策略先实现，新增答案仅用于评分，本轮未据此调整调查策略；不主张严格预注册盲测。评测器单独读答案，模型上下文不含答案、seed、case_id 或参考路径。全部预期目标保持在分母内，未调查目标明确列出，不据此调参。#47冻结 Oracle 未修改。

首次新案例执行在输入准备阶段因漏读 measurement_level 失败，未执行新案例；首轮专项30通过、2失败（上述适配缺陷与新增测试JSON数组类型断言）。修复仅读取逐例输入中已有且指纹固定的测量级别并核验两份输入一致性，以及数组断言；未改输入、答案或调查策略。

### 演示和独立对照

    python -m prototypes.research_agent_v2.demo --output D:\Temp\research-v21-new.json
    python -m evals.research_v21 --output D:\Temp\research-v21-eval-new.json

先设置与前文相同的 PYTHONPATH 和锁定环境。第一条输出教师.md、技术.technical.md及JSON；第二条保留原三个对照和新增三个评审案例，同数据、同6次模型/5次工具预算。模型延迟、fixture usage 和校验重放次数如实保留，实际 Qwen/费用仍 not_run/null。

48人演示实际链：概况 → I01/I03等级相关=0（未达发现资格）→ I01<=1且I02<=1下比较I03，目标12/其余36，总分母48、均值1与11/3、差-8/3 → finish。总体两项目相关为0不能排除条件组合中的描述性差异；不是因果交互、显著性或教学效果证明。下一步教师可按受控来源核查这些评分及评分依据是否合理；原始任务/量规仍缺失，不能解释原因。

停止契约不声称覆盖所有项目、阈值或教育问题。有限前沿会遗漏其他合法关系和组合，真实模型也可能主动过早停止。本轮仅证明执行机制、既有遗漏修复及新案例的实际得失；不证明 Qwen 优于规则、不证明教学效果。人工独立代码审核仍需完成，生产权限、真实资料外发、跨会话披露承接均未开放。


独立静态复核另发现P2：coverage原先只读运行Limits，可能在campaign授权请求额度已耗尽后仍标记can_continue。已增加只读remaining_requests，将本地次数、共享授权次数及原PRICE最坏输入预留对应的Token/费用容量取最小值；发送前的反思还保守计入当前请求。实际reserve/settle仍完全沿用原共享预算，不创建新账本、不降低输入预留。max_requests=1的HTTP Mock反例验证请求只执行1次、已核验事实保留、剩余为0且coverage为budget_limited；真实授权失败原因保持可见。
