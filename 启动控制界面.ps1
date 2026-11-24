param([string]$Port = 'COM16', [switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$panelScript = Join-Path $PSScriptRoot 'control_panel.py'
$candidates = @(
    (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
    (Join-Path $env:USERPROFILE 'Documents\ChatGPT\示波器电赛准备\work\dap-diagnostic-venv\Scripts\python.exe')
)
$found = Get-Command python.exe -ErrorAction SilentlyContinue
if ($found) { $candidates += $found.Source }
$panelPython = $null
foreach ($candidate in ($candidates | Select-Object -Unique)) {
    if (-not (Test-Path -LiteralPath $candidate)) { continue }
    & $candidate -c 'import tkinter, serial' 2>$null
    if ($LASTEXITCODE -eq 0) { $panelPython = $candidate; break }
}
if (-not $panelPython) { throw 'No Python with tkinter and pyserial found. Install Python and run: python -m pip install pyserial' }
if (-not (Test-Path -LiteralPath $panelScript)) { throw "Missing script: $panelScript" }
if ($CheckOnly) {
    & $panelPython -c 'import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8-sig")); print("Python dependencies and script syntax OK")' $panelScript
    if ($LASTEXITCODE -ne 0) { throw 'Script validation failed' }
    Write-Output "Project: $PSScriptRoot"
    Write-Output "Python: $panelPython"
    return
}
$windowPython = Join-Path (Split-Path $panelPython) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $windowPython)) { $windowPython = $panelPython }
Start-Process -FilePath $windowPython -ArgumentList @(('"' + $panelScript + '"'),'--port',$Port,'--connect') -WorkingDirectory $PSScriptRoot
