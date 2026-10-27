# 正式学情发现：页面接入与合成验收

> 本文保留固定基线的接口与集成记录；当前教师界面与验收入口以 [最终工作台说明](teacher-workbench-v1.md) 为准。

承接已合并的 #50，Issue #54。新增入口位于正式模式「学情洞察」页面，沿用教师会话、批次选择和画像探索筛选，保留现有布局与旧评分洞察。

## 教师操作

1. 使用已有教师会话或仅本机的合成测试会话，选择测评批次；也可从测评数据页上传 Excel、检查预览后确认入库。
2. 学情洞察中查看「主动学情发现」。列表按发现分页；当前分析人数来自服务端完整 scope，不是当前页学生数。
3. 点击发现查看完整统计图表、发现条件和解释限制。
4. 点击「选定群体」「少见组合学生」「边界新增学生」等服务端受控来源，查看按原条件重放的匿名学生名单。
5. 点击具体维度评分，进入已有评分证据抽屉；评语和依据正文继续受后端审核权限约束。
6. 如需调整外层分析范围，在画像探索中修改筛选，再返回学情洞察。切换批次或范围会销毁旧发现与证据状态。

## 接线边界

同源代理仅增加四条 POST 白名单，前缀 `/api/edu/batches/{batch_id}`：

- `/discoveries/query`
- `/discoveries/{discovery_id}/query`
- `/discoveries/{discovery_id}/chart/query`
- `/discoveries/{discovery_id}/records/query`

请求继续由既有固定上游代理转到 FastAPI `/api/v1/edu`；保留 Origin 校验、教师凭证保护、64 KiB JSON 上限、真实错误状态、no-store、X-Request-ID 和取消信号。没有新增认证或放宽匿名访问。

`DiscoverySpec` 为 `snapshot_id + profile=edu.discovery.profile.v1 + scope_conditions`。详情、图表和名单复用列表的原始 spec，`conditions` 仅展示发现内部条件，绝不覆盖外层范围。不向旧 groups/query 拼接有损条件；records/query 是原发现群体重放入口，适用于差值围栏、同维不同边界和超过旧两维限制的情形。

前端对四类 metrics/chart 分别解码，核验批次、快照、scope、发现 ID 和来源 metric。详情和图表还核对 input_key、版本、条件、完整图表与统计一致，失败不降级为 Demo。均值/差值展示两位小数，人数为整数；前端只格式化后端精确值，不从学生页重算统计。

名单只显示该受控来源返回的维度；`rare_members` 与 `pair_reference` 的人数分别呈现。后端仍负责内核 sources 校验、正式数据库归属及计算前后重新鉴权。所有发现是待教师核查的评分现象，不代表学生真实能力定论。

本次没有修改 AI 引擎、数据库、后端权限或 Qwen。新发现仍是重算查询，**没有自动写入旧 analysis-results、教师核验及报告导出流程**；本轮验收范围为「导入 → 发现 → 完整图表 → 原群体 → 评分证据」。

## 真实合成浏览器验收

先按仓库说明准备锁定依赖并构建前端：

```powershell
pnpm lint
pnpm format:check
pnpm typecheck
pnpm build
pnpm test
$env:EDU_TEST_PYTHON = '后端锁定虚拟环境的绝对 python 路径'
# 可选：使用本机已有 Chrome/Edge；否则使用 Playwright 已安装的 Chromium。
$env:EDU_TEST_BROWSER = '浏览器可执行文件绝对路径'
node tests/discovery-api-flow.mjs
```

脚本创建临时 SQLite、教师/只读教师、服务端测试凭证和完全合成双 Sheet Excel，启动仅回环监听的真实 FastAPI 与生产 Next，使用 Chromium 实际操作网页；正常链路不拦截或伪造响应。结束或捕获失败后关闭服务，核验临时路径后删除测试目录，不重启已有 3000/8000 服务。

覆盖：三份 Excel 实际上传预览入库；53人精确均值619/212；发现分页与名单分页不改变完整分母；四类真实图表；群体M+R+U=53；外层范围40人/边界新增13人；少见群体1人/完整参考17人；空发现；真实未知发现404/错误快照409；只读授权及撤权404清除卡片；撤销凭证401清除整个正式工作区；未审核文本不展示；390px列表与详情无整页横向溢出；浏览器不发送测试凭证、无凭证持久化、无远程模型请求、无页面运行错误。

结果和截图写入忽略的 `artifacts/discovery-flow/`。报告不记录令牌、数据库或学生原文件；示例输入全部由 `tests/make-discovery-fixtures.py` 现场生成。

独立开发目录复用 node_modules 联接时，Turbopack 可能拒绝超出项目根的依赖路径；本机可用 `next build --webpack` 验收同一应用源码，CI/正常安装环境继续执行默认 `pnpm build`，不为此更改仓库配置。
