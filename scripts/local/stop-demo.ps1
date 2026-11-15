# Stop only the exact processes recorded by an explicitly selected launcher session.
param([Parameter(Mandatory=$true)][string]$SessionDirectory)
$ErrorActionPreference = 'Stop'
$allowed = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'weiran-edu-ai-integration')) + [IO.Path]::DirectorySeparatorChar
$session = [IO.Path]::GetFullPath($SessionDirectory)
if (-not $session.StartsWith($allowed, [StringComparison]::OrdinalIgnoreCase)) { throw 'Session directory is outside the launcher state root.' }
foreach ($name in @('demo-web.owner.json', 'demo-host.owner.json')) {
  $record = Join-Path $session $name
  if (-not (Test-Path -LiteralPath $record)) { continue }
  $owner = Get-Content -Raw -LiteralPath $record | ConvertFrom-Json
  $process = Get-Process -Id $owner.ProcessId -ErrorAction SilentlyContinue
  if ($process -and $process.StartTime.ToUniversalTime().Ticks -eq $owner.StartedUtcTicks) {
    & taskkill /PID $process.Id /T /F | Out-Null
    Write-Host "Stopped owned process $($process.Id)."
  }
}
