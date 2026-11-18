# #90 + #91 独立集成候选验收

> ⚠️ 本文部分内容已被 V1.4 取代，以 docs/V1.4_BASELINE.md 为准。

本候选只组合用户指定交付，不修改当前演示，不合并 release/main，不启用真实模型。

## 提交与冲突处理

- 固定基线：`15788491b536d408005030ddbed2cee2d1a9f6b2`。
- #90：`31db912412a36c492f0c38c40ac7999e577a2b1b`。
- #91：`da7c27920f3c40cf53e57efafa805c73894a3e84`。
- 独立分支：`codex/edu-analysis-perf-integration-90-91`。
- 产品合并节点：`deebf4b1a96f53b2b4fcfaf431780dccc79abf85`；后续只增加验收脚本、记录及截图。

两个准确 SHA 均作为祖先保留；使用两次 `merge --no-ff`，没有引入其他 PR，没有文本冲突。合并后人工核对两个重叠文件：

| 文件 | 保留 #90 | 保留 #91 |
| --- | --- | --- |
| `LiveDiscoveries.tsx` | 既有最多 3 条代表发现直接展示；实际标题、完整解释、其他发现/分页 | 列表/代表详情改用诊断专用复用方法；候选资格、次序不变 |
| `client.ts` | 未知 AI 提问保持原 422/code，提供能力范围和操作提示 | 随机会话 nonce、退出/重连轮换、仅诊断摘要启用复用；普通证据/报告不启用 |

[逐文件来源审计](validation/integration-90-91/merge-audit.json) 验证产品差异恰好是两个提交的 29 文件并集，27 个非重叠文件与各自交付版本完全一致。`apps/api/`、`prototypes/`、两端锁文件与 #91 的产品版本完全一致。没有手改业务逻辑来强行消除失败。

## 静态与后端检查

- 前端 `vitest run`：18 文件，254 passed。
- ESLint、`next typegen`、`tsc --noEmit`：PASS。
- 组合涉及的后端 Python 文件与新增测试宿主 Ruff/format：PASS；后端在 `apps/api` 目录运行检查，测试宿主按仓库根目录的显式配置检查。
- `next build --webpack` 与 `node tests/smoke.mjs`：PASS。使用与两项交付完全相同锁文件的既有依赖；Webpack 避开跨 worktree node_modules junction 的 Turbopack 限制。
- Prettier `--check --end-of-line auto .`：PASS；标准 `--check .`：FAIL（138 个文件，Windows CRLF/LF 问题），不冒充全绿，未批量改全库格式。
- 后端针对性首轮：**74 passed、2 failed**，不能写成全绿。包括发现 API、来源验证、worker、复用隔离、报告及合成 AI 用例。
- 两个失败用例不改代码、停止并行计算后单独重测：**2 passed / 82.39 秒**。这不能抹去首轮超时。

| 首轮失败用例 | 原始失败阶段 |
| --- | --- |
| `test_persist_reload_evidence_review_and_report` | `POST .../review-submissions` 期望 200，实际 503 `DISCOVERY_TIMEOUT` |
| `test_teacher_priority_alias_rejects_unsafe_text_and_reuses_controlled_task` | 导入 commit 期望 201，实际 503 `IMPORT_PROCESSING_UNAVAILABLE` |

实际后端命令：

```powershell
# apps/api 目录；PYTHONPATH='.;../..'
python -m pytest -q tests/edu/test_discovery_api.py tests/edu/test_discovery_bridge.py tests/edu/test_discovery_worker.py tests/edu/test_discovery_reuse.py tests/edu/test_discovery_results.py tests/edu/test_discovery_report_limits.py tests/durable_teacher_agent/test_tasks.py tests/durable_teacher_agent/test_priority_questions.py
python -m pytest -q tests/durable_teacher_agent/test_tasks.py::test_persist_reload_evidence_review_and_report tests/durable_teacher_agent/test_priority_questions.py::test_teacher_priority_alias_rejects_unsafe_text_and_reuses_controlled_task
```

使用既有锁定 Python 3.12 环境；测试只用合成工作簿与外部临时 SQLite。原有相关回归覆盖 token/成员/只读授权撤销、命中后处理中撤权、评分交换但整体统计不变、筛选/快照/凭据/过期隔离。未运行全量后端 pytest。

## 浏览器与性能

使用生产 Next、真实 Uvicorn/FastAPI、临时 SQLite、用户目录 Chromium。没有拦截并伪造成功响应，正式产品 40 秒代理上限保持不变。

首轮 72 人已登记合成批次：基础账户/批次/评分标准可读，但 `discoveries/query` 在约 40 秒返回 **HTTP 504**，重点发现没有加载；120 秒是测试等待卡片的上限，不是平台成功耗时。第二次静态概况/发现请求均发生 504；第三次直接 Uvicorn 宿主下统计恢复 200，发现仍为 504。三次失败均保留真实 JSON 与截图，不能把未出现的分析文字当作浏览器 PASS。

为区分测试启动成本，验收宿主后续把测试专用 AI 导入放入 `main()`，避免 Windows 发现子进程通过 `__mp_main__` 重复导入；并取消初始化的额外页面冷加载。后续直接使用独立 Uvicorn 服务，仍调用原 FastAPI 路由、真实解析器、发现 worker、权限和来源验证。没有因此修改产品执行路径或超时。重复 504 尚未归因，不能只归咎于测试脚本。

[顺序对照及逐请求证据](validation/integration-90-91/performance-and-isolation.json)：同一确定性 105 人输入、同一脚本、不同独立生产实例，单位秒。

| 场景 | #91 单独 | #90 + #91 | 单独/组合真实 worker 次数 | 发现请求状态 |
| --- | ---: | ---: | --- | --- |
| 首次 | 9.970 | 10.424 | 1 / 1 | 各 3 次，全部 200 |
| 返回 1 | 5.161 | 5.097 | 0 / 0 | 各 3 次，全部 200 |
| 返回 2 | 5.079 | 5.110 | 0 / 0 | 各 3 次，全部 200 |
| 返回 3 | 5.162 | 5.079 | 0 / 0 | 各 3 次，全部 200 |

此场景未见重复计算退化；首次差 0.454 秒，不将单次顺序测量当作普遍性能保证。该成功不能覆盖合成 AI 宿主的失败。

组合候选在该真实生产场景下还通过：切换至另一测评、返回其他批次、同令牌结束会话后重连、教师切换（B 不显示 A 内容）、成员撤销 404、令牌撤销 401、恢复授权后的新会话、新修订批次/快照、错配快照 409、合法历史快照仍可读。撤权后旧发现卡为 0。普通图表、学生及证据直连 API 均 200，不携带复用 nonce。

另行执行了合成 AI 宿主中 53 人 normal 批次的浏览器提问检查：[原始记录](validation/integration-90-91/ai-browser-results.json)。统计 145ms / 200、评分概况 266ms / 200；发现冷请求 39.972 秒 / 504；提交未知问题的 `POST .../ai-investigation-tasks` 为 **40.014 秒 / 504**，没有得到应有的 422，更没有得到可宣称成功的调查结果。120 秒只是 Playwright 等待预期响应的测试上限。根因未归因，不把所有超时归因于并行压力或测试宿主。

| 组合浏览器项目 | 结论 |
| --- | --- |
| 105 人诊断、多条重点发现、差异化标题 | PASS，见截图；完整文本纯函数另有 254 项前端测试中的对应回归 |
| 返回性能、真实计算次数及教师/批次/快照/撤权隔离 | PASS，见上述原始 JSON；不等于其他宿主也通过 |
| 72 人已登记合成批次的重点发现 | FAIL，三次 504；完整说明未展示，不记为通过 |
| 96 人已登记批次的完整浏览器分析 | INCONCLUSIVE，脚本在 72 人前置失败后停止，未执行 |
| 合成来源与模拟模型标记、四个快捷问题按钮 | 界面可见；按钮选择/实际发起调查未完成，不记完整提问流程 PASS |
| AI 提问 API、成功调查及 AI 依据 | 提问入口 FAIL（504），后续 INCONCLUSIVE；没有绕过授权或伪造结果 |
| 正式报告入口导航及数据读取 | INCONCLUSIVE，浏览器在 AI 前置失败处停止；诊断页入口按钮可见，不能代替导航验收 |
| 未处理页面异常 | 各已执行浏览器运行均未捕获；超时均作为失败显示，不是 HTTP 成功 |

390px 业务截图未完成（前置请求失败），不声明窄屏验收通过。

![105 人组合诊断](validation/integration-90-91/diagnosis-105.png)

![72 人冷请求失败](validation/integration-90-91/blocked-72.png)

![53 人 AI 宿主失败](validation/integration-90-91/ai-blocked.png)

[三次失败请求原始记录](validation/integration-90-91/blocked-72-attempts.json) 与截图只包含本次合成资料。72/96 完整分析浏览器闭环尚未通过，不用原 PR 的单测或历史截图替代。

## 复测命令与边界

```powershell
# apps/web 目录；路径均指向独立 worktree 和锁定环境
$env:EDU_TEST_PYTHON = '<锁定环境 Scripts/python.exe>'
$env:EDU_TEST_BROWSER = "$env:LOCALAPPDATA/Google/Chrome/Application/chrome.exe"
node tests/analysis-perf-integration.mjs

# 仅独立检查 AI 提问、AI 评分依据与报告入口，不代表重点发现通过
$env:EDU_INTEGRATION_AI_ONLY = '1'
node tests/analysis-perf-integration.mjs

# 同一 105 人输入的性能与数据隔离
$env:EDU_BENCH_REPO = '<PR91 或组合候选绝对路径>'
$env:EDU_BENCH_LABEL = '<对应准确提交标识>'
$env:EDU_BENCH_OUTPUT = '<输出 JSON 绝对路径>'
node tests/diagnosis-return-reuse.mjs
```

浏览器测试只检查报告入口，不提交 review-submissions 或生成正式报告；后端隔离测试可在自己的临时库运行既有报告回归。模拟推理明确标记 `http_mock / not_run`；没有读取付费模型凭据，没有实际 Qwen 调用。

## 集成判断

当前作为 **Draft / 存在封版阻断** 保留。合成 AI 宿主冷启动发现和调查入口 504 未解决，不能宣称完整运行已验证。尚未对 #91 单独版本在同一 72 人 AI 宿主做对照，因此也不能断言该失败是组合新回归或既有问题。需要先定位该宿主的请求/计算阻塞，再重跑 72/96、成功调查、AI 依据与报告入口。

没有扩展业务逻辑、修改预算、权限、来源、Fact/Witness 或增加未经交付的后端修复；也没有把尚未交付的教师体验改造带入候选。当前演示启动脚本的既有未提交改动被原样保留，验收前后 SHA256 均为 `E32F11F8DDFB0A398AA71B2532A3EDA354B643E5B476220AA6E6113AAE610BE1`。不建议用这个尚未通过完整验收的候选替换现有演示。
