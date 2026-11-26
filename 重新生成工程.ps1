param([switch]$Build)
$ErrorActionPreference = 'Stop'
$mxRoot = Join-Path $env:LOCALAPPDATA 'Programs\STM32CubeMX'
$java = Join-Path $mxRoot 'jre\bin\java.exe'
$mx = Join-Path $mxRoot 'STM32CubeMX.exe'
if (-not (Test-Path -LiteralPath $mx)) { throw "CubeMX not found: $mx" }
$ioc = Join-Path $PSScriptRoot 'TKX_STM32F407_PWM_Breathe.ioc'
$script = Join-Path $env:TEMP ('tkx-cubemx-' + [guid]::NewGuid().ToString('N') + '.txt')
$encoding = [Text.Encoding]::GetEncoding([Globalization.CultureInfo]::CurrentCulture.TextInfo.ANSICodePage)
[IO.File]::WriteAllLines($script,@(('config load "' + $ioc + '"'),'project generate','exit'),$encoding)
$log = Join-Path $PSScriptRoot 'MDK-ARM\cubemx-generate.log'
$err = Join-Path $PSScriptRoot 'MDK-ARM\cubemx-generate.err'
try {
    $args = @('--add-opens','java.desktop/java.awt=ALL-UNNAMED','--add-exports','java.desktop/sun.awt=ALL-UNNAMED','-jar',('"'+$mx+'"'),'-q',('"'+$script+'"'))
    $process = Start-Process -FilePath $java -ArgumentList $args -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError $err -PassThru -Wait
    if ($process.ExitCode -ne 0) { throw "CubeMX failed; see $log and $err" }
    $text = Get-Content -LiteralPath $log -Raw
    if ($text -notmatch 'Time for Generating toolchain IDE Files' -or $text -notmatch 'Bye bye') { throw "CubeMX generation not confirmed; see $log" }
    $main = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'Core\Src\main.c') -Raw
    if ($main -notmatch 'Lamp_Init\(&htim3, &huart1\)' -or $main -notmatch 'Lamp_Task\(\)') { throw 'USER CODE application hooks missing' }
    Write-Output 'CubeMX generation complete; USER CODE hooks retained.'
} finally { if (Test-Path -LiteralPath $script) { Remove-Item -LiteralPath $script } }
if ($Build) { & (Join-Path $PSScriptRoot 'build.ps1') }
