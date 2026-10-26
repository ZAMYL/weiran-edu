# 未然 EDU V1 教师闭环：前端联合集成

## 集成来源与不变边界

以 main `5218af4` 为基线，通过 Git tree 对比并整合 #40（分析保存/核验/导出）与 #55（正式发现/图表/原群体/证据）的前端增量。**原 PR 分支不变。** 保持 #49/#56/#58 已合并 AI 内核、正式 FastAPI、SQLite、Excel 解析器、评测基准不变。

四个重叠位置采用有意识的联合改动：

| 文件 | 联合处理 |
| --- | --- |
| `src/app/api/edu/[...path]/route.ts` | 保留报告附件校验/GET/PATCH，同时仅对白名单放行四个发现 POST |
| `src/app/api/edu/relay.test.ts` | 保留文件响应、拒绝与安全头测试，追加发现代理测试 |
| `src/services/edu-api/client.ts` | 保留报告流式下载/版本错误响应，再增正式发现强类型查询与快照核验 |
| `src/features/live/LiveInsightsPage.tsx` | 同时保留「保存与核验」和「主动学情发现」；后者按批次/快照/筛选条件重新挂载 |

「发现详情 → 按发现条件探索」仅在 **原有 scope + 内部条件** 可无损表示为最多两个不同维度、百分位精度以内的 AND 筛选时提供按钮。超过 UI 能力上限时仍保留后端原群体 records/query，不将条件删减后伪称同一群体。独立单元测试覆盖重复维度、第三条件、非两位小数和显示值矛盾。

## 可复现验证（必须以执行结果为准）

使用本项目锁定版本：Node 24 + pnpm 11.25、Python 3.12 + uv。

```powershell
cd apps/api
uv sync --locked --group dev
uv run --locked pytest -q -ra
cd ../web
pnpm install --frozen-lockfile
pnpm lint
pnpm format:check
pnpm typecheck
pnpm build
pnpm test
# 后续两条脚本使用真 FastAPI、临时 SQLite、生产 Next、Playwright Chromium；
# 需要 Playwright 浏览器二进制，以及后端 Python 环境绝对路径
$env:EDU_TEST_PYTHON = 'D:\\path\\to\\locked\\python.exe'
node tests/discovery-api-flow.mjs
node tests/live-api-smoke.mjs
```

## 联合浏览器场景

`tests/discovery-api-flow.mjs` 在原有 53 人、少见组合、空发现、筛选边界和权限撤销测试上，增加 **同一个 53 人合成会话**：

1. 上传文件后先填写测评名称，显式点击「下一步：预览校验」，确认入库。
2. 正式发现列表 → 详情/完整图表 → 受控群体/分页 → 实际评分证据。
3. 群体发现原始条件 → 画像探索，核对服务端筛选人数；重置后回原测评。
4. 同一批次保存当前分析 → 修改候选核验状态 → 服务端重读 → 导出前预检 → 下载真实 Markdown。
5. 原有 E2E 分别补充其他版本冲突、XLSX、只读与撤权、空发现、窄屏测试。

截图和结果只写入仓库已忽略的本地 `apps/web/artifacts/`，禁止上传会话凭证、原始学生资料或未审核评语。没有使用正常链路 HTTP Mock，运行测试时不应触发 Qwen。

## 尚未解决的产品契约缺口

**特别重要：当前「主动学情发现」(`discoveries/query`) 与「保存本次分析」(`analysis-results`) 并非同一发现对象。**

服务端保存报告仍重新计算旧版评分洞察候选，而不是保存 #55 新的 Discovery ID。联合前端可以在同一教师会话中先探索发现、再保存分析并核验旧版候选，但 **不能宣称新发现已经随报告保存、被教师确认或导出**。该功能需要后端设计可信 Discovery 快照→AnalysisResult 的映射、对应权限/版本/证据契约，并经后端审核。为保持本轮前端-only、零 schema 变更，不伪造前端本地映射，也不假称产品最终闭环全部完成。

此外，正式 Teacher API 的输入目前仍为既有五维 Excel；逐题 ZIP/HTML 与远程 Qwen 是合成/受控技术原型，不能直接显示为生产教师功能。

## 当前验收状态

此文件记录**待复核的集成代码与可执行测试**，不是浏览器执行报告。原始 #40 / #55 分别有各自的 Chromium 实测结果，但不能替代本分支联合后的实际运行。应以本集成 head 上的 CI 与全新 Chromium 实测截图/Trace 为准，执行失败不得标记通过。
