# DS-LIVE 联网前验收

本次工作基于 `feat/live-wiring` 的 `ac3d3a4c857e7b1b363c5f3b0f6917129ead53ad`。正式应用在生产工厂中装配 `TaskService`。创建路由写入 `queued`，独立 Worker 领取该任务。Worker 经 `LiveAdmission`、`QwenSettings` 和 `DatabaseLiveBudget` 调用现有 Planner，后者使用受控 `post_chat`。工具执行后，系统原子保存结果、证明和来源，状态与证据读回会校验持久化证明。合成 SQLite 测试拦截了 HTTP，覆盖正式路由、领取、工具、结果读回、并发及重启。测试中的 `qwen_live` 是被拦截的正式代码路径标记，真实请求证据须另取。

## 运行只读自检

在仓库根目录，用与 Worker 相同的配置运行。Windows PowerShell：

```powershell
uv sync --locked --project apps/api --group dev
$env:PYTHONPATH = "$(Get-Location);$(Get-Location)/apps/api"
uv run --project apps/api --locked python -m scripts.edu_qwen_live_preflight
```

在部署目录也可用 `docker compose run --rm --no-deps worker uv run --project apps/api --locked python -m scripts.edu_qwen_live_preflight`。它临时启动自检进程，真实队列 Worker 不会启动。

输出为脱敏 JSON。`0` 表示静态检查通过，`2` 表示有阻塞。检查 Key 非空状态、模型与北京 Endpoint、数据库/状态/账本路径、两个审批文件的格式、日期及代码指纹、审批是否已消费和首次 Token/费用预留。路径与审批内容均不显示。自检不打开数据库，不启动 FastAPI、Worker 或 HTTP 客户端，也不写审批和账本。实际数据库迁移、目标批次/快照/用途授权、队列可用性及 Key 的远端权限仍要按下文验收。

2026-11-19，在本次隔离工作树的执行会话运行，结果 `blocked`，模型请求 `0`。Key、Endpoint、数据库、状态与账本路径、两份审批均未配置。当前价格快照检查通过。这个结果描述当前进程，部署主机需在自身 Worker 环境中复测。

同一工作树的定向 pytest 为 **18 passed**，含正式 FastAPI/Worker 离线路径、新自检、新旧价格有效期和审批去重。API 目录及本次改动的 Python 文件通过 Ruff 与格式检查。Docker Compose 和真实模型请求本轮均未执行。

## 价格与审批

[百炼 qwen3.8-flash 模型页](https://help.aliyun.com/zh/model-studio/qwen3-8-flash) 和 [百炼价目表](https://help.aliyun.com/zh/model-studio/model-pricing) 于 2026-11-19 核对。华北2（北京）每百万 Token 输入 ¥0.8，输出 ¥2.7，缓存命中输入 ¥0.1。代码按非思考、实时文本和无缓存上界预留。按最大输入 991,808 和最多 800 输出，单次预留 992,608 Token、¥0.7956064。六次和 5 次工具是任务硬上限，六次保守上界为 ¥4.7736384。实际可请求次数还受到单次审批的累计额度与已结算使用约束。

新快照 `qwen3.8-flash-beijing-2026-11-19-v1` 有效至 2026-11-26（含）。旧 11 月 12 日快照有效至 11 月 19 日（含），代码指纹变化已使分别绑定旧代码的审批无法用于这个补丁。每次审批绑定在实际运行环境自检输出的代码与价格指纹，历史快照和审批不会自动续期。Key 只放 API/Worker 的服务端环境。本次移除了 Web 的整份 `.env` 注入，并从镜像构建上下文排除真实环境文件及部署私有目录。

此次本地自检的代码指纹为 `4ef48914fa8420366e567db71b8e1e915e50e1e200a35441d7f55cf07d0cc411`，价格指纹为 `998d6bd3c3311f29f09b2a1328739e9f05b2847bb74da6a331e3e3ef01739bce`。正式部署以部署树重新计算的指纹为准。

正式任务需要两份仓库外审批、持久账本和数据库用途 grant。`RealDataAdmission.sources` 绑定原文件 SHA-256。`DatabaseLiveApproval.source_fingerprints` 的键绑定实际授权快照的 `source_sha`，不能用原文件哈希或注册合成 recipe 指纹替代。单次审批的 `campaign_id` 必须与任务一致。真实学生资料禁止用于本轮验收。

## 获得费用批准后，先验证一次合成联网

1. 使用最终受审代码执行下面的干跑，取得合成来源、源码和价格三个指纹。使用无画像的已登记合成样例，正常路径只需作一次 `finish` 决定。

   ```powershell
   uv run --project apps/api --locked python -m prototypes.qwen_execution.cli --recipe profile-no-pattern-81
   ```

2. 由负责人和独立审核人批准一个新的、仅此合成样例的 `ReviewedApproval`。批准文件与账本置于仓库外。将审批的 `max_requests` 设为 `1`、`max_output_tokens` 设为 `800`、`total_token_limit` 设为 `992608`，金额批准上限至少覆盖首次保守预留，例如 ¥0.80。端点使用已批准的 `https://dashscope.aliyuncs.com/compatible-mode/v1`，模型 `qwen3.8-flash`。文件格式和绑定字段见 [现有受审 CLI](../docs/QWEN_REVIEWED_ADAPTER.md)。该 CLI 有独立的合成审批，不能使用正式数据库任务审批。
3. Key 和 Endpoint 经服务端环境注入、确认账本及批准均未使用后，由负责人执行一次。以下命令会发出真实请求并可能计费。本轮未执行。

   ```powershell
   uv run --project apps/api --locked python -m prototypes.qwen_execution.cli --recipe profile-no-pattern-81 --approval 'D:/private/approved-qwen-smoke.json' --execute
   ```

4. 验收账本仅一次请求、HTTP 200、`response_received`、有效 usage 和费用上界。模型若未正确结束调查，分别记录联网结果和 Agent 结果。缺失 usage、超时或失败时保留账本，按人工审批复核处置。这个检查使用一条合成模型调用，正式 API/Worker 的真实联调仍需单独授权。

## B-04 联调交接

| 类别 | 当前结果与负责人动作 |
| --- | --- |
| 配置 | 操作员在 Worker 环境配置北京 HTTPS Endpoint、专用 Key、外部 SQLite 和共享持久账本。先执行 `python -m app.edu init` 初始化数据库。容器启动与真实 Key 权限本轮未测。 |
| 审批 | 负责人在最终代码指纹上独立复审，签发目标合成批次的来源和单次费用审批。采集源哈希、快照摘要及任务 campaign 时只从服务端受控元数据读取。清理或换账本目录会破坏一次性计费保护。 |
| 排队与绑定 | B-04 需用实际 grant/快照、提前对齐 `TaskStore.create` 生成的 campaign 和单次审批，再让 Worker 领取。当前任务 deadline 从入队开始，默认 120 秒。错误 campaign 会在预算镜像绑定阶段拒绝，创建前用正确审批避免误消费账本。 |
| 失败分类 | 底层 `post_chat` 区分认证、权限、端点/模型、限流、传输、超时和响应错误。`_run_session` 会把部分结果归并为 `model_request_failed`，而 `_persist_live_result` 对未完成统一拒绝。缺 Key 的 `InsightError` 也是 `ValueError`，现有 API 多显示 `AI_LIVE_CONFIGURATION_INVALID`。B-04 如需给操作员稳定分类，应从受限账本的 `transport_status`/HTTP 状态分开映射，不能返回响应正文或异常原文。 |
| 结果 | B-04 以单个合成任务验证 `queued → completed/failed`、查询的 task provenance、Fact/Witness 及重启读回。正式构建应以启动后实际路由为准。若已有运行服务，仅查看任务无需再提交或消费 campaign。 |

本补丁的运行时变更仅为价格快照、合成 CLI 干跑默认快照及部署隔离。正式 API、Worker、共享 Qwen 客户端与其他团队功能由原负责人继续维护。
