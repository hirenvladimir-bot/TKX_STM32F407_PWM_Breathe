$ErrorActionPreference = 'Stop'
$webPython = Join-Path $env:USERPROFILE 'Documents\ChatGPT\示波器电赛准备\work\dap-diagnostic-venv\Scripts\python.exe'
$webStarter = Join-Path $PSScriptRoot 'start_web_control.py'
if (-not (Test-Path -LiteralPath $webPython)) { throw 'Python runtime missing' }
& $webPython $webStarter
if ($LASTEXITCODE -ne 0) { throw 'Serial web service startup failed' }
