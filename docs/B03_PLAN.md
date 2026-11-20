# B-03 可信证据持久化与查询｜实施方案

- 负责人：彭仲楷 ｜ 关联：Issue #103、PR #105（B-01）、B-03 任务书
- 基线：`a0d1422`（已用 `git ls-remote` 核对：`refs/pull/105/head` 与 `refs/heads/feat/generic-extraction` 同一 SHA）
- 分支策略：从 `a0d1422` 切 `feat/b03-evidence-provenance`，**堆叠 Draft PR，base 设为 `feat/generic-extraction`**，只显示 B-03 增量；不碰 PR #105、不碰 main
- 边界：不重写解析器、不做 B-02 表格关联、不改 DS-03 特征、不改 C-01 Agent、不改林明前端与 Live Worker

---

## 0. 现状核查（方案的事实依据）

| 事实 | 位置 | 结论 |
| --- | --- | --- |
| 解析器已产出完整字段，但服务层几乎全丢 | `importing/edu/answersheet.py:84`（`SubmissionRecord` 有 `code_state`/`compiler_output`/`code_truncated`/`source_index`/`source_entry`）、`:133`（`ObservationRecord` 有 `status_known`/`duration_text`/`memory_text`/`source_row`）；`edu/assessments.py:609`（只写 10 列） | 字段在内存里存在，落库时被削平 |
| `materials`/`skipped`/`diagnostics`/`order_basis` 完全不落库 | `importing/edu/answersheet.py:152`（`MaterialRecord`）、`:165`（`order_basis`）；`edu/assessments.py` 全文无 `materials` 引用 | 角色判定与排除原因不可查询 |
| 小数耗时被取整 | `importing/edu/answersheet.py:1129`（`_duration_ms` 返回 `float`）、`edu/assessments.py:653`（`int(round(...))` 后写 `INTEGER` 列） | `0.5 ms` 入库变 `0`，`duration_text`（原始单位与小数位）丢失 |
| 状态未知被当成失败 | `edu/assessments.py:514`：`if row["status"] == "答案正确": continue`，其余一律计入 `failures` | 空值、未识别状态、`未作答` 全部计入失败，`failure_rate` 分母含未知 |
| 观测找不到提交就跳过 | `edu/assessments.py:653`：`if submission_id is None: continue` | 与提交侧"整批 422 回滚"口径不一致 |
| 链路 B 没有真实资产 | `006` 的 `source_assets.import_id NOT NULL REFERENCES imports`；`edu/assessments.py:98` 只存合并摘要 `file_sha256` | 无法生成真实 `EvidenceRef`，来源不可回查 |
| 重复上传判定依赖合并摘要 | `routers/edu.py:377`（`sha256(sorted(digests) 拼接)`） | 与单包摘要对不上，单包级去重与来源绑定都缺 |
| 同题满分无冲突检测 | `problems.total_score` 来自首次解析；`submissions.score_total_units` 来自各文件 `x/y` | 不同文件满分不一致时，得分率仍会被算出来 |
| 人数口径不区分"待确认" | `assessments.student_count` 直接写解析结果 | 角色冲突/考号前缀混合时仍发布为已确认 |
| 五维与个人证据的接口已存在 | `GET /assessments/{id}/students/{pk}`、`/patterns` | 查询骨架可复用，不需要新造一套 |

---

## 1. 迁移设计：`009_evidence_provenance.sql`

原则：**只做加法**，不重建表、不改 006/007/008、不删列，历史行靠默认值与 `NULL` 兼容。

### 1.1 新增表（3 张）

```sql
-- 包级资产：来源绑定的最小单位，一条包一个 asset
CREATE TABLE assessment_assets (
    asset_id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL REFERENCES assessments(assessment_id),
    entry TEXT NOT NULL CHECK (length(entry) BETWEEN 1 AND 512),
    kind TEXT NOT NULL CHECK (length(kind) BETWEEN 1 AND 32),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    source_index INTEGER NOT NULL CHECK (source_index >= 0),
    created_at TEXT NOT NULL,
    UNIQUE (assessment_id, entry)
) STRICT;

-- 材料角色与判定依据（含待确认）
CREATE TABLE assessment_materials (
    material_id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL REFERENCES assessments(assessment_id),
    entry TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('STUDENT_PAPER','STANDARD_ANSWER','BLANK_PAPER','TEST_DATA','PROBLEM_STATEMENT','UNKNOWN')),
    basis TEXT NOT NULL,
    signals TEXT NOT NULL DEFAULT '',
    needs_confirmation INTEGER NOT NULL DEFAULT 0 CHECK (needs_confirmation IN (0,1)),
    asset_id TEXT REFERENCES assessment_assets(asset_id),
    UNIQUE (assessment_id, entry)
) STRICT;

-- 解析诊断与排除记录（skipped 也走这张表，用 code 区分）
CREATE TABLE assessment_diagnostics (
    diagnostic_id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL REFERENCES assessments(assessment_id),
    code TEXT NOT NULL CHECK (length(code) BETWEEN 1 AND 64),
    entry TEXT,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;

CREATE INDEX assets_assessment ON assessment_assets (assessment_id);
CREATE INDEX diagnostics_assessment ON assessment_diagnostics (assessment_id, code);
```

### 1.2 现有表增列

```sql
-- assessments：题序依据 + 复核状态（人数不得"静默确认"）
ALTER TABLE assessments ADD COLUMN order_basis TEXT NOT NULL DEFAULT 'NATURAL_SORT';
ALTER TABLE assessments ADD COLUMN review_state TEXT NOT NULL DEFAULT 'confirmed'
    CHECK (review_state IN ('confirmed','needs_review'));
ALTER TABLE assessments ADD COLUMN review_reasons TEXT NOT NULL DEFAULT '';   -- JSON 数组

-- assessment_participants：表头级的每人事实（原来只存了姓名与考号）
ALTER TABLE assessment_participants ADD COLUMN total_score INTEGER;
ALTER TABLE assessment_participants ADD COLUMN score INTEGER;
ALTER TABLE assessment_participants ADD COLUMN started_at TEXT;
ALTER TABLE assessment_participants ADD COLUMN finished_at TEXT;
ALTER TABLE assessment_participants ADD COLUMN duration_minutes INTEGER;
ALTER TABLE assessment_participants ADD COLUMN source_asset_id TEXT REFERENCES assessment_assets(asset_id);

-- submissions：代码三态、编译输出、来源定位
ALTER TABLE submissions ADD COLUMN code_state TEXT NOT NULL DEFAULT 'PRESENT'
    CHECK (code_state IN ('PRESENT','UNANSWERED','MISSING'));
ALTER TABLE submissions ADD COLUMN code_truncated INTEGER NOT NULL DEFAULT 0 CHECK (code_truncated IN (0,1));
ALTER TABLE submissions ADD COLUMN compiler_output TEXT;
ALTER TABLE submissions ADD COLUMN compiler_output_truncated INTEGER NOT NULL DEFAULT 0
    CHECK (compiler_output_truncated IN (0,1));
ALTER TABLE submissions ADD COLUMN verdict_class TEXT
    CHECK (verdict_class IN ('PASS','FAIL','PARTIAL','NOT_ATTEMPTED','UNKNOWN'));
ALTER TABLE submissions ADD COLUMN source_asset_id TEXT REFERENCES assessment_assets(asset_id);
ALTER TABLE submissions ADD COLUMN source_entry TEXT;
ALTER TABLE submissions ADD COLUMN source_index INTEGER;

-- test_observations：状态冻结、精确耗时、原始文本
ALTER TABLE test_observations ADD COLUMN status_known INTEGER NOT NULL DEFAULT 1 CHECK (status_known IN (0,1));
ALTER TABLE test_observations ADD COLUMN status_class TEXT
    CHECK (status_class IN ('PASS','FAIL','PARTIAL','NOT_ATTEMPTED','UNKNOWN'));
ALTER TABLE test_observations ADD COLUMN duration_us INTEGER CHECK (duration_us >= 0);
ALTER TABLE test_observations ADD COLUMN duration_text TEXT NOT NULL DEFAULT '';
ALTER TABLE test_observations ADD COLUMN memory_text TEXT NOT NULL DEFAULT '';
ALTER TABLE test_observations ADD COLUMN source_row INTEGER;
```

**为什么不改 `duration_ms` 的类型**：`INTEGER → REAL` 在 SQLite 必须重建表（drop+rename+拷贝），而迁移执行器逐语句执行、无重建先例，风险高于收益。改为新增 `duration_us`（精确微秒）+ `duration_text`（原样文本），`duration_ms` 继续作为取整展示值；历史行 `duration_us IS NULL`，读取侧回退到 `duration_ms`。

**历史行兼容**：`status_class`/`verdict_class` 对旧行为 `NULL`，读取侧 `NULL → UNKNOWN`，统计里计入"未知"而不是"失败"——这正好是 B-03 要求的方向，且不改历史数据。

---

## 2. 服务层改造：`edu/assessments.py`

改动集中在 `create_answersheet` 一个事务内，新增 5 个写入步骤，全部在既有事务里，**任一步失败整批回滚**。

1. **资产表**：对每个上传包算一次 `sha256`（`routers/edu.py:370` 已有单包摘要，提到服务层），写 `assessment_assets`，并把包内每个 `material.entry` 关联到它的 `asset_id`。
   - 保留 `assessments.file_sha256`（合并摘要）不动，避免影响既有去重与测试；单包摘要进 `assessment_assets.sha256`。
2. **材料表**：`evidence.materials` 全量写入，带 `category/basis/signals/needs_confirmation`。
3. **诊断表**：`evidence.diagnostics + evidence.skipped` 全量写入（`skipped` 用 `SKIPPED_<reason>` 作为 code）。
4. **题序依据**：`evidence.order_basis` 写 `assessments.order_basis`。
5. **提交/观测补字段**：`code_state`、截断标记、`compiler_output`、`source_asset_id/entry/index`；观测写 `status_known`、`status_class`、`duration_us`（`round(ms*1000)`）、`duration_text`、`memory_text`、`source_row`。

同时修 3 个正确性缺陷：

- **观测引用缺失**：`_insert_observations` 的 `continue` 改为抛 `APIError(422, "ANSWERSHEET_INCONSISTENT")`，与提交侧一致。
- **同题满分冲突**：写库前按题聚合 `score_total`；不一致则 `problems.total_score = NULL` + 写诊断 `PROBLEM_TOTAL_SCORE_CONFLICT{label, values}`，并在读取侧让得分率不可用。
- **待确认状态**：`needs_confirmation` 材料、`PARTICIPANT_SERIES_MIXED`、`PROBLEM_COUNT_MISMATCH`、满分冲突任命中 → `review_state='needs_review'` + `review_reasons`（JSON 里的 code 列表）。

---

## 3. 统计可信性修复：`_pattern()` / `_dimension_from_problems()`

### 3.1 状态分类（解析期冻结，读取期不猜）

| 原始状态 | `status_class` | 计不计"通过" | 计不计"失败" |
| --- | --- | --- | --- |
| `答案正确` | `PASS` | 是 | 否 |
| `部分正确` | `PARTIAL` | 否 | 否（单列部分桶） |
| `答案错误`/`运行时错误`/`运行超时`/`运行错误`/`内存超限`/`段错误`/`非零返回`/`编译错误`/`格式错误` | `FAIL` | 否 | 是 |
| `未作答` | `NOT_ATTEMPTED` | 否 | 否（单列未作答桶） |
| 空值 / 不在已知集合（`status_known = 0`） | `UNKNOWN` | 否 | 否（单列未知桶） |

分类表放在 `importing/edu/answersheet.py`（单一真源），`edu/assessments.py` 只读 `status_class`。

### 3.2 DTO 与分母

`ProblemPattern` 扩展为（保持旧字段名不变，只增不改，前端不受影响）：

| 字段 | 含义 |
| --- | --- |
| `pass_count` / `fail_count` / `partial_count` / `not_attempted_count` / `unknown_count` | 五个桶 |
| `effective_count` | `pass+fail+partial`，即"有明确结论的有效测试点" |
| `observation_count` | 原始观测总数（含未知） |
| `failure_rate` | `fail_count / effective_count`；`effective_count=0` 时 `null`（原来给 `0.0`，会被读成"零失败"） |
| `unknown_rate` | `unknown_count / observation_count`，单独暴露 |
| `coverage` | `effective_count / observation_count` |
| `score_rate_available` | 同题满分冲突时为 `false` |
| `excluded` | `{code, count}` 列表，来自诊断表 |

`full_pass_count` 口径改为"满分 **且** `verdict_class` 为 PASS 或 `score_units == score_total_units`（旧行回退）"，避免把未知状态算进满分。

### 3.3 五维视图

`_dimension_from_problems` 只聚合 `verdict_class IN ('PASS','FAIL')` 的确定性得分，`UNKNOWN/PARTIAL/NOT_ATTEMPTED` 计入 `unscored` 并在 `DimensionView.note` 里给数量；`review_state='needs_review'` 时 `dimensions` 整体返回 `state='needs_review'`。

### 3.4 人数口径

`GET /assessments/{id}` 增加 `review_state`、`review_reasons`、`counts_provisional`；当 `needs_review` 时 `student_count` 照实返回但标 `provisional: true`，报告导出页首行打印"待人工确认（原因：…）"。

---

## 4. 查询闭环（复用现有 API，只扩字段）

| 步骤 | 复用/新增 | 验收点 |
| --- | --- | --- |
| 上传 ZIP | 既有 `POST /assessments` | 201 + 摘要 |
| 字段落库 | 009 + 服务层 | 逐字段与解析器输出对账 |
| 关库重开 | 新建 `Database` 实例读同一文件 | 全部字段仍可读 |
| 查题目/参与者 | 既有 `/patterns`、`/students` | 姓名考号原样，`review_state` 可见 |
| 查代码/编译/测试点 | 扩张 `GET /students/{pk}` 的 `SubmissionEvidence`/`Observation` | `code_state`、`compiler_output`、`duration_us`、`duration_text`、`status_known`、`status_class`、`source_locator` |
| 回查来源 | `GET /assessments/{id}` 增加 `assets`/`materials`/`diagnostics` 三段（不返回文件字节） | 能定位到 `entry` + `index` + `sha256` |
| 权限反例 | 既有 owner 校验 | 他人 404、跨 scope 404 |
| 来源变化拒绝 | 新增校验：请求带 `asset_sha256` 与库内不一致 → 409 `SOURCE_CHANGED` | 反例测试 |

> 不加新路由；"来源变化拒绝"落在既有 `GET /students/{pk}?asset_sha256=` 的可选参数上，不引入重复 API。若评审认为需要独立端点，再单开一条。

---

## 5. 测试矩阵

| 类型 | 文件 | 覆盖 |
| --- | --- | --- |
| 迁移 | `tests/storage/test_migration_009.py` | 旧库（007/008 数据）升级后：新列默认值正确、`status_class IS NULL`、`duration_us IS NULL`；重复执行幂等；校验和不符报 `MIGRATION_CHECKSUM_MISMATCH` |
| 字段对账 | `tests/edu/test_assessment_provenance.py` | 解析器输出 vs 库内逐字段断言（代码、`code_state`、截断、编译输出、`source_*`、耗时、`status_*`、材料、诊断、`order_basis`） |
| 重启回读 | 同上 | 关连接→新 `Database`→同 SQL 查询结果一致（含中文原文） |
| 统计正确性 | `tests/edu/test_pattern_statistics.py` | 1 通过 + 1 未知 → `failure_rate=0`、`unknown_rate=0.5`；`未作答` 不计失败；`部分正确` 单列；同题满分冲突 → `score_rate_available=false`；无有效观测 → `failure_rate=null` |
| 小数耗时 | `tests/importing/test_answersheet_precision.py` | `0.5 ms` → `duration_us=500`、`duration_ms=1`、`duration_text="0.5 ms"` |
| 人力资源冲突 | `tests/edu/test_review_state.py` | `PARTICIPANT_SERIES_MIXED`/角色待确认 → `needs_review` + reasons；报告首行提示 |
| 权限反例 | `tests/edu/test_assessment_api.py`（扩展） | 他人 404、跨 scope 404、`asset_sha256` 不符 409 |
| 旧批次兼容 | 既有 `tests/edu/test_*` 全跑 | 001–008 数据不改、B-01 既有 845 项不回归 |
| 合成样本 | `tests/importing/conftest.py` 扩展 | 不同人数/题数/未知状态/角色冲突/截断代码/小数毫秒各一套 |

真实资料只在本地受控核对，**不上传仓库**；仓库里只放合成样本与其生成器。

---

## 6. 实施顺序与提交拆分（6 个 commit，一个 PR）

| # | commit | 内容 | 完成判据 |
| --- | --- | --- | --- |
| 1 | `feat(storage): add 009 evidence provenance migration` | 009 + 迁移测试 | 旧库升级、新库建表、幂等 |
| 2 | `feat(edu): persist answersheet provenance and code state` | 服务层写全字段 + 资产/材料/诊断 + 观测不一致报 422 | 对账测试绿 |
| 3 | `fix(edu): classify test status instead of assuming failure` | 状态分类表 + `_pattern` 分母/桶/`null` + 五维聚合 | 统计测试绿 |
| 4 | `feat(edu): expose provenance and review state on reads` | DTO 扩展 + 概览/个人视图 + `asset_sha256` 校验 | HTTP 回读通过 |
| 5 | `test(edu): restart, permission and legacy compatibility` | 重启回读、权限反例、旧批次兼容 | 全量 `pytest tests/edu tests/importing tests/storage` |
| 6 | `docs(b03): evidence contract for DS-03 and C-01` | 只读证据契约（字段、口径、错误码、未知状态语义） | 交接文档可独立阅读 |

分支：`feat/b03-evidence-provenance` ← `a0d1422`；PR base `feat/generic-extraction`，**Draft**。

---

## 7. 交付物清单（对照 B-03 第 7 节）

- [ ] Draft PR 链接、HEAD SHA、base 分支名
- [ ] 迁移清单：009 新增 3 表 / 增列 6+6+6+8，逐字段说明与默认值
- [ ] HTTP 实测记录：上传 → 关闭进程 → 重启 → 查询（含响应片段）
- [ ] 来源核验：`entry + index + sha256` 定位；`asset_sha256` 变化被拒的反例
- [ ] 权限反例：他人 / 跨 scope 的 404 响应
- [ ] 未知状态统计修复前后对照表（同一合成样本，旧分母 vs 新分母）
- [ ] 旧批次兼容测试结果
- [ ] `pytest`、`ruff check`、GitHub CI 绿
- [ ] `docs/B03_EVIDENCE_CONTRACT.md`（给 DS-03 / C-01 的只读契约）

---

## 8. 风险与需确认（3 项）

1. **状态语义分组**：`部分正确` 我按"部分桶、不计通过也不计失败"处理。若产品要求"部分正确算未通过但不算失败点"，只需改分类表一行。
2. **待确认是否可人工确认**：当前 `review_state` 只读。若需要在网页上点"确认无误"，要加 `PUT /assessments/{id}/review`（1 个端点 + 审计字段），**默认不做**，除非 DS-03 需要。
3. **考号复用风险（超出 B-03 但影响统计可信）**：`_resolve_students` 按"同 scope + 同考号"复用 `student_pk`；若同一 scope 下多门课都用 `S001` 这类座位号，会把不同学生合并。建议后续在 `assessment_participants` 的复用键里加入课程/系列维度——本轮只记录，不改。
