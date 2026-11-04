$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.build-env\Scripts\python.exe')) {
    python -m venv .build-env
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create build environment' }
}
& .build-env\Scripts\python.exe -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Cannot install build dependencies' }
& .build-env\Scripts\python.exe -m unittest -v test_engine test_controller
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
& .build-env\Scripts\python.exe -m PyInstaller --clean --noconfirm --onefile --windowed --collect-all customtkinter --name JournalDownloader --distpath release --icon app.ico --add-data 'app.ico;.' --add-data 'app.png;.' app.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
Write-Host 'Built: release\JournalDownloader.exe'
