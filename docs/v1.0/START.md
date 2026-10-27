# 干净环境启动（Windows PowerShell）

前提：Git、Python 3.12、uv 0.11.14、Node.js 24、pnpm 11、Chrome/Edge等Chromium浏览器。
所有依赖以仓库锁文件为准；比赛前完成安装和构建，现场不需要Qwen或联网推理。
此阶段使用待审候选分支，审核合并前不要把它当作 main。

## 1. 获取、安装、构建

```powershell
git clone --config core.autocrlf=false --branch codex/edu-v1-release-validation `
  https://github.com/ZAMYL/weiran-edu.git weiran-edu-v1
Set-Location weiran-edu-v1
$repo = (Get-Location).Path
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:NEXT_TELEMETRY_DISABLED = '1'
uv sync --directory apps/api --locked --group dev
Push-Location apps/web
pnpm install --frozen-lockfile
pnpm build
Pop-Location
```

使用仓库局部 LF 配置，避免 Windows CRLF 导致 Prettier 检查失败；不修改全局Git设置。
本轮从新工作区实际安装了28个Python包和444个前端包，并用默认Turbopack完成生产构建，
没有复用指向其他成员项目外的 node_modules 联接。

## 2. 明确准备一次性合成运行环境

```powershell
$py = (Resolve-Path apps/api/.venv/Scripts/python.exe).Path
$prepared = & $py scripts/prepare_v1_local_demo.py | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw '合成环境初始化失败' }
$demo = $prepared.runtime_dir
$cfg = Get-Content (Join-Path $demo 'runtime.json') -Raw | ConvertFrom-Json
$demo
```

这个显式管理员命令只创建新的系统临时子目录，调用已有迁移/教师/来源/令牌CLI，
生成 `contrast.xlsx`（53人，26/27群体）和 `constant.xlsx`（17人同分）。
不会读取或覆盖现有Excel/教师数据库。令牌有效24小时，只写在临时服务端文件，命令不打印。
不要输出、截图、复制或提交该凭证文件；不要使用真实教师数据。

## 3. 第一个终端启动真实FastAPI

仍在仓库根目录、同一PowerShell：

```powershell
$env:WEIRAN_EDU_ENV = 'test'
$env:WEIRAN_EDU_DATABASE = $cfg.database
Push-Location apps/api
& $py -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

只监听本机；数据库是刚生成的临时SQLite。Ctrl+C停止。
健康检查为 `http://127.0.0.1:8000/api/v1/health`，运行时契约为 `/openapi.json`。

## 4. 第二个终端启动生产Next.js

把 `$repo` 和 `$demo` 换成第一个终端的仓库路径和**公开的运行目录路径**，无需复制凭证：

```powershell
$repo = 'D:/path/to/weiran-edu-v1'
$demo = 'D:/Temp/weiran-v1-demo-这里替换为刚才输出的目录'
$cfg = Get-Content (Join-Path $demo 'runtime.json') -Raw | ConvertFrom-Json
$env:EDU_API_ORIGIN = 'http://127.0.0.1:8000'
$env:EDU_TEST_MODE = '1'
$env:EDU_TEST_SESSION_FILE = $cfg.session_file
$env:NEXT_TELEMETRY_DISABLED = '1'
Set-Location (Join-Path $repo 'apps/web')
pnpm start --port 3000
```

打开 `http://127.0.0.1:3000`，保留“正式数据”模式，页面会建立本机合成教师会话。
本机适配器只在双端loopback且显式启用时工作，业务请求仍真实经过Next→FastAPI→SQLite。
不是Mock响应，也不是把令牌注入NEXT_PUBLIC、URL或浏览器长期存储。
若端口已占用，选择其他空闲端口并同步修改 `EDU_API_ORIGIN` 和浏览器地址，不终止他人服务。

## 5. 验收及关闭

上传 `$demo/contrast.xlsx`；第二轮上传 `$demo/constant.xlsx`。名称、课程、日期可以不补充，
不能推断班级、学校或比赛名称。请按 [演示流程](DEMO.md) 完成操作。
启动后不再对同一 `.next` 目录执行构建；需要重建时先停止本次Next。

结束后在两个服务终端Ctrl+C；需要立即撤销本次合成权限时，停止服务后执行：

```powershell
Set-Location (Join-Path $repo 'apps/api')
& $py -m app.edu --database $cfg.database token-revoke --teacher-id $cfg.teacher_id
```

临时目录不提交、不分享；不要使用含凭证、数据库或原始工作簿的整个目录作为评委材料。
每次重新准备会生成独立目录，不覆盖旧目录。录屏前确认只打开本次合成浏览器窗口。
