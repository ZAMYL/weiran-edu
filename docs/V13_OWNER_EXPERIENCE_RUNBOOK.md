# V1.3 负责人体验候选（隔离分支）

本分支用于 Windows 本机合成资料体验，不替代 release/main。网页 AI 的执行来源为 `http_mock`，没有发出真实 Qwen 请求；评分、发现、画像、来源查询、保存及导出均使用真实 FastAPI 和 SQLite。

## 从干净目录启动

安装 Git、Node.js 24、pnpm 11、Python 3.12 和 uv。首次安装依赖需要网络。使用普通 PowerShell，无需管理员权限：

```powershell
git clone --single-branch --branch integration/v13-owner-experience-20261115 https://github.com/ZAMYL/weiran-edu.git weiran-edu-experience
cd weiran-edu-experience
$env:PYTHONUTF8 = '1'
uv sync --locked --project apps/api --group dev
pnpm --dir apps/web install --frozen-lockfile
pnpm --dir apps/web build
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/local/start-demo.ps1 -Identity sample -Port 3417
```

打开 <http://127.0.0.1:3417/insights>。启动器只绑定本机，使用临时 SQLite 和已登记合成资料，令牌保存在仓库外。不要复制私有连接文件、令牌或数据库到 Git。默认运行一小时；退出后临时测评、任务和报告不作为长期档案保留。

端口占用时会拒绝启动，不结束已有演示服务。可换 `-Port 3418`。Ctrl+C 只结束本次启动的子进程；也可在另一个终端使用启动时打印的本次会话目录：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/local/stop-demo.ps1 -SessionDirectory '<启动器打印的本次会话目录>'
```

## 五分钟体验

1. 样例教师已有登记测评。在「学情诊断」选择 `normal`（53 人），查看重点发现，进入画像探索，核对 26/27 人群体，并打开原评分依据。
2. 返回诊断，使用「优先核查」开始 AI 分析，等待任务完成。规划回复来自 HTTP Mock，工具实际查询数据库；页面不代表真实 Qwen 网页接线已经完成。
3. 选择 `balanced72`（72 人画像样例）并开始分析。查看群体画像的五维均分；点击维度才加载评分依据，切换本组/对照组，再点原评分依据。隐私受限时不会展示明细。
4. 刷新页面，检查同一已完成任务恢复；从合格发现进入报告核查，选择确认或不加入报告，保存核验结果，再下载 Markdown/XLSX。报告只计入已确认内容，保存版本与导出版本应一致。
5. 选择 `empty`（17 人同分）或 `zero81`（81 人零画像），检查没有编造画像，仍可探索和生成合法空报告。其余样例为 `evidence`（证据缺失）和 `overlap96`（96 人交叠群体）。390px 窗口可重复主要操作。

首次上传体验请另开一个空闲端口运行 `-Identity first`，在文件选择器中使用启动器打印的本次登记合成 XLSX 路径。该文件已验证可完成上传、预览、质量确认、入库、AI 调查、教师核验和报告下载的同批次闭环。AI 只允许本宿主已登记并授权的合成资料；任意上传文件不会自动获得 AI 或远程处理授权。

## 本次集成范围

固定基线 `0b65bcf4139545fec5dd04f0d087c8e1132c03ec`，纳入 #92 `08b6f8c1c955b0f1ce2c32980d3e7e404892ed40`、#93 `13cdc04184f5d96e13bddcd31ba2b5da83a8be8e`、#96 `3eeb3b6d0f8b4bdb1a172bc3486f0a3ab8dea2c8` 和 Windows 宿主修复 `d96303ab86bc17d355c21a979d197d9103d0d7bc`。#94 的报告结论已在基线中，不重复引入。

前端以 #92 为主，保留 #93 按教师会话、批次和快照隔离的诊断复用。正常评分依据和权限查询继续走原接口。没有改正式数据库 schema、事实计算、6/5 预算、累计披露门禁或 Live 禁令。

合成宿主首次启动约需一分半钟登记资料，请等待账户/批次接口检查通过。启动后同一会话内的任务和保存可刷新恢复；关闭宿主后的新会话会重新生成临时数据，不承诺跨次启动保留演示档案。

报告数差异的根因是旧测试只统计 `discovery_findings`；正式导出统计 `findings` 与 `discovery_findings` 中所有已确认项。`receipt.candidate_ids` 是 AI 提交后映射出的候选引用，不是“已确认数量”。本补丁修正验收器，按实际保存结果计算，并校验 MD/XLSX 的版本、引用和数量，没有修改正式导出计数逻辑。

## 边界

- 支持当前固定五维双 Sheet 协议，不承诺逐题资料或任意 Excel 导入。
- 没有正式教师登录、真实 Qwen 网页接线或真实学生资料远程推理。
- 已有 8 客户端满速压测出现 503 限流/超时；普通单用户体验通过不能证明该负载通过。
- 本次不合并发布候选、不部署公网。具体运行证据见同目录验收记录；未运行的完整 CI/全量测试不得当作通过。
