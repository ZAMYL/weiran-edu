# V1.1 十三题切片进度

基线：V1 候选 f8e13f1。此分支实现独立的十三题切片，不扩大 V1 文件兼容声明；尚无前端页面接入验收。

已实现私有适配器 `app/importing/edu/questions.py`：复用现有有界 OOXML 检查，按真实单元格尺寸读取，保留数字词法、Fraction、原始题名、量表、题目类型和定位。缺分、非法数值、超范围和步长不合法分别记录，均不补零或参与有效评分。该协议仅服务完全合成的明确结构样例。

样例协议：两个工作表。Sheet1 第一行严格为 `edu-question-xlsx.v1, 题目名称, 最低分, 最高分, 步长, 单位`；随后依次十三行 Q01..Q13，范围和步长必须由文件提供数值。Sheet2 第一行为 `参评记录,Q01,...,Q13`；记录键为合成匿名 S 加4至8位数字。每条观测保留 Sheet2 单元格，项目定义保留 Sheet1 范围，整体绑定原文件 SHA256。此样例记录键规则不是现实身份匹配策略。

已接入既有 FastAPI 路径，无重复 API 或新认证：

| 路径 | 十三题响应 |
| --- | --- |
| POST /api/v1/edu/imports/preview | format=edu-question-xlsx.v1；import_id、preview_version、title、participant_count、observation_count、recognized_items、quality_issues |
| GET/PATCH /api/v1/edu/imports/{id} | 获取预览或更名；PATCH 仍需 preview_version，修改后旧版本拒绝 |
| GET /api/v1/edu/imports/{id}/quality-report | JSON 分类、数量和单元格定位；当前逐题不支持 Markdown 质量报告 |
| POST /api/v1/edu/imports/{id}/commit | 复用 CommitRequest/CommitResult，返回 batch_id/snapshot_id/result；有缺分/非法值需确认提醒 |
| GET /api/v1/edu/batches/{id}、/statistics | 每题原量表、完整分母、有效/缺失/非法数量、精确均值、依据引用及明确不支持的分析状态 |
| GET /api/v1/edu/batches/{id}/evidence/{evidence_id} | 当前授权和快照下的题目/参评记录/质量状态/精确评分及原文件摘要、Sheet2 单元格 |

五维响应原形保留，十三题由 format 区分。精确分子/分母复用 ExactJSONResponse 的无损协议；均值不先舍入。缺失和非法值存储但排除数值统计，零有效评分的均值为 null、状态 unavailable。题目不等于能力，不能跨量表直接合计或对比。

迁移 005_question_slice.sql 新增 question_imports、question_batches、question_items、question_observations、question_access；项目保存各自量表与来源范围，观测保存原词法、质量状态、精确分量与单元格；复用 teachers、scope_members 和现有 CLI 只读授权。确认后事实和原来源不可变。读取在同一授权事务内从原字节重建并对账全部项目/观测、快照及依据 ID；不从分页样本重算统计。既有五维表约束不改。由操作员使用原数据库迁移命令应用，HTTP 不自动迁移。

预览有效期1小时，确认校验版本和实时导入权限；重复文件（同范围及拥有者）返回已有批次。撤销只读授权或教学空间成员后立即拒绝查询；未知/跨批次依据404，错误快照409，完整性异常503。错误信息不回显源文本。

当前限制：逐题批次不进入五维群体/发现/保存/核验/报告流程；GET 新批次和 statistics 可直接获取逐题诊断统计。批次列表默认仍只返回旧五维，动态工作台通过 `include_dynamic=true` 显式请求混合列表并按format解码。逐题修订、学生列表及逐条证据分页未实现。V1 原有这些能力继续保留。不能调用五维引擎或宣称已有逐题 AI 发现。评分标准正文和答卷没有在本协议提供，评价复核明确标缺少前置资料。

已向林明 @AooMing 在 PR #68 技术评论同步最小契约；后端先完成，不等待前端实现。本分支不修改 apps/web，不调用模型、不读取教师资料、不自动合并或部署。

不支持任意 Excel、动态题数、八维模板、HTML/ZIP 历史资料、自动字段映射、AI 猜测量表或跨年身份匹配。

## 本次增量：对齐林明的逐项目契约

沿用同一#71、同一分支和迁移005，不重建系统、不更改原始五维表或任何已有教师资料。不假定参评者来自同一班或同一学校；本格式只保存同一授权测评内的匿名参评键，没有班级/学校推断或跨批次身份合并。

- `recognized_items[]` 保留原平铺字段，新增 `display_name`（原题名）和 `scale={minimum,maximum,step,unit}`。动态量表三个分量均为原十进制字符串；原五维继续原number契约，按format区分。
- 预览新增完整 `quality_counts`，区分valid/missing/invalid_numeric/out_of_range/off_step；原260条观测不会因缺分/非法值缩减。新增 `available_analyses` 字符串数组，有有效评分时含 `item_statistics`。
- 批次详情和显式混合列表包含成对的 `recognized_items` / `available_analyses`。混合列表有真实ACL、来源校验、全量计数和分页，不按当前学生页计算总数；未授权逐题批次不会出现在列表中。没有显式include_dynamic时旧列表响应不变。
- `/batches/{id}/statistics?snapshot_id=...` 增加 `item_statistics[]`，每项以 `item_id` 识别；可选 `item_id=Q01` 限定返回项目，未知题号404；不支持五维x/y参数。项目集合及分母来自正式完整快照，不来自分页数据。
- `item_statistics[]` 包括 `display_name/kind/scale`、denominator、valid_count、missing_count、invalid_count、quality_counts、statistics_status及mean/median/minimum/maximum。统计值沿用 `StatValue={value,exact,display}`；exact是精确分子/分母对，display只负责两位小数HALF_UP，value供图表近似展示，均不参与重新评分或内部比较。无有效分数时value/exact均null。兼容旧#71的 `items[]` 仍保留，其mean是原精确对或null。
- `distribution[]` 只含真实合法评分：`score`供图表显示，`exact`是权威精确分值，`count`为整数，`denominator`为该批次全部参评人数，`ratio`为同一StatValue结构。分布频数之和等于有效评分数，缺分/非法值不补零；不能跨题目量表合并比较。

林明需要在前端代理查询白名单加入 `include_dynamic`、`item_id`，并为动态format分支解码；本PR不修改UI。确认导入后使用返回的batch_id/snapshot_id直接读取本次逐题统计。[实际HTTP请求/响应与JUnit](evidence/v11-items-provider/README.md)已提供，不使用手造成功样例。16次真实TCP请求在代码提交af00cb0上执行，后续只加证据及文档；20人、13题、260观测及十三题均值/合法分布对账通过，正式模型用途拒绝。

本轮新专项141项通过，覆盖逐题XLSX、HTTP/SQLite、来源、Provider、五维上传与权限/统计兼容；不是重跑原1589项或本轮全量CI。历史全量和初次失败记录如下保留。Provider条件见 [正式服务端快照接线](V11_PROVIDER_HANDOFF.md)。

## 实际验证记录（2026-11-04）

- 组合全量：`uv run --locked --no-sync pytest -q -ra` → **1608 passed, 1 skipped**，437.65秒。唯一跳过仍为原 Windows 符号链接创建权限；1条原 Starlette 弃用 warning。包含旧五维统计、发现、权限、来源、保存核验、精确整数、报告及超时回归。
- 全量后只补强 A1 协议标记读取（正文含协议字样不抢占旧模板）、逐题固定错误码和相关边界测试；最终代码定向执行 `tests/edu/test_question_xlsx.py tests/edu/test_question_flow.py tests/edu/test_direct_upload.py` → **54 passed**，19.43秒。该结果不能表述为最终增补测试也已进入前述全量1608项。
- 最终 Ruff、format check 通过，210个Python文件。没有降低权限、统计或来源断言。
- 真实 TCP 测试使用 http.client → literal-loopback socket → Uvicorn/FastAPI → 临时SQLite；完成文件上传、确认、13题统计和依据查询，20人/260观测逐项均值11/4，依据Sheet2!B2。
- FastAPI/SQLite 回归另覆盖缺失/非法分排除和完整分母、零有效评分 unavailable/null、量表缺失拒绝且零写入、旧预览、重复导入、过期、跨批次依据、只读、撤权、成员重加不恢复旧授权、不可变事实以及强制损坏后的来源对账拒绝。
- 初次组合回归出现9处失败：6处为初始分派误拦五维修订，现已恢复旧分派；3处为迁移测试仍以4个迁移模拟旧库，已真实重建旧表及更新5个迁移断言，保留原事实与checksum验证。九处失败定向全部复验通过，之后全量1608项通过。未隐藏历史失败。

本机原始JUnit：`outputs/edu-v11-full.junit.xml`（初次失败）、`outputs/edu-v11-final.junit.xml`（组合全量）、`outputs/edu-v11-dispatch-final.junit.xml`（最终增补定向）。不提交临时SQLite、凭证或工作簿。

启动：在 `apps/api` 使用既有锁定依赖；为独立合成库配置 `WEIRAN_EDU_DATABASE`（仓库外绝对路径），执行 `uv run --locked python -m app.edu init` 应用5个迁移，沿用现有教师/空间管理命令，再以 `uv run --locked uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000` 启动。无需另建认证。测试可直接运行上述两个逐题测试文件；合成工作簿生成器位于 `tests/edu/test_question_xlsx.py`，不包含真实资料。
