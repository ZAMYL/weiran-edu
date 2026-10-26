# Qwen Adaptive Synthetic Live：受控真实调用入口

状态：叠加 PR #56 的独立后续实现；依赖 #49/#56，未合并前不作为正式教师能力。未迁移 #57 LangGraph。**本轮只运行 HTTP Mock，真实 Qwen 请求数为 0。** Mock 的路径、Token、费用记录不能当成模型质量或实际账单。

## 运行入口

仓库根目录（Python 3.12、既有 uv.lock）：

```powershell
uv run --project apps/api --locked python scripts/edu_qwen_adaptive_live.py --recipe five
uv run --project apps/api --locked python scripts/edu_qwen_adaptive_live.py --recipe contest13
```

默认输出 `dry_run_no_network`，不读 Key、不创建 HTTP Client、不核验账户、不发请求。输出实际项目、量表、工具 Schema、源指纹、价格指纹及预算预留要求。`constant17`、`contest13_complete`、`limited`、`no_evidence`、`small`、`empty` 也是固定合成注册表；不能上传任意文件或 JSON 数据。

离线完整协议演示（会生成全量 Planning/Execution/Validation/Selection 和费用账本）：

```powershell
cd apps/api
uv run --locked python -m pytest tests/adaptive_assessment/test_qwen_live.py -q
```

可从 Python 调用 `prototypes.adaptive_assessment.live.demo.demonstration()`，返回 `offline_mechanism`。`evals/adaptive_qwen_http_mock.json` 是本轮实际运行的结构化记录；usage 是明确编写的 HTTP 夹具。

## 接线与安全边界

- `QwenAdaptivePlanner` 的 `offline_only=False`；不伪装成离线 Client。原 `run_adaptive`、`run_agent`、`run_session` 仍拒绝远程 Planner；旧 `run_insights` 两阶段协议和 QwenClient 的两请求限制未改。
- 仅 `run_synthetic_live` 在固定工厂重新构造、校验输入指纹并绑定 `CampaignBudget` 后进入共用私有循环。该循环是内部实现，不是认证或外部调用入口。没有 Production 参数和 HTTP 路由。
- `NativeDialogue` 共用原生 Function Calling 对话封装；HTTP 仍经过原 `post_chat`。每轮单工具，原生 assistant/tool 消息通过 `tool_call_id` 配对；`finish` 是显式工具，可选择零事实。保留多轮实际结果，不预跑完整 discover()。
- `bound_schemas` 只列实际 AssessmentItem；catalog 提供合法量表、项目标签和能力。未知量表项目不进入关联/筛选工具枚举。服务端再次校验工具、项目、参数和当前事实 ID；发现资格和原始来源由既有 Executor 重放。
- 模型输入由绑定 Executor 的 catalog 和已重放的聚合 Fact 重建。调用者的任意问题/指令、原始评分、学生 ID、评语、证据定位、来源哈希、数据库和本地卡片不会转发。原披露门禁保持生效。
- 任意模型自然语言不进入卡片。卡片的数字、图表和固定事实文本来自既有程序生成器；确认资格不代表因果关系。
- 模型错误/超时/格式失败显式返回失败，最终卡片为空。保留已算事实作为调查账本，不作为成功展示。取消继续抛 CancelledError，账本保留。

## 整轮授权与费用

`SyntheticLiveAuthorization` 是**本地受信操作员配置**，不是教师鉴权 DTO、前端布尔授权或可上传任意数据的通行证。字段：

| 字段 | 含义 |
| --- | --- |
| campaign_id / human_approval_reference | 本轮唯一 ID / 项目负责人另行授权的引用；不是程序自动确认的人类身份 |
| purpose | 只能 synthetic_live |
| model / endpoint_sha256 | 精确模型、去除末尾 / 的 Base URL SHA-256；地域不默认猜测 |
| source_fingerprints | 明确获准的内置合成工厂名→输入 SHA-256；不发送给模型 |
| price_fingerprint / valid_until | 已审价格快照指纹和授权到期日期；不能晚于价格有效期 |
| max_requests | 整轮 1～6 次，包括失败/取消的尝试 |
| max_output_tokens | 每次 1～800 输出 Token |
| total_token_limit / cost_cap_cny | 整轮 Token、人民币硬上限 |

一个 campaign 当前只能绑定一次执行；支持多资料类型，但一次 campaign 不会自动遍历全部场景。每轮最多 5 次工具尝试，共用会话预算；重复/失败工具不免费恢复。新 Client 无法重新绑定同一账本。

CLI 固定在操作系统用户状态目录 `weiran-edu/adaptive-live-budgets` 独占创建 campaign journal，**请求前 fsync 预留**。既有 campaign 不能重启续跑或恢复额度；操作员必须重新取得授权。不要删除 journal 后复用授权。它不是抵御拥有本机文件权限的恶意操作员的计费/鉴权系统，也未提供跨主机统一预算。

### 价格和保守预留

价格核验日 2026-10-26；当前只支持 `qwen3.8-flash`、北京同步非思考文本、普通按量接口。官方输入/缓存输入/输出价格为 0.8 / 0.1 / 2.7 元每百万 Token；最大输入 991808 Token。来源：[模型官方页](https://help.aliyun.com/zh/model-studio/qwen3-8-flash)、[Function Calling](https://help.aliyun.com/zh/model-studio/qwen-function-calling)。不支持 Batch、显式缓存创建、高速模式、其他地域/价格和优惠假设。价格快照到 **2026-11-02** 失效，届时须重新审查并更新，不能仅延长授权。

没有已证明的本地 Token 估计上界。因此每次请求先按**官方最大输入 991808 + 本次最大输出**预留；最大输出 800 时为 **992608 Token、0.7956064 元**。即使实际提示很短，低于这项预留的剩余额度也会拒绝请求。此策略保守，不能把它解释成实际发送了近百万 Token。

有效 usage 必须有非负整数输入/输出/总量且总量相等、输入不超模型上限、输出不超请求上限；响应模型 ID 必须匹配。结算使用实际 usage 和 Decimal 价格；未给 cached 明细时按未缓存计费上界记录，不猜缓存折扣。没有 usage、非法 usage、超时、取消、网络错误会保留全部预留并禁止继续。已确认 usage 可释放未用预留；从不把未知费用填成 0，不重试。

账本标记 `verified_list_price_upper_bound_not_invoice`。百炼发票/活动价、供应商错误计费不能由本地程序保证；硬上限基于供应商遵守文档的 Token/价格约定。模型价格、地域或 usage 不可核验时停止。

## 第一轮真实测试启动清单（本次不执行）

1. Peng-Kai789 审查来源、累计披露、预算和共享循环边界；合并/更新堆叠依赖，确认没有覆盖 #49/#56/#57。
2. 项目负责人**另行**明确：合成场景及指纹、具体模型、地域/业务空间、最多请求量、Token 额度、人民币上限。以前其他任务的“6 次调用”授权不能复用。
3. 核对当前官方价格，确保快照/授权未过期；确认保守预留适合额度。账户自定义价格不在白名单时不执行。
4. 在服务端本机设置 `DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`、`WEIRAN_QWEN_MODEL`。Key 不进聊天、授权 JSON、仓库或日志。授权 JSON 保存在仓库外，不提交。
5. 以本轮唯一 campaign_id 填写上述严格授权对象；endpoint/source/price 哈希用 dry-run 和本机计算核对。示例中的 pending 占位不能作为批准。
6. 取得授权后才运行（当前禁止执行此命令）：

```powershell
uv run --project apps/api --locked python scripts/edu_qwen_adaptive_live.py --recipe five --execute --authorization <仓库外本机授权文件>
```

7. 核对每轮选择、计算事实、拒绝、最终卡片和 usage/费用；随后才评判真实 Qwen 调查质量。真实模型可能主动停止、选择无资格结果、失败或没有价值增量；不能用 Mock 得分代替。

## 尚未交付

无真实 Qwen 成功率/延迟/账单；无教师真实数据外发、Production、正式接口、下载或网页按钮；无 LangGraph 默认迁移。下一阶段真实合成对照评测需单独授权，并继续保留固定流程和冻结标准答案。
