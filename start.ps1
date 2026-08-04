<#
.SYNOPSIS
  One-command launcher for the local dev stack (uvicorn + Vite + SQLite).
  This is the supported way to run and demo this project - see README's
  "Quickstart (local, no Docker)" section. Docker is a separate, optional
  path (docker-compose.yml) that this script does not touch or require.

.DESCRIPTION
  Runs preflight checks first and fails loudly, with a copy-pasteable fix,
  before starting anything - a stack that half-starts and then breaks on
  request #1 is worse than one that refuses to start at all. Only the
  missing-LLM-key check is a warning, not a failure: no-LLM mode is a fully
  supported operating mode (every corruption escalates to human review,
  reports use the deterministic template), not a broken deployment.

  Both servers run as background processes with their output redirected to
  .launcher\*.log - not hidden windows you can't get back, and not console
  windows that vanish the moment this script exits. PID and port state is
  written to .launcher\ for stop.ps1 to find and clean up reliably, even
  across the child-process quirks uvicorn --reload and npm run dev both have
  (each spawns its own child; killing only the parent leaves an orphan).
#>

$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
$BackendDir = Join-Path $Root "backend"
$FrontendDir = Join-Path $Root "frontend"
$LauncherDir = Join-Path $Root ".launcher"
New-Item -ItemType Directory -Path $LauncherDir -Force | Out-Null

$BackendPython = Join-Path $BackendDir ".venv\Scripts\python.exe"
$BackendOutLog = Join-Path $LauncherDir "backend.out.log"
$BackendErrLog = Join-Path $LauncherDir "backend.err.log"
$FrontendOutLog = Join-Path $LauncherDir "frontend.out.log"
$FrontendErrLog = Join-Path $LauncherDir "frontend.err.log"
$BackendPidFile = Join-Path $LauncherDir "backend.pid"
$FrontendPidFile = Join-Path $LauncherDir "frontend.pid"
$FrontendPortFile = Join-Path $LauncherDir "frontend.port"

function Write-Ok([string]$Message) { Write-Host "[OK]   $Message" -ForegroundColor Green }
function Write-Warn([string]$Message) { Write-Host "[WARN] $Message" -ForegroundColor Yellow }
function Write-Fail([string]$Message) {
    Write-Host "[FAIL] $Message" -ForegroundColor Red
    Write-Host ""
    Write-Host "Nothing was started." -ForegroundColor Red
    exit 1
}

function Get-PortOwnerDescription([int]$Port) {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $conn) { return $null }
    $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
    if ($proc) { return "$($proc.ProcessName) (PID $($proc.Id))" }
    return "PID $($conn.OwningProcess)"
}

Write-Host ""
Write-Host "=== Preflight checks ===" -ForegroundColor Cyan

# 1. backend/.venv exists
if (-not (Test-Path $BackendPython)) {
    Write-Fail "backend\.venv not found. Run:  cd backend; python -m venv .venv; .venv\Scripts\pip install -r requirements.txt"
}
Write-Ok "backend\.venv exists"

# 2. dependencies installed - actually import the app, don't just check the
# venv exists. NOTE: deliberately using Start-Process -Wait -PassThru here,
# not `&`/`2>&1`/`*>` - under $ErrorActionPreference="Stop", THIS PowerShell
# treats ANY captured stderr from a native command as a terminating error
# regardless of the redirect operator used, even on a genuine exit code 0
# (confirmed directly: the plain `python -c "import app.main"` succeeds with
# exit 0 and no output when run outside PowerShell's own redirect handling).
# Start-Process sidesteps this entirely - exit code comes back as a plain
# property, never funneled through PowerShell's error/object pipeline.
# This import is slow (~20-30s cold - prophet/statsmodels/langgraph), not
# a bug; there's no artificial timeout on it for that reason.
$importLog = Join-Path $LauncherDir "preflight_import_check.log"
$importErrLog = Join-Path $LauncherDir "preflight_import_check.err.log"
$importCheck = Start-Process -FilePath $BackendPython -ArgumentList '-c "import app.main"' `
    -WorkingDirectory $BackendDir -RedirectStandardOutput $importLog -RedirectStandardError $importErrLog `
    -PassThru -Wait -WindowStyle Hidden
if ($importCheck.ExitCode -ne 0) {
    Get-Content $importErrLog -ErrorAction SilentlyContinue | Write-Host -ForegroundColor DarkGray
    Write-Fail "backend dependencies are not fully installed. Run:  cd backend; .venv\Scripts\pip install -r requirements.txt"
}
Write-Ok "backend dependencies import cleanly"

# 3. frontend node_modules exists
if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
    Write-Fail "frontend\node_modules not found. Run:  cd frontend; npm install"
}
Write-Ok "frontend\node_modules exists"

# 4. port 8000 (backend) free
$owner8000 = Get-PortOwnerDescription -Port 8000
if ($owner8000) {
    Write-Fail "Port 8000 is already in use by $owner8000. Stop it, or run .\stop.ps1 first."
}
Write-Ok "port 8000 is free"

# 5. port 5173 (Vite's default) free - Vite silently falls back to 5174/5175/...
# if this is taken, which is a real, previously-hit failure mode (a stale
# process squatting on 5173 makes the browser end up on a different origin
# than the backend's CORS allowlist expects, and every request fails
# identically with no useful error). Catching it here, loudly, before that
# can happen again.
$owner5173 = Get-PortOwnerDescription -Port 5173
if ($owner5173) {
    Write-Fail "Port 5173 is already in use by $owner5173. Vite will silently pick a different port if this isn't free, which breaks the backend's CORS allowlist. Stop it, or run .\stop.ps1 first."
}
Write-Ok "port 5173 is free"

# 6. .env / GEMINI_API_KEY - warning only, never a failure. No-LLM mode is a
# fully supported operating mode: every corruption escalates to human
# review, reports generate from the deterministic template.
$EnvFile = Join-Path $Root ".env"
$NoLlmWarning = "The app will run in NO-LLM mode: every corruption escalates to human review, reports use the deterministic template. This is a fully supported mode, not a broken one. To enable Gemini: copy .env.example to .env and fill in GEMINI_API_KEY."
if (-not (Test-Path $EnvFile)) {
    Write-Warn "No .env file found at the project root. $NoLlmWarning"
} else {
    $envContent = Get-Content $EnvFile -Raw
    if ($envContent -match "(?m)^GEMINI_API_KEYS=\S") {
        Write-Ok ".env has GEMINI_API_KEYS set (multi-key rotation)"
    } elseif ($envContent -match "(?m)^GEMINI_API_KEY=\S") {
        Write-Ok ".env has GEMINI_API_KEY set"
    } else {
        Write-Warn "Neither GEMINI_API_KEY nor GEMINI_API_KEYS is set in .env. $NoLlmWarning"
    }
}

Write-Host ""
Write-Host "=== Starting backend ===" -ForegroundColor Cyan

$env:DATABASE_URL = "sqlite:///" + ($BackendDir -replace '\\', '/') + "/local_dev.db"
# ^ Must be a real Windows drive path with forward slashes
# (sqlite:///D:/main_project/backend/local_dev.db) - Git Bash's own
# $(pwd)-style path (/d/main_project/...) is NOT valid here and fails with
# "unable to open database file"; this is pure PowerShell so that trap
# doesn't apply, but the path still has to be built explicitly like this.

$backendProc = Start-Process -FilePath $BackendPython `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000" `
    -WorkingDirectory $BackendDir `
    -RedirectStandardOutput $BackendOutLog `
    -RedirectStandardError $BackendErrLog `
    -PassThru -WindowStyle Hidden
$backendProc.Id | Out-File -FilePath $BackendPidFile -Encoding ascii

$backendHealthy = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 1000
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -eq 200) { $backendHealthy = $true; break }
    } catch { }
}
if (-not $backendHealthy) {
    Write-Host ""
    Write-Host "[FAIL] backend did not become healthy within 30s." -ForegroundColor Red
    Write-Host "Last lines of $BackendErrLog :" -ForegroundColor Red
    Get-Content $BackendErrLog -Tail 20 -ErrorAction SilentlyContinue
    exit 1
}
Write-Ok "backend is healthy on http://127.0.0.1:8000"

# Surface the [startup] LLM-provider line from app/main.py's own boot log,
# so the operating mode (Gemini active, or no-LLM) is visible here instead
# of only discoverable by tailing a log file by hand.
$startupLine = Select-String -Path $BackendOutLog -Pattern "\[startup\]" -ErrorAction SilentlyContinue | Select-Object -Last 1
if ($startupLine) {
    Write-Host $startupLine.Line -ForegroundColor Magenta
} else {
    Write-Warn "Could not find a [startup] line in $BackendOutLog - check it manually."
}

Write-Host ""
Write-Host "=== Starting frontend ===" -ForegroundColor Cyan

$npmCmd = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
if (-not $npmCmd) { $npmCmd = (Get-Command npm -ErrorAction Stop).Source }

$frontendProc = Start-Process -FilePath $npmCmd -ArgumentList "run", "dev" `
    -WorkingDirectory $FrontendDir `
    -RedirectStandardOutput $FrontendOutLog `
    -RedirectStandardError $FrontendErrLog `
    -PassThru -WindowStyle Hidden
$frontendProc.Id | Out-File -FilePath $FrontendPidFile -Encoding ascii

# Don't assume port 5173 - read whatever Vite actually printed it bound to
# (it prints "Local: http://localhost:PORT/" to its own stdout), since the
# whole point of the preflight check above is to make 5173 available, not
# to guarantee Vite chooses it if something changes between the check and
# this point.
#
# Vite colourises that line even when its stdout is a redirected file, and
# the escape sequences land INSIDE the text being matched:
#   ESC[1mLocal ESC[22m:   ESC[36mhttp://localhost: ESC[1m5173 ESC[22m/
# so neither the literal "Local:" nor ":\d+" matches the raw bytes - the
# frontend starts perfectly and the launcher still reports "did not report
# a URL within 30s". Strip ANSI SGR sequences before matching rather than
# trying to write a pattern that tolerates them at every insertion point.
$frontendUrl = $null
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 1000
    $frontendLog = Get-Content -Path $FrontendOutLog -Raw -ErrorAction SilentlyContinue
    if (-not $frontendLog) { continue }
    $plainLog = $frontendLog -replace "\x1b\[[0-9;]*[A-Za-z]", ""
    $urlMatch = [regex]::Match($plainLog, "Local:\s+(http://localhost:\d+)")
    if ($urlMatch.Success) {
        $frontendUrl = $urlMatch.Groups[1].Value
        break
    }
}
if (-not $frontendUrl) {
    Write-Host ""
    Write-Host "[FAIL] frontend did not report a URL within 30s." -ForegroundColor Red
    # The URL line goes to STDOUT; a crash usually explains itself on
    # stderr. Showing only stderr made this failure look like an empty
    # error, so show both.
    Write-Host "Last lines of $FrontendErrLog :" -ForegroundColor Red
    Get-Content $FrontendErrLog -Tail 20 -ErrorAction SilentlyContinue
    Write-Host "Last lines of $FrontendOutLog :" -ForegroundColor Red
    Get-Content $FrontendOutLog -Tail 20 -ErrorAction SilentlyContinue
    exit 1
}
$frontendPort = [regex]::Match($frontendUrl, ':(\d+)$').Groups[1].Value
$frontendPort | Out-File -FilePath $FrontendPortFile -Encoding ascii

$frontendResponding = $false
for ($i = 0; $i -lt 15; $i++) {
    try {
        $r = Invoke-WebRequest -Uri $frontendUrl -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -eq 200) { $frontendResponding = $true; break }
    } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $frontendResponding) {
    Write-Host "[FAIL] frontend reported $frontendUrl but is not responding." -ForegroundColor Red
    exit 1
}

if ($frontendPort -ne "5173") {
    Write-Warn "Vite is running on port $frontendPort, not the expected 5173. It picked a fallback because something else was on 5173 at start time. The backend's CORS allowlist covers 5173-5177 by default (see app/main.py), so this should still work - but if requests fail, that's why."
}
Write-Ok "frontend is up on $frontendUrl"

Write-Host ""
Write-Host "=== Ready ===" -ForegroundColor Green
Write-Host "Dashboard:    $frontendUrl" -ForegroundColor White
Write-Host "Backend docs: http://127.0.0.1:8000/docs" -ForegroundColor White
Write-Host "Logs:         $LauncherDir" -ForegroundColor White
Write-Host "Stop with:    .\stop.ps1" -ForegroundColor White
Write-Host "Reset with:   .\reset.ps1" -ForegroundColor White
Write-Host ""
