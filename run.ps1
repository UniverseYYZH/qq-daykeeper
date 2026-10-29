$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
Set-Location $PSScriptRoot

$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) {
    & py -3 -m qq_signin @args
} else {
    & python -m qq_signin @args
}

if ($LASTEXITCODE -ne 0) {
    Read-Host "程序退出，按 Enter 关闭"
}
