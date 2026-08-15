<#
.SYNOPSIS
  One command that leaves the platform sitting on the interesting part:
  a run PAUSED on exactly one escalation, waiting for a human decision.

.DESCRIPTION
  A live demo that starts from an empty database spends its first several
  minutes on setup - register a source, ingest it, wait, ingest again - and
  none of that is the thing worth showing. This gets to the escalation and
  stops there.

  What it does, in order:
    1. reset.ps1        - stops both servers, wipes the local DB and caches
    2. start.ps1        - brings uvicorn and Vite back up
    3. registers and ingests a CLEAN csv, which establishes the source's
       first (provisional) baseline - validation has nothing to compare
       against until this exists, so it cannot be skipped
    4. registers and ingests a CORRUPTED copy of the same shape, whose
       price column breaches the null tolerance against that baseline

  The corruption is chosen deliberately: `null_threshold` has NO auto-fix
  in app/gate.py's applicability matrix, so the gate cannot quietly repair
  it. It always escalates, which is what makes "exactly one pending
  escalation" a guarantee rather than a hope.

  Ends by printing the run number and the Approvals URL.

.PARAMETER SkipReset
  Seed on top of the current database instead of wiping it. Useful when you
  have other state you want to keep.

.EXAMPLE
  .\seed-demo.ps1
#>

param(
    [switch]$SkipReset
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Api = "http://127.0.0.1:8000"
$SeedDir = Join-Path $Root "backend\data\demo_seed"

function Write-Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Write-Ok($text)   { Write-Host "  $text" -ForegroundColor Green }

# ---------------------------------------------------------------- reset --
if (-not $SkipReset) {
    Write-Step "Resetting to a clean state"
    & (Join-Path $Root "reset.ps1")
} else {
    Write-Step "Skipping reset (-SkipReset)"
}

# ---------------------------------------------------------------- start --
Write-Step "Starting the stack"
Start-Process -FilePath "powershell.exe" `
    -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "start.ps1") `
    -WindowStyle Hidden

$deadline = (Get-Date).AddMinutes(3)
while ((Get-Date) -lt $deadline) {
    try {
        Invoke-RestMethod -Uri "$Api/runs?limit=1" -TimeoutSec 5 | Out-Null
        break
    } catch {
        Start-Sleep -Seconds 3
    }
}
if ((Get-Date) -ge $deadline) { throw "backend did not come up within 3 minutes" }
Write-Ok "backend responding on $Api"

# ------------------------------------------------------------- fixtures --
# Written fresh each run so the demo never depends on a file someone edited.
New-Item -ItemType Directory -Force -Path $SeedDir | Out-Null

$header = "order_id,price,quantity,category"
$cleanRows = 1..60 | ForEach-Object {
    "SEED-$_,{0:N2},{1},widgets" -f (20 + ($_ - 1) * 1.5), ((($_ - 1) % 5) + 1)
}
# Every third price blank: past the null tolerance, and null_threshold has
# no auto-fix, so the gate must escalate it to a person.
$corruptRows = 1..60 | ForEach-Object {
    $price = if ((($_ - 1) % 3) -eq 0) { "" } else { "{0:N2}" -f (20 + ($_ - 1) * 1.5) }
    "SEED-$_,$price,{0},widgets" -f ((($_ - 1) % 5) + 1)
}

# ONE path, written twice. Validation compares a run against its SOURCE's
# baseline, so the corrupted data has to arrive through the same source -
# registering it separately would make it that source's own first ingest,
# which establishes a baseline instead of being checked against one. (That
# is exactly what the first version of this script got wrong, and what the
# check at the bottom caught.)
$dataPath = Join-Path $SeedDir "orders.csv"
$cleanCsv   = (@($header) + $cleanRows)   -join "`n"
$corruptCsv = (@($header) + $corruptRows) -join "`n"
Write-Ok "fixture path: $dataPath"

# --------------------------------------------------------------- helpers --
function Register-Source($path, $label) {
    $body = @{ type = "file"; connection_config = @{ path = $path; original_filename = $label } } | ConvertTo-Json -Depth 5
    $response = Invoke-RestMethod -Uri "$Api/sources" -Method Post -Body $body -ContentType "application/json"
    return $response.id
}

function Invoke-Ingest($sourceId) {
    $ack = Invoke-RestMethod -Uri "$Api/ingest/$sourceId" -Method Post -ContentType "application/json" -Body "{}"
    $runId = $ack.run_id
    $deadline = (Get-Date).AddMinutes(5)
    while ((Get-Date) -lt $deadline) {
        $status = Invoke-RestMethod -Uri "$Api/ingest/$runId/status" -TimeoutSec 30
        # awaiting_approval is a terminal state for this script's purposes:
        # it is exactly what the demo wants to land on.
        if ($status.status -in @("completed", "awaiting_approval", "failed")) { return $status }
        Start-Sleep -Seconds 2
    }
    throw "ingest of $sourceId did not settle within 5 minutes"
}

# ------------------------------------------------- 1. establish baseline --
Write-Step "Ingesting the clean file (establishes the baseline)"
$cleanCsv | Out-File -FilePath $dataPath -Encoding utf8 -NoNewline
$sourceId = Register-Source $dataPath "orders.csv"
$cleanStatus = Invoke-Ingest $sourceId
Write-Ok "run #$($cleanStatus.run_number) -> $($cleanStatus.status)"
if ($cleanStatus.status -ne "completed") {
    throw "the clean ingest was expected to complete; got '$($cleanStatus.status)'"
}

# ---------------------------------------------------- 2. the escalation --
Write-Step "Corrupting the same file and re-ingesting (expected to escalate)"
$corruptCsv | Out-File -FilePath $dataPath -Encoding utf8 -NoNewline
$corruptStatus = Invoke-Ingest $sourceId
Write-Ok "run #$($corruptStatus.run_number) -> $($corruptStatus.status)"

# --------------------------------------------------------------- verify --
Write-Step "Verifying the demo state"
$pending = Invoke-RestMethod -Uri "$Api/approvals/pending" -TimeoutSec 30
$escalated = @($pending.validation_events).Count

Write-Host "  escalated validation events : $escalated"
Write-Host "  escalated queries           : $(@($pending.escalated_queries).Count)"
Write-Host "  escalated models            : $(@($pending.escalated_models).Count)"
Write-Host "  provisional baselines       : $(@($pending.provisional_baselines).Count)"

if ($escalated -ne 1) {
    Write-Host "`nEXPECTED EXACTLY ONE PENDING ESCALATION, GOT $escalated." -ForegroundColor Red
    Write-Host "The demo state is not what this script promises - fix before demoing." -ForegroundColor Red
    exit 1
}

Write-Host "`n=== Ready ===" -ForegroundColor Green
Write-Host "  Run #$($corruptStatus.run_number) is paused on one escalation."
Write-Host "  Open: http://localhost:5173/approvals"
Write-Host "  The escalated validation event is the first section on that page."
