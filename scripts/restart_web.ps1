# Restart Web UI: kill port occupant, then start main.py web.
# Usage (from My_rag/):
#   .\scripts\restart_web.ps1
#   .\scripts\restart_web.ps1 -Port 8765 -HostAddr 127.0.0.1

param(
    [string]$HostAddr = "127.0.0.1",
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Get-PidsOnPort([int]$port) {
    $pids = @()
    try {
        $conns = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
        if ($conns) {
            $pids = @($conns | Select-Object -ExpandProperty OwningProcess -Unique)
        }
    } catch {
        # fallback: netstat
    }
    if (-not $pids -or $pids.Count -eq 0) {
        $lines = netstat -ano | Select-String ":$port\s+.*LISTENING"
        foreach ($line in $lines) {
            $parts = ($line.ToString() -split "\s+") | Where-Object { $_ -ne "" }
            if ($parts.Count -ge 5) {
                $pids += [int]$parts[-1]
            }
        }
        $pids = @($pids | Select-Object -Unique)
    }
    return @($pids | Where-Object { $_ -and $_ -gt 0 })
}

Write-Host "Checking port $Port ..."
$pids = Get-PidsOnPort $Port
if ($pids.Count -gt 0) {
    foreach ($procId in $pids) {
        try {
            $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
            $name = if ($proc) { $proc.ProcessName } else { "?" }
            Write-Host "  Killing PID $procId ($name) on port $Port"
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        } catch {
            Write-Host "  Warn: could not kill PID $procId : $_"
        }
    }
    Start-Sleep -Seconds 1
} else {
    Write-Host "  Port $Port is free"
}

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    $py = "python"
}

Write-Host "Starting Web UI -> http://${HostAddr}:${Port}"
Write-Host "Ctrl+C to stop"
& $py main.py web --host $HostAddr --port $Port
