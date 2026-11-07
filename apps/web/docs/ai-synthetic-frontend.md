# 学情诊断的隔离 AI 任务联调

前端沿用动态测评 PR #73 与 AI PR #74，仅在本机合成宿主中开放。当前复验后端固定为 #75 `babd36e5b2f15fd42d0b5e714e3d06c73122a2a1`，父提交为 #72 `757bfd86cdd890e47d9e91fdc52c0742a39c2795`。两者的任务宿主与 OpenAPI 逐文件一致，#75 仅补充受控问句映射和后端测试。DTO 仍使用 `docs/contracts/ai-investigation-tasks.openapi.synthetic.json`。全程使用 HTTP Mock，真实 Qwen 未调用。旧 `0c73569` 验收仅作为历史证据。

## 启动与边界

按后端 `AI_SYNTHETIC_FRONTEND_HANDOFF.md` 启动 `python -m prototypes.durable_teacher_agent.serve`，读取其本机私有 connection 文件。把其中 bearer_token 写入同一私有目录的前端测试会话文件，形状沿用 `{synthetic_only:true,access_token:…}`。前端服务端设置 `EDU_API_ORIGIN`、`EDU_TEST_SESSION_FILE`、`EDU_TEST_MODE=1`、`EDU_AI_SYNTHETIC_MODE=1`。凭据由服务端读取，浏览器只收到开关状态。

入口显示在原学情诊断内容上方，发现卡片在桌面并列、手机纵向排列。合成模式使用「AI 分析演示」标题，当前测评选择器标明「合成测评」。任务路径必须同时满足回环访问、回环上游和两个显式开关。代理仅允许提交、读取、取消、依据查询及待核查提交的准确方法和路径。

## 操作流程

教师问题原样提交，使用同一幂等键重试。202 后按服务端状态轮询，终止或错误后停止轮询。任务指针保存在当前浏览器会话中，重新进入页面或刷新时重新鉴权读取；指针按教师、批次、快照隔离。

展示服务端选择且核验后的事实及原指标。依据查询使用 task/fact/metric 引用，打开原评分证据抽屉。继续探索使用返回的原条件。勾选至多三项事实后，后端重算正式发现并生成 pending 待核查结果，前端进入现有分析报告页面。教师确认和 MD/XLSX 导出复用原版本控制与文件检查。

## 常用问句和有限范围停止

#75 已支持原目标问句「有哪些值得关注的问题？」。前端原样提交，浏览器验收断言 HTTP 请求正文与教师输入完全一致、创建返回202、回读目标为 priority。真正不支持的「帮我分析一下」继续返回 `422 AI_GOAL_UNSUPPORTED`；测试在其后直接核对该批任务数为零，再执行正常请求，确保拒绝不消耗唯一 campaign。

服务端返回 `bounded_scope_stop` 时，开始分析旁只显示一条「核查未覆盖所有问题」。该状态仍可保留核验事实，不能解释为已发现所有问题。跨快照和跨批次任务引用保持服务端409，并在当前页面显示错误，不借用其他测评的结果。

正式教师产品和动态逐题 AI 准入仍关闭。此环境的 completed 是合成任务完成状态，不表示真实模型执行或因果解释成立。

## 浏览器验收

构建前端后设置 `EDU_AI_CONNECTION` 指向宿主 connection 私有文件、`EDU_AI_API_ROOT` 指向候选根目录、`EDU_TEST_PYTHON` 指向锁定 Python、`EDU_TEST_BACKEND_REVISION` 为实际后端完整SHA、`EDU_TEST_FRONTEND_REVISION` 为实际前端SHA，执行 `node tests/ai-task-e2e.mjs`。测试使用宿主新建的 normal/evidence/empty 三批次，实际核验原问句202、轮询、priority事实、原证据、探索、pending、教师确认、原两种报告、零发现、缺证及真实成员撤销后的403/404拒绝，并保留桌面、390px和跨批次错误截图。

宿主每批只允许一个持久 campaign，重跑需新的宿主环境。测试末尾会撤销本轮临时教学空间成员权限。本轮结果和截图位于 `artifacts/ai-task-priority/`，旧 `artifacts/ai-task/` 证据保留。所有输入均为合成数据，产物中不包含凭据、原始教师工作簿或真实模型账单。
