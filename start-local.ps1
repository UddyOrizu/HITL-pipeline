<#
.SYNOPSIS
    Start the whole Proof of Review stack locally on Windows (API, worker, MCP server, web app).

.DESCRIPTION
    Checks that Python 3.11+ and Node.js are installed, then hands over to run.py, which creates the
    virtualenv, installs dependencies, starts the Postgres container, applies migrations, seeds the
    demo data and starts every app in the background. Docker must be running for the database.

    With no arguments it runs `python run.py migrate`, then `python run.py up --seed`, so the database
    is always brought up to date before the apps start.
    Any arguments are passed straight to run.py, so the same script stops and inspects the stack.

.EXAMPLE
    .\start-local.ps1                 # install if needed, migrate, seed, start everything
    .\start-local.ps1 status          # what is running, with URLs
    .\start-local.ps1 logs api -f     # follow the API log
    .\start-local.ps1 down            # stop everything

.NOTES
    Full guide: docs/running-locally.md
#>

$ErrorActionPreference = 'Stop'
$MinPython = [version]'3.11'
$MinNode = 20

function Find-Python {
    # The first interpreter on PATH that is new enough. `py -3` is the Windows launcher.
    foreach ($candidate in @(@('python'), @('py', '-3'), @('python3'))) {
        $exe = $candidate[0]
        $pre = @($candidate | Select-Object -Skip 1)
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        try {
            $found = & $exe @pre -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        } catch {
            continue
        }
        if ($LASTEXITCODE -eq 0 -and $found -and ([version]"$found" -ge $MinPython)) {
            return , $candidate
        }
    }
    return $null
}

function Test-Node {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) { return $false }
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { return $false }
    $major = [int]((& node --version) -replace '^v(\d+)\..*$', '$1')
    return $major -ge $MinNode
}

$python = Find-Python
if (-not $python) {
    Write-Host "error: Python $MinPython or newer was not found on PATH." -ForegroundColor Red
    Write-Host "Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run this again."
    exit 1
}
$pythonExe = $python[0]
$pythonPre = @($python | Select-Object -Skip 1)

$runPy = Join-Path $PSScriptRoot 'run.py'

if ($args.Count -gt 0) {
    & $pythonExe @pythonPre $runPy @args
    exit $LASTEXITCODE
}

# Always bring the database up to date before anything starts. If a migration fails, nothing is started.
& $pythonExe @pythonPre $runPy migrate
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# --no-migrate: the migrations have just been applied above.
if (Test-Node) {
    $runArgs = @('up', '--seed', '--no-migrate')
} else {
    Write-Host "warning: Node.js $MinNode+ (with npm) was not found, so the web app will not be started." -ForegroundColor Yellow
    Write-Host "Install it from https://nodejs.org/ and run this again to get the web app on http://localhost:5173."
    $runArgs = @('up', 'api', 'worker', 'mcp', '--seed', '--no-migrate')
}

& $pythonExe @pythonPre $runPy @runArgs
exit $LASTEXITCODE
