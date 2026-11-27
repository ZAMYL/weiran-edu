# 未然 EDU 前端 Ant Design 重构交付

工作台已按 Ant Design 6.7.1 的布局、组件和 Design Token 重构。React 和 Next.js 继续承担页面与状态管理，业务图表继续使用 ECharts，并与当前主题同步配色。

## 工作台

- 仪表盘为默认首页。侧栏通过 `Layout.Sider` 和 `Menu` 组织仪表盘、上传、测评管理、分析、分析报告及学生管理。分析下设学情分析和画像探索，测评管理提供历史测评和历史答题卡。
- 顶部通过 `Tabs` 管理已打开页面，与侧栏、地址和浏览器前进后退同步。页面切换保留已打开页的输入和筛选状态。
- 上传是单一工作页，卡片内用 `Segmented` 切换评分与评价及编程答题卡。答题卡详情通过右侧 `Drawer` 查看资料来源。
- `ConfigProvider` 统一默认主色、明暗算法、间距和圆角。主按钮与 `Segmented` 选中项均使用 `colorPrimary`。侧栏主题按钮记住浏览器上次选择。
- 数据表使用 `Table` 默认外观和自动列布局。输入控件按语义使用 `Input`、`InputNumber`、`Input.TextArea`、`Input.Password`、`Select` 与 `Upload` 等原生组件。

## 验证

在 `apps/web` 运行 `pnpm lint`、`pnpm format:check`、`pnpm typecheck`、`pnpm build` 和 `pnpm test`。Windows 检出可用 `pnpm exec prettier --check --end-of-line auto .` 校验格式。

`pnpm test:browser` 使用独立的 FastAPI、Next.js 生产构建和合成数据库回归导入、筛选、报告导出与访问权限。令 `EDU_TEST_PYTHON` 指向安装了项目锁定依赖的 Python，Chromium 路径可通过 `EDU_TEST_BROWSER` 指定。导航专项为 [workspace-navigation.mjs](../tests/workspace-navigation.mjs)，以 `EDU_TEST_WEB` 指定预览地址。

工作区本机预览使用 `61288`。测试会话配置见 [本机联调说明](local-test-session.md)。学生管理目前提供导航入口和空状态。

## 验收截图

截图来自合成数据或测试夹具。

- 学情分析 [浅色](screenshots/antd-workspace/workbench-light.png) · [暗色](screenshots/antd-workspace/workbench-dark.png)
- [上传工作页](screenshots/antd-workspace/upload.png)
- [分析报告核验](screenshots/antd-workspace/report-review.png)
- [历史测评 390px 窄屏](screenshots/antd-workspace/history-mobile.png)

## 设计参考

- [Ant Design 左右布局](https://ant.design/docs/spec/layout-cn/)
- [Ant Design 研发平台工作台](https://ant.design/docs/spec/research-workbench-cn/)
- [Ant Design 主题变量](https://ant.design/docs/react/customize-theme-cn/)
