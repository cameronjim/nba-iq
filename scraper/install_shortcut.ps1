# creates the "NBA IQ Scrape" shortcut on the desktop and in the start menu.
# windows does not allow pinning to the taskbar from a script; right-click the
# desktop shortcut and choose "Pin to taskbar" once.

$ErrorActionPreference = 'Stop'
$target = Join-Path $PSScriptRoot 'run_local.ps1'
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$shell = New-Object -ComObject WScript.Shell

$locations = @(
    [Environment]::GetFolderPath('Desktop'),
    (Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs')
)
foreach ($dir in $locations) {
    $link = $shell.CreateShortcut((Join-Path $dir 'NBA IQ Scrape.lnk'))
    $link.TargetPath = $powershell
    $link.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$target`""
    $link.WorkingDirectory = Split-Path -Parent $PSScriptRoot
    $link.IconLocation = "$env:SystemRoot\System32\shell32.dll,238"
    $link.Description = 'Run the NBA IQ full scrape against prod'
    $link.Save()
    Write-Host "created $($link.FullName)"
}
