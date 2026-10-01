# creates the "NBA IQ Scrape" and "NBA IQ box-details backfill" shortcuts on the desktop and in the start menu.
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
$shortcuts = @(
    @{ Name = 'NBA IQ Scrape'; Extra = ''; Description = 'Run the NBA IQ full scrape against prod' },
    @{ Name = 'NBA IQ box-details backfill'; Extra = ' -Task box-details'; Description = 'Backfill box-score details (starters, positions, rebound split, DNP reasons) against prod' }
)
foreach ($dir in $locations) {
    foreach ($item in $shortcuts) {
        $link = $shell.CreateShortcut((Join-Path $dir "$($item.Name).lnk"))
        $link.TargetPath = $powershell
        $link.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$target`"$($item.Extra)"
        $link.WorkingDirectory = Split-Path -Parent $PSScriptRoot
        $link.IconLocation = "$env:SystemRoot\System32\shell32.dll,238"
        $link.Description = $item.Description
        $link.Save()
        Write-Host "created $($link.FullName)"
    }
}
