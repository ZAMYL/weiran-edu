# V1.3.1 Qwen Agent 核心交接

> ⚠️ 本文部分内容已被 V1.4 取代，以 docs/V1.4_BASELINE.md 为准。

日期：2026-11-16。接收人：Peng-Kai789（彭仲楷）。本轮仅整合 AI 核心并做离线验收，没有进行第二次真实 Qwen 调用，没有安装网页 Live 入口。

## 固定来源与复用

产品基线为 `95b6494ec5c690e68d7eb6766eead4958dec3e80`，独立分支 `codex/edu-v131-qwen-agent-core`。三个原 PR 分支均未改动，按以下顺序以 `cherry-pick -x` 重放：

| 原提交 | 作用与依赖 |
| --- | --- |
| #82 `5552ce5c62b671911a7189ebccef1c593c80c697` | 受审合成 Qwen 入口、原 Planner 的执行准入钩子和共享预算 |
| #83 `67d2ce13870c39107281fefcefc49f76f4ecf246` | 以 #82 为父提交；新增数据库任务绑定和来源契约 |
| #95 `6fec9fb59afd3a6f8bb61536f57902bbe4eecd95` | 以 #82 为父提交；服务端账本根目录及按审批去重 |

#83 与 #95 是同父的独立增量，不能各自再引入一遍 #82。实际代码没有文本冲突；Windows 读取句柄曾阻止 Git 替换 agent.py，关闭读取后在新工作树恢复并完成重放。另修复 #83 测试的 import 排序，通过当前 Ruff 门禁。

原有调用链保持为 `ProfilePlanner → ResearchPlanner → QwenAdaptivePlanner → NativeDialogue → _run_session`，工具仍由 `ProfileExecutor/ResearchExecutor` 执行。评分计算、Fact/Witness、画像构建/投影、披露及验证复用均保留产品基线实现；没有第二套 Agent 循环、统计内核、TaskStore 或预算。

额外生产代码只在 `prototypes/qwen_execution/__init__.py` 直接导出现有函数/类型，便于后端从一个模块导入；没有包装执行函数或改变其语义。原模块导入路径继续可用。

## 统一入口

```python
from prototypes.qwen_execution import (
    DatabaseTaskInput,
    ExecutionProvenance,
    ExternalizationScope,
    ReviewedApproval,
    ReviewedBudget,
    code_fingerprint,
    ledger_root,
    run_database_profiles,
    run_reviewed_profiles,
)
```

- `await run_reviewed_profiles(recipe, budget, settings)`：已有受审合成入口。预算必须是同一个 `ReviewedBudget`，数据必须是精确的已登记 `AdmittedSynthetic`，不接受 DatabaseSource 或教师对象。没有 MockTransport 时才可能请求真实模型；未审批不得调用。
- `await run_database_profiles(binding, *, limits)`：#83 的数据库接线入口，返回 `(raw_result, profile_book, executor)`，没有分配新任务/预算。当前数据库 Mock 预算必须被拒绝，不能把它升级或改标为 Live。
- 两者调用同一 `run_profile_session`；单次最多 6 个模型请求、5 次工具尝试（包含 seed），每请求最多 800 输出 Token。Agent 预算来自 CampaignBudget/Limits，不能把旧单次洞察的 `QwenSettings.max_requests` 改成 6 来接线。

## DatabaseTaskInput 的实际契约

以下对象均由后端现有任务服务提供，不能从浏览器 JSON 构造可信对象：

| 字段 | 服务端必须提供的内容 |
| --- | --- |
| task_id / batch_id / snapshot_id | 已存在且 running 的同一任务和快照 |
| actor | 已认证 Principal，保留原始凭据/归属复查 |
| store | 原任务的同一个 TaskStore，不另开实例或平行任务表 |
| source | 原服务取得的精确 DatabaseSource，绑定 actor/provider、快照及 source_sha |
| budget | 原任务唯一的 CampaignBudget；其 store/task_id/campaign/source 必须与任务一致 |
| settings | 服务端 QwenSettings；Key 只从服务器环境读取，在授权后加载，不接受客户端 Key/model/endpoint |
| require_externalization | 服务端回调，接收 ExternalizationScope；缺授权、撤权、过期或审核/费用/来源不符就抛错 |

`ExternalizationScope` 包含 teacher_id、task_id、campaign_id、batch_id、snapshot_id、source_sha256、model、endpoint，及固定用途 `assessment_aggregate_remote_inference`。这些是本地授权输入，不是模型消息。回调还须闭包绑定可信审核记录、审批内容及稳定账本配置；客户端的 synthetic_only 或审批字符串不能授权。

```python
# 所有变量来自后端已授权的现有任务；本段不会安装 Live 预算或自动批准外发。
binding = DatabaseTaskInput(
    task_id=row["task_id"],
    batch_id=row["batch_id"],
    snapshot_id=row["snapshot_id"],
    actor=principal,
    store=task_service.store,
    source=authorized_source,
    budget=task_owned_budget,
    settings=server_settings,
    require_externalization=require_remote_admission,
)
raw_result, profile_book, executor = await run_database_profiles(
    binding, limits=task_service.profile_limits,
)
```

入口先验证任务身份/来源/预算绑定，再检查外发准入、原协议与评分来源，并在读取后、每个模型发送前后、最终发布前复查。验证复用只在本次 DatabaseSource 的不可变阶段有效，出口仍检查实际数据库与授权。末尾执行 `verify_all()` 和原 provider 的批量 Witness 回放，`source_replay_verified=True` 不来自模型自报。

## 后端仍需完成的接线（本轮未修改）

1. 安装受独立审核的数据库 Live 准入和预算持久化。当前 grant purpose 为 synthetic_mock，DurableBudget 与 store.budget 只接受 http_mock；原 ReviewedBudget 又只接受 AdmittedSynthetic，不能直接套在 DatabaseSource 上。即使外发回调返回正常，现有 Mock 预算仍会报 `database_live_budget_not_installed`。
2. 继续只登记允许远程技术测试的合成文件，按实际文件哈希、受支持协议、教师/任务/快照和专门用途授权。不得通过伪造 synthetic_only、允许任意上传，或重写资料为 recipe 来取得准入。真实教师资料继续拒绝外发。
3. 每个任务只创建一次预算，采用 #95 的同一服务端 journal/审批索引，并将同一预算状态写回原 TaskStore；不能先开一个 Mock 预算再另开 Live 额度。取消、撤权、120 秒任务期限及结果发布失败继续失败关闭，未知费用不得退还或自动重试。
4. 将 raw_result、book、executor 送入原来的 Fact、画像投影、证明和持久化路径。`raw_result["profiles"]` 不是可以直接替代持久化 ProfileInvestigationResult 的对象；复用原 projection/extend_proof/replay_profiles、评分证据及来源查询，不把 profile_id 伪装成 Fact ID。保持 P1 的可信证明决定结果类型，包括零画像。当前三元组没有单独返回 profile staged 披露分区；发布前须用原 `project_selected(book, raw_result, executor, staged=staged, selection_origin=raw_result["origin"])` 重建并比较画像，再将实际 staged 分区交给原 extend_proof。不能凭公开评分猜分区或传空列表绕过累计披露。
5. 同时绑定持久化结果、proof 与真实 ExecutionProvenance，扩展原 Mock-only DTO 的受控来源契约。现有 replay_profiles 内部仍使用 Mock 默认 selection_origin；Live 回读须从可信任务执行模式重建正确来源并验证，不能取待核验 JSON 的自报值。回读只重放证明，不重新联网或创建 campaign。前端 DTO 解码由林明/后端另行接线，不能把 Live 结果改标 http_mock 绕过解码。

## ExecutionProvenance

字段仍为 `origin=qwen_live/http_mock/not_run`、`state=not_run/completed/failed`、`request_state=not_run/attempted/responses_received/failed`、`model_requests(0..6)`、`tool_attempts(0..5)`、`source_replay_verified`、`error_code`。

该 DTO 只验证字段一致性，不证明声明真实。后端必须从实际预算 journal、HTTP metrics 和 executor 账本填充，不能相信模型或客户端。预留表示可能发生请求，不代表 HTTP 200；未知 Token/超时须保留预留并明确失败。completed 必须同时满足原任务正常结束和来源重放；零发现可合法完成。qwen_live 及真实请求状态不能改成 Mock；离线 MockTransport 仍明确为 http_mock、real_qwen=not_run。

## 账本、外发和审核边界

#95 用独占创建和 fsync 保留 campaign journal；同一 human_approval_reference 的审批索引只能绑定一个 campaign。换工作目录、调用方 state_dir 或换 campaign 都不能在同一服务端账本下重领额度；CLI 不再接受 --state-dir。未知用量的预留不退，不能自动恢复/重试。

真实宿主必须在启动时把 `WEIRAN_EDU_CAMPAIGN_LEDGER_ROOT` 固定为仓库外、长期保留、服务端独占的绝对路径，所有 worker/重启共享同一路径。不得从请求传入，或每次启动设为临时目录。默认 LOCALAPPDATA/XDG 路径受运行身份和 Codex 沙箱影响，本轮 dry-run 的默认路径不是已审核的生产账本。操作员删除/更换根目录、重新签发审批属于操作员信任边界，不能宣称这是抵御服务器操作员的租户计费系统。

外发仅复用现有白名单：固定系统指令、受控工具 schema/参数/协议 tool_call_id、服务端 catalog（已登记合成 item 编码/量表/可分析性布尔标记）、核验后聚合 Fact view、运行内事实 ID、预算余量、合法工具状态、受控调查方向及限制。候选方向不是预先发给模型的正确答案。

不得外发学生身份/成员列表、逐人评分、原始答卷/文件、评语或 Rubric 正文、教师凭据、ExternalizationScope、内部来源对象、Witness、评分定位符、evidence_id/reference_id。Key 只用于受审 HTTPS 端点的 Authorization header。上传 label/元数据不能自动视为安全文本。原 deny_live 未改。

## 版本、锁文件和指纹

未新增依赖，pyproject.toml、uv.lock、pnpm-lock.yaml 与产品基线完全一致。`code_fingerprint()` 对 prototypes、apps/api/app、docs/canonical_assessment 中的实际 Python 文件及相对路径计算 SHA-256（CRLF 归一化）；它覆盖未提交源码，**不等于完整 Git 树或依赖配置签名**。

完整提交/树还包括页面、测试、锁文件与配置，必须一并固定。当前 dry-run 与锁文件摘要见 `evals/qwen_v131_ai_core_acceptance.json`；最终提交 SHA/tree 在交付和通知中给出。后续任何覆盖目录的改动都须重算，不能沿用 #82 的 `fb66d452…` 或 #83 的 `0cbe9a1b…` 审批。

沿用的价格是 2026-11-12 冻结快照，有效至 2026-11-19，不代表本轮重新联网核价。保守上界为 `6 × (991808 × 0.8 + 800 × 2.7) / 1000000 = 4.7736384 元`；本轮未获准第二次付费执行，也未发起。

此前 #82 原固定版本的一次真实合成运行已得到 4 次 HTTP 200、真实多轮工具选择及 2 张通过来源核验的画像。这是历史原版本证据，不是本整合版本的真实网络验收，已用过的批准与预算不能复用。新版本仍需独立审核和另一次明确费用批准；本轮禁止发起第二次请求。

## 离线验证

当前固定 Python 3.12.10 锁定环境，设置 PYTHONUTF8=1；pytest 全程禁止外网，并把账本隔离在每个测试自己的临时目录，不访问历史真实运行账本。命令、实际数量和错误/跳过信息写入同一 acceptance JSON；不是借用原 PR 的历史测试结果。

- 契约专项：20 passed，74.15 秒。
- AI/画像/持久任务大范围回归：453 passed，868.10 秒。
- 两轮共 466 个不同用例通过，重叠 7 项；均 0 failed、0 skipped，各有同一条既有 Starlette/httpx 弃用警告。没有提高超时或删除用例。
- apps/api 全目录 Ruff check 和 format --check 通过（241 个 Python 文件），另对 8 个受影响 prototype 文件检查通过；git diff --check 通过。
- 未运行全仓 pytest、GitHub CI 或前端检查（前端零改动）。没有把原 PR CI 或此前真实运行当作此 SHA 的测试。

在 apps/api、PYTHONPATH 包含仓库根目录与 apps/api 的环境中实际运行：

```powershell
python -m pytest -q -ra tests/ai_edu/test_qwen_core_integration.py tests/ai_edu/test_campaign_ledger_root.py tests/durable_teacher_agent/test_qwen_task_contract.py
python -m pytest -q -ra tests/ai_edu tests/adaptive_assessment/test_qwen_live.py tests/research_v2/test_capability_profiles.py tests/durable_teacher_agent/test_tasks.py tests/durable_teacher_agent/test_database_profiles.py tests/durable_teacher_agent/test_status_latency.py
```

新增两项整合反例实际预留一个 Live 账本 ticket（没有 HTTP 调用）后，关闭对象、换工作目录、换 campaign、传新目录均不能重领审批额度；同时核对原 journal 未变、新 campaign journal 被回滚。另一项禁止凭据读取/HTTP 客户端构造，验证统一包导入后的 CLI 仍只 dry-run。其余沿用原测试验证多轮工具、独立评分、Fact/Witness、隐私交叠、异常参数、超时/取消/未知用量、撤权、零画像、P1 持久化篡改、重启、评分下钻和审核报告。

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = "$(Get-Location);$(Get-Location)/apps/api"
uv run --project apps/api --locked python -m prototypes.qwen_execution.cli
```

默认只 dry-run，不读取 API Key、不构造 HTTP 客户端、不创建或重置 campaign。不要添加 --execute。网页仍未接入 Live；本轮不修改前端或后端任务存储、不合并 main/release，也不部署。

## 修改文件索引

完整路径见 acceptance JSON 的 changed_files。本轮新增/引入的文件为：

- `prototypes/qwen_execution/{__init__,runtime,cli,task_contract}.py`：统一导出、原受审合成执行、dry-run、数据库接线。
- `apps/api/tests/ai_edu/test_reviewed_qwen_adapter.py`、`test_campaign_ledger_root.py`、`test_qwen_core_integration.py`，以及 `apps/api/tests/durable_teacher_agent/test_qwen_task_contract.py`：原安全用例和本轮整合用例。
- `docs/QWEN_REVIEWED_ADAPTER.md`、`QWEN_TASK_ADMISSION_HANDOFF.md`、本交接文档，以及 `evals/qwen_reviewed_adapter_dry_run.json`（历史指纹记录）和 `evals/qwen_v131_ai_core_acceptance.json`（本轮证据）。

修改既有 `prototypes/adaptive_assessment/live/{budget,planner,pricing}.py`、`prototypes/capability_profiles/agent.py` 均来自上述固定 PR；本轮没有另改其算法/执行逻辑。`apps/api/tests/conftest.py` 仅引入 #95 的测试账本隔离。未修改 `prototypes/durable_teacher_agent/`、apps/api/app、apps/web 或任何锁文件。
