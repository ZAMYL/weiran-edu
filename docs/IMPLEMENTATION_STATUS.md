# 实现状态与历史草案差异

核对日期：2026-10-25。用于 PR 合并审核及前端后续联调。本文描述仓库代码能力，不表示服务已部署或浏览器整条流程已验收。

## 已实现与尚未交付

| 范围 | 状态与入口 |
| --- | --- |
| 前端三页、图表与抽屉 | 设计 Demo 已完成，仍使用浏览器内合成记录和本地批次 |
| Excel 解析、预览与质量报告 | 固定双工作表模板、0.01 精度、独立分页、全量质量统计；见 [EXCEL_PARSER.md](EXCEL_PARSER.md) |
| 数据库、导入确认与批次修订 | 仓库外 SQLite、校验和迁移、原子确认、防重/重放、不可变历史；见 [DATABASE_BATCH_AUTH.md](DATABASE_BATCH_AUTH.md) |
| 教师权限 | 管理员预置教师、来源成员和限时可撤销随机凭证，逐请求核验；学校 SSO/公开注册未实现 |
| 统计、群体与评分证据 | 全批五维统计、二维矩阵、1–2 维 AND 筛选、匿名学生、同快照评分及依据来源；见 [接口说明](STATISTICS_GROUP_EVIDENCE_API.md) |
| 多规模内核验证 | 合成输入覆盖 1–2000 人及 401 个合法分值；见 [V1_ANALYSIS_ACCEPTANCE.md](V1_ANALYSIS_ACCEPTANCE.md)，不代表导入/AI/并发容量 |
| 内部候选、评语复核和 AI | 已有内核与合成边界；正式批次洞察查询及逐指标参与记录 HTTP 回查未交付 |
| 原文展示 | 非空评语与评分依据原文暂不展示；未实现原文审核/发布流程 |
| 浏览器真实接口接入 | 尚未完成，需要前后端共同联调与整条流程验收 |
| 导出、真实教师试用、生产部署 | 尚未完成；不因后端测试通过而自动视为完成 |

配置 `WEIRAN_EDU_DATABASE`、执行管理员初始化并启动服务后，`/docs` 与 `/openapi.json` 提供现行路由和 DTO。未配置数据库时只注册基础路由，不隐式创建数据库。前端应消费运行时契约；`docs/contracts/edu-api.v0.1.openapi.json` 是保留的历史设计与合成 Mock，已明确标记，不能直接当成当前客户端契约。

## 联调必须识别的差异

| 历史草案或设计意图 | 当前实际行为 |
| --- | --- |
| 仅健康检查、无迁移或教师授权 | 已有 SQL 迁移和受控业务 API；凭证由管理员 CLI 签发，最长 168 小时，非学校 SSO |
| 全部设计操作均可调用 | 实际另有 `/me` 和 `/scopes`；`insights/query` 与 `insights/{candidate_id}/records/query` 未注册 |
| 部分草案问题码与快照码 | 使用解析器实际问题码；快照冲突是 `409 SNAPSHOT_MISMATCH`，不是草案的 `SNAPSHOT_STALE` |
| GroupResult.items 为较完整的 StudentProfile | 实际为匿名 `student_id/display_label/scores`；快照在结果容器内，详情按需请求学生证据 API |
| 依据正文必返、评语已有展示投影 | `criteria_text=null, criteria_status=withheld`；评语为 `comment=null`，区分 empty/withheld，不把扣留当作没有内容 |
| 预览评分计数可能理解为合法评分数 | `parsed_evaluation_count` 包括检测到的评分行及非法行；完整学生计数与错误/警告计数分别返回 |
| 导入版本/问题名称完全沿用设计示例 | 以服务端返回的版本、稳定行/问题 ID 和运行时 Schema 为准；确认使用最新 preview_version |
| 快照涉及未来可变的原文审核投影 | 当前评分、依据及来源事实不可变，修订产生新批次和新快照；未提供原文展示状态编辑接口 |
| 示例学生和内部引用可直接作为正式标识 | 只使用 API 返回的稳定匿名学生及评分证据 ID；内部 RecordRef/EvidenceRef 和预览 ID 不授予正式读取权 |

统计使用完整批次或完整匹配群体，分页不改变分母。矩阵为全批次，群体统计没有矩阵。Decimal 阈值在原始 JSON 上保留精度，数据库整数转 Fraction 后计算；`exact.denominator` 是有理数分母，不是样本数。证据与依据来源可回到原 Excel 的工作表和单元格/范围，但普通 API 不返回原始身份或文件下载。

## 审核与后续验收边界

本次联合本地后端回归：675 项测试通过，Ruff 与格式检查通过，使用合成 Excel 和临时数据库。GitHub CI 的结果须以各 PR 当前 head 和最终 main 的实际运行分别核验；这不是前端真实数据联调、真实教师试用或生产验收。

下一阶段再准备联调服务、教师测试凭证和合成 Excel，由前端接入上传 → 预览/质量报告 → 确认入库 → 批次切换 → 统计 → 群体筛选 → 学生证据。还需验证加载、空群体、withheld、权限撤销、快照冲突与请求失败状态。

保留的 [冻结规则](BACKEND_CONTRACT_V0.1.md) 是用户确认的后端行为基线；[API](API_CONTRACT_V0.1.md)、[物理模型](DATA_MODEL_V0.1.md)、[Demo 对齐](FRONTEND_API_ALIGNMENT_V0.1.md)、[引擎交接](BACKEND_ANALYSIS_HANDOFF_V0.1.md) 是对应时期的设计背景。完整 PRD v0.2 与真实数据保留、加密/备份、部署保护、原文审核和外部模型授权仍需单独落实。
