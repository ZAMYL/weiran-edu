# B-02-S1 资料入库：结构快照 + 教师确认

本切片在 009 的 `assessment_*` 正式测评层之前加一层可确认的资料记录。
上传只负责“识别”，确认只能由教师显式发出；服务端**不保存任何原始文件字节**。

## 可信链条

教师身份 + 确认时间 + `sha256` 内容绑定 + 结构快照。

- 内容绑定：`material_records.sha256` 就是上传字节的摘要，`UNIQUE(sha256, owner_teacher_id)`
  保证每位教师对同一份字节只一条记录；重复上传只刷新结构快照，不改写 `source_id`。
- 结构快照：`plan_json` 只存表/列引用、表头、行数、候选角色与候选字段含义，不存任何单元格值。
- 确认审计：`material_confirmations` 只追加。同一位置（角色按表，字段按列）重复提交相同值幂等，
  提交不同值返回 `409 CONFIRMATION_CONFLICT`——想改主意就必须以新的文件版本重新上传。
- 作用域：上传时 `data_scope_id` 为空，确认时必须 `require_scope(importing=True)`；
  之后同一资料不能改挂其他教学空间（`409 MATERIAL_SCOPE_CONFLICT`）。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/edu/materials` | 有界多文件上传，返回识别结果并写入结构快照 |
| POST | `/api/v1/edu/materials/confirm` | 教师确认角色/字段含义，唯一的“已确认”来源 |
| GET | `/api/v1/edu/materials` | 本人资料加同空间共享资料，`offset`/`limit`（上限 50）/`status`（`active`\|`revoked`） |
| GET | `/api/v1/edu/materials/{source_id}` | 只回读库里已有的快照与确认，不重新解析 |

`MaterialRecordView.confirmations` 逐条给出审计信息（`confirmation_id`、`confirmed_by`、
`confirmed_at`、`basis`），`confirmed` 仍是同一批决策的精简视图，两者顺序一致。

同一份字节在同一请求里出现多次时，库中只登记首发那条记录，响应里所有副本都报告首发
`source_id` 与首发的表/列 ref，因此任一副本都能确认与回读；文件名不同但字节相同的两份
文件会被视为同一份资料。

回读视图把确认叠加到识别结果上：对应角色/字段状态变为 `confirmed`，
`confirmed_role:` / `confirmed_field:` 类缺口从 `missing` 中移除后重算 `available`，
`pending` 只保留尚未确认的位置。

## 加固语义（016）

- **快照指纹**：`material_records.plan_sha256` 记录结构快照摘要，回读时以
  `MaterialRecordView.plan_sha256` 暴露。`confirm` 可回带同一指纹，不一致返回
  `409 MATERIAL_PLAN_STALE`，避免按旧视图把决策落到新快照上。
- **快照一变，旧确认整体失效**：同一份字节重传且识别结构不同时，`material_confirmations.stale`
  置 1 —— 历史行一行不删（审计仍在），但读取、重放与冲突判断都不再把它们当作现行结论。
- **重传即复活**：同一份字节重新上传会把 `revoked` 记录恢复为 `active`，教师不必另建资料。
- **同空间共享读**：资料一旦绑定 `data_scope_id`，该教学空间的其他教师可读（`GET`/列表），
  但确认仍只有上传者能做，非上传者返回 `403 MATERIAL_OWNER_REQUIRED`；空间外教师仍是
  `404 MATERIAL_NOT_FOUND`。

## 代价（已明确接受）

- 服务端不能再出示原件，也不能事后从字节重抽字段；
- 事后验证靠重新上传同一份字节并比对 `sha256`，不同字节永远不会继承已有确认；
- 原始字节的去向仍由教师的本地文件承担，平台只证明“谁在何时确认了何种结构”。

## 迁移

`015_material_intake.sql`：`material_records`、`material_confirmations`、
`assessment_assets.material_id` 可空桥列；不改动已发布的 001–014。

`016_material_confirmation_epoch.sql`：加 `material_records.plan_sha256`、
`material_confirmations.stale`，以及 `data_scope_id` 的部分索引；只做加法，不重建表。
