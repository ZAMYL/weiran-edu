# 数据库、批次管理与教师权限

状态：Issue #14 的可运行试点后端实现，依赖 PR #12 的 Excel 解析与质量报告。现有分析引擎、AI、前端页面没有改动；本页描述已实现功能，旧协议草案不代表所有分析接口已经上线。

## 启动

需要 Python 3.12 和 uv，在 `apps/api` 目录执行。数据库必须使用**仓库目录之外的绝对路径**；服务端不自动创建教师、不自动迁移、不读取 `.env`。路径仅从显式参数或 `WEIRAN_EDU_DATABASE` 环境变量读取，不返回给客户端。

PowerShell 示例（教师与来源ID由命令返回，此处不预设真实账号）：

```powershell
uv sync --locked --group dev
$env:WEIRAN_EDU_DATABASE = 'D:\AI\传智杯\未然EDU\runtime\edu.sqlite3'
uv run --locked python -m app.edu init
$teacherId = (uv run --locked python -m app.edu teacher-create --subject 'pilot-teacher-01' | ConvertFrom-Json).teacher_id
$scopeId = (uv run --locked python -m app.edu scope-create --label '试点课程' | ConvertFrom-Json).data_scope_id
uv run --locked python -m app.edu member-set --teacher-id $teacherId --scope-id $scopeId --can-import
uv run --locked python -m app.edu token-issue --teacher-id $teacherId --hours 24
uv run --locked uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

`token-issue` 仅在这条显式管理命令中输出明文凭证。保存返回的 `access_token`，在本机 `/docs` 的 Authorize 中填写后，可上传Excel并调用业务API。凭证不放入Git、前端构建变量、URL或长期浏览器存储。此处的 `pilot-teacher-01` 为操作示例，不是已创建账号或默认密码。

未配置 `WEIRAN_EDU_DATABASE` 时只开放原有健康检查；配置数据库后注册EDU接口。未执行迁移、数据库不可用时返回安全的503，不创建空库假装成功。服务只监听本机的示例配置不能直接当作互联网部署；实际运行目录须由部署方配置服务账号访问控制及HTTPS。

这是管理员预置的试点凭证机制，不是学校SSO或公共注册/密码登录系统。凭证由32字节随机量生成，默认有效24小时，最长168小时，数据库只存SHA-256摘要。教师主体以 `(auth_issuer, auth_subject)` 唯一映射；未来接学校身份源时，先校验发行方与主体，再接入同一权限服务，不能信任前端声明的教师ID。

## 管理命令与权限

除 `init` 外，管理命令使用已初始化的数据库。它们仅供服务端操作人员执行，没有对应的公开权限管理或令牌签发API。

```powershell
uv run --locked python -m app.edu token-revoke --teacher-id $teacherId
uv run --locked python -m app.edu teacher-disable --teacher-id $teacherId
uv run --locked python -m app.edu teacher-enable --teacher-id $teacherId
uv run --locked python -m app.edu member-set --teacher-id $teacherId --scope-id $scopeId
uv run --locked python -m app.edu member-remove --teacher-id $teacherId --scope-id $scopeId
uv run --locked python -m app.edu batch-grant-read --teacher-id $readerTeacherId --batch-id $batchId
uv run --locked python -m app.edu batch-revoke-read --teacher-id $readerTeacherId --batch-id $batchId
uv run --locked python -m app.edu cleanup-expired
```

`member-set` 不带 `--can-import` 时保留成员资格但撤销导入权。移除来源成员时同步撤销该范围的显式共享，重新加入不会自动恢复旧共享。禁用教师会撤销现有凭证，重新启用后需要签发新凭证；`token-revoke` 撤销该教师全部现存凭证。凭证摘要也不作为客户端参数或公开ID返回。

| 操作 | 必须满足 |
| --- | --- |
| 读取本人身份、来源列表 | 有效未过期/未撤销凭证、教师活跃 |
| 创建或修改预览、首次确认 | 当前来源成员且 `can_import=1` |
| 读取暂存与质量报告 | 上传者本人、教师活跃、当前来源成员；其他教师即使被授予批次读取也不能查看 |
| 读取批次、学生、证据 | 教师活跃、当前来源成员、批次拥有者或显式只读授权 |
| 修订批次 | 批次拥有者、当前来源导入权、修订目标仍是链末端 |
| 管理教师/来源/共享授权 | 服务端操作人员执行CLI；不向教师开放 |

同一来源成员资格不等于能够读取所有批次。所有身份和资源权限均从数据库实时核验，不依赖客户端自报 `teacher_id`，不缓存授权结果。读取不存在、跨批次或无权访问的资源统一404；无有效身份401；无导入权403。同范围重复文件命中不可读批次时只返回通用409冲突，不泄露该批次ID或创建教师。

## 已实现API

除健康检查外，下列路径都需要 `Authorization: Bearer <access_token>`。数据响应包含服务端 `X-Request-ID` 和 `Cache-Control: no-store`；失败沿用统一错误信封，不回显输入、文件名、路径或内部异常。

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | `/api/v1/edu/me` | 当前教师不透明ID |
| GET | `/api/v1/edu/scopes` | 当前来源成员列表和导入能力 |
| POST | `/api/v1/edu/imports/preview` | 固定模板multipart上传、受控暂存、分页脱敏预览 |
| GET | `/api/v1/edu/imports/{import_id}` | 本人暂存回读、学生/行/问题独立分页 |
| PATCH | `/api/v1/edu/imports/{import_id}` | 按版本更新标题、课程、日期，不延长有效期 |
| GET | `/api/v1/edu/imports/{import_id}/quality-report` | 完整JSON报告；`format=markdown` 返回可读报告 |
| POST | `/api/v1/edu/imports/{import_id}/commit` | 警告确认、原子入库、重复关联及幂等重放 |
| GET | `/api/v1/edu/batches` | 可读批次列表，默认显示各修订链末端 |
| GET | `/api/v1/edu/batches/{batch_id}` | 批次元数据、数量和快照 |
| GET | `/api/v1/edu/batches/{batch_id}/students` | 匿名学生、固定五维评分和稳定证据ID |
| GET | `/api/v1/edu/batches/{batch_id}/students/{student_id}/evidence` | 本批学生五条证据 |
| GET | `/api/v1/edu/batches/{batch_id}/evidence/{evidence_id}` | 按批次限定的单条证据 |

实际请求与响应Schema见启动后的 `/openapi.json`、`/docs`。上传字段沿用草案：`file`、`data_scope_id`、`title` 必填；`course`、`assessment_date`、`revision_of_batch_id` 可选。拒绝未知或重复字段，不允许通过表单设置教师ID或修正评分。课程或日期未知时省略；PATCH允许显式置为null，标题不能为空。

预览回读分页：`student_offset/student_limit` 默认0/20，`row_offset/row_limit` 默认0/25，`issue_offset/issue_limit` 默认0/50，页大小1～100。预览中的 `parsed_evaluation_count` 沿用已实现解析器口径，统计检测到的评分行，包括非法行；完整学生只进入 `student_samples`，错误行仍在 `rows` 供定位。质量问题码沿用解析服务的实际问题码，旧草案中的部分命名不同，以运行时Schema和 `EXCEL_PARSER.md` 为准。

确认请求示例（版本必须来自服务端预览）：

```json
{"preview_version":"服务端返回的64位十六进制版本","acknowledge_warnings":true}
```

首次成功201，返回 `result=created`；同一文件或同一规范内容已入库且可访问时200、`duplicate`；同一预览再次确认200、`replayed`。三者都返回 `import_id`、`batch_id`。有警告而未显式确认返回409，字符串 `"true"` 和数字1不作为布尔值接受。错误版本409、过期410、无效预览409；不会创建半份批次。

批次列表参数：可选 `data_scope_id`，`include_revisions=false`，以及 `offset/limit`。列表使用 `created_at DESC, batch_id DESC` 稳定排序，总数为完整授权结果数。匿名学生按批内显示序排列，顺序不代表能力排名。学生列表支持 `snapshot_id` 校验；不匹配返回409，快照ID不代替权限。

## 存储、迁移和事务

使用Python标准库sqlite3，不依赖另一个数据库服务。迁移位于 `app/storage/migrations`：001建立教师/凭证、来源成员、暂存、身份映射、评分依据、批次、学生与证据；002阻止已确认事实及原始来源的UPDATE/DELETE。迁移记录版本、SHA-256校验和及UTC时间；重复执行不重复建表，已应用脚本被修改或出现未知版本时拒绝继续。

每个连接都启用外键，读取事务设置query_only，写入使用 `BEGIN IMMEDIATE` 和3秒busy timeout。表使用STRICT类型、定点评分整数100～500、同范围文件/规范内容唯一索引、维度唯一与范围/依据复合外键。SQL参数均绑定值，不拼接用户条件。当前批次不支持删除或编辑分数，修订通过新批次表达，旧评分、证据ID、原始来源和评价依据保持不变。

预览阶段只创建 `imports` 与 `import_payloads`；不会提前创建正式学生、身份或评分。暂存将原始xlsx保存在受控BLOB列，公开预览/报告使用单独白名单JSON。原始文件名不保存或返回。预览有效24小时；修改元数据会换版本但不延长有效期。

确认在写事务之外重新解析受控原文件，事务内再核验教师、来源、版本、过期、警告、身份冲突、文件及内容摘要，再执行防重与修订末端校验。同学号姓名冲突先于重复结果判断，避免内容摘要不包含姓名而掩盖身份变化。并发写入由SQLite事务和唯一约束共同限制；失败全部回滚，暂存仍可重试，不把数据库失败伪装成成功。

成功后，正式身份与对外匿名ID分离，按来源范围匹配学号；同范围跨批次匿名ID稳定，不同来源不自动合并。每条评分生成稳定证据ID、保留原分数词法、原始评语及B/C/D来源坐标。原始文件移动到不可变的 `batch_sources` 受控BLOB，暂存payload删除；重复导入不覆盖原批次标题、标准、评语或来源。

已确认预览保留非敏感元数据供幂等重放，不再返回学生样本、行、问题及质量报告。重放仍检查版本和批次读取权，但不会因为原24小时到期拒绝已确认结果；撤销来源成员资格或教师凭证后，既有ID与缓存都不能绕过权限。未确认暂存由显式 `cleanup-expired` 清理并标记expired，没有偷偷启动后台定时任务。

非空原始评语继续 `withheld`、空评语为 `empty`；普通API只返回 `comment=null`，不假装完成脱敏审核。身份、原始评语和原始文件的分表/BLOB保存不等于加密：本实现没有数据库加密、备份加密或已批准的长期保留方案。SQLite删除暂存也不等于擦除历史页与备份；运行目录、备份、账户访问控制由部署方按试点方案配置。

## 上传执行边界

先认证再读请求，实际流式读取限制为10MiB文件加64KiB表单开销，不信任Content-Length。最多一个文件、五个元数据字段，限制字段大小，拒绝未知字段与非xlsx媒体类型。保留解析器原有50MiB解压、ZIP节点/行列/文本限制和公式/外链/宏拒绝策略。

HTTP解析和确认重解析在spawn子进程中执行：15秒等待超时会终止/回收子进程，每个Web进程最多两个并行解析；忙或不可用返回503，非法文件422，过大413。SQLite写事务不包住解析等待。子进程通信仅传服务端产生的对象，不接受上传的pickle。代码未实现操作系统级进程内存硬上限，正式部署仍须配置资源隔离；当前为受控教师试点，不提供匿名公共上传。

## 验证与后续边界

```powershell
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -q
```

测试使用临时数据库、合成教师、随机测试凭证和合成Excel。根测试夹具隔离开发者的 `WEIRAN_EDU_DATABASE`，避免误打开实际配置的数据文件。覆盖迁移校验、事务回滚、并发防重、修订历史、身份冲突、过期/版本/权限在解析期间变化、实时撤销、跨教师/跨来源/跨批证据拒绝、完整HTTP链路与真实子进程解析。未读取或写入真实学生数据库。

本轮未实现学校SSO、公开注册、批次删除、评分编辑、评语审核/解密、分析统计/群体/洞察HTTP适配及前端切换真实API。已有分析引擎保持原样，后续通过同一授权服务和正式批次快照接入。

实现依据：[SQLite外键](https://www.sqlite.org/foreignkeys.html)、[事务](https://www.sqlite.org/lang_transaction.html)、[Python sqlite3](https://docs.python.org/3.12/library/sqlite3.html)。
