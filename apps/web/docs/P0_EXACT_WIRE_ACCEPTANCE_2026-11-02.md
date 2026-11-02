# V1 P0：精确整数接入与浏览器验收

日期：2026-11-02。责任范围：林明，前端 PR #40 原分支。仅修改 exact 解码、精确值到图表数值的转换及必要展示。未修改页面布局、导航、后端算法、Agent、权限、快照或来源检查。准确前端 SHA 见本提交的 PR 验收评论和运行产物，不以历史 CI 代替。

## 固定组合

- 前端起点：`9446a2470c05dd41aece85c66b3878023b058d0b`。
- 后端 #61：`a64e58b25f01d82aa1b2607a3aad1e0ef6b70445`。
- 上传 #65：`24c442cf9b48e11886fdaf45ac9e644b23e7c2d6` 的上传增量，应用于隔离的忽略目录，不修改后端分支。
- 正式契约：[EXACT_JSON_WIRE_V1.md](https://github.com/ZAMYL/weiran-edu/blob/a64e58b25f01d82aa1b2607a3aad1e0ef6b70445/docs/EXACT_JSON_WIRE_V1.md)，[仲楷确认](https://github.com/ZAMYL/weiran-edu/pull/61#issuecomment-6567242151)。

## 生产修复

安全整数保留 number，超界分量为规范十进制 string。先检查类型、ASCII 格式、1024 位预算和真实超界，再使用临时 BigInt；分母严格为正。DTO 保持 number|string，可安全 JSON.stringify。旧后端已经不安全的 JSON number 仍拒绝。

DiscoveryExact 覆盖 ranking、metrics、chart、conditions.threshold、parameters；统计 ExactRational 及保存/核验/重开的两处嵌套发现共用严格解码。普通 count、分页、版本保留原安全整数检查，评语/依据初始版本 0 保留。DiscoveryExact 的 display 检查固定四位、长度与有限性，沿用后端 Python 显示契约；普通统计两位 HALF_UP 未改。

图表转换先以 BigInt 做有界除法，按 binary64 最近偶数舍入；不先 Number(大分子)/Number(大分母)，不用 display 判断排序、资格或事实。历史摘要的 Fraction 文字也使用有界除法。服务端发现顺序、筛选与分母不变。

## 实际验证

使用真实 Next.js 生产服务、FastAPI、临时 SQLite 和 Playwright Chromium（已安装 Chrome 156 内核）。正常流程没有替换成功响应，没有调用远程模型，没有读取真实教师表格或当前服务数据库。默认 `precision105` 为固定种子 20261031 的两位小数样例，不设置缺陷复现或安全整数对照开关。

- 105 人、525 条评分：选择 Excel → 自动检查 → 确认导入 → 诊断 → 发现详情/图表 → 原批次、快照和完整条件的群体 → 归属正确的评分依据 → 保存 → confirmed → 刷新、从列表重开 → MD/XLSX 下载重开。
- 实际 ranking.score 的分子 `24840307107174983`、分母 `25000000000000000` 为 string；两处保存副本与刷新回读一致，普通人数为 number。真实过期快照返回 409。
- 文件核对 6 项元数据：人数、确认发现数、批次、快照、结果引用、核验版本；核对确认发现 ID，以及该关联发现的范围人数、有效成对人数和系数。XLSX 用 openpyxl 重开，MD 按实际内容核对。
- 53 人正常发现、原 26/27 群体、证据、保存和两种报告；17 人同分零发现、空报告和手动探索；实际 409 保留编辑、只读/撤权、真实凭据失效 401 通过。
- 前端 lint、format:check、typecheck、build、191 项单测及生产 smoke 通过；额外 1 项历史不安全数字拒绝诊断通过。新用例覆盖正负超大分量、1024 位边界、非法格式、旧安全数字、零负分母、display、图表、条件、统计和嵌套保存一致性。

当前提交的 CI 链接和最终状态见 #40 最新验收评论；CI 未结束时不宣称通过。浏览器组合验收不代替仲楷联合复验与组长产品验收，PR 不自动合并。

## 重跑与证据

在 `apps/web` 设置隔离后端和锁定 Python 后执行：

```powershell
$env:CI='true'
$env:PYTHONUTF8='1'
$env:EDU_TEST_API_ROOT='<a64e58b 加 #65 上传增量的 apps/api>'
$env:EDU_TEST_PYTHON='<锁定环境 python.exe>'
$env:EDU_TEST_FRONTEND_REVISION=(git rev-parse HEAD).Trim()
$env:EDU_TEST_BACKEND_REVISION='a64e58b25f01d82aa1b2607a3aad1e0ef6b70445'
$env:EDU_TEST_UPLOAD_REVISION='24c442cf9b48e11886fdaf45ac9e644b23e7c2d6'
Remove-Item Env:EDU_INTEGER_CONTROL,Env:EDU_EXPECT_INTEGER_REJECTION -ErrorAction SilentlyContinue
node tests/teacher-integer-e2e.mjs
node tests/teacher-product-e2e.mjs
```

`artifacts/integer-browser/acceptance-record.json` 记录准确版本、wire ranking、浏览器、`fixture=precision105`、`control_only=false`、`status=complete`、`product_accepted=true` 及文件校验。`result.json` 记录 HTTP 和页面异常。实际截图：`105-after-fix.png`、`105-discovery-detail.png`、`105-score-evidence.png`、`105-confirmed-reopened.png`。`artifacts/teacher-product/product-result.json` 和 `result.json` 记录 53/17 人及错误边界。

所有产物均为合成数据，保留在忽略目录，不提交 Excel、数据库或凭据。原始失败位置与词元保留在[历史定位记录](P0_DISCOVERY_INTEGER_2026-10-31.md)，其中等待协议、尚未修复等均为当时状态，以本记录和最新提交验收为准。

已知边界：必须配套部署 #61 无损后端；旧后端的超界 JSON number 继续拒绝。私有 JSON/ZIP 归档未改为浏览器 DTO，本轮仅验收现有 MD/XLSX 导出。没有新增界面或 AI 执行能力。
