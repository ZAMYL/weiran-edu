# 教师可读分析：接入与真实性边界

> ⚠️ 本文部分内容已被 V1.4 取代，以 docs/V1.4_BASELINE.md 为准。

基线：`15788491b536d408005030ddbed2cee2d1a9f6b2`。本轮只改变展示解释与提问入口，不增加分析算法、API 或数据库结构。

## 内容从哪里来

`teacher-analysis.ts` 的纯函数返回 `title / finding / numbers / whyItMatters / nextCheck / limitation / state / source`。

- `describeDiscovery(detail, items?)`：解释现有发现。群体比较直接使用已有分组条件、两组人数、有效评分人数、均分，以及同一分析范围的总体均分；不重算发现或差值。
- `describeDiscoverySummary(summary)`：没有详情时保留服务端标题，并明确尚未加载人数和评分；不从自由文本猜测数据。
- `describeItemStatistics(statistics, item, scope)`：项目 ID、名称、人数、量表和分布均来自参数，支持任意项目数量。已有分布的首尾分数及人数补充到均分旁；不会据此生成群体结论。`points / level` 仅分别显示为“分 / 级”，数值不变。
- `describeProfile(profile)`：仅解释已提供 `source_replay_verified=true` 的画像，保留 Fact ID；条件定义的评分不冒充另一项新发现。此函数不替代或增加来源/Fact 校验。
- `describeStudentScores(student, items, scope)`：只显示已有评分及 evidence_id，不补缺失分数、不判断能力。
- `teacherAnalysisOverview(ordered, 2 | 3)`：只分出摘要与余项，原数组与顺序不变。不要用它代替服务端排序或资格判断。

`source` 保存 batch/snapshot、原证据或 Fact ID 及取值字段。具体证据仍走现有评分依据接口；展示文字不产生权限。

## 已有页面的最小接线

1. `discoveryPresentation()` 已接 `describeDiscovery()`，保留原指标数值、比较方向与 `eligibleForFocus()`；详情说明有具体数字、整体均分掩盖的组间差别及核查动作。
2. `LiveDiscoveries.tsx` 仅移除单卡切换器、直接渲染既有 `focus`（最多 3 项）；`loadDetails / focus` 原候选、过滤、顺序、分页、查询次数不变，全部发现仍在下方。
3. 缺详情的列表项改用服务端原始标题，避免所有条目都被前端换成“查看两组学生的评分比较”。若服务端本身同名且尚未加载条件，仍可能同名；不通过补造条件或追加假结论强行区分，也没有新增整页详情重算请求。
4. `TeacherQuestionSuggestions` 是独立小组件；选择只填入输入框，仍由教师点击“开始分析”提交。现有面板只增加该组件及项目目录参数。
5. `LiveInsightsPage.tsx`、报告页、图表和评分依据组件均未修改。逐题统计/学生/画像的其他页面可按下述字段接入；本轮不声称已重做这些页面。

```tsx
const copy = describeItemStatistics(statistics, item, {
  batchId: batch.batch_id,
  snapshotId: batch.snapshot_id,
});
// 按结构呈现，避免把整段文字塞成新的术语说明。
<>
  <h3>{copy.title}</h3>
  <p>{copy.finding}</p>
  <p>{copy.numbers}</p>
  <p>{copy.whyItMatters}</p>
  <p>{copy.nextCheck}</p>
</>
```

传统 `statistics.dimensions` 可用 `{...dimension, item_id: dimension.dimension_code}` 接 `describeItemStatistics`；原 `item_statistics` 可直接传入，不强制映射为五维。评分依据仍使用原 ID，不解析生成文字反向生成查询。

## 提问范围

已核对 `TaskStore.create → 项目名称替换 → understand`。当前宿主为注册合成五项测评；快捷提问只提供：优先查看线索、两项评分对照、按评分分组比较、核查缺分。

`teacherQuestions(items)` 返回显示标签、后端已支持的完整 `question` 及用途说明。未知结构/逐题表返回空数组，不许借此承诺逐题 AI。未知输入不被改写或自动提交；`AI_GOAL_UNSUPPORTED` 保留 HTTP 422/原 code/request_id，只改善主文案，引导教师明确选择。

`teacherAiResult(task)` 提供与实际终态相符的通俗说明、`canStartAgain=false` 及“受控模拟分析；未调用真实 Qwen”。它是供其他前端接入的状态辅助；本轮未改变既有预算、campaign 或恢复逻辑。不能把模拟分析说成真实模型调用。

## 实测证据与复现

详见 [自动生成的实际例子](TEACHER_ANALYSIS_EXAMPLES.md)。

- 72 人和 96 人工作簿：真实解析、临时 SQLite 入库，运行原正式发现内核并使用现有 `project()` 和 `exact_wire()` 导出聚合结果。没有导出个人记录或数据库。
- 14 人/2 项与 27 人/7 项逐题数据：运行已有离线 canonical Kernel 的统计，保留项目名、量表、分布、有效和缺失人数、来源引用。它们不等于 release 新增了逐题 AI HTTP 功能。
- 所有例文由同一纯函数生成；实际数字不在产品代码中写死。测试检查输入不变、人数分母、正负方向、缺失均值、条件定义、同均分下不同分布、不同项目数量、exact 舍入、来源 ID 及终态。

```powershell
# 仓库根目录；使用已准备好的锁定 Python 环境
$env:PYTHONPATH='.;apps/api'
python apps/web/tests/make-teacher-analysis-examples.py apps/web/tests/fixtures/teacher-analysis.synthetic.json
python apps/web/tests/check-teacher-questions.py

cd apps/web
node scripts/generate-import-samples.mjs
$env:EDU_WRITE_TEACHER_EXAMPLES='1'
node node_modules/vitest/vitest.mjs run
node node_modules/eslint/bin/eslint.js . --max-warnings=0
node node_modules/next/dist/bin/next typegen
node node_modules/typescript/bin/tsc --noEmit
node node_modules/next/dist/bin/next build --webpack
```

本机 lockfile 与已安装候选完全一致，通过依赖目录链接复用；默认 Turbopack 不支持此链接位置，因此生产构建使用 Next 官方 webpack 参数。未改变 package.json/锁文件/Next 配置。

## 已知限制

- 当前仍是规则与受控模拟调查；没有启用真实 Qwen，也不证明模型新增了统计发现。
- 代表项来自既有分页与资格规则，最多 3 项；不声称全量最优、统计显著或因果解释。
- 短标题和完整解释使用项目实际名称；名称较长时仍需前端复核窄屏排版。未隐藏合成来源或必要的使用范围。
- 未给缺少量规/答题过程的数据编写教学原因、能力评价或改分建议。
- 浏览器业务/视觉回归未完成；已更新受影响 E2E 的文案和多卡断言，但不能把未执行脚本写成通过。未触碰当前演示服务。

## 本轮实际检查

- 全量前端单测：17 个测试文件、248 项通过。首次直接运行 Vitest 时，因新 worktree 尚未生成原有 XLSX 模板而失败 1 项；执行项目原定的 `generate-import-samples.mjs` 后全量通过。
- ESLint `--max-warnings=0`、`next typegen` 和 TypeScript `--noEmit` 通过。
- Prettier 全量只读检查 `--end-of-line auto` 通过，未修改全局格式配置。
- webpack 生产构建及 `tests/smoke.mjs` 通过；smoke 验证启动、页面路由、模板下载和 404，不代表真实浏览器业务闭环。
- Python 夹具生成器真实执行完成；两个新 Python 测试工具的 Ruff 与格式检查通过。
- 快捷提问契约检查：4 条建议经原后端解析器接受，5 条未知/不安全输入被拒绝。没有启用新的问法映射或调用模型。
- `apps/api/`、`prototypes/`、依赖与锁文件、CI 相对基线完全无改动；没有执行后端全量 pytest。
- Playwright 环境未配置可用本地浏览器，本轮没有浏览器业务截图或视觉通过声明。PR 保持待复核状态。
