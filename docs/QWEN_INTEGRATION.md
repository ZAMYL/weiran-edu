# Qwen 合成学情洞察接入（v0.1 草案）

> ⚠️ 本文部分内容已被 V1.4 取代，以 docs/V1.4_BASELINE.md 为准。

本轮在 PR #5 的共同工作流上接入 Qwen，不新增 HTTP 路由、数据库、认证或前端。
基础 PR #5 尚未合并，因此分支为 feat/edu-qwen-integration，
新 PR 以 feat/edu-ai-insights 为目标。PR #2 的后端适配草案仍未作为正式 DTO 使用。

## 模型和官方依据

默认请求模型：qwen3.8-flash，允许由服务端 WEIRAN_QWEN_MODEL 显式配置。
没有自动换模型、猜地域、改 endpoint、无限工具循环或重试。

核验日期：2026-10-24。

- [首次调用](https://help.aliyun.com/zh/model-studio/first-api-call-to-qwen)：地域、业务空间与 Key 对应，必须使用控制台的 Base URL。
- [Function Calling](https://help.aliyun.com/zh/model-studio/qwen-function-calling)：模型只返回工具指令，实际执行在应用端。
- [结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)：列明 Qwen3.8-Flash 支持 JSON Schema。
- [OpenAI 兼容 Chat](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)：本轮关闭 thinking / preserve_thinking，显式限制 max_tokens。

使用 httpx 0.28.1 的异步 OpenAI 兼容 HTTP，不依赖 OpenAI/DashScope SDK。
该包原已在 dev 锁定，本轮只提升为运行依赖；无版本升级及新包解析。

## 运行模式

| 模式 | 行为 |
| --- | --- |
| Offline（默认） | 保留原 Fake Client 路径；远程 client 在 plan 前被拒绝 |
| Synthetic Live | 同时指定模式和 allow_synthetic_live=True，且 QwenClient 明确开启；仅接受内置合成场景及其完全匹配的数据/问题 |
| Production | 明确返回 unsupported_execution_mode；无真实用户数据入口 |

SyntheticAccess 的布尔声明不能独自开启外发：Synthetic Live 会与
synthetic.py 中的固定场景、数据、批次、来源、证据授权快照逐项比较。
伪造 synthetic_only=True、替换学生记录、添加真实问题都不能通过。
这是内部受信代码边界，不是可对抗任意恶意 Python 程序的沙箱。
不要删除这层检查后将接口接到真实上传流程。

CI 和默认 pytest 只用 MockTransport / Fake Client，测试夹具继续阻断外部连接。
仅导入模块、存在 Key、启动 FastAPI 或运行普通 pytest 都不会触发真实费用。

## 服务端配置

使用根目录 .env.example 的空占位；不默认读取 .env。

| 环境变量 | 含义 |
| --- | --- |
| DASHSCOPE_API_KEY | 必填服务端密钥，SecretStr 保存，repr 隐藏 |
| DASHSCOPE_BASE_URL | 必填，按控制台实际地域和 WorkspaceId 填写，不提供默认值 |
| WEIRAN_QWEN_MODEL | 默认 qwen3.8-flash |
| WEIRAN_QWEN_OUTPUT_MODE | 默认 json_schema；json_object 仅为显式选项，仍须本地校验 |

Base URL 只允许 HTTPS、443、已知百炼域名、/compatible-mode/v1 路径；
拒绝用户信息、query、fragment、任意外部主机、占位 WorkspaceId 和重定向。
不跟随代理环境变量，TLS 校验保持开启。白名单不是账户权限验证；
模型/空间不开放时仍会明确返回认证、权限或 endpoint/model 错误。

Windows 可在本机运行：

```powershell
pwsh -NoProfile -File D:/AI/weiran-edu/scripts/configure-qwen.ps1
```

脚本先询问控制台 Base URL，再以掩码读取 Key，存入 Windows 当前用户环境变量，
不回显 Key、不写仓库、不调用模型。用户环境变量不是凭据保险库；本机账户权限仍需保护。
新开服务端终端使配置生效。不要在聊天、命令参数、截图或 NEXT_PUBLIC_* 中放 Key。

## 共同执行与验证

1. QwenClient.plan 把现有工具 Schema 注册为原生 Function Calling。
   通过 tools/function/arguments 适配为 Plan/ToolCall；没有调用、非法 JSON、
   重复参数 key、过多调用或未知工具均明确失败，不以普通聊天回复替代。
2. 原 run_insights 仍创建唯一 ToolExecutor，执行白名单、批次、群体和预算检查。
   统计与筛选继续使用 summarize / select_students。
3. filter_student_group 增加内部可选 include_statistics=false。
   为 true 时再次调用 summarize 读取命中群体的五维统计；与全群体统计并列，
   不定义新的分析公式，不让模型计算差值，不改变公共 API 或评分精度。
4. 工具结果按原生 tool_call_id 送回模型，只有实际完成的结果能进入组合阶段。
   结果投影包含精确数值及分母、条件、事实 ID 与允许的证据引用关系；
   不包含学生 ID、原始分数列表、定位符、评语正文或量表正文。
5. Qwen 使用严格 JSON Schema 返回现有 Proposal 的事实/模板选择。
   json_object 不自动重试或降级，必须显式配置；两种模式都经过 Pydantic、
   当前运行引用白名单和 PR #5 的实际来源校验。
6. 观察文本、标题和数值由程序生成，模型仅选择真实 fact_id 与保守模板。
   任何数字/value/text/日期等自由填报、多余字段、未知引用或错误来源都拒绝。

PR #5 修复继续保留：个人证据须属于 Fact.source_refs AND scope.records，
并满足批次/维度；零命中不能借用其他学生证据。Rubric 只按本批标准上下文关联，
不套用学生记录规则。模型获得 compatible_fact_ids 提示，但最终仍由同一个
evidence_matches_fact 谓词与 validate_proposal 强制校验，提示不是授权。

本轮投影规则版本为 edu-analysis-v0.1-draft/ai-projection-v0.2.0。
“较高>=4、较低<3”只在内置合成场景中作为明确临时约定传入，
不代表教师认可的永久高低分标准。

## 上限、异常和观测

每次工作流最多 2 次远程请求：plan 最大 800 输出 Token，compose 最大 1800；
settings 上限分别 1600/2400，不自动重试。默认每阶段总超时 30 秒，
演示将同样的 deadline 传给共同 orchestrator；取消会传播并关闭 HTTP 资源。
请求体不超过 64000 UTF-8 字节；响应默认最多 65536 字节。
原工具次数、累计输出、群体记录和正文长度限制继续生效。

明确区分：配置缺失、认证/权限错误、429、服务异常、网络错误、重定向、
超时、响应截断、无工具、格式错误、引用失败和数据不足。
模型/权限失败返回 error / report=None；数据不足由程序降级且跳过 compose。
错误响应正文、Key、模型原始输出和问题不进入日志。

CallMetric 只保存阶段、模型、真实 elapsed_ms、HTTP 状态、固定状态码、
API 返回的 input/output/total/cache Token 与请求输出上限。
未返回的用量为 None，不伪装为零。response_received 表示收到响应，
不表示 Proposal 或引用已经通过验证，工作流状态须另看 Outcome。
费用字段保持 None：应按实际地域、计费阶梯、缓存和账单核对，不能用 Mock Token 当费用。

## 最小展示投影

project_card(Outcome) 是内部草案，非仲楷的正式 DTO：

- 程序生成标题、执行模式、批次和状态（待核实/无命中/不足/错误）。
- 最多六项观察：fact_id、维度、精确值、分母、群体人数、条件、缺失与规则版本。
- 真实引用的 fact_id / evidence_id，以及待验证解释和进一步探索步骤。
- 必须显示局限；未选择展示的事实仍在本地台账，可由未来后端协调下钻。
  当前不编造可点击 URL；林明的跳转行为、仲楷的授权回查需后续共同确认。

同一份个人证据可追溯，不等于其文字支持某种教学原因；
validated 只保证结构/数值/来源关系。模板适切性、意图理解、观察选择偏差、
小样本、因果关系与教学效果仍无法自动证明。

## 合成 Demo 与质量记录

列出内置场景，不读环境 Key，不联网：

```text
cd apps/api
uv run --locked python -m app.ai.edu.qwen_demo --list
```

真实 Smoke 必须单独确认费用与合成数据，默认最多两次 API 请求：

```text
uv run --locked python -m app.ai.edu.qwen_demo --synthetic-live --confirm-paid --case high_strategy_low_alg --max-api-calls 2
```

可显式选择其他 case，或 --all；整个批次 API 预算最多 24，预算不足标记 not_run，
不补跑、不重试。Key 缺失、无法联网或模型未开放时如实输出错误，不伪称跑通。
CLI 只输出合成场景的校验后卡片、规范化工具参数、完整工具 Trace、用量和状态，
失败的原始模型文本及未经授权的参数不输出。不要改成读取教师文件。

准备 12 个场景：五维概况、AND、<3、<=3、同轮两个边界、策略高/算法低、零命中、
缺失、捏造/改分、跨批次、错误证据/非命中证据、问题及评语注入。
qwen_demo 的 intent_check 只做有限工具/阈值核对，安全拒绝和可用结果分开记录；
它不等于完整中文意图或教学质量评估。真实/Mock 覆盖范围见 [QWEN_EVALUATION.md](../evals/QWEN_EVALUATION.md) 和 PR #8；
本轮已使用授权的 6 次真实请求，两次流程通过、首次被引用校验拒绝；不再继续调用。

```text
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest tests/ai_edu/test_qwen.py tests/ai_edu/test_qwen_modes.py -q -ra
uv run --locked pytest tests/ai_edu -q -ra
uv run --locked pytest -q -ra
```

## 后端与前端下一步

仲楷需确认真实身份/批次授权、脱敏与外发同意、Provider 重检、
统计 DTO 的精度与展示范围；当前生产模式不得接真实数据库。
林明可据内部卡片讨论状态、分母和下钻交互，页面布局完全由前端决定。
开放真实数据、自由生成文本或更多教学模板都需要独立评审，不能沿用合成测试
来宣称真实教师语料准确率。PR #5 未合并前保持堆叠 PR，不自动合并或部署。
