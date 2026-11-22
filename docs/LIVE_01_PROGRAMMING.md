# LIVE-01 合成编程调查

本入口供负责人在隔离工作区单独验收。正式 FastAPI 继续沿用 #129 的编程任务模式。仲楷负责将受审执行入口接入 Worker 和正式结果存取。

## 联网前

确认当前 Git SHA、锁文件和预检输出的 `code_sha256`。审核需绑定该代码指纹、`scope`、`synthetic_zip_sha256`、北京 endpoint 指纹、价格指纹及有效期、账本根目录指纹和费用范围。受信任操作员保存审核和费用批准材料在仓库外；程序读取本机审批文件，GitHub 引用真实性及审核独立性由操作员核对。

从 Windows PowerShell 在仓库根目录运行：

```powershell
Set-Location apps/api
uv sync --locked
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = '.;../..'
$env:WEIRAN_EDU_ENV = 'test'
$operatorRoot = Join-Path $env:LOCALAPPDATA 'weiran-edu'
New-Item -ItemType Directory -Force $operatorRoot | Out-Null
$stateDir = Join-Path $operatorRoot 'live-01-programming-reviewed'
$env:WEIRAN_EDU_CAMPAIGN_LEDGER_ROOT = Join-Path $operatorRoot 'campaign-ledgers'
uv run --locked --no-sync python -m prototypes.research_agent_v2.programming_live_cli prepare --state-dir $stateDir
uv run --locked --no-sync python -m prototypes.research_agent_v2.programming_live_cli preflight --state-dir $stateDir
```

`prepare` 只执行一次，使用内置合成 ZIP 经 FastAPI TestClient 调用正式导入路由，保存真实 SQLite 和 OriginalStore，登记一个隔离验收任务 ID。重复打开时使用 `preflight` 或 `replay`。保持该私有目录的访问权限受控，里面有合成教师凭证。预检只报告凭据是否已进入当前进程。

`WEIRAN_EDU_PROGRAMMING_LIVE_APPROVAL` 指向独立审批文件。其字段包括：

- `purpose=programming_synthetic_aggregate_remote_inference`，唯一 `campaign_id`、`human_approval_reference` 和本仓库的 `independent_review_reference`。
- `teacher_id`、`task_id`、`source_fingerprints={assessment_id: snapshot_id}`、`zip_sha256` 和 `ledger_root_sha256`。
- `reviewed_code_sha256`、`model=qwen3.8-flash`、`endpoint_sha256`、`price_fingerprint`、`valid_until`。
- `max_requests` 最多 6、`max_output_tokens` 最多 800、`total_token_limit` 最多 5,955,648、`cost_cap_cny` 最多 5 元，由负责人按实际审核范围批准。工具最多 5 次。

服务端 `DASHSCOPE_API_KEY` 和 `DASHSCOPE_BASE_URL` 必须由运行进程读取。审定价格使用北京按量的同步非思考模式，输入和输出每百万 Token 分别 0.8 元和 2.7 元。现有价格快照有效至 2026-11-26。六次无缓存全额预留为 `6 × (991808 × 0.8 + 800 × 2.7) / 1000000 = 4.7736384` 元。账本保留每次预留和可核验用量结算，未知用量保留全额预留。[百炼官方价格](https://help.aliyun.com/zh/model-studio/model-pricing)

## 仅在批准后执行一次

```powershell
uv run --locked --no-sync python -m prototypes.research_agent_v2.programming_live_cli preflight --state-dir $stateDir
uv run --locked --no-sync python -m prototypes.research_agent_v2.programming_live_cli run --state-dir $stateDir
```

成功后，在新 Python 进程重新运行 `replay --state-dir $stateDir`。回读从同一账本恢复决策、重新执行已接受工具并核验原件、权限、Fact、Witness 和结果摘要。回读无需再请求 Qwen。账本和私有审批、测试库留在仓库外。失败保留记录，不换 campaign 重跑。

## 审核和接线

- AI 入口为 `run_programming_live(ProgrammingLiveInput)`。Worker 提供服务器选定的 EvidenceScope、AssessmentEvidenceProvider、当前任务权限/取消守卫、审核配置和服务端模型设置。只准入内置合成 ZIP 的完整字节摘要。普通教师上传继续使用原路径。
- 模型看到固定指令和工具 Schema、问题/测试点/方向匿名角色、通过披露门禁的聚合数量/分母/代码特征/限制码、当轮 Fact ID 和工具状态、剩余预算。Key 仅用于 DashScope 认证头。请求正文无姓名、逐人结果、原始代码、路径、来源摘要、身份映射或教师凭证。
- 记录包含 HTTP 状态、通过模型标识校验的响应、工具、账本用量、聚合发现、Witness 数和摘要。原始 Witness 仅留在本地可信 Provider。DS-06B 可消费按角色映射的步骤，`model_origin_verified=false` 表示仍需要独立核对使用来源及账本。本内置验收数据没有冻结效果评测 oracle。
- 正式队列、任务行、授权撤销与取消、失败码和结果回读接线由 B-04C/Worker 负责人审阅。当前 CLI 读取的是隔离验收账本，尚未写入正式编程任务结果行。
