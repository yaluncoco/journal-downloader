$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.build-env\Scripts\python.exe')) {
    python -m venv .build-env
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create build environment' }
}
& .build-env\Scripts\python.exe -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Cannot install build dependencies' }
& .build-env\Scripts\python.exe -m unittest -v test_engine test_controller test_pdf test_updater test_storage test_catalog test_inventory test_pipeline
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
& .build-env\Scripts\python.exe -m PyInstaller --clean --noconfirm --onefile --windowed --collect-all customtkinter --name JournalDownloader --distpath release --icon app.ico --add-data 'app.ico;.' --add-data 'app.png;.' --add-data 'journals.json;.' --add-data 'journal_metadata.json;.' --add-data 'DATA_SOURCES.md;.' --add-data 'data-licenses;data-licenses' app.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
Copy-Item -LiteralPath '使用说明.md', 'DATA_SOURCES.md' -Destination 'release'
Copy-Item -LiteralPath 'data-licenses' -Destination 'release' -Recurse -Force
Write-Host 'Built: release\JournalDownloader.exe'
