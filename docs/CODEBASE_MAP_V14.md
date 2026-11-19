# 仓库代码梳理：V1.4 后端现状、链路与不一致清单

- 审阅对象：`feat/generic-extraction` @ `a0d1422`（相对 `main` `d2ccd7f` 领先 21 个提交、32 个文件、+4963/−36）
- 审阅方式：逐模块静态阅读 + 云主机实跑测试（本机沙箱无法落盘 SQLite，见第 8 节）
- 结论口径：只描述**代码实际行为**，不把 PRD 的计划当成已实现
- 日期：2026-11-19

---

## 1. 一句话结论

答题卡证据链路（识别 → 解析 → 入库 → 群体模式 → 个人下钻 → 报告）在后端已跑通且有 845 项测试保护，**但它是与旧五维 Excel 链路完全并行的第二条路**：数据模型、接口命名、AI 任务入口、报告内容、前端消费都只覆盖了链路的一半。真正卡住 V1.4 验收的不是"能不能解析"，而是**证据在入库时被削平、来源链断开、AI 面仍是 503、前端未接**。

---

## 2. 仓库形态

| 项 | 现状 |
| --- | --- |
| 主仓库目录 | `D:\AI\传智杯\weiran-edu-git`（bare 主体）+ 14 个 worktree |
| 当前开发树 | `weiran-edu-final` → 分支 `feat/generic-extraction`，HEAD `a0d1422`，工作区干净 |
| 远端 | `git@github.com:ZAMYL/weiran-edu.git`；该分支已在远端 |
| 与 main 关系 | 未合并，领先 21 个提交（V1.4 全部新功能都在这一支） |
| 文档分支 | `docs/v14-baseline`（比 main 多 4 个文档提交：V1.4 基线标签 3 个 + `DECISIONS_V14.md`） |
| 风险 | 所有 V1.4 功能都在一条未合并分支上，佘俊泽做集成决策前无法在 main 演示 |

其他 13 个 worktree（`weiran-edu-ux`、`weiran-live-wt`、`weiran-edu-p0`…）是历史评审/修复现场，多数为 detached HEAD，**不要当作基线**。

---

## 3. 后端目录地图

`apps/api/app/` 共 17 个包，职责如下（只列本轮相关）：

| 目录 | 职责 | 本轮是否改动 |
| --- | --- | --- |
| `core/` | 配置、统一错误、精确 JSON、中间件、请求追踪 | 否 |
| `storage/` | SQLite 连接与事务、校验和迁移、就绪检查 | 是（新增 006–008 迁移） |
| `routers/` | `edu.py` 主路由（约 40 个端点）、`ai.py` AI 面、`edu_*` 三组辅助路由 | 是（`edu.py` +308 行） |
| `edu/` | 业务服务与读取：批次、复核、摘要、诊断、洞察、分析、**assessments 新链路** | 是（+assessments、+assessment_models） |
| `importing/edu/` | 旧双 Sheet 解析（`parser.py`）+ 新识别/抽取/答题卡解析（`detect`、`extract`、`adapter`、`answersheet`） | 是（+3 个新文件、`answersheet` 1146 行） |
| `ai/edu/` | Qwen Agent、工具、校验、discovery 子模块 | 否 |
| `analysis/`、`reporting/` | 统计分析与报告渲染 | 是（+`assessment_report.py`） |
| `schemas/` | 统一 `ErrorResponse` | 否 |

---

## 4. 两条并行的业务链路

这是理解本仓库的关键：**V1.4 没有改造旧链路，而是新开了一条**。

### 链路 A：旧五维 Excel 批次（main 上已有）

```
POST /imports/preview (xlsx, 必须 Sheet1/Sheet2)
  → detect_asset → adapter_for(kind) 必须非空
  → parse_isolated（子进程隔离解析）
  → imports 表 + source_assets 记录
  → PATCH /imports/{id} 改名 → POST /imports/{id}/commit
  → batches / evaluations / rubric_snapshots
  → 统计、证据、五维、复核、洞察、报告、导出
```

### 链路 B：V1.4 答题卡证据（本次新增）

```
POST /assessments (ZIP 内含多个 HTML 答卷，单次最多 20 个包)
  → detect_asset → 必须 kind == ZIP_HTML（绕过 adapter 注册表）
  → parse_answersheet / merge_answersheets（纯函数，无 DB）
  → AnswerSheetEvidence（学生/题目/提交/测试点/资料角色/诊断）
  → AssessmentService.create_answersheet（单事务写入）
  → assessments / assessment_participants / problems / submissions / test_observations
  → GET /assessments/{id}              概览
  → GET /assessments/{id}/students     参与者（原样姓名+考号）
  → GET /assessments/{id}/students/{pk} 个人代码与逐测试点证据
  → GET /assessments/{id}/patterns     按题错误模式（确定性统计）
  → GET /assessments/{id}/problems/{label}/students 从错误模式下钻到人
  → PUT /assessments/{id}/problem-dimensions 配置题目→五维
  → GET /assessments/{id}/dimensions   五维视图（无映射时明确"无数据"）
  → GET /assessments/{id}/report       Markdown / CSV 导出
```

**两条链路唯一的交点**是 `students` 表（都有 `data_scope_id`），此外没有任何共享：不共享 `batches`、不共享 `imports`、不共享导入预览/确认流程、不共享统计模块、不共享 AI 任务入口。

---

## 5. 数据模型现状（001–008）

| 迁移 | 表 | 归属链路 |
| --- | --- | --- |
| 001 | `teachers`、`teacher_tokens`、`data_scopes`、`scope_members`、`students`、`student_identities`、`rubric_snapshots`、`rubric_criteria`、`imports`、`import_payloads`、`batches`、`evaluations`… | A |
| 002–004 | 不可变事实、学生身份约束、复核结果 | A |
| 005 | `batch_archives`（教师归档） | A |
| 006 | `source_assets`（引用 `imports(import_id)`） | A |
| 007 | `assessments`、`assessment_participants`、`problems`、`submissions`、`test_observations` | B |
| 008 | `problem_dimensions`（题目→五维） | B |

与 V1.4 文档 12 节逻辑模型对照：

| 文档要求 | 代码现状 | 差距 |
| --- | --- | --- |
| SourceAsset（asset_id, file_name, content_type, source_path, file_hash） | 有表，但**只由链路 A 写入**；链路 B 的 `assessments` 没有 `asset_id`/`import_id` | 链路 B 文件来源无法回溯到具体包 |
| Assessment / Problem | 有 | 一致 |
| Participant（display_name, student_no） | 有（`external_key` 存考号、`display_name` 存姓名，两者都来自文件原值） | 一致 |
| Submission（language, code_ref） | 有 `language`、`code_text` | 存代码正文而非引用；缺 `code_state`、`compiler_output` |
| TestObservation（status, duration, memory） | 有 | 一致（但见第 7 节 D 项） |
| CodeFeature（feature_type, value, location） | **无** | 未建表（009 候选） |
| Finding / EvidenceRef（support_refs, model_origin） | **无** | 未建表（010 候选） |

---

## 6. 解析层现状

| 文件 | 行数 | 实际被谁调用 | 状态 |
| --- | --- | --- | --- |
| `detect.py` | 114 | `routers/edu.py`（预览与答题卡两处） | 生效，按魔数与 ZIP 内部结构识别，不信扩展名 |
| `adapter.py` | 23 | 仅 `routers/edu.py:318` | **只登记 `xlsx → dual_sheet_xlsx`**；测试还断言 `supported_kinds() == (xlsx,)` |
| `extract.py` | 265 | **仅测试**（`tests/importing/test_extract.py`） | 支持 xlsx/csv/txt/html/zip_html/docx/pdf 抽取，但**没有任何生产路由使用** |
| `answersheet.py` | 1146 | `routers/edu.py:370` | 生效，生产主力 |

`answersheet.py` 的设计要点（这部分质量最高，值得保留）：

- 自建 DOM（`_TreeBuilder`）+ 选择器常量，不引入 bs4；换模板只改选择器。
- 角色判定三分：内容证据（`答卷类型`/考生字段）、路径线索（`正式答卷/验题` 等）、冲突检测（`_role_conflicts`）；证据不足一律 `needs_confirmation`，不静默计入。
- 题目顺序诚实：只有所有文件题序完全一致才标 `SOURCE`，否则用自然排序标 `NATURAL_SORT`。
- 测试表列含义：表头完整才 `thead` 可信；否则靠内容特征（序号/状态词/ms/KB）三重命中，未命中即"不生成测试点"并给诊断。
- 代码状态三态：`PRESENT / UNANSWERED / MISSING`，占位符（`无`/`-`/`—`/`/`）不算代码。
- 截断可见：代码 200k、编译日志 200k、单题 500 行、单文件 8MB，超限一律写诊断。

---

## 7. 不一致与缺口清单（按影响排序）

> 编号用于后续 Issue / PR 引用。

### P0-1｜证据入库被削平
- 位置：`importing/edu/answersheet.py:85`（`SubmissionRecord` 有 `code_state`、`code_truncated`、`compiler_output`、`compiler_output_truncated`、`source_entry`、`source_index`）、`edu/assessments.py:627`（INSERT 只写 10 个字段）、`007_evidence_model.sql:38`
- 现象：解析器判定的"未作答 vs 缺代码"入库后统一变成 `code_text=""`；编译日志、截断标记、来源文件、块序号全部丢弃；`materials`/`skipped`/`diagnostics`/`order_basis` 一个字段都没落库。
- 影响：界面与报告无法回答"这条数据为什么被忽略"；`EvidenceRef.locator` 无法定位；重复上传后无法复核当初的判定。
- 建议：009 迁移给 `submissions` 增列（`code_state`、`compiler_output`、`code_truncated`、`source_asset_id`、`source_entry`、`source_index`），并新增 `assessment_materials` 存角色与依据、`assessment_diagnostics` 存诊断（可 JSON）。这是**最高优先级**，因为它同时修好"证据可追溯"和"来源链断开"。

### P0-2｜来源链断开
- 位置：`006` 的 `source_assets.import_id NOT NULL REFERENCES imports(import_id)`、`edu/assessments.py:98`（`assessments` 只存 `file_sha256`）
- 现象：链路 B 不写 `source_assets`；`assessments.file_sha256` 存的是**多文件合并摘要**（`routers/edu.py:377`，`sha256(sorted(digests) 拼接)`），与任何一个真实文件都对不上。
- 影响：V1.4 12.2 的 `EvidenceRef{asset_id, problem_id, submission_id, locator}` 无法满足；审计时回不到原始包。
- 建议：与 P0-1 合并做 009：把 `source_assets` 的 `import_id` 改为可空，或新增 `assessment_assets(assessment_id, asset_id)`；每个包单独记一条资产（文件名、类型、SHA-256、字节数）。

### P0-3｜观测记录静默丢弃
- 位置：`edu/assessments.py:653`（`if submission_id is None: continue`）
- 现象：`test_observations` 找不到对应提交时**跳过**，不报错、不回滚；而同一文件里 `_insert_submissions` 对 KeyError 的处理是整批 422 回滚。
- 影响：与"绝不静默"的项目原则直接冲突，会产出"人数对但测试点少"的批次。
- 建议：改为 `raise APIError(422, "ANSWERSHEET_INCONSISTENT")`，与提交侧保持一致；补一条用例。

### P0-4｜适配器注册表名存实亡
- 位置：`importing/edu/adapter.py:13`、`routers/edu.py:318`（预览用注册表）、`routers/edu.py:364`（答题卡直接判 `ZIP_HTML`）
- 现象：注册表只有 `xlsx`，而生产上真正跑通的第二种格式（`zip_html`）**没有登记**，靠路由里硬编码绕过去；`extract_document` 支持 9 种类型却无人调用。
- 影响：V1.4 07 节"新增格式只补适配器"的架构承诺没有落地；后面加 CSV/PDF 时会继续分裂成第三个判断点。
- 建议：`ADAPTERS` 登记 `zip_html → answersheet_v1`；`/assessments` 改为"取适配器 → 按适配器名分发"；`extract.py` 明确二选一：接入表格类资料（FR-09），或标注"实验层，未接入"并从 `supported_kinds` 中隔离。

### P0-5｜报告不含五维、不含个人
- 位置：`reporting/edu/assessment_report.py:10`、`routers/edu.py:465`
- 现象：导出只有"题目错误模式 + 结论分布"，五维视图在 `/dimensions` 里但没进报告；个人证据完全不进报告。
- 影响：18.1 签收项"报告包含五维与错误模式、可回看依据"不达标。
- 建议：`render_markdown(overview, patterns, dimensions, evidence)` 增参；个人证据以"按题列出 N 名学生代码摘录"或"附个人视图链接"体现，避免把全部代码塞进报告。

### P0-6｜AI 面与证据模型没接上
- 位置：`routers/ai.py:45`（全部端点 `_unavailable()`）、`routers/ai.py:26`（任务前缀是 `/batches/{batch_id}/...`）
- 现象：① 所有 AI 端点固定 503 `AI_LIVE_NOT_ENABLED`；② AI 任务挂在 `batch_id` 上，而链路 B 的实体叫 `assessment_id`，两者无法绑定。
- 影响：V1.4 M1-A"真实 Qwen 在网页跑通"结构上无入口；即使接上 Qwen，也要先决定 ID 语义。
- 建议：在 `assessments` 上新增 `POST/GET /assessments/{id}/ai-investigation-tasks`（复用现有任务服务，不改前缀语义），或让 `assessment` 直接复用 `batch_id` 命名空间。此项需佘俊泽定，属于跨人决策。

### P1-1｜权限口径与既有模型不一致
- 位置：`edu/assessments.py:186`（`_owned_assessment` 只允许 `owner_teacher_id`）
- 现象：链路 A 支持 `batch_access(permission='read')` 给协作者只读；链路 B 一律 404。
- 影响：教师之间无法共享/复核同一批测评；与 15 节"三人验收"实际使用冲突。
- 建议：新增 `assessment_access` 或复用 `batch_access` 语义；默认仍是 owner-only，但允许显式授权只读。

### P1-2｜五维口径是"推算"而非评分
- 位置：`edu/assessments.py:350`（`_dimension_from_problems`，`state="derived_from_problem_scores"`）
- 现象：五维由题目得分按映射聚合，`note` 已声明"不是官方五维评分"；未作答不计入分母。
- 影响：不违反"不编造"，但前端若当官方分显示会误导。
- 建议：DTO 保留 `state` 与 `note`，林明侧必须显示口径标签；后续有真实五维评分时按 `state` 分支。

### P1-3｜多文件上传的两处口径不齐
- 位置：`routers/edu.py:208`（`MAX_ASSESSMENT_FILES = 20`）、`routers/edu.py:262`（总字节 ≤ 单文件上限）、`routers/edu.py:389`（`data=parts[0][0]` 只作摘要 fallback）
- 现象：20 个包共享一个 `compressed_bytes` 预算；`create_answersheet` 的 `data` 参数在多文件时是"第一个包"，语义含混。
- 影响：批量上传容易触发 413；后续加字段时 `max_fields=5` 也会卡住（`ASSESSMENT_FIELDS` 已占 5 个）。
- 建议：`data` 改为显式 `None`（调用方已给 `file_sha256`），`max_fields` 提到 8，总字节按"单包上限 × 允许包数"或独立配置。

### P2-1｜前端零接入
- 现象：`apps/web/src` 全仓检索 `assessments` 无任何引用；21 个 `features/live` 组件仍按旧批次 DTO 消费。
- 影响：后端跑通不等于产品可用；18.1 端到端验收无法开始。
- 建议：产出 `docs/ASSESSMENT_DTO_HANDOFF.md`（8 个端点的请求/响应示例、字段口径、`state/note` 展示要求、错误码），交给林明。

### P2-2｜文档与测试的工程债
- `docs/` 有 40+ 篇，命名混杂（`V1_FILE_INTAKE`、`V13_*`、`QWEN_*`、`v13-trusted-*` 小写）；无索引，新人无法判断哪篇还有效。
- 全量测试在云主机单次 >15 分钟未跑完；核心两套 `tests/importing + tests/edu` 需 371 秒。有规模/性能类用例拖慢全局。
- 建议：`docs/INDEX.md` 按"现行 / 已被 V1.4 取代 / 历史评审"三栏归类；给慢测试加 `@pytest.mark.slow` 并在 CI 分组。

### P2-3｜死代码与旧入口
- `parser.py`（双 Sheet）仍活跃且是链路 A 唯一入口，V1.4 14.1 要求"取消双 Sheet 强制"，但**这条没做**：`/imports/preview` 仍只接受 xlsx 且必须双 Sheet。
- `archive.py`、`preview.py`、`printer_settings.py` 属旧链路，保留无妨，但需在文档里标注"V1.4 不再扩展"。

---

## 8. 测试与工程状态（本次实测）

| 项 | 结果 | 说明 |
| --- | --- | --- |
| `tests/importing` + `tests/edu` | **845 passed，371 秒** | 云主机 Linux / Python 3.12 / uv 冻结依赖 |
| `ruff check app tests` | **All checks passed** | 项目规则 `E,F,I,UP,B`，line-length 100 |
| 全量套件 | 未完成 | 云主机单次尝试超 15 分钟未结束，需分批或加 marker |
| 本机（Windows 沙箱） | 不可用 | SQLite 写入不落盘（建库后 0 页、`sqlite_master` 为空），568 个 setup 错误全部由此产生，**不是代码缺陷** |
| 新增用例密度 | 答题卡链路 105 个用例（API 25、解析 44、识别/抽取 13、资产 2 等） | 覆盖重复包、跨包重复考号、列含义不明、编译日志在代码前、截断、下钻过滤、权限隔离 |

---

## 9. 建议的整理顺序

| 顺序 | 动作 | 迁移 | 依赖 |
| --- | --- | --- | --- |
| 1 | **009 证据定位与来源**：`submissions` 增列 + `assessment_assets` + `assessment_materials` + `assessment_diagnostics` | 009 | 无，纯后端 |
| 2 | 修 P0-3 静默丢弃；补 2 条用例 | — | 无 |
| 3 | 适配器收敛：登记 `zip_html`，路由改为按适配器分发；`extract` 定性 | — | 决定 1 |
| 4 | 报告补五维与个人入口 | — | 1、2 |
| 5 | AI 任务入口（assessment 口径）与 Live 接线 | 010 | 佘俊泽定 ID 语义 |
| 6 | 权限口径统一 + 前端 DTO 交接文档 | — | 林明确认字段 |
| 7 | `docs/INDEX.md`、慢测试 marker、旧链路标注 | — | 无 |

第 1 项会改变 `DECISIONS_V14.md` 里"009 = code_features"的预留——按实际依赖，**009 应先做来源与定位，code_features 顺延到 011**。这份文档与 `DECISIONS_V14.md` 冲突时，以本节的优先级为准，我随后会同步修订决策文档。

---

## 10. 未决问题（需要佘俊泽或林明确认）

1. AI 调查任务的 ID 语义：`assessment_id` 直接复用，还是另建映射表对齐 `batch_id`？（影响 010 与前端契约）
2. 同一批测评是否要给协作者只读权限？（影响 P1-1 是否本轮做）
3. 五维在界面上是否必须显示"推算"标签，还是等真实五维评分再上？（影响 DTO 与文案）
4. `extract.py` 定位：作为 FR-09 正式接入路径，还是保留为实验层？

---

## 11. 本次未做的事

- 未改任何生产代码，未新增迁移，未动 `apps/web`、`apps/api/app/ai`（他人负责区域）。
- 未提交分支、未开 PR（`feat/generic-extraction` 仍保持 `a0d1422`）。
- 全量测试与前端构建未验证（环境与时间限制）。
