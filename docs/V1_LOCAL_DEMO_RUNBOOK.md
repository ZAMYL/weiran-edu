# V1 本地合成测评与真实 HTTP 验收

对应 Issue #29。仅使用当前已合并 main 的 V1 双 Sheet、五维模板；不依赖 #32。
工具不会读老师 Excel、自动迁移数据库、签发凭证或调用 Qwen。管理员显式执行下列现有 CLI。
请使用本 PR 的独立 checkout，不切换其他成员正在开发的目录。需要 Python 3.12、uv。

## Windows PowerShell：一次可复制的本地启动

在本 PR **仓库根目录**打开 PowerShell。下面创建全新的系统临时子目录与合成专用来源；
不使用现有教师数据库。凭证只经进程内捕获保存在当前终端环境，不放命令行、文件或截图。

```powershell
$ErrorActionPreference = 'Stop'
$repo = (Get-Location).Path
$demo = Join-Path ([IO.Path]::GetTempPath()) ('weiran-edu-v1-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $demo | Out-Null
$env:PYTHONUTF8 = '1'
$env:WEIRAN_EDU_ENV = 'test'
$env:WEIRAN_EDU_DATABASE = Join-Path $demo 'synthetic.sqlite3'

Push-Location (Join-Path $repo 'apps/api')
try {
    uv sync --locked --group dev
    if ($LASTEXITCODE -ne 0) { throw '依赖安装失败' }
    uv run --locked python -m app.edu init
    if ($LASTEXITCODE -ne 0) { throw '数据库初始化失败' }
    $teacher = (uv run --locked python -m app.edu teacher-create --subject 'synthetic-demo-only' | ConvertFrom-Json).teacher_id
    $scope = (uv run --locked python -m app.edu scope-create --label '纯合成开发验收' | ConvertFrom-Json).data_scope_id
    if (!$teacher -or !$scope) { throw '试点账户/来源创建失败' }
    uv run --locked python -m app.edu member-set --teacher-id $teacher --scope-id $scope --can-import
    if ($LASTEXITCODE -ne 0) { throw '来源授权失败' }
    $env:WEIRAN_EDU_DEMO_TOKEN = (uv run --locked python -m app.edu token-issue --teacher-id $teacher --hours 1 | ConvertFrom-Json).access_token
    if (!$env:WEIRAN_EDU_DEMO_TOKEN) { throw '凭证签发失败' }
} finally { Pop-Location }

$python = Join-Path $repo 'apps/api/.venv/Scripts/python.exe'
$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$listener.Start()
$port = $listener.LocalEndpoint.Port
$listener.Stop()
$base = "http://127.0.0.1:$port"
$apiProcess = Start-Process -FilePath $python -WorkingDirectory (Join-Path $repo 'apps/api') -ArgumentList @('-m','uvicorn','app.main:create_app','--factory','--host','127.0.0.1','--port',"$port",'--no-access-log') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $demo 'server-out.log') -RedirectStandardError (Join-Path $demo 'server-error.log')
$ready = $false
for ($i = 0; $i -lt 100; $i++) {
    $apiProcess.Refresh()
    if ($apiProcess.HasExited) { throw '服务启动失败；在本机检查 server-error.log' }
    try {
        $health = Invoke-RestMethod "$base/api/v1/health"
        $ready = $true
        break
    } catch { Start-Sleep -Milliseconds 200 }
}
if (!$ready) { throw '本地服务未就绪' }
```

已有授权本地服务时可省略启动，显式设置 `$base`、`$scope`，让 smoke 隐藏提示输入本地 token；
也可用当前进程的 `WEIRAN_EDU_DEMO_TOKEN`。不要把 token 作为脚本参数。
smoke 只允许 `http://127.0.0.1[:端口]` 或 `http://[::1][:端口]`，禁用代理与重定向。

## 生成 Excel，交给林明

```powershell
& $python scripts/edu_demo_fixtures.py --students 53 --seed 42 --case valid --output-dir $demo
if ($LASTEXITCODE -ne 0) { throw '生成失败' }
& $python scripts/edu_demo_fixtures.py --students 53 --seed 42 --case warning --output-dir $demo
& $python scripts/edu_demo_fixtures.py --students 17 --seed 42 --case blocked --output-dir $demo
& $python scripts/edu_demo_fixtures.py --students 17 --seed 42 --case valid --output-dir $demo
& $python scripts/edu_demo_fixtures.py --students 105 --seed 713 --case valid --output-dir $demo
Get-ChildItem $demo -Filter '*.xlsx' | Select-Object Name
```

主联调文件是 **`$demo\edu-v1-valid-n53-seed42.xlsx`**。另给林明 warning 和 blocked 文件，
分别检查警告确认与整批阻断；不要传递数据库、凭证或日志。林明在自己的授权来源中上传这些文件，
前端真实浏览器流程和截图仍由 Issue #26 验收。

- valid：53 人、265 条评分，五维标题与依据合并区完全对应当前解析器。
- warning：同样的评分，`Sheet2!D3` 空评语，`D4/D5` 完全重复；共 3 条警告，确认前需复核。
- blocked：`Sheet2!C3=5.01` 越界、`C4=2.751` 超精度；整批不能入库，原值不舍入。
- 全部身份、评语、依据均为明确的合成占位。支持 1–1666 人（现有每表 10000 行预算）。
  评分显示两位小数；相同 seed/人数/场景的内容可重放，XLSX ZIP 时间戳不保证字节一致。
- 输出目录必须是已存在的系统临时子目录，拒绝仓库、相对路径、链接目录及覆盖已有文件。
  再次生成同名 Excel 会非零退出；需要重建时新建临时子目录，不删除或覆盖老师文件。

## 运行真实 HTTP smoke

```powershell
& $python scripts/edu_demo_smoke.py --base-url $base --scope-id $scope --students 53 --seed 42 --case valid --output-dir $demo
if ($LASTEXITCODE -ne 0) { throw '53 人真实 HTTP 验收失败' }
& $python scripts/edu_demo_smoke.py --base-url $base --scope-id $scope --students 17 --seed 42 --case valid --output-dir $demo
if ($LASTEXITCODE -ne 0) { throw '17 人真实 HTTP 验收失败' }
& $python scripts/edu_demo_smoke.py --base-url $base --scope-id $scope --students 105 --seed 713 --case valid --output-dir $demo
if ($LASTEXITCODE -ne 0) { throw '105 人真实 HTTP 验收失败' }
& $python scripts/edu_demo_smoke.py --base-url $base --scope-id $scope --students 53 --seed 42 --case warning --output-dir $demo
if ($LASTEXITCODE -ne 0) { throw 'warning 验收失败' }
& $python scripts/edu_demo_smoke.py --base-url $base --scope-id $scope --students 17 --seed 42 --case blocked --output-dir $demo
if ($LASTEXITCODE -ne 0) { throw 'blocked 验收失败' }
Get-ChildItem $demo -Filter 'v1-smoke-summary-*.json' | ForEach-Object {
    Get-Content -Raw $_.FullName | ConvertFrom-Json
} | Select-Object passed,case,students,seed,flow,failure_code,elapsed_ms
```

每次 smoke 自己重新生成上传字节，不读取任何现有 Excel。会留下合成预览/正式批次及一个
用于跨批次拒绝检查的 1 人合成批次，不自动清理或改动已有批次。
重复运行接受内容防重的 `duplicate` 和幂等 `replayed`，仍完整重验原批次和来源哈希。
摘要文件名带随机后缀，包含配置、步骤通过情况、状态/失败码、计数、耗时；不含 URL、令牌、
身份、匿名学生 ID、评分列表或评语。失败退出码 2；blocked 的成功含义仅是确认被正确拒绝，
`flow=blocked_rejected`，不宣称完成统计链路。

如何确认是真实结果：运行时 `/openapi.json` 必须有正式洞察和 records/query，`/me` 和 `/scopes`
必须验证本地凭证；预览上传、确认、统计、筛选、洞察、证据都实际经过 HTTP。
smoke 从生成时原始 Decimal 评分独立核验均值/中位数/分布、精确分母和 AND 匹配集合，
不会拿 statistics 返回值当正确答案。53 人 seed 42：`算法≤2.75 AND 策略≥4` 为 **5/53**，
`算法<2.75 AND 策略≥4` 为 **4/53**，等于 2.75 命中 **1/53**；正常零命中返回空集合。
改人数/seed 后结果必须随原始评分重算。单纯 health=200 或网页显示静态 Demo 不算通过。

正式洞察只核验现有评分候选；逐指标下钻检查实际参与者、所需评分维度和同批证据。
均值、2.75 频数、零来源指标、群体指标均取完整分页（含尾部空页）；其余频数核验值与来源计数。
全部学生五维证据核验到工作表/单元格和 Rubric，原文应保持 withheld/empty。
检查未认证 401、未授权来源 403、跨批次 404、错误预览版本/快照及未确认警告 409、非法参数 422。
不会自动测试撤销正在使用的真实教师凭证，也不替代既有后端权限/并发测试。

## 回归与关闭

```powershell
Push-Location apps/api
uv run --locked ruff check . ../../scripts/edu_demo_fixtures.py ../../scripts/edu_demo_smoke.py --config pyproject.toml
uv run --locked ruff format --check . ../../scripts/edu_demo_fixtures.py ../../scripts/edu_demo_smoke.py --config pyproject.toml
uv run --locked pytest tests/edu/test_demo_tools.py -q
uv run --locked pytest -q
uv run --locked python -m app.edu token-revoke --teacher-id $teacher
Pop-Location
Remove-Item Env:WEIRAN_EDU_DEMO_TOKEN
Stop-Process -Id $apiProcess.Id
```

离线测试使用 MockTransport/TestClient 适配器和合成临时数据库；CLI 没有该适配器，真实 HTTP
不在 CI 自动执行。GitHub CI 的后端作业会收集新测试，前端作业按既有配置运行，未修改依赖或 CI。
Windows 无符号链接权限时仅链接创建用例跳过，路径与覆盖拒绝用例仍执行。
请林明（AooMing）检查前端联调适配；彭仲楷（Peng-Kai789）检查 API 契约和权限/快照语义。
完整性诊断、报告下载、审核页面及真实学生/付费模型验收不在本任务范围。
