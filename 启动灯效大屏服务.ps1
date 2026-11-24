$ErrorActionPreference = 'Stop'
$dashboardPython = Join-Path $env:USERPROFILE 'Documents\ChatGPT\示波器电赛准备\work\dap-diagnostic-venv\Scripts\python.exe'
$dashboardStarter = Join-Path $PSScriptRoot 'start_dashboard.py'
if (-not (Test-Path -LiteralPath $dashboardPython)) { throw 'Python runtime missing' }
& $dashboardPython $dashboardStarter
if ($LASTEXITCODE -ne 0) { throw 'Dashboard service startup failed' }
