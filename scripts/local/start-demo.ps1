# 未然 EDU 本机完整演示启动器（UTF-8 with BOM，兼容 Windows PowerShell 5.1）
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\local\start-demo.ps1 -Identity first
#   powershell -ExecutionPolicy Bypass -File scripts\local\start-demo.ps1 -Identity sample
# 前置（首次使用）：
#   cd apps\web
#   corepack pnpm install --frozen-lockfile
#   corepack pnpm build
#   cd ..\..\apps\api
#   uv sync --locked --group dev
# Identity=first  ：空数据教师（0 个批次），用于验证真实首次上传流程。
# Identity=sample ：合成样例教师（6 个样例批次），用于体验 AI 群体画像与报告。
# 全程真实 HTTP + SQLite 合成数据，不调用真实 Qwen；仅绑定本机回环；Ctrl+C 停止。
param(
  [ValidateSet('first', 'sample')]
  [string]$Identity = 'first',
  [int]$Port = 3000,
  [int]$HostSeconds = 3600
)
$ErrorActionPreference = 'Stop'
$Repo    = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Web     = Join-Path $Repo 'apps\web'
$Api     = Join-Path $Repo 'apps\api'
$Python  = Join-Path $Api '.venv\Scripts\python.exe'
$Private = Join-Path $env:LOCALAPPDATA 'weiran-edu-ai-integration'
New-Item -ItemType Directory -Force -Path $Private | Out-Null
$hostProc = $null
$webProc  = $null

function Stop-Tree([int]$ProcId) {
  if ($ProcId -and (Get-Process -Id $ProcId -ErrorAction SilentlyContinue)) {
    try { & taskkill /PID $ProcId /T /F 2>$null | Out-Null } catch { }
  }
}

function Stop-DemoProcesses {
  $patterns = @('node_modules/next/dist/bin/next start', 'prototypes.durable_teacher_agent.serve')
  $killed = 0
  foreach ($pattern in $patterns) {
    $procs = Get-CimInstance Win32_Process -Filter "Name='node.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
      Where-Object { $_.CommandLine -like "*$pattern*" }
    foreach ($proc in $procs) {
      if (Get-Process -Id $proc.ProcessId -ErrorAction SilentlyContinue) {
        try { & taskkill /PID $proc.ProcessId /T /F 2>$null | Out-Null } catch { }
        $killed = $killed + 1
      }
    }
  }
  return $killed
}

$stale = Stop-DemoProcesses
if ($stale -gt 0) { Write-Host "已清理 $stale 个遗留演示进程。" -ForegroundColor Yellow }

try {
  # ---------- 1. 启动合成后端宿主（含空教师与样例教师两种身份） ----------
  if (-not (Test-Path $Python)) {
    throw "缺少后端锁定环境：$Python（先执行 uv sync --locked --group dev）"
  }
  if (-not (Test-Path (Join-Path $Web '.next\BUILD_ID'))) {
    throw "缺少前端生产构建：apps\web\.next（先执行 corepack pnpm build）"
  }
  $env:PYTHONPATH = '.;apps/api'
  $env:PYTHONUTF8 = '1'
  $connectionPath = $null
  for ($hostAttempt = 1; $hostAttempt -le 3 -and -not $connectionPath; $hostAttempt++) {
    $hostOut = Join-Path $Private ('host-startup-' + $hostAttempt + '.log')
    $hostErr = Join-Path $Private ('host-startup-' + $hostAttempt + '.err.log')
    Remove-Item $hostOut, $hostErr -ErrorAction SilentlyContinue
    $hostParams = @{
      FilePath               = $Python
      ArgumentList           = @('-m', 'prototypes.durable_teacher_agent.serve', '--seconds', "$HostSeconds")
      RedirectStandardOutput = $hostOut
      RedirectStandardError  = $hostErr
      WorkingDirectory       = $Repo
      PassThru               = $true
      WindowStyle            = 'Hidden'
    }
    $hostProc = Start-Process @hostParams
    Set-Content -LiteralPath (Join-Path $Private 'demo-host.pid') -Value $hostProc.Id
    Write-Host ('正在启动合成后端宿主（真实 HTTP + SQLite）... 第 ' + $hostAttempt + ' 次尝试') -ForegroundColor Cyan
    $deadline = (Get-Date).AddSeconds(300)
    while ((Get-Date) -lt $deadline -and -not $connectionPath) {
      Start-Sleep -Milliseconds 500
      if ($hostProc.HasExited) { break }
      if (Test-Path $hostOut) {
        $line = Get-Content $hostOut | Where-Object { $_ -match '"ready": true' } | Select-Object -Last 1
        if ($line) { $connectionPath = ($line | ConvertFrom-Json).private_connection_file }
      }
    }
    if ($connectionPath) { break }
    if ($hostProc -and -not $hostProc.HasExited) { Stop-Tree $hostProc.Id }
    if ($hostAttempt -eq 3) {
      $tail = Get-Content $hostErr -Tail 20 -ErrorAction SilentlyContinue
      throw "合成后端宿主多次启动均未成功，请查看日志：$hostErr；末尾内容：$tail"
    }
    Write-Host '合成后端宿主本次启动未成功（解析子进程可能超时或引导较慢），自动重试...' -ForegroundColor Yellow
  }
  if (-not $connectionPath) { throw '合成后端宿主启动超时，请查看日志：' + $hostOut }
  $connection  = Get-Content -Raw -Encoding UTF8 -LiteralPath $connectionPath | ConvertFrom-Json
  $sessionFile = Join-Path (Split-Path $connectionPath) 'web-session.private.json'
  if ($Identity -eq 'sample') {
    $token = $connection.bearer_token
  } else {
    $token = $connection.first_use_token
    $samplePath = $connection.first_use_sample_path
    if (-not $samplePath -or -not (Test-Path -LiteralPath $samplePath -PathType Leaf)) {
      throw '后端宿主未提供本次登记的首次上传样例。'
    }
  }
  $sessionJson = @{ synthetic_only = $true; access_token = $token } | ConvertTo-Json
  [System.IO.File]::WriteAllText($sessionFile, $sessionJson, (New-Object System.Text.UTF8Encoding($false)))
  Write-Host "合成后端宿主就绪：$connectionPath" -ForegroundColor Green
  if ($Identity -eq 'sample') { $identityLabel = '合成样例教师' } else { $identityLabel = '空数据教师' }
  Write-Host "当前身份：$identityLabel" -ForegroundColor Green

  # ---------- 2. 启动 Next.js 生产服务 ----------
  $env:EDU_TEST_MODE           = '1'
  $env:EDU_AI_SYNTHETIC_MODE   = '1'
  $env:EDU_TEST_SESSION_FILE   = $sessionFile
  $env:EDU_API_ORIGIN          = $connection.api_origin
  $env:NEXT_TELEMETRY_DISABLED = '1'
  $webParams = @{
    FilePath               = 'node'
    ArgumentList           = @('node_modules/next/dist/bin/next', 'start', '--hostname', '127.0.0.1', '--port', "$Port")
    RedirectStandardOutput = Join-Path $Private 'web-startup.log'
    RedirectStandardError  = Join-Path $Private 'web-startup.err.log'
    WorkingDirectory       = $Web
    PassThru               = $true
    WindowStyle            = 'Hidden'
  }
  $webProc = Start-Process @webParams
  Set-Content -LiteralPath (Join-Path $Private 'demo-web.pid') -Value $webProc.Id

  Write-Host "正在启动 Next.js（127.0.0.1:$Port）..." -ForegroundColor Cyan
  $ready = $false
  $deadline = (Get-Date).AddSeconds(60)
  while ((Get-Date) -lt $deadline -and -not $ready) {
    Start-Sleep -Milliseconds 500
    if ($webProc.HasExited) {
      $tail = Get-Content (Join-Path $Private 'web-startup.err.log') -Tail 20 -ErrorAction SilentlyContinue
      throw "前端服务提前退出，请查看日志：$Private\web-startup.err.log；末尾内容：$tail"
    }
    try {
      $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
      if ($resp.StatusCode -eq 200) { $ready = $true }
    } catch { }
  }
  if (-not $ready) { throw "前端服务在 $Port 端口未就绪" }

  # ---------- 3. 校验账户/批次接口，全部 200 且批次数符合身份才算成功 ----------
  $failed = $null
  $batchTotal = $null
  foreach ($ep in @('test-session', 'me', 'scopes', 'batches')) {
    try {
      $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/edu/$ep" -UseBasicParsing -TimeoutSec 8 -ErrorAction Stop
      if ($resp.StatusCode -ne 200) { $failed = "$ep -> HTTP $($resp.StatusCode)"; break }
      if ($ep -eq 'batches') { $batchTotal = ($resp.Content | ConvertFrom-Json).total_count }
    } catch {
      $failed = "$ep -> 请求失败（$($_.Exception.Message)）"
      break
    }
  }
  if ($failed) {
    throw "账户/批次接口未就绪（$failed）。会话文件：$sessionFile；请运行 stop-demo.ps1 清理遗留进程后重试。"
  }
  if ($Identity -eq 'first' -and $batchTotal -ne 0) {
    throw "空数据教师应有 0 个批次，实际 $batchTotal；身份或会话配置异常。"
  }
  if ($Identity -eq 'sample' -and $batchTotal -lt 1) {
    throw "合成样例教师应有样例批次，实际 $batchTotal；身份或会话配置异常。"
  }
  Write-Host "账户/批次接口校验通过（test-session / me / scopes / batches = 200，批次 $batchTotal）" -ForegroundColor Green

  # ---------- 4. 打开浏览器并保持运行 ----------
  $demo = "http://127.0.0.1:$Port/insights"
  Write-Host ''
  Write-Host '未然 EDU 本机演示已启动' -ForegroundColor Green
  Write-Host '  学情诊断: ' -NoNewline; Write-Host $demo -ForegroundColor Yellow
  Write-Host '  画像探索 / 测评数据 / 报告 : /explorer  /data  /reports' -ForegroundColor DarkGray
  if ($Identity -eq 'first') {
    Write-Host '  首次上传请在文件选择框中打开本次登记的样例：' -ForegroundColor DarkGray
    Write-Host "  $samplePath" -ForegroundColor Yellow
  } else {
    Write-Host '  样例身份：批次选择器已包含 6 个合成样例，可直接体验 AI 分析。' -ForegroundColor DarkGray
  }
  Write-Host "  合成宿主 $HostSeconds 秒后自动撤销凭证并清理；Ctrl+C 可提前停止。" -ForegroundColor DarkGray
  try {
    Start-Process "http://127.0.0.1:$Port/insights"
  } catch {
    Write-Host '未能自动打开浏览器，请手动访问上面的地址。'
  }

  while ($true) {
    Start-Sleep -Seconds 2
    if ($hostProc -and $hostProc.HasExited) {
      Write-Host '合成后端宿主已停止（达到时限或异常），正在关闭前端服务...' -ForegroundColor Yellow
      break
    }
  }
} finally {
  Write-Host '正在停止...'
  if ($webProc)  { Stop-Tree $webProc.Id }
  if ($hostProc) { Stop-Tree $hostProc.Id }
  Remove-Item (Join-Path $Private 'demo-web.pid'), (Join-Path $Private 'demo-host.pid') -ErrorAction SilentlyContinue
  Write-Host '已停止（宿主凭证最长 1 小时后过期）'
}
