# B-02-S2 通用资料 → 业务事实

B-02-S1 只做到“结构快照 + 教师确认”，资料本身没有变成任何可查询的事实。
本切片补的是最短的一段路：**教师确认过的列位置 + 同一份字节重传** → 写入 `material_facts`，
再由只读接口统计、检索。不伪造 `batches`，也不改 009 的 `assessment_*` 主结构。

## 为什么必须“重传同一份字节”

服务端从不保存原始文件字节（B-02-S1 已明确接受这条代价），库里只剩 `sha256` 与结构快照。
因此写入事实时，唯一还能证明“这是被确认过的那份表”的办法，就是让教师再传一次同样的字节：

- `sha256(重传字节) != material_records.sha256` → `409 MATERIAL_CONTENT_MISMATCH`，任何确认都不复用；
- 字节一致但结构快照被后来的同字节上传改过，`plan_sha256` 会变，按 `plan.locators` 取列时可能对不上，
  直接 `409 MATERIAL_STRUCTURE_CHANGED`；
- 资料未绑定教学空间 → `409 MATERIAL_SCOPE_REQUIRED`；已撤回 → `409 MATERIAL_REVOKED`；
  非上传者 → `403 MATERIAL_OWNER_REQUIRED`。

哈希校验在解析之前做一次（`check_bytes`），写事务里再做一次，两次之间不允许“先看后改”。

## 从哪一列取什么

只认教师确认过、且属于同一张表的一组列：

| 确认含义 | 落库字段 |
| --- | --- |
| `student_key`（必需） | `participant_key` |
| `problem_key` | `problem_key` |
| `score` / `verdict` | `score_raw`/`score_value`、`verdict`（任一有值即产出 `kind='score'` 事实） |
| `dimension` + `dimension_score` | `dimension`、`dimension_score_raw`/`dimension_score_value`（产出 `kind='dimension'`） |
| `feedback` | `feedback` |

一张表若没有确认 `student_key`，或一个“可记录的东西”（`score`/`verdict`/`dimension_score`）都没有，
就整体跳过——`409 MATERIAL_CONFIRMATION_REQUIRED`，不会写半张表。

行级规则：参与者单元格为空的行计入 `skipped_without_participant`，不产生事实；
`score_value` 只在纯数字文本上填，`"8.25分"` 这类保留原文、不猜数值。

## 身份关联

`identity_basis` 只有两种取值：

- `student_number`：参与者单元格恰好是 12 位学号，且该教学空间的 `student_identities`
  里存在同号学生 → 填 `student_pk`，读取时返回 `students.public_id`；
- `none`：其余全部保持独立记录（含 `contest-009`、`S-02` 这类非学号编号）。

不按行序、不按姓名、不按相似度拼接；同一份资料里两个人共用一个编号也只按编号归并。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/edu/materials/{source_id}/facts` | `multipart/form-data` 恰好一个文件；校验字节与确认后整份替换写入 |
| GET | `/api/v1/edu/materials/{source_id}/facts` | `offset`/`limit`（1–200）分页列出事实 |
| GET | `/api/v1/edu/materials/{source_id}/facts/statistics` | 按题目 / 维度聚合 |

写入是**整份替换**：先 `DELETE FROM material_facts WHERE material_id=?` 再重写，
所以同一份资料重传多少次都只保留最后一次的事实；`facts` 表按
`UNIQUE(material_id, source_table, source_row, kind)` 兜底。

读取与统计对“上传者本人 + 同空间教师”可见，其他教师仍是 `404`；`rule_version` 固定 `material_facts.v1`。

### 统计口径

- `fact_count` / `participant_count` / `linked_participant_count`：事实条数、涉及参与者数、其中已关联学生数；
- `problems`：只统计 `kind='score'` 的事实，按 `problem_key` 分组（无题号归入 `null` 组），
  给 `fact_count`、`scored_count`、均值/最小值/最大值与 `verdicts` 分布；
- `dimensions`：只统计 `kind='dimension'` 的事实，按维度名分组给同样的四件套；
- 没有数值时输出字符串 `暂无有效数据`，不输出 `0`，避免把“缺数据”误读成“零分”；
- 数值按 `Decimal` 汇总、`ROUND_HALF_UP` 保留两位。

## 支持与不支持

- 支持：XLSX / CSV / HTML（含表格的 HTML 页面），只要是现有提取能力能识别出表的资料；
  教师按列位置命名含义即可，不要求表头恰好叫“得分”。
- 不支持：ZIP 答题卡（仍走原正式导入主线，`422 MATERIAL_FACTS_ARCHIVE_UNSUPPORTED`）；
  代码、语言、测试点等 ZIP 专属字段不映射为事实；任意维度资料不会进入 5 维
  `batches`/`evaluations`，只在 `material_facts` 里统计与评价；
  未确认、异字节、已撤回的资料一律不出事实。

## 迁移

`017_material_facts.sql`：新增 `material_facts`（STRICT）与两个索引（`material_id` + 行序、
`data_scope_id` + 参与者）。只做加法，不改 001–016 的任何表。
