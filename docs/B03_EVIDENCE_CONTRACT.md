# B-03 只读证据契约（交接 DS-03 / C-01）

适用：`feat/b03-evidence-provenance`，基线 `a0d1422`（PR #105）。
读者：DS-03 特征分析、C-01 Agent。**只读**，不要直接写这些表，也不要绕过服务层拼 SQL 发到外部模型。

## 1. 数据来源与边界

- 证据来自真实上传的答题卡 ZIP，经 `parse_answersheet` 解析后**逐字段落库**（迁移 009）。
- 姓名、考号、代码原文按 V1.4 原样保存、原样展示；这些 DTO 含个人信息与代码原文，**不得直接出境**。发给远端模型前必须走 `docs/DECISIONS_V14.md` 的白名单与别名机制。
- 权限：仅测评拥有者可读；他人与跨 scope 一律 `404 RESOURCE_NOT_FOUND`。

## 2. 状态分类（统计的唯一口径）

`status_class` / `verdict_class` 在入库时由 `classify_status()` 冻结，读取侧不要再对文本猜一次。

| 类别 | 来源文本 | 计通过 | 计失败 |
| --- | --- | --- | --- |
| `PASS` | `答案正确` | 是 | 否 |
| `FAIL` | `答案错误`、`运行时错误`、`运行超时`、`运行错误`、`内存超限`、`段错误`、`非零返回`、`编译错误`、`格式错误` | 否 | 是 |
| `PARTIAL` | `部分正确` | 否 | 否（单列） |
| `NOT_ATTEMPTED` | `未作答` | 否 | 否（单列） |
| `UNKNOWN` | 空文本、不在已知集合的文本、`status_known = 0` | 否 | 否（单列） |

历史行（009 之前入库）的 `status_class` / `verdict_class` 为 `NULL`：读取侧用同一张映射表兜底，判不出来的一律 `UNKNOWN`，**绝不计入失败**。

## 3. 统计字段与分母

`GET /api/v1/edu/assessments/{id}/patterns` 的每个题目：

| 字段 | 含义 |
| --- | --- |
| `observation_count` | 原始测试点记录总数 |
| `pass_count` / `fail_count` / `partial_count` / `not_attempted_count` / `unknown_count` | 五个互斥桶，合计等于 `observation_count` |
| `effective_count` | `pass + fail + partial`，即"有明确结论"的记录数 |
| `failure_count` | 明确失败的记录数 |
| `failure_rate` | `failure_count / effective_count`；`effective_count = 0` 时为 `null`，**不是 0** |
| `coverage` | `effective_count / observation_count` |
| `unknown_rate` | `unknown_count / observation_count` |
| `worst_test_points` | 只统计 `FAIL`，未知状态不会进入失败榜 |
| `score_rate_available` | 同题满分冲突时为 `false`，此时 `average_score` 为 `null` |

`/problems/{label}/students` 的 `failed_test_points` 与 `test_index` 过滤同样只认 `FAIL`。

## 4. 复核状态（人数不可静默确认）

`GET /assessments/{id}` 新增：

- `review_state`：`confirmed` / `needs_review`
- `review_reasons`：原因码列表，可能为 `MATERIAL_ROLE_UNCONFIRMED`、`PARTICIPANT_SERIES_MIXED`、`PROBLEM_COUNT_MISMATCH`、`PROBLEM_TOTAL_SCORE_CONFLICT`
- `counts_provisional`：`needs_review` 时为 `true`——此时 `student_count` 等计数是**暂定值**，不得当作已确认结论展示或写入报告

`GET /assessments/{id}/dimensions` 同样返回 `review_state`。报告导出在有疑义时首行标注"待人工确认"。

## 5. 来源定位

`SourceLocator`：`asset_id`、`asset_sha256`、`entry`（包内路径）、`source_index`（题块序号）、测试点另有 `source_row`。

- `assessment_assets` 记录每个上传包的 `entry`（文件名）、`kind`、`sha256`、`byte_size`、`source_index`。
- `asset_id` 为 `null` 表示无法唯一绑到某个包（例如多包同名路径），此时会写 `SOURCE_ENTRY_AMBIGUOUS` 诊断；**不要**据此编造来源。
- `GET /assessments/{id}/students/{pk}?asset_sha256=<64位>`：摘要与本批资产不一致时返回 `409 SOURCE_CHANGED`，用于"来源变了就别再解读旧证据"。

## 6. 耗时与原始文本

| 字段 | 含义 |
| --- | --- |
| `duration_ms` | 取整后的展示值（历史兼容字段） |
| `duration_us` | 精确微秒；`0.5 ms → 500`。历史行为 `null`，回退用 `duration_ms` |
| `duration_text` / `memory_text` | 源文件原文，保留单位与小数位 |

## 7. 其他新增字段

- 提交：`code_state`（`PRESENT` / `UNANSWERED` / `MISSING`）、`code_truncated`、`compiler_output`、`compiler_output_truncated`。`UNANSWERED` 与 `MISSING` 的 `code` 都是空串，**必须靠 `code_state` 区分**。
- 参与者：`started_at`、`finished_at`、`duration_minutes`、`total_score`、`score`。
- 概览：`assets`、`materials`（角色 + 判定依据 + `needs_confirmation`）、`diagnostics`（含 `ENTRY_SKIPPED` 排除记录）。

## 8. 错误码

| 场景 | 状态码 | code |
| --- | --- | --- |
| 测评不存在 / 无权访问 | 404 | `RESOURCE_NOT_FOUND` |
| 来源摘要与该学生证据的实际来源包不一致 | 409 | `SOURCE_CHANGED` |
| 来源快照与当前资产集合不一致 | 409 | `SOURCE_CHANGED` |
| 原件缺失 / 已撤销 / 未配置原件存储 | 409 | `ORIGINAL_UNAVAILABLE` |
| 原件内容与登记哈希不一致（被改动） | 409 | `SOURCE_CHANGED` |
| 同一哈希下已存在不同内容（不覆盖） | 409 | `ORIGINAL_CONTENT_CONFLICT` |
| 上传字节与声明摘要不一致 | 422 | `ORIGINAL_DIGEST_MISMATCH` |
| 测试点找不到对应提交（整批回滚） | 422 | `ANSWERSHEET_INCONSISTENT` |

## 9. B-03B：原件、快照与可信回读（C-01 接入点）

- `AssessmentOverview.source_snapshot_id`：64 位十六进制，绑定测评 ID 与"资产集合 + 各自 SHA-256"，与写入顺序无关；重启后不变，来源变化即变化。
- `AssetInfo.storage_state`：`stored`（原件在受控目录可回读）/ `absent`（历史批次未存原件）/ `revoked`（来源已撤销）。
- `AssessmentReader.original(principal, assessment_id, asset_id, *, expected_sha256=None) -> OriginalEvidence`：**服务端内部接口**，先校验测评归属与资产归属，再校验 `storage_state`，最后逐次回读并核对文件 SHA-256；返回 `payload / sha256 / entry / byte_size / asset_id / source_snapshot_id`。没有公开的原件下载 HTTP 接口。
- `AssessmentReader.student(..., asset_sha256=..., source_snapshot_id=...)`：摘要必须命中"该学生提交实际绑定的那个包"，同批次里 A 包摘要不能证明 B 包证据；快照不符合同样 409。
- 原件目录由 `Settings.originals_path`（环境变量 `WEIRAN_EDU_ORIGINALS`）指定，默认数据库同级 `originals/`，一律在仓库之外；目录 0700、文件 0600，按 `sha[:2]/sha[2:4]/sha.zip` 内容寻址，不静默覆盖。
- C-01 的 EvidenceProvider 建议：先取 `overview.source_snapshot_id`，用 `original()` 拿原件，再按 `source.entry` + `source_index`（题块序）/ `source_row`（表行序）定位代码与测试点；把 `source_snapshot_id` 与 `asset_sha256` 一并写进结论，便于复算。

## 10. 不在本契约内

B-02 表格关联、DS-03 的特征提取与 `code_features` 表（预留 011）、C-01 的 Agent 调用与 `findings` 表（预留 010）都不在本次交付范围。
