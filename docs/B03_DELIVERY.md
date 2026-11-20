# B-03 交付说明：可信证据持久化与查询

- 分支：`feat/b03-evidence-provenance`（从 `a0d1422` 切出，PR #105 的 HEAD）
- PR：[#120](https://github.com/ZAMYL/weiran-edu/pull/120)（**Draft**），base `feat/generic-extraction`，head `19b2b62d4882022c615c1e1df400c288f90a6a49`；不改 PR #105、不改 main
- 负责人：彭仲楷 ｜ 日期：2026-11-19
- 边界遵守：未重写解析器、未做 B-02 表格关联、未改 DS-03 / C-01 / 前端 / Live Worker；真实学生资料未上传仓库，测试全部为合成样本

## 1. 改了什么

| 方向 | 文件 | 内容 |
| --- | --- | --- |
| 迁移 | `app/storage/migrations/009_evidence_provenance.sql` | 新增 3 表 + 5 张现有表增列，只做加法 |
| 状态口径 | `app/importing/edu/answersheet.py` | 新增 `classify_status()` 与五个状态类别，统计口径单一真源 |
| 入库 | `app/edu/assessments.py` | 解析器产出全字段落库；资产/材料/诊断/题序依据入库；观测引用不一致整批 422 回滚 |
| 统计 | 同上 `_pattern` / `_dimension_from_problems` / `problem_students` | 明确通过/失败才计数，分母改为有效测试点，满分冲突不给得分率 |
| 读取 | 同上 + `app/edu/assessment_models.py` + `app/routers/edu.py` | 概览带来源与复核状态；个人证据带代码三态/编译输出/精确耗时/来源定位；`asset_sha256` 校验 |
| 报告 | `app/reporting/edu/assessment_report.py` | 待确认首行提示、失败率可空、未知状态列、满分冲突标记 |

## 2. 数据库变更（迁移 009）

新增表：

- `assessment_assets(asset_id, assessment_id, entry, kind, sha256, byte_size, source_index, created_at)`，`UNIQUE(assessment_id, entry)`
- `assessment_materials(material_id, assessment_id, entry, category, basis, signals, needs_confirmation, asset_id)`
- `assessment_diagnostics(diagnostic_id, assessment_id, code, entry, message, created_at)`，被排除的 entry 以 `ENTRY_SKIPPED` 记录

增列：

| 表 | 新列 |
| --- | --- |
| `assessments` | `order_basis`、`review_state`、`review_reasons` |
| `assessment_participants` | `total_score_units`、`score_units`、`started_at`、`finished_at`、`duration_minutes`、`source_asset_id` |
| `problems` | `total_score_conflict` |
| `submissions` | `code_state`、`code_truncated`、`compiler_output`、`compiler_output_truncated`、`verdict_class`、`source_asset_id`、`source_entry`、`source_index` |
| `test_observations` | `status_known`、`status_class`、`duration_us`、`duration_text`、`memory_text`、`source_row` |

兼容策略：不重建表、不改 006/007/008；历史行 `status_class` / `verdict_class` / `duration_us` 为 `NULL`，读取侧按原文重新归类，归不了类的一律 `UNKNOWN`。

`duration_ms` 保留为取整展示值（四舍五入），精确值在 `duration_us`，原文在 `duration_text`——避免 `INTEGER → REAL` 必需的整表重建。

## 3. 实测证据（HTTP + 关库重开）

合成样本：1 名学生、1 道题、3 条测试点（`答案正确` / `答案错误` / `等待评测`，耗时含 `0.5 ms`）。

1. `POST /assessments` → `201`，`{"student_count": 1, "problem_count": 1, "observation_count": 3, "duplicate": false}`
2. 新建 `Database` 实例与全新应用（等价进程重启）后 `GET /assessments/{id}` → `200`，计数一致；`assets` 含真实包摘要与字节数，`order_basis = "SOURCE"`
3. `GET /students` → 姓名考号原样（`周同学 / S001`），`started_at / finished_at` 与源文件一致
4. `GET /students/{pk}` → 代码原文一致；观测含 `status_class`、`duration_us = 500`、`duration_text = "0.5 ms"`、`source_row`；`source.asset_sha256` 与上传包一致
5. `GET /patterns` → `pass_count=1, fail_count=1, unknown_count=1, effective_count=2, failure_rate=0.5, unknown_rate=0.3333`
6. 权限反例：他人 `GET /assessments/{id}` 与 `/students/{pk}` 均 `404`
7. 来源反例：`?asset_sha256=000…0` → `409 SOURCE_CHANGED`；正确摘要 → `200`

### 统计修复前后对照（同一份样本）

| 指标 | 修复前 | 修复后 |
| --- | --- | --- |
| 失败计数 | `答案错误` + `等待评测` = 2 | 只有 `答案错误` = 1 |
| 失败率分母 | 全部 3 条 | 有明确结论的 2 条 |
| `failure_rate` | `0.6667` | `0.5` |
| 未知状态 | 不可见，混进失败 | `unknown_count=1`、`unknown_rate=0.3333` 单列 |
| 无有效结论时 | `0.0`（被读成零失败） | `null` |

## 4. 测试与检查

- 新增测试：`tests/storage/test_migration_009.py`（旧库升级/约束/幂等）、`tests/edu/test_assessment_provenance.py`（字段对账/重启回读/权限与来源反例/整批回滚/小数毫秒）、`tests/edu/test_pattern_statistics.py`（五桶口径/满分冲突/待确认/下钻过滤）、`tests/importing/test_status_classification.py`（分类与耗时解析）、合成样本 `tests/edu/answersheet_samples.py`
- 既有断言更新：`test_assessment_api.py` 的观测字典补上新字段；`test_database.py` 的迁移计数 8 → 9；`test_reviewed_results.py` 与 `test_snapshot_integrity.py` 的旧库模拟同步拆除 009 新表、迁移计数改为 9

### 验证记录（2026-11-19，Linux，Python 3.12）

| 检查 | 结果 |
| --- | --- |
| `ruff check app tests` | All checks passed |
| `pytest tests/edu tests/importing tests/storage` | **873 passed**，389.30s，0 failed |
| 其中 B-03 定向（来源/统计/迁移/既有评估与适配器） | 299 passed，17.61s |
| 旧库升级回归（`test_reviewed_results`、`test_snapshot_integrity`） | 50 passed；两处旧库模拟已同步拆除 009 新表、迁移计数改为 9 |
| HTTP + 关库重开实测 | 见第 3 节，全部符合预期 |
| GitHub Actions | **未触发**：`.github/workflows/ci.yml` 只在 `pull_request → main`、`push → main` 或手动 `workflow_dispatch` 时运行；本 PR base 是 `feat/generic-extraction`，因此 PR #120 的 check runs 为 0。需要 CI 证据时手动触发一次 `workflow_dispatch`，或等 #105 合入 main 后由上游 CI 覆盖 |

过程记录：第一轮全量为 871 passed / 2 failed，失败的是两个"模拟旧库"的测试——它们只拆掉 004–008 的表，没有拆 009 的新表；修复后第二轮全量 873 passed。

## 5. 交接

- DS-03 / C-01 的只读契约：`docs/B03_EVIDENCE_CONTRACT.md`
- 方案与决策过程：`docs/B03_PLAN.md`
- `code_features`（011）、`findings`（010）不在本次范围

## 6. 遗留与建议（不在本 PR）

1. `review_state` 目前只读；若要在网页上人工确认，需要 `PUT /assessments/{id}/review` 与审计字段。
2. `_resolve_students` 按"同 scope + 同考号"复用学生；同 scope 多门课复用 `S001` 这类座位号会串人，建议后续把课程/系列加入复用键。
3. `adapter.py` 仍只登记 `xlsx`，`/assessments` 走硬编码 `ZIP_HTML` 判定；适配器收敛建议单开 PR。
