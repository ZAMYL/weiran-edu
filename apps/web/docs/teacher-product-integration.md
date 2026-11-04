# V1 教师闭环集成验收

> 本文保留固定基线的接口与集成记录；当前教师界面与验收入口以 [最终工作台说明](teacher-workbench-v1.md) 为准。

此分支仅整合前端及测试，不合并到 main，不改正式后端、AI、安全授权或数据库。

## 固定代码基线与依赖

- main：`5218af4daa621f6a39ed1055481790d708e7459f`，已含 #49/#56/#58；没有重复合并 AI 提交。
- #55：`cfd523388094d657e35b34199f528b29aab4d404`，正式发现查询、图表和来源下钻。
- #40：`7c1e1584833038a3add6f20e3a9505883f5942c4`，保存分析、逐卡核验、报告导出及既有三页交互。
- 联合验收后端 #61：`0b6eeded5ac0de174f09b015c139eb3ae2444a97`。项目负责人明确允许使用该固定提交作为只读隔离后端；前端仍基于 main。

**main 目前不能保存正式 discovery。完整新发现闭环依赖 #61 审核合并，不可将本次隔离验收宣称为 main 已完成的能力。** 主线旧版统计候选保存仍走原契约；正式发现保存不降级伪装为统计候选。

## 逐文件冲突解决

两分支共同修改四个文件：

| 文件                             | 处理                                                                                      |
| -------------------------------- | ----------------------------------------------------------------------------------------- |
| `LiveInsightsPage.tsx`           | 手工合并，保留正式发现及统计概况，并接入保存与核验。评分统计依据与主动发现分开展示。      |
| `services/edu-api/client.ts`     | 手工合并，保留 discovery 查询和保存、核验、预检、二进制下载方法，全部沿用会话及响应校验。 |
| `app/api/edu/[...path]/route.ts` | 自动合并后复查；保留发现 POST 白名单、PATCH 和受限二进制下载，未新增通用转发。            |
| `app/api/edu/relay.test.ts`      | 保留双方测试，覆盖发现、版本冲突、请求 ID 和下载安全响应。                                |

保留 #40 分步导入：选择文件、可修改名称、下一步预览、确认导入。#55 浏览器用例只调整操作和定位器，其发现类型、图表、分页、空状态和权限断言不删除。

## 集成行为

- 教师显式勾选最多三项正式发现，保存时发送原 `input_key`、发现 ID 和外层 `scope_conditions`；可选择仅保存范围概况，不默认保存全部发现。
- 原群体以服务端受控 `metric_key` 回放，避免将内部复杂条件挤进至多两个手动筛选。分页不改变人数。
- 画像链接仅含批次、快照、发现、指标与外层条件；刷新后重新检查授权批次，并重新查询发现及原成员。链接不代表授权，不含凭证或学生明细。
- 应用手动条件或切换批次时退出原发现，清除指针；未应用的手动修改禁止保存。
- 已保存结果分别解码统计候选和正式发现；核对批次、快照、范围、规则版本、指标及核验 ID，正式发现与保存快照必须完全一致。
- 原发现群体的保存范围是发现的外层分析范围，保存的发现内部仍保留目标群体条件。例如范围 53 人、对照群体 26/27 人不是分母冲突。
- 未提供比例的参与记录接口只展示实际人数和分母，前端不另造比例。教师确认不是因果证明。
- 只展示授权评分与已审核依据；withheld 文本不作为空评语，不伪造原文。

## 可复现运行

前端在本分支安装锁定依赖并先完成 `pnpm build`，再启动测试；运行中的生产服务不要与构建共用同一 `.next` 写入过程。

```powershell
cd D:\AI\weiran-edu-v1-teacher-integration\apps\web
pnpm install --frozen-lockfile
pnpm lint
pnpm format:check
pnpm typecheck
pnpm build
pnpm test

# 指向项目负责人批准的只读固定后端，不修改后端源码。
$env:PYTHONUTF8='1'
$env:EDU_TEST_API_ROOT='D:\AI\weiran-edu-v1-backend-pr61\apps\api'
$env:EDU_TEST_PYTHON="$env:EDU_TEST_API_ROOT\.venv\Scripts\python.exe"
# 需要时指定现有 Chromium 可执行文件，否则测试尝试已安装 Chrome/Edge。
# $env:EDU_TEST_BROWSER='...'
node tests/credential-relay-e2e.mjs
node tests/discovery-api-flow.mjs
node tests/live-api-smoke.mjs
node tests/teacher-product-e2e.mjs
```

测试均自动选择独立端口，启动正式 FastAPI + Next 生产服务，创建临时 SQLite、合成双 Sheet Excel 和短期教师凭证，结束后关闭本次服务并删除临时目录。成功路径不拦截或模拟 HTTP。不会读取真实教师资料或调用模型。

`artifacts/discovery-flow`、`artifacts/live-api` 和 `artifacts/teacher-product` 为忽略的本地验收产物，包含真实 Chromium 截图和 JSON 记录。联合报告还重新打开 XLSX 检查批次、快照、已确认发现 ID 和敏感文本排除；Markdown 同样核对。

## 验收记录

最终实际命令结果见本 PR 描述。原 #40/#55 场景与新增联合 E2E 分别执行，不能用单元 Mock 替代浏览器成功链路。常规 GitHub CI 执行主线后端与前端静态、构建、生产 smoke，不冒称 CI 已使用未合并 #61。

## 产品验收与剩余边界

保留「学情洞察」「画像探索」「测评数据」三页及原视觉，不新增聊天框。当前正式输入仍为五维 Excel；逐题 ZIP/HTML 与远程 Qwen 只是受控合成原型，教师页面不开放。

请 AooMing 检查发现选择、原群体跳转及窄屏交互；请 Peng-Kai789 检查 #61 保存契约、快照、版本与权限。正式上线须先完成 #61 后端审核，再由负责人做产品验收。本分支不自动合并、不部署公网。

### 本机后端回归说明

固定 #61 源码保持不变。首次直接运行全量 pytest：1437 passed / 1 skipped / 2 failed（1459.99s）。失败项为独立 CLI 子进程输出的 UTF-8 解码，以及崩溃子进程在 5 秒内未返回而得到 `DISCOVERY_TIMEOUT`。按 CI 配置设置 `PYTHONUTF8=1` 后复测：CLI 项通过，崩溃检测项仍超时；未修改测试超时或后端代码掩盖结果。#61 自身 GitHub CI 的 Ubuntu、Windows 后端均通过（run 40368733942），本集成 PR 的主线后端 CI 也独立执行。这里的远端结果不能代替本机失败记录。

### 凭证失效的代理回归

真实 HTTP 最小复现确认：撤销同一合成凭证后，FastAPI 直接 POST 返回 401，Next 的流式 JSON POST 曾返回 503 `BACKEND_UNAVAILABLE`。本次只对非文件上传请求先在原 64 KiB 上限内完整读取，再转发；40 秒超时及浏览器取消信号也约束读取过程。10 MiB 文件上传仍流式转发，不放宽路径、授权或响应校验。单元测试验证立即 401、请求 ID、no-store 与原超限场景；独立 `credential-relay-e2e.mjs` 用真实后端复核。

已保存结果内的发现依据抽屉不允许直接跨页跳转，避免绕过未保存核验修改的离开确认。正常发现页面仍可进入原群体。
