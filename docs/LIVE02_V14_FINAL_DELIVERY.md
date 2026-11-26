# V1.4-LIVE-FINAL 交付报告（2026-11-26）

分支：`feat/v14-live-final`（基于 #151 集成点 `80f372b`，未合并 main）
本轮新增提交：`5ea6054` chore(live): 价格快照引用统一到 REVIEWED_PRICE_20261125
远端：#151（Draft，head 仍为 `80f372b`）等待 Antigravity 把本分支并入集成候选

## 1. 结论

网页侧「上传合成答题卡 → 发起 AI 调查 → 真实 Qwen 调用 → 工具执行 → 结果展示 → 个人证据下钻」全链路已用真实模型跑通，并有可复核证据；原有的离线/测试启动方式没有任何改动。

未接通的部分只有一条：五维学情分析（`database_live`）的真实 AI 路径。它缺的是两份独立授权文件与一个五维来源批次，不是代码缺陷，因此按任务书「如实报告」处理，未做任何伪造放行。

## 2. 真实端到端证据（第 4 次评测 `ad082104-3cfa-4459-8587-947dbae861d5`）

| 项 | 值 |
| --- | --- |
| 快照 | `1f10488f18302996fc9a4f8ee460fdb0e2b2e3c431a8c92fe3bda8c47b7ea3f0` |
| campaign | `v14-live-campaign-04` |
| 任务 | `acc3d5cb-c54e-4a86-9e1a-adfb521a60eb` |
| 状态 | `completed`，`execution_origin=qwen_live`，`real_qwen=completed` |
| 模型请求 | 5 次，全部 HTTP 200；输入/输出 token 928/13、1392/85、2241/119、3343/119、4701/188 |
| 计费 | `charged_tokens=13129`，`charged_cny_upper_bound=0.0114988` |
| 工具调用 | 4 次：`get_programming_overview` 1 次 + `inspect_failure_evidence` D1/D2/D3 各 1 次，全部 verified 并绑定 fact_id |
| 结论 | 3 条 finding：7-3 测试点 3 答案错误、7-1 测试点 1 答案错误、7-2 测试点 2 运行错误，各带 8 条学生证据 |
| 结果摘要 | `result_sha=82349c772785107748700b44486b51b58d069fcb9116d9f8495463dc453072ee` |
| 时延 | 14:26:03 创建 → 14:27:18 completed，约 75 秒 |

网页复核截图（本机 `D:\AI\传智杯\live-final\logs\`）：

- `20-ai-completed.png`：已完成面板，含 3 条 finding 与「查看个人证据」入口。
- `21-personal-evidence.png`：点开 S001 后抽屉里显示 7-1/7-2/7-3 三题代码原文（GXX）、逐测试点状态/耗时/内存、来源 `synthetic-v14-answersheets-v4.zip · 包/正式答卷/S001.html · 题块 n`。

失败关闭亦已被验证，不是口头声明：无 Worker 时任务 `timed_out`（`5ef76162…`）；审批预算过紧时 `AI_BUDGET_AUDIT_MISMATCH`（`335d7fb3…`）；请求次数上限 3 不够时 `AI_BUDGET_EXHAUSTED`（`73397658…`，当时 3 次真实请求均已 HTTP 200）。

## 3. 启动方式（外部目录，不入仓库）

外部根 `D:\AI\传智杯\live-final`，脚本：`start-api.ps1`（127.0.0.1:8118，production + 外部 DB/原件/AI state + 审批路径 + 从 User 作用域注入 `DASHSCOPE_API_KEY`）、`start-web.ps1`（3418，`EDU_AI_LIVE_MODE=1` 且清掉测试/合成开关）、`start-worker.ps1`（独立 Worker，production 下任务只由 Worker 领取）。

网页真实模式不需要改前端：非 test 模式下出现「连接账户」弹窗，粘贴教师 token 即可；若页面重载，sessionStorage 指针 `weiran.edu.programming-ai-task.{teacherId}.{assessmentId}.{snapshotId}` 可恢复已建任务。

## 4. 价格快照（本轮代码改动）

`REVIEWED_PRICE_20261119.valid_until=2026-11-26`（当天最后一天）已到期，本轮把测试/文档/审批样例统一切到 2026-11-25 复核快照：

- 测试 6 个文件：`test_material_qwen.py`、`test_ds_live_preflight.py`（TODAY 同步到 2026-11-26、有效期 2026-12-02）、`test_live_admission.py`、`test_live_branch_offline.py`、`test_live_formal_path.py`、`test_programming_live.py`。
- 文档/样例：`docs/LIVE02_PROGRAMMING.md`、`deploy/README.md`、`deploy/programming-live-approval.example.json`（指纹 `8370fae1…`，有效期 2026-12-02）。
- `pricing.py` 的注册表未改：`REVIEWED_PRICE_20261119` 仍保留，历史审批按旧有效期解析，不会被悄悄延长。
- 指纹不变：`code_fingerprint=320a81be9fa7d0c5de9fe34ebe36f8a0c369dee6f9e713c9d8162524d4fbacb2`、`price_fingerprint=8370fae126ae6e5d6c8429349ea47de7a161cc9707377ca0b28ee987eb99113d`。审批文件无需重签。

验证：`pytest tests/durable_teacher_agent tests/edu tests/ai_edu` 共 **1262 passed / 1 skipped**（23 分 47 秒）；改动文件 `ruff check` 全部通过。定向 33 个用例另跑一遍全绿（79 秒）。

## 5. 五维（database_live）真实接入现状

- 已接通：8118 实例带 `WEIRAN_EDU_REAL_DATA_APPROVAL` 重启后，入口不再是 503；五维批次已入库并完成真实调用。
- 演练用合成批次（`profile-balanced-72` / `profile-overlap-96` 变体，72/96 人，学号 900001+/950001+/960001+），审批引用均为 `local-…-rehearsal-*`，只证明链路可通，不构成独立复核。
- 真实调用事实：`origin=qwen_live`、`price_fingerprint=8370fae1…`，单次预留 992,608 token / 0.7956 元。A 批 4 请求 / 4 工具 / 13,129 token / 0.0130854 元；F 批 3 请求 / 3 工具 / 17,275 token / 0.0087335 元；G 批 4 请求 / 4 工具 / 30,041 token / 0.0142650 元。工具全部经服务端核验。
- 统一止步：`AI_LIVE_LIVE_PROFILE_NOT_COMPLETED`（4 次真实尝试，含把审批请求上限降到 4、5 的两次）。账本无超支、无超时，属失败关闭语义。
- 根因（已定位）：profile 路径有效工具预算 = `tool_attempts(5) − 1 种子 = 4`；离线 mock 策略 3–4 次工具即可产出 ≥3 条合格事实并完成，而 qwen3.8-flash 每次都把 4 次工具用于继续探查、从不调用 `finish`，第 5 次请求被工具预算拒绝。即数据与算法没问题，是真实模型在现有预算下的行为不收敛。
- 尝试过的低风险杠杆均无效：降审批 `max_requests` 到 4/5、在模型视图加“合格事实计数 + 尽快 finish”提示（本地试跑后已回滚，未进分支）。
- 结论：五维真实 AI **已接通、可计费、可审计**，但当前“4 次工具内必须 finish”的硬预算与真实模型行为不匹配，尚未产出 `completed`。要真正跑通，需要产品层改动（提高 profile 工具预算，或服务端在最后一次工具后强制收敛），这会让 `code_fingerprint` 变化、必须重新复核；本轮不伪造审批、不自行改指纹，故留作下一步。

## 6. 交给 Antigravity 的事项

1. 把 `feat/v14-live-final`（`5ea6054`）并入 #151 集成候选；本分支未改 `apps/web`、`apps/api/app/ai`。
2. 本分支只改测试/文档/`deploy` 样例，`code_fingerprint` 不变，#151 现有审批契约仍成立。
3. 五维真实路径已可启用，但受 profile 4 次工具预算限制，qwen 真实调用屡次不调用 `finish`；需产品侧调整预算或强制收敛后重跑，届时 `code_fingerprint` 会变，需重新复核审批。
4. 历史遗留待决（本轮未动）：旧分支批量清理、`ci.yml` 的 `codex/**` 触发、`live02-acceptance.yml` 的 `expected_fingerprint` 默认值。
