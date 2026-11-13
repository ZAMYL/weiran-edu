# 教师账号登录接口交接

首页登录弹窗和独立 `/login` 共用表单。未登录时，上传、历史测评、画像探索和报告导航先打开弹窗。验证成功后继续刚才的操作，关闭弹窗则丢弃该操作。访问凭据仍可用于现有试点会话。

以下是前端为联调预留的契约，后端实现及审核由彭仲楷负责。默认关闭正式账号登录。前端不负责签发身份、保存密码、分配权限或续期。

## 后端接口

| 方法和路径                     | 请求                                             | 成功响应                                                       |
| ------------------------------ | ------------------------------------------------ | -------------------------------------------------------------- |
| `POST /api/v1/edu/auth/login`  | JSON `{ "account": "...", "password": "..." }`   | HTTP 200，JSON `{ "access_token": "...", "expires_in": 3600 }` |
| `GET /api/v1/edu/me`           | `Authorization: Bearer <access_token>`           | 复用现有 HTTP 200，JSON `{ "teacher_id": "..." }`              |
| `POST /api/v1/edu/auth/logout` | `Authorization: Bearer <access_token>`，无请求体 | HTTP 204，撤销本次会话令牌。已失效可返回 401，前端按已退出处理 |

- `account` 为去除首尾空白后的 1–128 个字符。密码为 1–256 个字符，保留原样。当前前端按 JavaScript 字符串长度限制。请确认是否需要按 Unicode 码点或字节统一长度。
- `access_token` 沿用现有 EDU API Bearer 凭据格式，20–200 个 ASCII 字母、数字、下划线或连字符。需要唯一会话标识，注销当前会话不影响同教师其他独立会话。
- `expires_in` 为正整数秒数，必须对应服务端可执行的失效时间。前端 Cookie 最长保留 3600 秒，不自动续期。签发的令牌必须可被现有 `/me`、`/scopes`、`/batches` 和其他 EDU API 校验。
- 错误沿用 `{ "error": { "code": "...", "request_id": "req_..." } }` 及 `X-Request-ID`。无效账号、密码或停用账户统一返回 401，避免区分账户存在性。输入不合法返回 422，限流返回 429，临时不可用返回 503。已登录但无资源权限继续走现有 403/404。
- 后端负责凭据验证、密码安全存储、登录限流、过期、撤销和审计。请求或日志不得记录密码或完整令牌，失败响应不回显输入。

## 浏览器和 Next 接口

- 同源 `POST /api/teacher-session` 接收 `account`、`password`。Next 调用后端登录后，再用同一令牌查询 `/me`。验证通过才设置 Cookie，响应仅包含 `teacher_id`。
- Cookie 为 `HttpOnly; SameSite=Strict; Path=/api`。正式会话需要 HTTPS 并设置 `Secure`。浏览器不读取令牌。既有 EDU 代理仅把指定 Cookie 中的有效令牌转换为后端 Bearer，不转发其他 Cookie。账号会话不开放隔离 AI 路由。
- 刷新时用 `/me`、`/scopes`、`/batches` 恢复工作台。窗口聚焦、恢复可见、可见期间每 60 秒调用 `/me` 检查会话，不延长有效期。收到 401 清除身份、受保护页面数据和当前会话 Cookie。短暂网络故障不视为撤权。
- 同源 `DELETE /api/teacher-session` 转发后端注销。成功或令牌已失效时过期 Cookie，失败时隐藏工作台并保留重试入口。临时访问凭据模式依旧只在页面内存中保留，刷新不恢复。
- 两个同源写入端点以及 Cookie 授权的业务写入要求浏览器 `Origin` 与当前站点相同。生产部署需确保 Next 收到正确的 HTTPS 请求协议与 Host。Next 至 FastAPI 使用 HTTPS 或本机回环地址。
- 后端交付并完成安全与浏览器验收后，由部署方设置 `EDU_ACCOUNT_LOGIN_ENABLED=1`。未启用时返回 503，现有凭据登录仍可使用。

## 隔离测试账号

`EDU_ACCOUNT_TEST_MODE=1` 仅在浏览器与后端都使用回环地址时生效。测试脚本为临时 SQLite 新建合成教师，签发真实 FastAPI 凭据，把测试账号、密码和凭据存入仓库外临时文件，由 `EDU_ACCOUNT_TEST_FILE` 指定。测试账号使用不同的 Cookie 名，非回环访问不读取它。测试退出清除本地 Cookie，服务端令牌撤权由测试脚本通过现有管理员命令验证。

生产构建后，在 `apps/web` 运行 `node tests/teacher-login-e2e.mjs`，通过 `EDU_TEST_PYTHON` 指向已有后端 Python 环境。脚本覆盖登录失败、弹窗登录继续上传、Cookie、刷新、退出、独立页登录和真实撤权。后端正式接口交付后需在关闭测试模式的情况下复验同一流程。
