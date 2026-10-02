# one-click full scrape from this PC. stats.nba.com blocks cloud ips, so this is
# the only way the players and league-wide game-log phases get fresh data.
# box-details tries stats.nba.com and falls back to the nba.com box-score pages;
# game-logs-web fills games with no logs from those pages alone.
# launched by the "NBA IQ Scrape" and "NBA IQ box-details backfill" shortcuts
# that install_shortcut.ps1 creates.
# -Season empty means run_scraper.py uses its own current-season default.

param(
    [ValidateSet('scrape', 'box-details', 'game-logs', 'game-logs-web')]
    [string]$Task = 'scrape',
    [string]$Season = '',
    [int]$Limit = 300,
    [int]$MaxBatches = 20
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$scraper = $PSScriptRoot
$venv = Join-Path $scraper '.venv'
$python = Join-Path $venv 'Scripts\python.exe'
$requirements = Join-Path $scraper 'requirements.txt'
$stamp = Join-Path $venv 'requirements.sha256'
$envFile = Join-Path $repo '.env'
$logDir = Join-Path $scraper 'logs'

$Host.UI.RawUI.WindowTitle = "NBA IQ $Task"

function Wait-AndExit([int]$code) {
    if ($code -eq 0) {
        Write-Host "`nDone. Closing in 5 seconds..." -ForegroundColor Green
        Start-Sleep -Seconds 5
    } else {
        Write-Host "`nTask $Task FAILED (exit $code). The log above says why." -ForegroundColor Red
        Read-Host 'Press Enter to close'
    }
    exit $code
}

try {
    Set-Location $repo

    $hasUrl = (Test-Path $envFile) -and (Select-String -Path $envFile -Pattern '^\s*DATABASE_URL\s*=\s*\S' -Quiet)
    if (-not $hasUrl) {
        if (-not (Test-Path $envFile)) { Copy-Item (Join-Path $repo '.env.example') $envFile }
        Write-Host 'DATABASE_URL is not set in .env.' -ForegroundColor Yellow
        Write-Host 'Notepad is opening it: paste the prod Neon connection string after DATABASE_URL=, save, close, then run the shortcut again.'
        Start-Process notepad.exe $envFile -Wait
        Wait-AndExit 1
    }

    # pull main so the scrape runs merged code, but never touch a branch or uncommitted work.
    $branch = (git rev-parse --abbrev-ref HEAD).Trim()
    $dirty = git status --porcelain --untracked-files=no
    if ($branch -eq 'main' -and -not $dirty) {
        Write-Host 'Updating to latest main...'
        git pull --ff-only --quiet
    } else {
        Write-Host "Not pulling: on '$branch'$(if ($dirty) { ' with uncommitted changes' }). Running the code as checked out." -ForegroundColor Yellow
    }

    if (-not (Test-Path $python)) {
        Write-Host 'First run: creating the scraper virtualenv...'
        py -3.12 -m venv $venv
        if ($LASTEXITCODE -ne 0) { python -m venv $venv }
    }

    $hash = (Get-FileHash $requirements -Algorithm SHA256).Hash
    if (-not (Test-Path $stamp) -or (Get-Content $stamp) -ne $hash) {
        Write-Host 'Installing scraper dependencies...'
        & $python -m pip install --quiet --upgrade pip
        & $python -m pip install --quiet --upgrade -r $requirements
        if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }
        Set-Content -Path $stamp -Value $hash
    }

    New-Item -ItemType Directory -Force $logDir | Out-Null
    $log = Join-Path $logDir ("{0}-{1:yyyyMMdd-HHmmss}.log" -f $Task, (Get-Date))
    Write-Host "Running task '$Task' against PROD (log: $log)`n" -ForegroundColor Cyan

    $env:PYTHONUNBUFFERED = '1'
    $ErrorActionPreference = 'Continue'
    $script = Join-Path $scraper 'run_scraper.py'
    $seasonArgs = if ($Season) { @('--season', $Season) } else { @() }

    $batchArgs = switch ($Task) {
        'box-details' { @('--backfill-box-details', '--source', 'auto') }
        'game-logs-web' { @('--backfill-game-logs', '--source', 'web') }
        default { $null }
    }

    if ($batchArgs) {
        $code = 0
        for ($batch = 1; $batch -le $MaxBatches; $batch++) {
            Write-Host "Batch $batch of $MaxBatches (up to $Limit games)" -ForegroundColor Cyan
            $lines = @(& $python $script @batchArgs @seasonArgs --limit $Limit 2>&1 |
                ForEach-Object { "$_" } | Tee-Object -FilePath $log -Append |
                ForEach-Object { Write-Host $_; $_ })
            $code = $LASTEXITCODE
            if ($code -ne 0) { break }
            $processed = $null
            foreach ($line in $lines) {
                if ($line -match '^box_details_processed=(\d+)\s*$') { $processed = [int]$Matches[1] }
            }
            if ($null -eq $processed) {
                Write-Host 'No box_details_processed line in the output; stopping.' -ForegroundColor Red
                $code = 1
                break
            }
            if ($processed -eq 0) {
                Write-Host 'No games left to process.' -ForegroundColor Green
                break
            }
            if ($batch -lt $MaxBatches) {
                Write-Host 'Sleeping 60 seconds before the next batch...'
                Start-Sleep -Seconds 60
            }
        }
    } elseif ($Task -eq 'game-logs') {
        & $python $script --backfill-game-logs 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $log
        $code = $LASTEXITCODE
    } else {
        & $python $script 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $log
        $code = $LASTEXITCODE
    }
    Get-ChildItem $logDir -Filter '*.log' | Sort-Object LastWriteTime -Descending | Select-Object -Skip 20 | Remove-Item
    Wait-AndExit $code
} catch {
    Write-Host $_ -ForegroundColor Red
    Wait-AndExit 1
}
