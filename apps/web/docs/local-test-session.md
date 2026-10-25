# 本机联调：打开页面直接测试

测试阶段可集中使用一个合成教师，打开页面及刷新时自动连接，无需填写或反复粘贴 Token。
真实上传、预览、确认、批次、统计、群体、洞察与证据接口保持原有业务行为。

## 配置一次

1. 后端按已有 CLI 初始化仓库外的测试数据库，创建合成教师与来源，赋予导入权限并签发临时凭证。
2. 在仓库外创建私有 JSON 文件，字段为 `synthetic_only: true` 和 `access_token`。值来自该合成教师，文件不得加入 Git。
3. 在启动 Next.js 的终端设置下面的服务端变量，然后运行 `pnpm dev`，或构建后运行 `pnpm start`。

```powershell
$env:EDU_API_ORIGIN = 'http://127.0.0.1:8000'
$env:EDU_TEST_MODE = '1'
$env:EDU_TEST_SESSION_FILE = '<私有测试会话 JSON 的绝对路径>'
pnpm dev
```

页面出现“联调测试”标记，自动读取测试教师的来源及批次。刷新或从 Demo 返回会重新连接；接口故障不会转成 Demo 或假数据。
凭证只由 Next 服务端读取并添加到上游请求，浏览器不会获取该文件、Token 或 Authorization 头。
临时凭证过期时由维护联调环境的人更新这一个私有文件，再点“重新连接测试数据”，不用逐个页面修改。

## 现有鉴权放在哪里

| 位置                                      | 职责                                                  |
| ----------------------------------------- | ----------------------------------------------------- |
| `apps/api/app/edu/auth.py` 与原有鉴权依赖 | 原有教师身份、来源和批次权限；完整保留                |
| `src/server/test-session.ts`              | 集中的本机测试会话适配，只有服务端读取私有配置        |
| `src/app/api/edu/[...path]/route.ts`      | 沿用原 API 白名单和同源限制，测试模式自动带上测试教师 |
| `src/app/data-mode.tsx`                   | 自动连接；常规手动教师会话仍保留                      |

不设置 `EDU_TEST_MODE=1` 即恢复原有手动会话。开关在运行时读取，无需改业务代码或数据库迁移。
测试会话只用于本机回环地址：页面及上游都必须为 localhost、127.0.0.1 或 ::1，启动仍绑定127.0.0.1。
配置丢失、格式错误或上游拒绝时返回真实错误；明确传入的其他教师凭证不会被测试教师覆盖。
没有新增后端 HTTP 操作，仍为30个；新增的 `/api/edu/test-session` 是 Next 内部状态入口，只返回 enabled 布尔值。

## 验收

常规 `pnpm lint`、`pnpm format:check`、`pnpm typecheck`、`pnpm build`、`pnpm test`。
真实浏览器脚本使用临时合成数据库及教师，并在结束时清理服务和私有会话文件：

```powershell
$env:EDU_TEST_PYTHON = '<锁定后端环境的 python.exe 绝对路径>'
node tests/live-api-smoke.mjs
$env:EDU_BROWSER_TEST_SESSION = '1'
node tests/live-api-smoke.mjs
```

两种模式均检查真实上传和统计；测试模式额外检查自动连接、刷新恢复、Demo 返回、服务端撤销及浏览器无凭证。
