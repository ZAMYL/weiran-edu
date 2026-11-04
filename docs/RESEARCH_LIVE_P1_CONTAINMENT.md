# #66 Live P1 安全收口（仅离线）

适用基线：`f3799a1f1d9abb0520f38b8dec6780217e3cbd5f`。
正式审核：[Peng-Kai789 CHANGES_REQUESTED](https://github.com/ZAMYL/weiran-edu/pull/66#pullrequestreview-5770520743)。这不是批准，更不是模型执行授权。

## 复现与处置

本机项目工作区的 `outputs/review-66-67-offline-probe.py` 和 `review66-offline-proof.json` 未找到，不能声称读取过原文件。已读取正式审核和两条行内意见，按其参数独立复现：

- 固定旧快照有效日 **2026-10-26**，不更改价格或授权日期。不是 11 月真实执行或费用。
- 相同字节授权、同 campaign，上限 1 请求 / 992608 Token / 0.8 元，复制到两个父目录。
- 旧代码实际进入原 ResearchPlanner 构造，凭据加载为替身，HTTP 客户端禁止构造：两次预留共 **2 请求 / 1985216 Token / 1.5912128 元旧目录价上界**。
- 原测试先得到 **1 failed**，本机证据 `D:/Temp/edu-review66-live-before.xml`；真实模型调用、真实 Key 读取均为 0。

**本轮选择封禁，不开发新 Live 授权体系。当前允许的 Live 入口数为零。**

1. Research demo `--execute` 在检查授权/输出路径前固定拒绝，不读授权文件，不加载配置，不预留额度。删除 `authorization.parent/research-v2-budgets` 可执行分支。
2. 旧 adaptive CLI 的 `--execute` 及其可直接导入的 `execute()` 同样拒绝，删除其另一套 Live 账本根派生分支。
3. 原预算构造器 `origin=synthetic_live` 在授权处理和创建目录/消费文件之前拒绝。
4. 原 QwenAdaptivePlanner 与两条调查运行层拒绝任何非 HTTP Mock 或 `allow_network=True` 调用；不能绕过 CLI 直接读 Key。
5. 固定错误 `synthetic_live_disabled_pending_independent_review`。没有授权字段、日期、路径或 CLI 开关可以打开。

HTTP Mock 的统计、Fact、来源/披露、请求/工具/Token/人民币限制、预留和未知 usage 保守持有逻辑不改。没有以 `offline_only=True` 伪装真实网络执行。

### 对“共享账本”的准确边界

本补丁不是已经实现了可用的统一 Live 消费服务。通过关闭全部旧/新底层 Live 路径消除多账本消费机会，复制授权、换输出目录、失败或取消后再次执行均不能进入 Live 准备或获得新额度。

原 `CampaignBudget(state_dir, origin=http_mock)` 仍供隔离的离线测试使用，不宣称其任意目录具备全局收费预算隔离能力。未来恢复任何真实调用，必须由**唯一、独立审核过的服务**绑定准确代码/计划/真实 APPROVED，并强制所有入口使用同一个由服务端控制、与输入/授权文件/报告路径无关的账本根和稳定 campaign；禁止仅删掉封禁函数或重新开启旧 CLI。OS 管理员手动删除账本不属于本原型承诺的对抗范围。

## 离线验收

新增 `tests/research_v2/test_live_safety.py`：

- 同授权换父目录，检查 Planner 准备、配置读取替身、预留及新 journal 均为 0。
- 预算、Planner、adaptive runtime、Research runtime、旧 CLI 五层直接绕过均拒绝。
- 失败/取消后，原离线 ledger 的 1 请求 / 992608 Token / 0.7956064 元预留不退还，同 campaign 原账本不能重开；换授权目录也无法启用 Live。
- `--execute` 在授权或输出路径任何文件访问前拒绝。
- 更新旧“显式 opt-in 即可进入 Live 构造”的政策测试为“即使 opt-in 也封禁”；这是安全政策收紧，不改评分 Oracle、费用预期或统计断言。

10 项聚焦安全检查实际通过（2.80s），真实模型/Key 读取均为 0。Research/原生 HTTP Mock/预算联合回归 **90 passed（187.38s）**，本机 JUnit `D:/Temp/edu-review66-targeted-final.xml`。首次扩展回归曾 89 passed / 1 failed（Mock 身份错误码），随后调整代码保留原 `mock_transport_required`，没有改旧断言；保留 `D:/Temp/edu-review66-targeted.xml`。后端 Ruff / format 通过（198 文件），改动原型额外按后端配置检查通过。未修改价格、冻结答案、统计 Kernel、教师 API、数据库、前端、共享依赖或 CI。

## #67 / #70 / #72 的依赖处理

- **#67 需要同步**：它仍携带旧 Research demo 分支。其 pilot 虽已有独立 Live 根与批准门禁，不能保护旧 CLI 旁路。同步后 Live 一并关闭；恢复需新的准确 SHA 独立审核与统一服务设计，不能沿用旧批准。保留其新价格/预算硬限，不能整文件覆盖预算实现。
- **#70 需要在后续依赖整合时同步**：保留 OfflineSnapshotPermit 的精确 DB 准入、`origin=http_mock` 与 Witness 校验，选择性加入封禁。runtime 已拆出 `_investigate`，应把封禁放在共享实际执行入口，不能覆盖 DB adapter。
- **#72 本轮冻结不改**：后续随审核后的 #70 同步，保留 SQLite task budget/disclosure、worker ownership 与 AuthorizedEnvelope 用途复查，再跑其32项回归。当前合成 API 已只用 HTTP Mock，但分支携带的旧 CLI 风险仍需依赖更新，不能声称父补丁已自动传播。

本轮只改 #66 原分支，未自动 cherry-pick、merge/rebase 或改动上述工作区。

## #72 Windows CI

固定运行 `41866645380`、Windows job `126703209867`：开始 2026-11-05 00:24:22，结束 00:39:31（北京时间）。原始 check annotation：`The job has exceeded the maximum execution time of 15m0s`，随后 `The operation was canceled.`。Ubuntu 与前端已 SUCCESS。

所以不是普通手工取消，也没有可供宣称通过的 Windows pytest 完整结果。未修改冻结 #72 的15分钟配置，未重跑已成功作业，也未盲目重复同样超时任务。若另行决定重试，应仅重跑 Windows job；增加作业时长或排查慢测试属于后续明确审核范围，不能吞断言或放宽 Worker 测试。
