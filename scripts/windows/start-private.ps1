# StarIsle private desk. ASCII only. Binds to 127.0.0.1.
# Works on Windows PowerShell 5.1 and PowerShell 7.
# Python writes private-desk.log itself as UTF-8. Do not redirect native stderr.
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}

$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$api = Join-Path $repo "apps\api"
$python = Join-Path $api ".venv\Scripts\python.exe"
$dataRoot = $env:STAR_ISLE_DATA_ROOT
if (-not $dataRoot) { $dataRoot = "D:\StarIsleData" }
$logDir = Join-Path $dataRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "private-desk.log"
$utf8 = New-Object System.Text.UTF8Encoding $false

function Add-Utf8Line([string]$Path, [string]$Line) {
  [System.IO.File]::AppendAllText($Path, ($Line + "`n"), $utf8)
}

function Note([string]$Message) {
  Write-Host $Message
  Add-Utf8Line $log $Message
}

if (-not (Test-Path -LiteralPath $python)) {
  Note "VENV_MISSING"
  exit 2
}
$runtime = Join-Path $dataRoot "runtime.env"
if (-not (Test-Path -LiteralPath $runtime)) {
  Note "RUNTIME_ENV_MISSING"
  exit 2
}

Get-Content -LiteralPath $runtime -Encoding utf8 | ForEach-Object {
  $line = $_.Trim()
  if ($line -eq "" -or $line.StartsWith("#")) { return }
  $eq = $line.IndexOf("=")
  if ($eq -lt 1) { throw "RUNTIME_ENV_INVALID" }
  $name = $line.Substring(0, $eq).Trim()
  $value = $line.Substring($eq + 1)
  if ($name -notmatch '^[A-Z][A-Z0-9_]*$') { throw "RUNTIME_ENV_INVALID" }
  Set-Item -Path "Env:$name" -Value $value
}

if ($env:STAR_ISLE_BIND_HOST -ne "127.0.0.1") {
  Note "BIND_HOST_REFUSED"
  exit 2
}
if ($env:STAR_ISLE_BIND_PORT -notmatch '^[0-9]+$') {
  Note "PORT_INVALID"
  exit 2
}
$env:STAR_ISLE_PRIVATE_DESK_LOOPBACK_ONLY = "1"
$env:PYTHON_DOTENV_DISABLED = "1"
$env:PYTHONUTF8 = "1"
$env:STAR_ISLE_DESK_LOG = $log

Set-Location -LiteralPath $api
$savedPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $python -B -m app.daily_backup
$backupCode = $LASTEXITCODE
$ErrorActionPreference = $savedPreference
if ($backupCode -ne 0) { Note "BACKUP_FAILED" } else { Note "BACKUP_DONE" }

$live = "http://127.0.0.1:" + $env:STAR_ISLE_BIND_PORT + "/api/v1/health/live"
try {
  $resp = Invoke-WebRequest -UseBasicParsing -Uri $live -TimeoutSec 2
  if ($resp.StatusCode -eq 200) {
    Note "ALREADY_RUNNING"
    exit 0
  }
} catch {
  Note "STARTING"
}

Note "PRIVATE_DESK_URL http://127.0.0.1:$($env:STAR_ISLE_BIND_PORT)/private/login"
$ErrorActionPreference = "Continue"
& $python -B -m app.private_server
exit $LASTEXITCODE
