<#
.SYNOPSIS
  Returns the project to a clean demo state: both servers stopped, the
  local SQLite DB and every cache/artifact directory wiped.

.DESCRIPTION
  Deletes exactly what start.ps1's own run creates or the app writes at
  runtime (backend\local_dev.db, backend\.cache\, backend\.run_artifacts\)
  - never source code, never .env, never uploaded files under
  backend\data\uploads\ (those are closer to user data than to disposable
  cache, and this script's job is a clean SLATE for the app's own state,
  not a wipe of everything you've put in the data directory).
#>

$Root = $PSScriptRoot
$BackendDir = Join-Path $Root "backend"

& (Join-Path $Root "stop.ps1")

Write-Host "=== Resetting state ===" -ForegroundColor Cyan

$targets = @(
    (Join-Path $BackendDir "local_dev.db"),
    (Join-Path $BackendDir ".cache"),
    (Join-Path $BackendDir ".run_artifacts")
)
foreach ($target in $targets) {
    if (Test-Path $target) {
        Remove-Item $target -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "[OK] removed $target" -ForegroundColor Green
    } else {
        Write-Host "[INFO] $target did not exist - nothing to remove" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Clean state. Run .\start.ps1 to bring the stack back up." -ForegroundColor Green
Write-Host ""
