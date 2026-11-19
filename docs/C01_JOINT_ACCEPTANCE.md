# 未然 EDU V1.4 C-01 联合验收

通过 B-01 实际解析、DS-03 实际内核、C-01 离线 Agent 和本机授权来源回查。验收使用一份已有授权的真实脱敏 ZIP，以及三种从原始 HTML 生成的合成 ZIP。生产 Provider、B-03 持久化、B-04 Live API、前端和真实 Qwen 均为未验收。

## 固定版本

| 对象 | 远端准确 SHA |
| --- | --- |
| main，集成起点 | d2ccd7f79d355a73d28861224602fff2934c31fe |
| B-01 [#105](https://github.com/ZAMYL/weiran-edu/pull/105) | a0d14221f7e12f4d376047b84fbc135a5c84ebc7 |
| DS-03 [#108](https://github.com/ZAMYL/weiran-edu/pull/108) | b929787284b32162f522c1fbec17ef1891e3ab4f |
| C-01 [#107](https://github.com/ZAMYL/weiran-edu/pull/107) | ce8fb6acecddeeb80d759b912dcb367da5f9f9cc |

GitHub PR 接口和实际 fetch 确认 #105 已从提示中的 6e39de7 更新至 a0d1422。按 #105 → #108 → #107 在独立 worktree 依次执行 git merge --no-ff，无冲突。合并节点依次为 cfb9820414bc3fa3a38fc47472f67a1b1ec74235、e24634032afd8434519d4c0219029f2ac941c5b0、61e6ade98b0657a881a8b3cd392a9fa9a795f570。最后一个是验收补丁的父提交。#105 已有路由、数据库等历史改动随原始提交进入基线，联调只调用解析入口。

## 真实脱敏 ZIP

本次本机运行处理 109 个文件，正式答卷 105 份，排除标准答案 1、空白答卷 1、验题组 2。13 题、1,365 条提交与 5,575 条测试点，与已有 DS-02 独立读原 ZIP 的结果一致。按参与者及原始文件/题块序号逐条校验题目、编译器、代码原文、编译输出、判题与分数，按原始行号逐行校验测试点编号、状态、得分、耗时和内存原始值。

771 条 PRESENT，594 条 UNANSWERED。DS-03 对完整代码实际提取 410 条，未知或不支持的语言 360 条，另 1 条不可解析。594 条占位均不提取特征。示例中的 GCC / MODERN_GCC 不能可靠区分 C 与 C++，继续归为未知。CLANGXX 按明确的 C++ 编译器标识处理。原始判题、测试点状态与归一状态分别保留供核验。

适配器将实际 B-01 行传给 DS-03，使用 DS-03 计算结果后构建 C-01 输入。对 ZIP 原字节、ZIP 成员、题块与结果行做来源定位和摘要。特征 Witness 绑定代码内容摘要、DS-03 特征值及置信级别。初次建证据时逐条与 DS-02 独立来源比对。本机 Provider 每次读取重新检查 actor、task、batch、snapshot 和 ZIP 摘要，C-01 回放 Fact 并检查 Witness 归属。验证了 1,365 条判题、771 条代码、5,575 条测试和 435 条特征 Witness 的绑定关系。

真实工具轨迹取自本机成功运行，题目与测试点采用会话别名。该次使用原生工具协议和 HTTP Mock 的确定性选择器，按返回方向的失败比例选取。现有会话实际执行工具，记录 3 个离线计划请求和 2 次工具调用。

| 步骤 | 实际动作或返回 |
| --- | --- |
| get_programming_overview({}) | 返回可核验方向，D1 指向 P7/T2 |
| inspect_failure_evidence | 引用本轮概览 f001，参数 direction=D1。错答 25，覆盖 32，分母为该点有效记录 |
| finish | 选择本轮详情 f002，completed |
| 授权教师 resolve | D1.coverage 32，D1.affected 25，code.available 25，input_parsing.checked 16，input_parsing.present 16 |
| 证据限制 | cause_unconfirmed、features_unavailable_or_withheld，其余特征未达到该次可披露条件 |

覆盖和错答的 Witness ID 集合分别与该题该测试点的实际有效记录及错答记录比较。25 个失败、整题参与人数与排序候选数量均没有作为覆盖分母。报告仅包含脱敏汇总及会话别名。

## 合成反例与拒绝

| 实际运行 | 结果 |
| --- | --- |
| 40 人，第一题点位 900 | 25 错答 / 35 有效覆盖。另 5 条未知状态排除。点位 901 的 40 条正确记录没有进入点位 900 的有效分母 |
| 同一输入直接调用 DS-03 原投影 | 5 条整题部分正确但此点错答，旧投影漏掉它们。索引 checked/present 为 5/0，正确口径为 10/5，5 条特征 Witness 可 resolve |
| 改变另一题失败分布，重排表头并更换题目标签 | 同一选择器将下一步工具参数从 P1 的 D1 改为 P2 的 D2。15/15 的编译失败使用已知作答数，回查 15 条判题见证，显示缺测试证据及缺特征 |
| 目标测试点全部未知 | 有效覆盖 0，概览无方向，finish 返回 insufficient_data，无调查卡 |
| 源文件字节变化或旧快照的 Witness 集合变化 | 已签发 Fact 回查返回 source_validation_failed |
| 空缺的判题来源引用 | C-01 拒绝构建，source_scope_mismatch |
| actor、batch、task、snapshot 错配，任务更换，撤回详情权限或整批授权 | access_denied |
| 另一个 Agent 执行上下文回查旧 Fact | unknown_fact_id |

首个合成 ZIP 的同题状态包括完整代码、5 条未知语言、5 条截断、5 条缺代码；另一题含 UNANSWERED 占位。25 个实际错答者中只有 15 份完整代码可回查，10 份代码的索引特征可判定，其中 5 份命中。对 DS-03 的整题 2×2，5 条部分正确不属于已确认通过/失败集合。已确认通过或失败的 35 条中有 15 条特征不可测，剩余 20 条参与关联计算。

## 最小适配及归属

- scripts/c01_joint_acceptance.py（位于 apps/api）使用 B-01 和 DS-03 既有接口。本地对编译器别名和三种运行错误状态做明确转换，过渡到 C-01 时按实际测试点状态划分群体。DS-03 的整题关联计算沿用原实现。
- tests/research_v2/test_c01_joint_acceptance.py（位于 apps/api）补上从原始 ZIP 到完整工具轨迹的回归。实际脱敏样本通过本机环境变量接入，常规 CI 自动跳过私有数据用例。
- DS-03 / C-01 交接应明确按整题或按点位划定群体。正式 Provider 仍由 B-03/B-04 保留代码状态、截断、原始字段和来源定位，再绑定账号与任务。当前本地 Provider 仅用于隔离验收。
- B-01 对未作答的原始分数占位保留空值。首轮验收脚本误用 int 读取该占位，修正独立核验脚本后再次执行通过。

## DS-05A 复验与教师示例

在 `codex/v14-ds05a-offline-integration` 复用同一组合树。实际重跑本机脱敏 ZIP、两种合成失败分布及缺失证据和预算反例，共 **4 passed，47.50 秒**。工具调用由原 C-01 `ProgrammingExecutor`（`ResearchExecutor` 的编程子类）执行，原生模型协议由 `httpx.MockTransport` 驱动。

教师查看某题错答较多时，本轮记录如下。P7、T2、D1 是本次会话别名。

1. `get_programming_overview({})` 签发概览 `f001`。其中方向 D1 指向 P7/T2，覆盖 32 条已知状态，错答 25 条。
2. 离线选择器只读概览返回的 `affected/covered` 选择下一步。实际执行 `inspect_failure_evidence({overview_id: f001, direction: D1})`，同时提交 `basis_fact_ids=[f001]`。详情 `f002` 返回 25 份代码来源，16 份代码可核验输入解析特征，其中 16 份存在此特征。
3. `finish` 选择 `f002`。调查卡提示核对题意、边界输入和输出并复查代码。原因状态为 `cause_unconfirmed`。存在输入解析结构不能解释错答原因。
4. 分别对 `f002` 的 `D1.coverage`、`D1.affected`、`code.available`、`input_parsing.checked`、`input_parsing.present` 调用 `resolve`，返回 32、25、25、16、16 条 Witness。按成员摘要、文件内题块和测试表行定位逐项校对。公开记录只展示数量和别名。

相同选择器在合成分布 A 选择 D1/P1/T1 的错答（25/35），在分布 B 选择 D2/P2 的编译失败（15/15 已知作答），证据改为判题行。A 的索引特征有 5/10；DS-03 独立整题 2×2 的四格为 0 失败/5 通过（有索引）及 5 失败/10 通过（无索引），总体有效样本 20，差值 `0/5－5/15＝−1/3`。整题的关联分母与测试点的有效覆盖 35、失败群体中可测代码 10 互不替代。

反例还验证了两个离线模型额度用尽后，已经执行过的概览及详情也不能发布调查卡。同一账本无法重新打开。同一 ZIP 字节构建的另一个任务无法使用原任务的预算。首轮已绑定的 actor、task、batch、snapshot、详情权限、来源字节和 Witness 变更反例继续覆盖。

### B-03 最小字段接口

| 对象 | 需要持久化并供正式 Provider 回放的字段 |
| --- | --- |
| 批次、快照与授权 | 稳定 batch ID、不可变 snapshot ID、原资产 SHA-256、受信任 actor/task 绑定和独立证据详情授权。查询及返回前重新检查授权；只能读取请求锁定的快照。 |
| 提交 | 稳定 submission ID/参与者匿名 ID/题目 ID、原始判题文本和规范状态、语言标签、代码原文或授权引用、代码 PRESENT/MISSING/UNANSWERED、截断标记、原文件 entry 摘要与题块定位；编译诊断沿原来源保留。 |
| 测试证据 | 稳定 test-observation ID、所属提交 ID、点位 ID、原始判题和 known/unknown 标记、原行定位与内容摘要；得分、耗时、内存保留实际值与原单位，空值不填充。 |
| DS-03 特征（可选） | 题目＋可选点位＋失败状态的准确调查口径、特征版本、检查集合、命中集合及代码 Witness 引用；保留可测分母和提取置信级别。 |
| Fact/Witness | 服务端回查所用稳定来源引用和 `source_id / source_sha256 / entry_sha256 / locator / content_sha256 / kind`，以及提交/题目归属；本地回放必须能重新取得相同来源。模型只见别名和允许披露的汇总。 |

内存适配按当前 B-01/C-01 一人一题一条提交契约连接测试行。正式 B-03 如支持多次提交，应按稳定 `submission_id` 挂载测试点，并明确选取哪次提交进入同一调查，避免混合版本。此分支未新增数据库字段或正式 Provider。

## 复现

从验收分支的 apps/api 运行。使用锁定依赖，合成数据当场生成。

~~~powershell
uv sync --locked --group dev
$env:PYTHONUTF8 = '1'
$env:WEIRAN_EDU_ENV = 'test'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
uv run --locked --no-sync ruff check .
uv run --locked --no-sync ruff format --check .
uv run --locked --no-sync pytest -q tests/importing tests/code tests/research_v2 tests/test_windows_shard_plan.py
uv run --locked --no-sync pytest -q -s tests/research_v2/test_c01_joint_acceptance.py
~~~

有本地授权脱敏 ZIP 和 DS-02 脚本时，在同目录设置下列变量。真实核验只输出汇总，保留终端回溯关闭。

~~~powershell
$env:EDU_C01_REDACTED_ZIP = '<本机已授权脱敏 ZIP 路径>'
$env:PYTHONPATH = '.;../..;<含 ds02_audit.py 的本地目录>'
uv run --locked --no-sync pytest -q -s --tb=no tests/research_v2/test_c01_joint_acceptance.py
~~~

实际环境为 Windows、CPython 3.12.10。底层定向检查 323 passed。联合用例含真实 ZIP 为 3 passed，15.25 秒。最终导入、DS-03、Research V2 与分片计划定向回归包含真实样本，共 480 passed，244.07 秒。后端 Ruff / format 293 文件通过，原型六个变更文件从仓库根按原型门禁执行 Ruff / format 通过。

原始提交 CI 快照，#105 8/8 成功、#107 8/8 成功、#108 Windows 与前端任务通过而 Ubuntu 后端为 cancelled。联合 Draft 的 CI 用于验证该分支的合成回归。私有样本仅在本机实际执行。
