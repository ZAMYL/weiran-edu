# 教师工作台真实 API 联调

Issue #26 第一阶段。沿用三页布局、Ant Design 和 ECharts，未改后端、公共 DTO、数据库、依赖或 CI。读取实际运行的 `/openapi.json` 后实现前端私有 DTO 及运行时响应校验；历史静态契约不作为依据。

## 启动与教师会话

先按仓库 `docs/DATABASE_BATCH_AUTH.md` 配置后端锁定的 Python 环境。数据库必须使用仓库外的绝对路径。管理员在受控终端执行下面的命令，替换占位符；签发输出中的 access_token 仅交给该教师，不写入文件、仓库、URL、构建变量、日志或聊天记录。

```bash
python -m app.edu --database <external-absolute-db-path> init
python -m app.edu --database <external-absolute-db-path> teacher-create --subject <pilot-subject>
python -m app.edu --database <external-absolute-db-path> scope-create --label <scope-label>
python -m app.edu --database <external-absolute-db-path> member-set --teacher-id <teacher-id> --scope-id <scope-id> --can-import
python -m app.edu --database <external-absolute-db-path> token-issue --teacher-id <teacher-id> --hours 1
```

在 `apps/api` 启动实际应用工厂，环境变量 `WEIRAN_EDU_DATABASE` 指向同一外部数据库：

```bash
python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

在 `apps/web` 设置**仅服务端**的 `EDU_API_ORIGIN=http://127.0.0.1:8000`，运行 `pnpm dev`，或 `pnpm build` 后 `pnpm start`。正式模式点击「连接教师」，输入教师临时凭证。只读教师由管理员按既有 CLI 授予批次读取权，不用管理员凭证登录页面。

同源代理只开放当前教师 API 白名单，固定上游，不转发 cookies，不开放通配 CORS。后端继续逐请求鉴权。此功能限受控本机试点，尚未实现学校 SSO。没有新增公共部署或认证方案。

凭证只放客户端闭包；刷新、结束会话、切换 Demo 或收到 401 均清除。正式请求禁用缓存，页面不把教师数据保存进 localStorage/sessionStorage。Demo 的内存模型与正式 DTO 完全分离，接口失败不会回退到合成数据。

## 数据和统计口径

- 正式上传接收原始双 Sheet 五维 `.xlsx`，1–5、精度 0.01。上传只生成预览；确认后才入库。预览行、问题列表分别分页，问题可定位评分行；质量报告统计全文件。
- 错误阻断；警告绑定当前 `preview_version` 显式确认。PATCH 名称后须重新确认新版本；提交版本由后端再次检查。created、duplicate、replayed 使用实际结果提示。
- 批次、匿名学生和指标参与记录使用后端分页。统计来自完整批次或完整匹配群体，绝不由单页重新计算。群体 `matched_count / total_count` 与 `items.length` 含义不同。
- 均值等数字使用服务端 `display`，图形保留原始数值。阈值使用文本草稿，拒绝超过两位有效小数，不自动舍入。矩阵只渲染实际存在的评分组合，分布指标按需展开。
- 洞察保存原始 `snapshot_id + conditions + include_dimensions`；指标下钻携带同一查询及 `candidate_id + metric.key`。维度卡可能自带空 conditions，不能替换原始查询。`exact.denominator` 是有理数分母，不能当人数；卡片条数、指标统计分母、来源记录数、参与名单总数分别呈现。
- 原始评语 `withheld` 明确显示「非空，待审核」，与 empty 区分。证据与依据兼容当前 main 的 `published` 状态，只展示后端已审核文字及独立 review_version，注明仅限授权教师。关闭后重新打开重新请求，撤回文字不复用旧缓存。评语分析 unavailable 表示当前能力不可用，不表示零异常。

## 错误与并发

401 清除凭证和全部正式数据。403/404/409/410/413/415/422/503、网络失败和超时均显示安全提示，保留错误码及安全格式的服务端请求编号，不回显原始异常或用户输入。SNAPSHOT_MISMATCH 提供刷新批次入口。

切换批次、快照、页面查询或会话时取消旧请求，并以查询标识和会话代次校验结果；旧会话的迟到 401 不能注销新会话，旧批次统计不能覆盖新批次。切换批次清空群体、学生和洞察选择。

## 本地验收

```bash
pnpm lint
pnpm format:check
pnpm typecheck
pnpm build
pnpm test
node tests/live-api-smoke.mjs
```

浏览器联调需要 Playwright 可用的 Chromium（Windows 可使用已有 Chrome），以及安装仓库 `apps/api/uv.lock` 对应依赖的 Python。设置 `EDU_TEST_PYTHON` 为该 Python 绝对路径；未设置时使用仓库忽略目录 `.local/api-venv`。脚本自建仓库外临时 SQLite、CLI 教师和纯内存凭证，自行启动/停止本机 FastAPI 和生产 Next.js。实际 `.xlsx` 由 `tests/make-live-fixtures.py` 生成在临时目录，结束后清除。

覆盖正常、带错误/警告文件，版本确认、重复入库、实际统计、精确 2.75、完整人数与分页、匿名来源回查、已审核文字及撤回后隐藏、原查询下钻、空结果/无候选、401 种合法评分稀疏渲染、错误提示、迟到请求、真实撤权、刷新持久化和 390px 三页。失败状态注入只用于错误提示测试；正常流程、权限撤销和数据分析均调用真实后端。文字发布/撤回由测试脚本直接调用后端准备场景，前端没有审核写入口。超时边界由客户端单元测试控制时钟验证。

`artifacts/live-api/` 的截图、运行时 OpenAPI 和 result.json 只留本地用于审阅，均已被仓库忽略，不含教师凭证。Demo 回归使用 `pnpm test:browser`（先启动前端，可设置 APP_URL），脚本显式切换 Demo。

## 阶段边界

开发期间同步到 main `028493d`，PR #25 已合并。本次按用户优先级交付 Issue #26 第一阶段，兼容现有证据接口的已审核文字读取；尚无文字审核/发布/撤回操作入口、分析保存、逐卡核验、JSON/ZIP 或 XLSX/PDF 导出、多格式导入。这些作为下一阶段独立联调。没有调用 Qwen 或其他付费模型。后端启动配置、权限及洞察口径仍需负责人联合验收；这里的本机实测不代替团队审核。

当前解析器会拒绝带 `xl/printerSettings/*.bin` 的工作簿（`UNSUPPORTED_WORKBOOK_PART`），即使两张 Sheet 的单元格结构符合模板。按用户决定保留原文件结构，交后端兼容；前端不静默移除打印设置或改写上传文件。完全合成的端到端验收不表示此类教师文件已经能够入库。真实表格及脱敏副本均只保留本机，不属于测试夹具或 Git 提交。
