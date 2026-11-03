# Evidence-Driven Research Agent V2（隔离合成原型）

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

