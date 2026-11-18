# 旧未然通用能力迁移评估

> ⚠️ 本文部分内容已被 V1.4 取代，以 docs/V1.4_BASELINE.md 为准。

## 参考范围与证据

本次只读参考旧仓库 `ZAMYL/weiran` 的 main 提交 [d8e84f9ed75e9a207cefe1759e5d04230bc68e5d](https://github.com/ZAMYL/weiran/tree/d8e84f9ed75e9a207cefe1759e5d04230bc68e5d)，核验日期为 2026-10-23。

旧本地工作区只检出规则和文档，缺少运行工程，故通过已授权的 GitHub 只读接口读取上述固定提交，没有切换、拉取或修改旧仓库。参考提交的 [CI 运行 39914746698](https://github.com/ZAMYL/weiran/actions/runs/39914746698) 为 success；这只是参考项目证据，本次没有重新安装或运行旧项目测试。

新工程没有复制旧项目全量代码、数据、数据库、业务夹具或旧提交历史。

## 本次实际选择

| 旧模块 / 机制 | 本次处理 | 理由 |
| --- | --- | --- |
| web/package.json 与 pnpm-lock.yaml | 沿用 Node 24、pnpm 11.25.0、Next 16.4.1、React 19.3.1、TS 5.9.3 等版本；生成独立锁文件 | 参考工程已通过 CI，工具链兼容；删除 Three.js 及其类型依赖 |
| web/eslint.config.mjs、.prettierrc.json | 复用通用配置 | 不依赖决策业务；保留 ESLint 9.39.5。虽然 Next 顶层配置允许 10，但 React/import/jsx-a11y 插件 peer 范围尚不支持 10，不能忽略此冲突 |
| web/tsconfig.json、next.config.ts | 保留 strict/noUncheckedIndexedAccess 等规则并精简；保留 agentRules:false | 不覆盖团队自定义规范，不带入旧测试及 UI |
| web 页面、API 客户端、策略组件 | 不迁移 | 决策沙盘、模型管理、条件卡等不属于 EDU 基础工程；新首页独立编写 |
| api/pyproject.toml、uv.lock | 沿用 Python 3.12 和最小依赖集合，直接依赖固定为参考锁文件版本，独立生成 uv.lock | 便于复现，不带入业务模块 |
| core/errors.py、schemas/common.py | 保留统一错误信封、状态码和安全响应头机制；重写最小实现 | 移除 record_error、业务校验消息和诊断链；422 使用固定安全消息 |
| core/middleware.py | 重写请求编号与异常边界 | 服务端生成 req_UUID，不迁移 request_diagnostics 或模型跟踪 |
| routers/health.py | 复用独立路由与响应形式 | 无数据库、游客会话或分析业务依赖；仅表示进程存活 |
| core/config.py | 独立重写 | 原配置包含数据库路径、cookie、session 等，本次只读取服务端环境名 |
| tests/test_http.py 等 | 复用测试思路，重写合成数据与断言 | 只测试本工程行为，不带入 case、条件卡、数据库或游客认证 |
| .github/workflows/ci.yml | 复用 PR/main 触发、最小权限、固定 action SHA、双系统后端等模式，独立编写步骤 | 移除旧 AI 环境变量，新增本工程前端 smoke test，不带入业务测试 |

## AI 能力逐项评估（本次未迁移实现）

已阅读以下文件及相关测试：

- [client.py](https://github.com/ZAMYL/weiran/blob/d8e84f9ed75e9a207cefe1759e5d04230bc68e5d/apps/api/app/ai/client.py)
- [settings.py](https://github.com/ZAMYL/weiran/blob/d8e84f9ed75e9a207cefe1759e5d04230bc68e5d/apps/api/app/ai/settings.py)
- [diagnostics.py](https://github.com/ZAMYL/weiran/blob/d8e84f9ed75e9a207cefe1759e5d04230bc68e5d/apps/api/app/ai/diagnostics.py)
- errors.py、extract.py、tests/test_ai_client.py、test_ai_extract.py、test_model_diagnostics.py、test_network_diagnostics.py。

| 通用能力 | 已观察到的机制 | 耦合及后续建议 |
| --- | --- | --- |
| OpenAI-compatible 请求 | Python 标准库请求 /chat/completions；Bearer 认证；显式模型与 response_format；拒绝重定向；响应体上限 256,000 bytes | client 默认导入条件候选 ModelOutput，并依赖网络诊断。未来由 ZAMYL 抽出无业务默认 schema 的传输层，调用者显式传 schema；不要复制旧 models/prompt |
| 服务端 API Key | ModelSettings 从明确环境变量读取；SecretStr；验证 key 字符及 HTTPS base_url，不允许 URL 内凭据、query、fragment；配置失败转安全错误 | 基本校验可复用；变量前缀、提供商能力、密钥授权由 EDU 单独确认，不复制 WEIRAN_* 或管理后台配置服务 |
| 请求超时 | 传输层 socket timeout；extract 使用 monotonic deadline 和 asyncio.timeout，让最多两次尝试共用预算；取消向上传播 | 总预算原则可复用，但 extract 本身绑定候选条件与来源映射。to_thread 的等待取消不等于立即终止底层线程，未来传输选型需明确取消及资源释放语义 |
| 结构化输出 | json_schema/json_object 请求；校验 envelope、finish_reason、refusal 和 content；extract 调用 parse_output 后才返回，有界修复一次 | 发送 schema 不能替代应用校验。旧 parse_output、模型和质量规则绑定决策条件/策略；EDU 需按经确认的解释结构重新设计 Pydantic schema 与校验，不复用旧业务 schema |
| 错误脱敏与失败状态 | errors.py 区分 MODEL_UNAVAILABLE / AI_OUTPUT_INVALID，以受控 reason 标记配置、超时、网络、HTTP、拒绝等；不透传供应商错误正文 | 可借鉴受控错误分类，后续与后端协商 HTTP 映射；AIExtractionError 名称和提取语义应去耦，不能把失败当成正常解释 |
| 基础诊断 | ContextVar 隔离请求/尝试；有界元数据；验证错误不含 input/context，字段路径按白名单脱敏 | diagnostics 的 FIELDS/STAGES/QUALITY_RULES 含 case_id、conditions、depends_on、策略预算等旧规则，且依赖 network_diagnostics；仅借鉴元数据白名单和边界，不整体迁移 |

相关旧测试证明了哪些行为被测试：mock urllib 请求格式与超时、非法 JSON/envelope、超大响应、拒绝/截断、错误正文脱敏、禁止重定向；提取层的有限重试、总超时、输入校验、取消；诊断的字段脱敏与并发隔离。此处记录的是对测试源码的阅读，不声称本次运行了这些旧测试。

后续由 ZAMYL 在 PRD 和数据授权边界确认后建立独立 AI 模块，以 mock transport 与合成结果做默认离线评测；涉及真实付费模型的验证必须另行明确授权。本次未新增 AI 客户端、SDK、凭据字段、模型接口或 live-test 开关，也没有调用任何模型。

## 明确不迁移

- 决策案例、条件卡、策略卡、证据业务模型、预算及多情境规则。
- 旧数据库连接与表结构、迁移脚本、游客认证、cookie/session、管理后台。
- 绑定旧业务的日志/诊断聚合、真实输入输出与旧测试数据集。
- 决策沙盘 UI、Three.js 场景、旧业务 API 客户端及提示词。
- 旧 CI 中的 AI provider 环境变量与 live 调用配置。

这些内容分别由 EDU 前端、后端和 AI 负责人依据后续确认的需求重新设计。当前 PRD 仍待确认，本报告不定义六类学情分析、评分规则、数据库 Schema 或教师认证方案。

## 工具链维护注意

ESLint 9.39.5 已被 npm 标记为停止支持。本次为满足旧工程及配套插件的实际兼容范围保留此版本，不通过忽略 peer 依赖强行升级。后续由前端负责人协调 Next/ESLint 插件的完整升级；这是已知维护限制。

pnpm 11 默认禁止未经审查的依赖构建脚本。项目明确禁止 unrs-resolver 的 fallback 构建脚本，使用锁文件安装的可选原生包，并通过本地和 CI 的 lint/build 验证可用性。
