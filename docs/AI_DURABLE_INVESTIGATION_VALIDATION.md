# 持久调查合成验收记录

## 版本与结论

最终执行代码：`7f6be225469169f4836e79eb186a739497bf60e3`，基于 #70 `fa49f6ef10160f103cd15a7c2f66d3a2d8767b63`。后续本记录/HTTP JSON 的提交只补证据，不改执行代码。

已跑通合成闭环，**未开放正式教师 AI、未调用真实模型**。#70 尚无正式 Review；#72 已请求 Peng-Kai789 和 AooMing 审核。CI 排队不能代替批准。

## 实际结果

| 检查 | 结果 |
|---|---|
| 新增持久任务与原 #70 联合专项 | **32 passed，1 warning，321.67s**，包括取消、用途/凭证撤销、并发、重启/到期、预算封顶、错误模型响应及原切片回归 |
| 后端 Ruff / format | 通过，217 个后端文件格式检查；改动原型按同一后端配置另检，14 个文件通过 |
| 实际本机 HTTP / SQLite | **83 个预期响应核对通过**，包含轮询；两次独立 Uvicorn 服务进程，不是 Mock 成功接口 |
| 服务重启 | 第一个服务进程退出，第二个从同一 SQLite 与 campaign journal 重建服务，任务结果一致 |
| 评分依据 | 实际命中 26 人，分页返回 2 条，逐条通过原评分证据接口核对；未审核评语为空 |
| 正式核查 | 关联/群体两项经正式发现规则及全部来源核验，写入原 analysis_results，初态均 pending |
| 教师确认与报告 | 原 PATCH 从 v1 到 v2；MD 可读、XLSX 可重新打开；过时 v1 导出被拒绝 |
| 无发现 | 17 人同分资料返回 0 条选中发现，不凑卡片 |
| 缺证 | 关联原因问题保留统计观察，停止 `needs_external_evidence`，未编造量规/任务依据 |
| 撤销用途 / 凭证 | 已完成任务分别拒绝为 403 / 401；在途测试不发布部分事实，预算预留保留 |
| 预算与重复 | 同键重读原 task；新键不能重置 campaign；6 次模型预留及5次工具上限拒绝越界；重启及更换对象无效 |
| 错误响应 | HTTP Mock 非法模型响应安全失败，不发布已计算的部分事实 |
| 接口 | 实际 OpenAPI 5 条路径 / 25 个可达 Schema；引用无悬空 |

正常合成结果：目标组 26 人、余组 27 人，实际有效均值分别 1 和 5，差值 **−4**；分母 53。调查消耗 3 次 HTTP Mock 规划请求、3 次工具尝试。它验证的是持久接线和可信事实承接，**不是 Qwen 自主调查能力的成绩**。

实际远程模型请求 0；真实 Token 和账单未产生。Mock 的预算预留/usage 仅用于机制验证，不能当真实账单。

## 可核查文件与命令

- `docs/contracts/ai-investigation-tasks.openapi.synthetic.json`：从实际测试宿主提取。
- `docs/contracts/ai-investigation-tasks.http.synthetic.json`：实际 HTTP 状态、聚合事实、Trace、预算、待核查回执和源码指纹。没有原始工作簿、个人行、原评语或凭证。
- 本机 JUnit：`D:/Temp/edu-durable-agent-races-fixed.xml`。
- 本机完整 HTTP 记录：`D:/Temp/edu-durable-investigation-races-fixed-http.json`。

```powershell
# 仓库根目录，已有锁定依赖
$env:PYTHONPATH='.;apps/api'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
uv run --project apps/api --locked python -m pytest apps/api/tests/durable_teacher_agent apps/api/tests/teacher_agent_bridge -q
uv run --project apps/api --locked python -m prototypes.durable_teacher_agent.demo --output D:/Temp/edu-durable-http.json
```

源码指纹用于本次演示运行前后的一致性检查，不是授权凭据，也不替代 Git 版本。演示数据全部由服务端注册夹具生成，原始评分只进入临时私有 SQLite，未发送给任何外部模型。

## 剩余验收

- 本机未重复执行耗时的全量后端；已触发准确代码提交的远端全量 CI，当前仍 queued，**没有全量通过结论**。最终状态见 PR，不引用 #70 旧 CI 冒充本次结果。
- 单机后台任务不自动恢复/续查；进程崩溃后在途任务等 deadline 安全超时，已用额度不退还。
- 教师核查承接 preparing 阶段若进程崩溃，需 operator 核对，不自动再写一份结果。
- 正式用途授权、跨真实会话累计披露、正式动态 Provider、真实模型及独立人工审核仍是阻断。当前可供前端联调的是合成宿主 API，不是已上线产品能力。


## 收尾自查的真实失败与最小修复

在 `815adf5` 执行代码不变的阶段，追加两项竞态回归，**先得到 2 failed**：

1. 第二个 worker 领取同一 running 任务被拒绝后，错误把活跃任务标为 failed。
2. 证据回放期间撤销 AI 用途，长时回读仍返回 200；应拒绝为 403。

对应复现：`D:/Temp/edu-durable-race-before.xml`。修复为“未取得领取权不改变任务”，并给所有只读来源回放绑定持续用途/旧凭证/到期检查；评分快照读取前后均复核用途。修复后的两项专项 **2 passed，61.89s**（`D:/Temp/edu-durable-race-after.xml`）。无用途准入时也在加载评分材料前拒绝。修复后的最终联合回归为 **32 passed**，跨两个服务进程的 HTTP 检查 **83 项预期响应全部通过**。早期 `1e7d5ab` 的 31 项/82 HTTP 是历史记录，不作为当前安全结论。
