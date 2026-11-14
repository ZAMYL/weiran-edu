# 学情诊断返回复用验收

基线：`15788491b536d408005030ddbed2cee2d1a9f6b2`。本补丁独立于教师分析文字 PR，不修改演示工作树、发现内核、统计规则、来源核验、数据库结构或请求超时。

## 根因与复用位置

DS 提供的 `D:/Temp/run-perf.sh`、`D:/Temp/perf-nav.mjs` 已只读确认存在。其计时等待重点发现按钮，跨画像/报告页导航，记录发现列表和详情重复请求。此前报告的 20.6 / 20.9 / 20.5 / 21.5 秒没有配套原始 HAR，不能算本次独立测量。

独立复现确认：路由切换卸载 `LiveDiscoveries`，`useRemote` 只保存挂载期间状态；返回后列表与代表发现详情再次调用服务，每个调用原先都重新启动 Windows 计算子进程。`useRemote` 保持原样。

只按 batch/snapshot 元数据缓存前端结果并不充分：合法范围内交换两名学生的评分可以保持各项整体统计不变。新回归测试覆盖了这种情况。因此复用放在原有 `DiscoveryReader.calculation` 的完整输入读取与前后授权检查之间。

- 诊断摘要的两个专用客户端方法显式发送随机会话 nonce；连接、退出、凭据更换、401 清理时均轮换。
- Next 只在发现列表/摘要详情 POST 路由转发有效 nonce；它不是授权凭据。
- 每个 FastAPI app 实例保留最多 8 个已完成结果，逻辑有效期 300 秒；进程重启即消失，不持久化。
- 隔离键包括教师、令牌哈希、nonce、原有规范化输入键；还必须与实时读出的完整评分、学生引用、来源引用、快照和筛选输入相等。分页不改变整批发现计算。
- 命中仍执行原来的来源完整性核查、实时授权、结果核查，以及返回前的新事务复核。计算上下文内检查失败会丢弃该会话的复用条目，不返回旧结果；入口鉴权直接拒绝的请求不会读取缓存。
- 学生、证据详情、图表详情、正式报告继续走原来的实时读取/计算。统计概况也保持实时读取；没有把权限判定或浏览器响应作为缓存的授权依据。

## 可重复实测

脚本：`apps/web/tests/diagnosis-return-reuse.mjs` 与 `diagnosis-reuse-server.py`。后者仅计数真实 `discovery_worker.execute`，仍执行真实子进程；没有替换算法、HTTP 响应或权限。每次新建外部临时 SQLite、合成教师和 XLSX，随机 loopback 端口，生产 Next + FastAPI + Chromium。只关闭脚本自己创建的进程。

依赖使用现有相同锁文件的 Node 24 / pnpm 11.25 环境及 Python 3.12 锁定环境。Windows worktree 的 node_modules junction 需要使用 Webpack 生产构建，避免 Turbopack 对外部 junction 的限制。

```powershell
# 两个独立 worktree 分别准备，不能在运行中的演示目录构建。
uv sync --project apps/api --locked --group dev
# 在各 worktree 的 apps/web 中：
pnpm install --frozen-lockfile
node scripts/generate-import-samples.mjs
node node_modules/next/dist/bin/next build --webpack

# 在补丁 worktree 的 apps/web 中运行；不调用任何演示启动器。
$env:EDU_TEST_PYTHON = '<锁定 Python 环境的 Scripts/python.exe>'
$env:EDU_TEST_BROWSER = "$env:LOCALAPPDATA/Google/Chrome/Application/chrome.exe"
$env:EDU_BENCH_REPO = '<待测独立 worktree 的绝对路径>'
$env:EDU_BENCH_LABEL = 'before-15788491' # 或 after
$env:EDU_BENCH_OUTPUT = '<输出 JSON 的绝对路径>'
$env:EDU_BENCH_PERF_ONLY = '1' # 只测首次与三次返回；完整隔离验收须清除此变量
node tests/diagnosis-return-reuse.mjs
```

`measurements` 记录重点卡片可见耗时、网络静默耗时、发现 HTTP 状态/次数、真实 worker 次数。`network` 逐请求记录直连/代理、状态和耗时；不输出令牌、请求头、评分正文或正式资料。页面切换和撤权后取消未完成请求的 `ERR_ABORTED` 也保留记录，不当作 HTTP 成功。

## 验收结果

生产浏览器完整隔离验收 PASS；[计时及逐发现请求记录](validation/diagnosis-return-reuse-results.json) 来自实际输出，单位秒：

| 场景 | 基线 | 补丁 | 基线/补丁计算次数 | 基线/补丁发现 API 次数 |
| --- | ---: | ---: | --- | --- |
| 首次重点关注 | 21.033 | 9.226 | 3 / 1 | 3 / 3 |
| 返回 1 | 17.138 | 4.189 | 3 / 0 | 3 / 3 |
| 返回 2 | 18.028 | 4.104 | 3 / 0 | 3 / 3 |
| 返回 3 | 15.410 | 4.792 | 3 / 0 | 3 / 3 |

以上发现请求均 HTTP 200。首次含登录交互和冷进程启动；两组为同机顺序运行，不以单次数据保证其他机器的耗时。完整隔离运行的另外三次返回为 5.239 / 5.672 / 6.280 秒，同样均为 0 次计算。接口请求次数没有减少，减少的是高成本重复计算。

| 检查 | 实际结果 |
| --- | --- |
| 同批次返回、另一批次返回 | PASS；均 0 次重复计算 |
| 切换批次、退出后同令牌重连、切换教师 | PASS；独立计算，不复用先前会话；B 教师不显示 A 批次内容 |
| 成员撤销、令牌撤销 | PASS；404 / 401；页面旧发现卡片为 0 |
| 新修订批次/快照 | PASS；新快照独立计算；错误快照 409；合法历史批次仍按原权限可读 |
| 筛选不同、令牌更换、过期 | PASS；真实 worker 回归测试确认重新计算 |
| 同整体统计但学生评分交换 | PASS；完整输入不同则重新计算 |
| 只读授权撤销、复用命中后处理中撤权 | PASS；404，恢复后重新计算 |
| 来源坐标损坏 | PASS；503，未返回已完成结果 |
| 图表、学生和评分依据接口 | PASS；真实 HTTP 200，保持普通实时读取 |
| 页面异常 | 完整隔离运行未捕获未处理页面异常 |

前端：`vitest run` 217 passed；lint、TypeScript 与改动文件默认 Prettier 检查通过。后端：`test_discovery_api.py test_discovery_bridge.py test_discovery_worker.py test_discovery_reuse.py` 44 passed；补充后的 `test_discovery_reuse.py test_discovery_results.py test_discovery_report_limits.py` 33 passed（两组含重复项，不相加为独立测试数）。修改的 Python 文件 Ruff/format 通过。`test_tasks.py test_priority_questions.py` 6 passed，覆盖既有 AI 合成调查、来源/画像及报告持久化和授权边界，使用原有 HTTP Mock；真实 Qwen 未运行。没有运行全量后端 pytest。

全库标准 `prettier --check .` 为 FAIL：基线 129 个文件、补丁 124 个原有文件，Windows checkout 使用 CRLF；基线和补丁的 `prettier --check --end-of-line auto .` 均为 PASS。没有为了通过格式检查批量修改无关文件。

最终 `next typegen`、`tsc --noEmit`、`next build --webpack` 均 exit 0；`node tests/smoke.mjs` PASS（生产服务器启动、工作区路由渲染、合成模板下载、缺失路由 404）。

故障证据也保留在本地 `apps/web/artifacts/diagnosis-reuse/`：早期脚本定位器/异步事件记录断言失败；一次登录弹窗未完成且未发出账户请求；一次重连提示网络错误；一次重复源文件修订的 commit 等待超过测试的 60 秒。它们不计作 PASS，也没有据此改登录、上传业务或延长产品超时。随后完整新临时库隔离运行通过；额外性能复测采用原有输入框 Enter 提交通过。登录偶发停滞及重复源修订超时原因未归因，不能宣称已修复。本次不重跑所有浏览器 AI/报告操作链；其验证范围须与后端离线回归区分。

## 风险与审核

- 这是计算复用，不是免请求的即时页面缓存；实时数据库检查仍有耗时。首次访问、换教师/会话/筛选/快照、过期、被逐出或进程重启都可能重新计算。
- 每个进程最多保留 8 份内存输入/结果；过期条目不能命中，但没有后台清理线程。新请求读到或容量逐出时释放；进程退出全部释放。多进程间不共享，也没有并发冷请求合并。
- 退出/撤权使旧 nonce 或凭据的条目不可达，并不通过浏览器去删除服务端内存。服务器始终重新验证权限；旧条目的物理保留上限仍受容量和进程生命周期约束。
- 合法历史修订批次仍按原契约可读；不能把它误称为已失效批次。新批次/快照独立计算，错误快照 409，撤权批次 404，失效令牌 401。
- Draft PR 需后端/前端负责人审核 nonce 传递与复用位置；尚无独立人工审核通过结论，不自动合并。
