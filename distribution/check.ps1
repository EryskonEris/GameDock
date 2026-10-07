$ErrorActionPreference = 'Stop'
try {
    $process = Start-Process -FilePath (Join-Path $PSScriptRoot 'GameDock.exe') -ArgumentList '--verify-package' -WindowStyle Hidden -Wait -PassThru
    $report = Join-Path $PSScriptRoot 'logs\sprawdzenie.txt'
    if (Test-Path -LiteralPath $report) {
        Get-Content -LiteralPath $report -Encoding UTF8
    } else {
        Write-Host 'Nie udało się uruchomić diagnostyki. Sprawdź, czy wypakowano cały ZIP.'
    }
    exit $process.ExitCode
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
