$ErrorActionPreference = 'Stop'
$uv = 'C:\Keil_v5_543a\UV4\UV4.exe'
$project = Join-Path $PSScriptRoot 'project\MDK\TKX_STM32F407_PWM_Breathe.uvprojx'
$log = Join-Path $PSScriptRoot 'project\MDK\build.log'
if (-not (Test-Path -LiteralPath $uv)) { throw "Keil executable missing: $uv" }
$arguments = '-r "{0}" -t "TKX_STM32F407_PWM_Breathe" -o "{1}"' -f $project,$log
$build = Start-Process -FilePath $uv -ArgumentList $arguments -WindowStyle Hidden -PassThru -Wait
Get-Content -LiteralPath $log
if ($build.ExitCode -ne 0) { throw "Keil build failed: $($build.ExitCode)" }
if ((Get-Content -LiteralPath $log -Raw) -notmatch '0 Error\(s\), 0 Warning\(s\)') { throw 'Build did not finish cleanly.' }
