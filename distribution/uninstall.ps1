$ErrorActionPreference = 'Stop'
try {
    $applicationRoot = [IO.Path]::GetFullPath($PSScriptRoot)
    $executable = Join-Path $applicationRoot 'GameDock.exe'
    $running = Get-Process -Name GameDock -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $executable }
    if ($running) { throw 'Najpierw zamknij GameDock z menu ikony obok zegara, potem uruchom Odinstaluj.cmd ponownie.' }
    $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    $current = Get-ItemProperty -LiteralPath $runKey -Name GameDock -ErrorAction SilentlyContinue
    if ($current -and $current.GameDock -like ('*"' + $executable + '"*')) {
        Remove-ItemProperty -LiteralPath $runKey -Name GameDock
    }
    $windowsShell = New-Object -ComObject WScript.Shell
    foreach ($folder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
        $linkPath = Join-Path $folder 'GameDock.lnk'
        if (Test-Path -LiteralPath $linkPath) {
            $shortcut = $windowsShell.CreateShortcut($linkPath)
            if ($shortcut.TargetPath -eq $executable) { Remove-Item -LiteralPath $linkPath }
        }
    }
    Write-Host 'Skróty i autostart tej instalacji zostały usunięte.'
    Write-Host "Aby usunąć aplikację oraz jej ustawienia, usuń teraz cały folder: $applicationRoot"
    Write-Host 'Pliki i skróty Twoich gier pozostają na komputerze.'
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
