# 未然 EDU

未然 EDU｜AI 时代的编程能力与学习成效诊断平台。主导航为学情诊断、画像探索、分析报告。依据本地选定的设计稿实现浅蓝顶部导航布局，设计参考资料不纳入代码仓库。

技术栈：Next.js 16 App Router、React 19、TypeScript 5.9、Ant Design 6、ECharts 6、Zustand 5、read-excel-file 9。沿用正式仓库的框架、严格类型检查及 CI；没有引入 Vite 或 React Router。完整技术决策、组件规范、数据规则见 [前端架构说明](docs/frontend-architecture.md)。实际依赖版本由本目录 `pnpm-lock.yaml` 锁定。

## 本地运行

需要 Node 24 与 pnpm 11.25.0，在 `apps/web` 目录运行。

```bash
pnpm install --frozen-lockfile
pnpm dev
```

打开命令输出的本地地址，默认 `http://127.0.0.1:3000/`，首页跳转到 `/insights`。

```bash
pnpm lint
pnpm typecheck
pnpm test:unit
pnpm build
pnpm test
pnpm start
```

生产构建位于 `.next/`，使用 Next.js 原生文件路由。`pnpm test` 包含领域测试和现有的生产服务 smoke 检查，生产检查需要先 `pnpm build`。前端通过路由组 layout 保持批次、筛选条件和全屏上下文。

## 教师工作台

先启动 FastAPI 并配置仓库外 SQLite，使用[管理员命令](../../docs/DATABASE_BATCH_AUTH.md)创建教师、授权数据来源并签发访问凭据。Next.js 通过同源 `/api/edu` 转发到服务端 `EDU_API_ORIGIN`，默认 `http://127.0.0.1:8000`。教师进入首页后连接账户。无测评时进入上传页，选择后端支持的 `.xlsx` 测评表，处理检查结果并确认导入。

诊断、画像和报告使用后端统计与获授权的学生标识。默认评分显示最多两位小数。报告优先打开该测评的已有版本，教师核验后导出 Excel 或 Markdown。导出需实时检查权限、报告版本与服务器预检。

真实 AI 任务接线由服务端的 `EDU_AI_LIVE_MODE=1` 开启，必须有支持并授权真实任务的后端。当前产品基线仅有隔离的合成任务宿主。运行中的界面使用真实任务状态，网页调试及演示需用[独立开发宿主](../../scripts/local/README.md)，其环回上下文与教师访问分开。

## 浏览器验证

先安装已锁定的 Python 环境并运行 `pnpm build`，再设定 `EDU_TEST_PYTHON` 为其解释器绝对路径，运行 `pnpm test:browser`。脚本启动隔离的 Next.js、FastAPI 与 SQLite，并将浏览器证据写入 `artifacts/`。Windows 可使用本机 Chrome，其他环境可安装 Playwright Chromium。用于解析测试的工作簿由测试命令生成在私有测试目录。
