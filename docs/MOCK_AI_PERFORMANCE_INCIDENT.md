# Mock AI「优先核查」排障记录（2026-11-13）

## 范围与结论

隔离分支基于发布候选 `a9b8ec1ae5dc7fcd02a93306c1c984de3548ec0f`。
本机另一发布候选 `11e9115d76c1e9e012c5bb5895b51f428a27451a` 与它的
`prototypes/`、`apps/api/`、`scripts/` 相同，差异仅在网页。
没有修改发布候选、#82、后端任务/数据库文件、前端或 Live 禁令。

**AI 冗余验证已作最小修复；SQLite 状态读取争用和慢 GET 仍是交付阻断，
不能宣称完整产品已恢复稳定。** 原始四并发真实 HTTP 复现了
`DATABASE_UNAVAILABLE` 和 120 秒任务超时；未复现完全相同的“四分钟”时长。

## 两条调用链

1. Worker / 完成 GET → Fact 展示或验证 → `ResearchExecutor._verify`
   → 原先逐 Fact `AdaptiveExecutor._verify` 重建 Kernel、重算 Fact
   → 再进入整账本重放。同一账本已有完整成功证明时，逐 Fact 重算仍不断发生。
2. 状态 GET → `TaskService._row` → `TaskStore.get`
   → **即使无需改变状态也使用 `BEGIN IMMEDIATE`**。
   然后 `TaskService.view` → source capture → validation_scope 入口 capture
   → verified_result（完成任务才重放 Fact/Witness/画像）→ 出口 capture。
   queued/running 请求也执行三次全快照 capture；轮询会与 worker 争用 SQLite。

`ProfileBook` 已经复用一次构建的数据集/Kernel 和候选验证，
`DatabaseSource.validation_scope` 也已支持阶段内 capture 复用；此次没有再造缓存。
完成结果每次 GET 都重建自己的验证环境，原完整来源/画像重放仍然保留。

## 生产代码的唯一修改

`prototypes/research_agent_v2/runtime.py`：

- 已验证的**同一个执行器、同一不可变完整账本**复用完整重放结论，
  删除随后重复逐 Fact 构建 Kernel 的步骤；新执行器、另一 GET 不共享结论。
- key 覆盖完整 Fact dataclass（含 repr 隐藏的 Calculation.sources、所有存储摘要、
  继承来源字段）、source 对象和指纹、run_id、规则、limits、尝试账本、披露群体和输出量。
- 每次命中前仍 `source.validate()`，复查当前授权、取消和来源。
  数据库阶段出口仍重新 capture，捕获验证期间修改评分或撤权。
- 新工具尝试立即失效；Fact、来源、披露或额度上下文变化也失效。
  不接受不在账本中的另一个 Fact 对象。
- 成功结论仍来自全部查询的真实重放、完整 Fact 相等以及披露/输出量相等。
  保留原规范化 view/proof SHA 检查，不能仅依赖 Python 的数值相等比较。
  Witness 批量核验、P1 类型绑定、画像回放、预算和模型入口均未删除或修改。

## 实际测量

Windows / Python 3.12.10 / 项目锁定依赖 / UTF-8。
Uvicorn 本机 TCP + 临时 SQLite + 正式合成 XLSX 预览/确认入库 + 原 Task API
+ HTTP Mock；非 TestClient、非真实 Qwen。每个 probe 新建数据库、任务和服务。
未启用 cProfile。客户端每轮结束后等待 0.5 秒，完成任务额外读取第二次。
计时不含合成文件生成/导入；POST、worker、GET 分开统计。嵌套 inclusive 时间不可相加。
冷启动指新进程/新数据库，没有清除操作系统文件缓存。

下表的性能候选尚未补充最终规范化摘要防御；最终代码 HTTP 复测另列，不能混用准确版本。

| 合成输入 / 读取者 | 代码 | POST 秒 | worker 秒 | 完成 GET 秒 | 总观测秒 | 结果 |
| --- | --- | ---: | ---: | --- | ---: | --- |
| 72 / 1 | 原版 | 1.74 | 62.43 | 18.54 / 39.03 | 123.71 | 200，2 画像 |
| 72 / 1 | AI 补丁 | 1.87 | 43.00 | 39.39 / 25.06 | 110.17 | 200，2 画像 |
| 72 / 1 | AI 补丁，再次独立冷启动 | 1.11 | 23.33 | 8.29 / 9.26 | 43.65 | 200，2 画像 |
| 72 / 4 | 原版 | 2.64 | 118.92 | 无 | 129.76 | 一次 503，任务 timed_out |
| 72 / 4 | AI 补丁 | 2.14 | 98.28 | 40.68—51.86（8 次） | 197.85 | 全部 200，2 画像 |
| 72 / 4 | AI 补丁 + 只读状态原型 | 1.40 | 102.29 | 44.22—48.04（8 次） | 200.59 | 全部 200，2 画像 |
| 53 / 1 | AI 补丁 | 0.74 | 11.17 | 2.75 / 3.35 | 18.43 | 全部 200，1 画像 |
| 81 / 1 | AI 补丁 | 2.24 | 11.55 | 3.52 / 3.18 | 22.21 | 全部 200，零画像 |

原版 72/1 测量期间另一临时数据库的 pytest 也在运行，有 CPU 争用，
这组耗时只能参考，不能用作严格加速比。四读取者组、优化后各 HTTP 测量顺序执行，
没有同时启动其他本任务 benchmark。宿主的其他系统负载未控制，不能作为 SLA。
原版独立 TestClient 性能用例也实测通过：worker 61.52 秒、GET 25.38 秒；
TestClient POST 等待后台任务，与真实 HTTP POST 不可直接比较。

### 验证次数（不是付费/计费工具次数）

72/1 原版 → AI 补丁，worker：

| 项目 | 原版 | AI 补丁 |
| --- | ---: | ---: |
| Kernel 构建 | 30 | 12 |
| derive | 28 | 10 |
| 原工具执行 + 证明重放工具执行 | 9 | 9 |
| source.validate | 82 | 80 |
| capture / 载入参与记录 / 载入 Witness 行 | 11 / 792 / 7920 | 11 / 792 / 7920 |
| Witness 核验批次 / 引用次数（含重复） | 1 / 1008 | 1 / 1008 |
| 搜索 / 投影 | 1 / 1 | 1 / 1 |
| Mock 模型请求 / 任务账本工具尝试 | 3 / 3 | 3 / 3 |

最终独立复测的 worker 实际完整账本重放 3 轮、6 次查询；
完成 GET 每次完整重放 1 轮、3 个查询、
核验 1008 个 Witness 引用。没有以减少这些证明换取加速。
完成 GET 的 Kernel 构建由每次 15 降至 7；逐次读取仍有三次 capture。
重复冷启动的耗时有明显波动；保留上表较慢结果，不以最终较快一次宣布稳定。

### 最终代码（包含规范化 SHA 检查）的真实 HTTP 验收

独立新进程、新 SQLite，72 人、单读取者：POST **1.0139 秒**，
worker **22.7540 秒**，两次完成 GET **6.1571 / 6.5952 秒**，
完整观测 **38.0632 秒**；全部 HTTP 200，3 Facts、2 画像、Mock 请求/工具各 3 次。
Kernel 构建 12 次；worker 完整账本重放 3 轮/6 查询、11 capture、
1 批/1008 次 Witness 引用核验；每次完成 GET 完整账本 1 轮/3 查询，
3 capture、1 批/1008 次 Witness 引用核验。并未减少正确性要求。
原始脱敏报告为 `.local/final-readers1.json`。
最终代码没有重跑四读取者，不能用初版的四并发通过代替最终稳定性结论。

四并发原版在 53 次 view 中累计 capture 156 次（含一次失败），
AI 补丁在 75 次 view 中 capture 225 次。它们运行状态及完成读取数不同，
不能把总 capture 数直接当作回归/改进；**每次成功 view 的三次 capture 没有减少**。
补丁四并发 read 阶段 871 次授权回查，worker 阶段 181 次，未缓存授权。
这些次数及各环节耗时由下方 probe 自动输出，无身份、评分行或凭据。

## 交给彭仲楷的精确 SQLite 反例

用真实 API 创建合法 queued 任务，**明确禁用本次 worker，仅隔离事务问题**。
另一连接持有普通读事务；GET 只查询状态，无需修改任何行：

| 事务方式 | HTTP | GET 秒 | 驱动证据 |
| --- | ---: | ---: | --- |
| 当前 TaskStore.get | 503 DATABASE_UNAVAILABLE | 3.63 | SQLITE_BUSY，write commit |
| 同进程只读查询原型 | 200 queued | 2.16 | 无 SQLITE_BUSY、无状态写事务 |

原始并发失败当时尚未捕获 commit 的 sqlite_errorname；
以上受控反例补充证明了同一错误码的一条确定触发路径，不能据此断言所有
`DATABASE_UNAVAILABLE` 均由锁造成。

最小建议：`TaskStore.get` 普通分支使用只读事务并保留 `_row`/`check`；
发现 queued/running 已过期时，进入写事务**重新读取、重新授权、重新判断状态与时间**，
再执行原超时转换。取消/撤权/并发状态转换不可使用旧行直接 UPDATE。
诊断脚本 `--readonly-status-prototype` 仅临时 monkeypatch，
过期分支委托原 get 重读；**不是已安装的后端修复**，未修改 store/service/database。
四并发原型没有显著提速，仍需后端审核 queued/running 是否重复构建完整快照，
保留必要的授权/来源门禁，避免每个轻量状态轮询都做完整结果物化。

## 复现命令

从仓库根目录，在已同步项目锁定依赖的 Python 环境中：

```powershell
$env:PYTHONPATH = "apps/api;."
$env:PYTHONUTF8 = "1"
python -u -m scripts.edu_mock_performance_probe --readers 4 --output .local/run-new.json
python -u -m scripts.edu_mock_performance_probe --held-reader-probe --output .local/lock-new.json
python -u -m scripts.edu_mock_performance_probe --held-reader-probe --readonly-status-prototype --output .local/lock-readonly-new.json
```

输出使用排他创建，不覆盖已有报告。数据库和 workbook 位于系统临时目录，
退出即清理；报告只含状态码、数量和耗时。只读原型 flag 永远不改变仓库后端文件。
受控锁探测的 200 queued **不算 AI 成功**，与真实完成任务的 HTTP 验收独立记录。

## 检查状态

新增回归覆盖：复用确实不重建 Kernel、新事实使结论失效、重算哈希后伪造人数、
隐藏评分来源、存储摘要、Fact 身份、工具参数、披露群体、输出量及当前撤权。
初版性能候选的完整相关 `research_v2` / `durable_teacher_agent` 回归：
**216 passed，0 skipped，1 warning，1569.66 秒**。
其中当时新增验证复用回归 13 项全部通过。命令（仓库根目录、UTF-8）：

```powershell
python -m pytest -q apps/api/tests/research_v2 apps/api/tests/durable_teacher_agent --durations=15 --junitxml=.local/mock-regression.xml --basetemp D:/AI/weiran/.test-temp/mock-ai-regression-20261113
```

实际复用已安装的锁定环境解释器
`D:/AI/weiran/.worktrees/edu-v13-integration-preview/apps/api/.venv/Scripts/python.exe`，
`PYTHONPATH=apps/api;.` 确保加载当前隔离工作区代码。
唯一 warning 是已锁定 Starlette TestClient 对 httpx 的弃用提示，没有新增跳过。
原性能计数用例本次 47.97 秒，通过所有 capture、重放和 Witness 约束。
多规模测试覆盖 12、37、99、29 人及不同分布，使用独立 Fraction 输入预期对账。
P1 删除画像/伪造 Fact/重算哈希、来源修改、规则和披露修改、k=25、撤权、
重启、旧任务、教师审核及报告回归均包含在上述通过结果中。

慢项仍然存在：五维依据测试多个请求累计 123.68 秒，
k=25 重启测试 75.29 秒，多规模测试 76.92 秒。这不是单个 GET/任务的耗时。
本次没有提高任务上限、跳过用例或放宽断言。

最终自查追加了第 14 项回归：`53` 与 `53.0` 的 dataclass 比较相等，
不能替代规范化摘要校验。先实测该回归 **1 failed / 9.07 秒**，
随后在整账本重放中保留原 view/proof SHA 校验；只移除重复 Kernel 计算。

最终代码定向回归（14 项新测试、原性能模块、P1、真实评分、来源/撤权和旧任务报告）：
**26 passed，1 failed，0 skipped，1 warning，3553.21 秒**。
14 项新增回归全部通过。唯一失败为
`test_validation_scope_fails_closed_if_source_changes_mid_phase`，
发生在篡改注入前的正常任务准备（TestClient）：执行 1098.80 秒、
POST 1101.09 秒、GET 2042.99 秒，结果为 `timed_out / AI_TASK_TIMEOUT`，
证明重放计时约 7 微秒，尚未进行画像重放。不能称为模型网络超时。
同轮其他性能用例：多规模 78.33 秒，pipeline 54.37 秒。
期间还观察到请求等待 40 秒的宿主工具实际 1114.28 秒返回；
这与长时间宿主停顿相符，但读取不到睡眠/恢复事件，**不能确认为操作系统睡眠，
也不能据此直接排除代码/环境稳定性问题**。保留此失败，不改断言、不延期。
上一轮 216 项不能代替最终代码的完整通过结论。

该失败用例使用相同参数和断言隔离重跑：**1 passed，1 warning，54.18 秒**，
测试主体 40.26 秒，完成了验证期间修改数据库来源后出口必须拒绝的检查。
日志 `.local/mock-final-isolated.log`、JUnit `.local/mock-final-isolated.xml`。
这缩小了原因范围，但没有消除整轮超时记录；**稳定性仍未达到全绿，不能推送验收**。

最终定向的原始日志：`.local/mock-final-regression.log`，
JUnit：`.local/mock-final-regression.xml`；初版记录分别为
`.local/mock-regression.log`、`.local/mock-regression.xml`。

三个改动 Python 文件 Ruff check、format check 通过；`git diff --check` 通过。
最终未重跑全部 217 项相关测试，也未运行全仓 pytest、前端检查或 GitHub CI；
上述 216 项不是全仓测试结果，
也不证明 Ubuntu 全量 CI 已满足 15 分钟限制。

尚存阻断：最终定向整轮仍保留一次超时失败；后端事务建议尚未正式实施；
完成 GET 在性能候选并发实测仍达几十秒，
不宣称“Mock AI 全链路稳定性已验收”。不继续真实 Qwen 集成，不推送或合并。
