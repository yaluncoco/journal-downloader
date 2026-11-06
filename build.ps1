param([string]$ISCC = "")
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.build-env\Scripts\python.exe')) {
    python -m venv .build-env
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create build environment' }
}
& .build-env\Scripts\python.exe -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Cannot install build dependencies' }
if (-not $ISCC) {
    $foundCompiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($foundCompiler) { $ISCC = $foundCompiler.Source }
    else { $ISCC = "${env:ProgramFiles}\Inno Setup 7\ISCC.exe" }
}
& .build-env\Scripts\python.exe -X utf8 build_installer.py --iscc $ISCC
if ($LASTEXITCODE -ne 0) { throw 'Installer build failed' }
