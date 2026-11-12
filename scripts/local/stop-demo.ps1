# 停止未然 EDU 本机演示（清理启动器与所有遗留进程）
# 按命令行特征精确匹配，不误杀无关进程。
$patterns = @('node_modules/next/dist/bin/next start', 'prototypes.durable_teacher_agent.serve')
$killed = 0
foreach ($pattern in $patterns) {
  $procs = Get-CimInstance Win32_Process -Filter "Name='node.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*$pattern*" }
  foreach ($proc in $procs) {
    if (Get-Process -Id $proc.ProcessId -ErrorAction SilentlyContinue) {
      try { & taskkill /PID $proc.ProcessId /T /F 2>$null | Out-Null } catch { }
      Write-Host "已停止：PID $($proc.ProcessId)（$pattern）"
      $killed = $killed + 1
    }
  }
}
$Private = Join-Path $env:LOCALAPPDATA 'weiran-edu-ai-integration'
foreach ($name in @('demo-web.pid', 'demo-host.pid')) {
  Remove-Item (Join-Path $Private $name) -ErrorAction SilentlyContinue
}
if ($killed -gt 0) {
  Write-Host "共停止 $killed 个演示进程。"
} else {
  Write-Host '没有发现运行中的演示进程。'
}
Write-Host '说明：直接强杀不会触发宿主的撤销清理；合成凭证最长 1 小时后自动过期，'
Write-Host '临时目录位于 %LOCALAPPDATA%\weiran-edu-ai-integration\handoff-*，可手动删除。'
