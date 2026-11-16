# 数据库 Agent 的受信任离线测试接缝

基线：`36f8cc89a8cfed6839ee262e20d9f686984f386b`。本补丁不安装网页 Live、不调用真实 Qwen，不修改前端、TaskStore、数据库或统计/画像算法。

## 后端测试调用

```python
import httpx
from prototypes.qwen_execution.testing import run_database_profiles_offline

# binding 仍是现有 DatabaseTaskInput，包含同一 running task/store/source/budget。
# 预算必须是该任务原来的 HTTP Mock 预算；不能新建或转用 Live 额度。
raw_result, profile_book, executor = await run_database_profiles_offline(
    binding,
    limits=task_service.profile_limits,
    transport=httpx.MockTransport(trusted_test_handler),
)
```

handler 使用后端测试代码生成合法原生工具调用响应。测试必须像现有 pytest 一样禁用外网；MockTransport 回调是受信任 Python 代码，不是恶意服务器代码的沙箱。

测试入口只在 `prototypes.qwen_execution.testing`，不加入公共包导出、HTTP schema、浏览器参数或环境开关。必须传入精确的 `httpx.MockTransport` 实例；None、真实 HTTP transport、任意对象和 MockTransport 子类均拒绝。内部使用不能由 JSON 构造的 Python capability，并逐次核验 transport 对象身份和原任务预算身份。

`run_database_profiles(binding, *, limits)` 的真实入口签名不变，仍不接受 transport 参数；默认 DatabasePlanner 仍要求 transport=None、allow_network=True 及 qwen_live 预算。不能用测试环境变量开启离线模式，也不会因真实调用失败而自动回退。

## 保留与标记

两条入口共享同一个执行实现、DatabasePlanner、ProfilePlanner/QwenAdaptivePlanner、原 run_profile_session、工具执行器、Fact/Witness 回放及发布前检查。没有第二套 Agent 或评分内核。

离线入口保留原任务/凭据/快照/来源绑定、读取前外发准入回调、每轮发送前后和发布前的复查，以及 k/累计披露、6 模型请求/5 工具尝试（含 seed）、每请求最多 800 输出 Token。它不能把缺少准入、撤权或来源改变变成模拟成功。

成功或失败的模拟结果均不会声明真实 Qwen 成功：

- result.origin、budget.origin、profile.selection_origin 为 `http_mock`，real_qwen 为 `not_run`。
- simulation=true、execution_mode=`offline_http_mock`、actual_remote_requests=0。
- actual_tokens、actual_cost_cny 为 null；Mock usage 只保留在明确为 Mock 的原预算账本中。
- source_replay_verified 表示数据库统计与来源核验，不表示模型网络请求成功；production_enabled 与 teacher_api_enabled 均不启用。

**明确拒绝使用 DatabaseLiveBudget 或任何 qwen_live 预算来跑模拟。** 否则 Mock HTTP 200 和伪造 usage 会进入 Live 账本，可能成为错误的“真实运行证明”。如果需要验证 LiveBudget 本身，使用 #98 的独立预算/准入单测；不要将模拟执行改标为 Live。本接缝测试真实编排代码的数据库路径，不声称完成真实网络或 Live 结果持久化验收。

## 与 PR #98 的实际兼容验证

在独立组合工作树，将本补丁实现提交 `e4737b9c2fe3d93690fd0632b066508ca6ae1715` 与 #98 固定提交 `7e9dd3af421927881438a5ea748f7798363c2b2b` 原样组合，并把兼容检查改为非默认收集的显式文件名，得到最终验证树 `b81bcabf2f3829ac1314c003a2e71db4c9234c33`。未修改 #98 原文件或远端分支。

实际使用 #98 的 LiveAdmission.load/require，审批仅为仓库外临时测试文件，不是独立审核或真实费用批准。两个端到端检查及 #98 原有九项检查共 **11 passed，46.77 秒**（原文件名的首轮同样为 11 passed，65.45 秒）：

1. 实际已登记 72 人 XLSX 经正式解析入 SQLite，原 task/source/Mock budget 加 #98 实际准入回调，完成原生多轮工具选择、统计、画像和 Fact/Witness 核验。五维均分与 Witness 直接按数据库评分独立对账。Live 账本目录没有创建，正常真实入口仍拒绝 Mock 预算。
2. #98 未登记来源在评分 capture/HTTP 之前拒绝，request_count=0。

复现组合验证（在新的、可丢弃的工作树中叠加 #98；不要改现有审核分支）：

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = "$(Get-Location);$(Get-Location)/apps/api"
uv run --project apps/api --locked python -m pytest -q -ra evals/qwen_database_offline_pr98_check.py apps/api/tests/durable_teacher_agent/test_live_admission.py
```

该 compatibility 文件只显式运行，不属于未安装 #98 的默认测试集；缺少 #98 时直接导入失败，不伪称通过或 skipped。它复用项目的禁外网与临时账本 fixture。

#98 接线时应在离线测试中提供原 Mock 预算，并使用上述测试接口；真实执行继续使用原入口与受审 Live 预算。grant purpose 迁移、真实预算写回原 TaskStore、画像 staged 披露证明、来源 DTO 与 Live 重启回放仍由后端完成。本补丁不代表 #98 已获安全批准。

## 本轮回归与指纹

完整实际结果见 `evals/qwen_database_offline_seam_acceptance.json`。覆盖正式 72 人资料、81 人零画像、错误 transport、API 额外字段 422、环境开关无效、真实入口不能接 Mock、撤权后的预留保留、源评分变化 503，以及原 Qwen 和画像安全测试。

最终专项 **107 passed，340.69 秒**；#98 组合检查 **11 passed，46.77 秒**。两组均 0 failed、0 skipped，仅既有 Starlette/httpx 弃用警告。apps/api Ruff 和 format 检查通过（242 文件），受影响的两个 prototype 与显式兼容检查也通过。

最小文件清单：`prototypes/qwen_execution/task_contract.py`、`prototypes/qwen_execution/testing.py`、`apps/api/tests/durable_teacher_agent/test_qwen_database_offline.py`、`evals/qwen_database_offline_pr98_check.py`、本文档及上述 acceptance JSON。生产修改仅前两项。

首轮 9 passed/1 failed 的失败是篡改夹具被原不可变触发器阻止；已改成临时事务中移除并恢复触发器、最终恢复评分，并断言实际 `DATA_INTEGRITY_ERROR`。生产 schema 和安全断言未放宽。该反例隔离重跑通过；最终整轮结果另行记录，不用隔离结果冒充整轮。

修改的 task_contract.py 分支和新增 testing.py 都属于 code_fingerprint 的覆盖目录，因此基础版的 `ca8acf05…` 审批指纹失效。补丁源码指纹为 `877b12dd70da6ec106c7a0fd93d74a48312b3508d9ca5c2a1b6e7b3c12aaddd2`；组合 #98 后为 `440a3c5c60ac9915028a3f8bb67e6b7847349f08b4b9f2ef808e4cc4e6030f21`。最终准确 SHA、完整源码及依赖须重新固定并独立审核，禁止沿用之前那次已用过的付费批准。

锁文件与依赖无变化。未运行真实 Qwen、全仓 pytest、GitHub CI 或前端检查；不会把离线 HTTP 200/usage 当作真实模型证据。
