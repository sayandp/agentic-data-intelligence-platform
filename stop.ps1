<#
.SYNOPSIS
  Stops both servers started by start.ps1, cleanly and completely.

.DESCRIPTION
  Two things make this less trivial than "kill the PID": uvicorn --reload
  spawns a child worker process under the process start.ps1 recorded, and
  npm run dev spawns a child node (vite) process under npm.cmd - killing
  only the parent can leave the actual listening process orphaned and the
  port still held. This stops the whole process tree per PID file, then
  falls back to killing whatever is actually listening on the ports in
  question, so a missing or stale PID file (e.g. this machine was
  restarted, or start.ps1 wasn't the last thing to touch these ports)
  still results in a genuinely clean state rather than a silent no-op.
#>

$Root = $PSScriptRoot
$LauncherDir = Join-Path $Root ".launcher"
$BackendPidFile = Join-Path $LauncherDir "backend.pid"
$FrontendPidFile = Join-Path $LauncherDir "frontend.pid"
$FrontendPortFile = Join-Path $LauncherDir "frontend.port"

function Stop-ProcessTree([int]$ProcId) {
    Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcId" -ErrorAction SilentlyContinue | ForEach-Object {
        Stop-ProcessTree -ProcId $_.ProcessId
    }
    Stop-Process -Id $ProcId -Force -ErrorAction SilentlyContinue
}

function Stop-ByPidFile([string]$PidFile, [string]$Name) {
    if (Test-Path $PidFile) {
        $procId = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($procId -and (Get-Process -Id $procId -ErrorAction SilentlyContinue)) {
            Stop-ProcessTree -ProcId ([int]$procId)
            Write-Host "[OK] stopped $Name (PID $procId and its children)" -ForegroundColor Green
        } else {
            Write-Host "[INFO] $Name was not running" -ForegroundColor Yellow
        }
        Remove-Item $PidFile -ErrorAction SilentlyContinue
    } else {
        Write-Host "[INFO] no PID file for $Name - nothing recorded to stop" -ForegroundColor Yellow
    }
}

function Stop-ByPort([int]$Port, [string]$Name) {
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($conn in $conns) {
        if (Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue) {
            Stop-ProcessTree -ProcId $conn.OwningProcess
            Write-Host "[OK] stopped whatever was still listening on port $Port ($Name)" -ForegroundColor Green
        }
    }
}

Write-Host ""
Write-Host "=== Stopping servers ===" -ForegroundColor Cyan

Stop-ByPidFile -PidFile $BackendPidFile -Name "backend"
Stop-ByPidFile -PidFile $FrontendPidFile -Name "frontend"

# Fallback: whatever is actually bound to these ports, regardless of
# whether the PID files above were present, current, or matched a live
# process - the goal is "the ports are free afterward", not "the specific
# PIDs we happened to record are gone".
Start-Sleep -Milliseconds 500
Stop-ByPort -Port 8000 -Name "backend"
if (Test-Path $FrontendPortFile) {
    $frontendPort = (Get-Content $FrontendPortFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($frontendPort) { Stop-ByPort -Port ([int]$frontendPort) -Name "frontend" }
    Remove-Item $FrontendPortFile -ErrorAction SilentlyContinue
} else {
    Stop-ByPort -Port 5173 -Name "frontend"
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host ""
