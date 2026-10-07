param(
    [string]$Destination = (Join-Path $env:LOCALAPPDATA 'GameDock'),
    [switch]$NoLaunch,
    [switch]$SkipShortcuts
)

$ErrorActionPreference = 'Stop'
try {
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw 'Ta paczka wymaga 64-bitowego Windows.'
    }
    $sourceRoot = [IO.Path]::GetFullPath($PSScriptRoot)
    $targetRoot = [IO.Path]::GetFullPath($Destination)
    if ($targetRoot.TrimEnd('\') -eq $sourceRoot.TrimEnd('\')) {
        throw 'Instaluj do innego folderu niż folder rozpakowanej paczki. Możesz też po prostu uruchomić GameDock.exe.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot 'GameDock.exe'))) {
        throw 'Nie znaleziono GameDock.exe. Najpierw wypakuj cały ZIP.'
    }
    $targetExe = Join-Path $targetRoot 'GameDock.exe'
    $running = Get-Process -Name GameDock -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -eq $targetExe -or $_.Path -eq (Join-Path $sourceRoot 'GameDock.exe')
    }
    if ($running) { throw 'Zamknij GameDock z menu przy ikonie obok zegara i uruchom instalator ponownie.' }
    if ((Test-Path -LiteralPath $targetRoot) -and
        (Get-ChildItem -LiteralPath $targetRoot -Force | Select-Object -First 1) -and
        -not (Test-Path -LiteralPath $targetExe)) {
        throw 'Folder docelowy zawiera inne pliki. Wybierz pusty folder albo istniejącą instalację GameDock.'
    }
    $manifest = Get-Content -LiteralPath (Join-Path $sourceRoot 'package-manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $sourcePrefix = $sourceRoot.TrimEnd('\') + '\'
    $targetPrefix = $targetRoot.TrimEnd('\') + '\'
    foreach ($relative in $manifest.files) {
        $from = [IO.Path]::GetFullPath((Join-Path $sourceRoot $relative))
        $to = [IO.Path]::GetFullPath((Join-Path $targetRoot $relative))
        if (-not $from.StartsWith($sourcePrefix, [StringComparison]::OrdinalIgnoreCase) -or
            -not $to.StartsWith($targetPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Nieprawidłowa ścieżka w paczce.'
        }
        $expected = $manifest.sha256.PSObject.Properties[$relative].Value
        if (-not $expected -or (Get-FileHash -LiteralPath $from -Algorithm SHA256).Hash -ne $expected) {
            throw "Plik paczki jest uszkodzony: $relative. Wypakuj ZIP ponownie."
        }
    }
    [void][IO.Directory]::CreateDirectory($targetRoot)
    foreach ($relative in $manifest.files) {
        $from = Join-Path $sourceRoot $relative
        $to = Join-Path $targetRoot $relative
        if ($relative -eq 'config.json' -and (Test-Path -LiteralPath $to)) { continue }
        [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($to))
        Copy-Item -LiteralPath $from -Destination $to -Force
    }
    Copy-Item -LiteralPath (Join-Path $sourceRoot 'package-manifest.json') -Destination (Join-Path $targetRoot 'package-manifest.json') -Force
    [void][IO.Directory]::CreateDirectory((Join-Path $targetRoot 'Shortcuts'))
    if (-not $SkipShortcuts) {
        $windowsShell = New-Object -ComObject WScript.Shell
        foreach ($folder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
            $shortcut = $windowsShell.CreateShortcut((Join-Path $folder 'GameDock.lnk'))
            $shortcut.TargetPath = $targetExe
            $shortcut.WorkingDirectory = $targetRoot
            $shortcut.IconLocation = "$targetExe,0"
            $shortcut.Description = 'Szklany panel gier GameDock'
            $shortcut.Save()
        }
    }
    Write-Host "GameDock jest zainstalowany w: $targetRoot"
    Write-Host 'Autostart można włączyć w ustawieniach pod prawym przyciskiem w panelu.'
    if (-not $NoLaunch) { Start-Process -FilePath $targetExe -WorkingDirectory $targetRoot }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
