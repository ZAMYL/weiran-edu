# P0：105 人发现响应无损整数定位与回归准备

日期：2026-10-31。范围：#40 原分支、前端。**当前为缺陷定位和测试准备，生产修复与完整闭环验收尚未完成。** 页面设计、生产解码器、后端算法、Agent 和权限均未修改。

## 复现基线

- 生产前端：`238f209a47cec1388d06d5dfaa4c9dfe9b1f0448`；新增测试在其上运行，最终测试提交以 #40 评论为准。
- 隔离后端：#61 `27212f60216ff97d7e98ef27111edb7eacf8346c`＋#65 `24c442cf9b48e11886fdaf45ac9e644b23e7c2d6` 上传增量。
- 所有输入由 `tests/make-discovery-fixtures.py` 的 `precision105` 生成：固定随机种子 20261031，105 个合成学生块，五维共 525 条合法评分，范围 1–5、两位小数；预览错误 0。使用独立临时 SQLite，未读取真实表格或当前运行测评。
- 早期 `contrast105` 只有少量固定分值，不能覆盖本次排序精确分数超界；保留原夹具，新增小数样例而不修改计算规则。

## 精确失败位置

真实 `POST /api/v1/edu/batches/{batch_id}/discoveries/query` 返回 200，列表可正常解码。第一个发现的详情 `POST .../discoveries/{id}/query` 也返回 200，但原前端完整详情解码失败。

**首次拒绝路径：`$.ranking.score.denominator`。** `discovery-types.ts` 的 `exact` 用 `schema.count` 解码 denominator；该字段是有理数分母，不是人数。`count` 要求有限、非负且 `Number.isSafeInteger`。对象先逐字段解码，因此 denominator 的检查先于 exact 整体对 numerator 的安全整数检查触发。

| 路径                           | 原始 JSON 整数词元（Python 无损读取） | 现有浏览器 JSON 解码后类型 | 原解码要求   |
| ------------------------------ | ------------------------------------- | -------------------------- | ------------ |
| `$.ranking.score.numerator`    | `24840307107174983`                   | number，存在精度丢失       | 安全整数     |
| `$.ranking.score.denominator`  | `25000000000000000`                   | number                     | 正的安全整数 |
| `$.ranking.effect.numerator`   | `9840307107174983`                    | number，存在精度丢失       | 安全整数     |
| `$.ranking.effect.denominator` | `10000000000000000`                   | number                     | 正的安全整数 |

JS 最大安全整数为 `9007199254740991`。不能因 HTTP 200 就忽略校验，也不能删掉 ranking 或放宽全局 count。上述原始整数在 Python 中按任意精度整数解析，再转为字符串记录；没有通过 JS number 反推原始值。

诊断 Vitest 仅包装原 schema 的 object/list 以标注字段路径，count、numeric、checked 等实际校验仍执行。测试副本中仅把 ranking.score/effect 替换为安全的小数值后，整个详情 DTO 可解码，证实本次拒绝点；替换不用于 HTTP 响应、生产页面或修复方案。诊断中对旧 JSON number 的读取只是复现现有缺陷，不能作为无损协议实现。

## 实际浏览器结果

使用生产 Next.js、真实 FastAPI、SQLite 和已安装 Chrome：从选择 Excel、自动检查、确认导入进入 105 人 / 525 评分诊断；列表和详情均为 200，页面出现 `INVALID_API_RESPONSE`，没有可操作的重点发现卡。截图已经实际查看。

- `artifacts/integer-browser/105-before-fix.png`：当前失败截图。
- `artifacts/integer-browser/acceptance-record.json`：`status=bug_reproduced`、`product_accepted=false`、`full_workflow=not_run_blocked_by_decoder`。
- `artifacts/integer-precision/integer-diagnostics.json`：Python 保留原整数的路径/类型记录。
- `artifacts/integer-precision/frontend-decoder-diagnostic.json`：首次拒绝路径及测试隔离结果。

“诊断测试通过”只表示缺陷确实复现；页面运行异常为 0 也不代表产品通过，ContractError 已被界面正常捕获。上述产物在忽略目录，不上传截图、Excel、数据库或凭据。

本轮 lint、format:check、typecheck、生产 build、160 项既有单测及生产 smoke 均通过；单独运行的 1 项字段定位诊断也通过。它们证明测试准备没有破坏既有检查，**不代表超大整数已兼容**。完整修复提交、无损新协议兼容测试和修复后 105 人闭环仍未交付。

## 待仲楷确认的前置契约

已在 [#40 的 P0 交接评论](https://github.com/ZAMYL/weiran-edu/pull/40#issuecomment-6539495699) 请求确认并提供全部原始合成整数与路径。

需要后端给出修复 SHA 和正式规则：哪些 exact 字段采用无损线编码；十进制整数的格式、长度、符号、分母约束；是否保留安全旧 number；display 的格式和验证方式；保存结果内嵌 DiscoveryDetail 是否同样适用；报告预检中是否有相应变动。本轮复核尚未得到该协议提交，**不自行猜测编码、不批量修改字段**。

保存结果会在 `analysis.discovery.findings[i].ranking` 与 `discovery_findings[i].discovery.ranking` 重现同类精确值；后续修复须覆盖它们，不能只让首屏忽略排序。人数、分页、版本号仍保持全局安全整数检查。

## 运行方式与后续验收

在 `apps/web`，设置已批准隔离后端和锁定 Python 路径，运行：

```powershell
$env:PYTHONUTF8='1'
$env:EDU_TEST_API_ROOT='<固定隔离后端的 apps/api>'
$env:EDU_TEST_PYTHON='<锁定环境的 python.exe>'
$env:PYTHONPATH=$env:EDU_TEST_API_ROOT
& $env:EDU_TEST_PYTHON tests/probe-discovery-integers.py artifacts/integer-precision
pnpm exec vitest run --config tests/integer-probe.vitest.config.mjs

# 只验证原缺陷存在，不代表产品通过。
$env:EDU_EXPECT_INTEGER_REJECTION='1'
node tests/teacher-integer-e2e.mjs

# 正式闭环验收：协议/前端未修复时必须失败。
Remove-Item Env:EDU_EXPECT_INTEGER_REJECTION
node tests/teacher-integer-e2e.mjs
```

同时用 `EDU_TEST_FRONTEND_REVISION` / `EDU_TEST_BACKEND_REVISION` / `EDU_TEST_UPLOAD_REVISION` 记录准确版本；未提供为 null。浏览器测试自行分配端口和临时数据，不重启或改写当前教师服务。

`teacher-integer-e2e.mjs` 默认要求完整链路：105/525 上传 → 诊断 → 详情/图表 → 原群体/快照 → 评分依据 → 保存 → confirmed → 真实 MD/XLSX 下载及重开。**这条完整链路目前未通过；后半段只完成测试准备，还需在协议修复后真实执行。** 不允许用复现模式替代正式验收。

协议发布后的工作顺序：先核对后端提交与线格式，再仅修相应解码、无损精确值处理和必要展示；补超大分子、超大分母、负值/零分母、非法编码、安全旧响应、排序/详情/保存/报告回归；最后运行完整浏览器闭环并记录新的准确提交。显示或计算前不能先把超大整数转为 number；保留所有人数、分页、权限、快照、版本、来源和非法输入门禁。不自动合并 PR。

## 2026-11-01 验收准备补强

原默认小数样例和失败证据保留。新增两处独立于未发布线协议的验收检查：confirmed 后完整刷新、从已保存列表重新打开并核对新 GET 返回的两处嵌套发现及核验版本；MD/XLSX 下载后核对测评人数、确认发现数、批次、快照、保存结果引用、核验版本和确认发现 ID，而不只检查文件能否打开。`tests/report-artifact-utils.mjs` 同时用于既有 53 人正常回归和 105 人入口。

为验证完整测试路径本身，可显式设置 `EDU_INTEGER_CONTROL=1` 运行已有 `contrast105` 安全整数对照。该模式的独立产物为 `artifacts/integer-control`，记录 `fixture=contrast105`、`status=control_complete`、`product_accepted=false`。它不覆盖 `artifacts/integer-browser` 的小数失败证据，也不能作为 P0 修复验收；默认仍使用会触发超界的 `precision105`。对照与缺陷复现开关不得同时使用。准确运行结果及测试提交 SHA 记录在 #40。

仲楷的明确契约与修复 SHA 仍在 [#61 技术对接评论](https://github.com/ZAMYL/weiran-edu/pull/61#issuecomment-6558742592) 请求中，无待用户批准事项。生产解码与界面继续保持原样，不能把测试入口完善写成无损修复完成。
