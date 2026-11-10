# Optional. Register the private desk to start at sign-in for the current user.
# Skip this script to keep startup manual. No administrator rights. ASCII only.
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}

$repo = $PSScriptRoot
$repo = Split-Path (Split-Path $repo -Parent) -Parent
if ($args.Count -ge 1 -and $args[0]) { $repo = $args[0] }
$script = Join-Path $repo "scripts\windows\start-private.ps1"
if (-not (Test-Path -LiteralPath $script)) {
  Write-Host "START_SCRIPT_MISSING"
  exit 2
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ("-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"" + $script + "`"")
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "StarIslePrivate" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "LOGON_TASK_REGISTERED StarIslePrivate"
