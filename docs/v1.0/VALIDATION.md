# 本次本机验收记录

本次新建独立工作区、Python虚拟环境和实际node_modules，使用锁定依赖。
成功链路是真实Next生产包→FastAPI→临时SQLite；浏览器使用Playwright Chromium驱动、
本机Google Chrome 155.0.8059.40的隔离临时上下文，未用Mock代替正常API。

| 检查 | 实际结果 |
| --- | --- |
| 后端Ruff / format | 通过，198文件 |
| 后端完整pytest | **1439 passed、1 skipped、1既有warning**，1116.24秒 |
| 前端lint / format / TypeScript | 通过 |
| 默认pnpm build | Next.js Turbopack生产构建通过 |
| pnpm test | **143项单元测试**及生产服务smoke通过 |
| credential-relay-e2e | 1组通过，直接401、Next转发401 |
| discovery-api-flow | 11组通过，31次成功发现响应，0页面异常 |
| live-api-smoke | 22组通过，含真实409、撤权、只读、导出和390px，0页面异常 |
| teacher-product-e2e | 4组通过，53人完整闭环、17人零发现/空报告、真实过期凭证 |
| 新合成准备脚本 | 实际创建新运行目录、数据库/权限、53/17工作簿；拒绝已有目录及相对目录，未打印凭证 |

PNG、已重开的导出文件和JSON结果位于本机交付目录。401/404/409是刻意验证的安全失败，
浏览器资源日志保留；未预期控制台错误为0，不声称所有请求都200。

## Windows Worker失败归因与修复

#63报告中的5秒失败是时间敏感测试夹具：spawn要先导入包含HTTP fixture的测试模块，
才执行os._exit(7)。原main与修复7bb在同一Python下均可复现；5秒时子进程仍活。
仅作诊断给30秒时约5.4—5.7秒退出7；同样函数放轻量模块后约1.5秒完成。
此前的隔离证据见本地质量基线核验c67aa59。本轮只移植两份测试文件，正式Worker不变。
5秒及所有安全/清理断言不变；本候选完整后端已通过，正式真实API浏览器流程也通过。
没有证据表明此夹具失败意味着正常正式发现接口存在相同稳定性故障。

## 非产品P0问题与限制

- 首次Windows工作副本CRLF使Prettier失败；用同一Git blob的LF副本复测通过，源码内容无改。
  干净启动文档使用仓库局部`core.autocrlf=false`，不改变用户全局配置。
- Playwright专用Chromium包下载遇CDN连接重置/DNS失败；实际浏览器验收使用已安装Chrome的
  Chromium内核。现场无需重新下载浏览器，但要提前完成依赖安装/构建。
- 桌面控制桥接的应用授权超时，未绕过；没有生成桌面录屏文件。独立产品窗口已经启动，
  可由组长手动操作/录屏；交付截图来自真实Chromium E2E，不是静态合成图片。
- 本地跳过为Windows无symlink权限；warning为既有Starlette/httpx弃用提示。
- 真实Qwen教师数据测试未进行，付费调用0；无新Agent工具、框架、架构、数据库迁移或公网部署。

#61原head CI 40368733942、#63原head CI 40405023967均已核对全绿。
这些是依赖各自head的证据，不能替代本轮补充后的候选CI；候选新CI及实际审核状态
由交付目录`VALIDATION.json`记录，未完成时明确pending/blocked。
