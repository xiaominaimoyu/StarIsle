# Stop the private desk started by start-private.ps1. ASCII only.
# Optional logon task is not removed here.
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}

$dataRoot = $env:STAR_ISLE_DATA_ROOT
if (-not $dataRoot) { $dataRoot = "D:\StarIsleData" }
$pidFile = Join-Path $dataRoot "logs\private-desk.pid"
if (-not (Test-Path -LiteralPath $pidFile)) {
  Write-Host "NOT_RUNNING"
  exit 0
}
$procText = ([System.IO.File]::ReadAllText($pidFile)).Trim()
if ($procText -notmatch '^[0-9]+$') {
  Write-Host "PID_INVALID"
  exit 2
}
$procId = [int]$procText
$cim = Get-CimInstance -ClassName Win32_Process -Filter ("ProcessId=" + $procId) -ErrorAction SilentlyContinue
if (-not $cim) {
  Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
  Write-Host "NOT_RUNNING"
  exit 0
}
$command = [string]$cim.CommandLine
if ($command -notlike "*app.private_server*") {
  Write-Host "PID_NOT_DESK"
  exit 2
}
Stop-Process -Id $procId -Force
Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
Write-Host "STOPPED"
exit 0
