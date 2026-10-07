$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$environmentPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $environmentPython)) {
    & python -m venv (Join-Path $projectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Nie udało się przygotować środowiska Python.' }
}
& $environmentPython -m pip install -r (Join-Path $projectRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Nie udało się zainstalować zależności GameDock.' }
Write-Host 'GameDock jest gotowy. Otwórz GameDock.vbs.'
