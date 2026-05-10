# Remove the server-monitor agent from a Windows host.
# Usage:
#   iwr http://<monitor>/uninstall.ps1 -UseBasicParsing | iex
#   Uninstall-MonitorAgent
#
# This is a local-only cleanup. The server's row will remain in the monitor's
# database with an "agent offline" badge until an operator deletes it manually
# (see README "Removing a server").
#
# Compatible with PowerShell 5.1 (Server 2016/2019/2022/2025, Win10/11 default)
# and PowerShell 7+.

function Uninstall-MonitorAgent {
    [CmdletBinding()]
    param()
    $ErrorActionPreference = 'Stop'

    if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
            [Security.Principal.WindowsBuiltInRole] "Administrator")) {
        throw "Must run from an elevated PowerShell."
    }

    $InstallDir  = "$env:ProgramFiles\server-monitor-agent"
    $DataDir     = "$env:ProgramData\server-monitor-agent"
    $CaPath      = "$DataDir\ca.pem"
    $ServiceName = "ServerMonitorAgent"

    Write-Host "==> stopping and removing service"
    $NssmPath = "$InstallDir\nssm.exe"
    if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
        if (Test-Path $NssmPath) {
            # Prefer NSSM since it's how we registered it; cleaner unregister.
            & $NssmPath stop $ServiceName 2>$null | Out-Null
            & $NssmPath remove $ServiceName confirm 2>$null | Out-Null
        } else {
            # Fallback for installs that pre-date the NSSM-based registration.
            Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
            & sc.exe delete $ServiceName | Out-Null
        }
        # sc.exe / nssm remove are asynchronous; let SCM finish before tearing
        # down files so $InstallDir\nssm.exe isn't held open.
        Start-Sleep -Seconds 2
    }

    # Best-effort: remove the monitor's CA cert from the LocalMachine Root store.
    # Skipped if ca.pem is already gone.
    if (Test-Path $CaPath) {
        Write-Host "==> removing CA cert from trust store"
        try {
            $caCert = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($CaPath)
            $store  = [System.Security.Cryptography.X509Certificates.X509Store]::new("Root", "LocalMachine")
            $store.Open("ReadWrite")
            try {
                $matching = $store.Certificates | Where-Object { $_.Thumbprint -eq $caCert.Thumbprint }
                foreach ($c in $matching) { $store.Remove($c) }
            } finally {
                $store.Close()
            }
        } catch {
            Write-Warning "failed to remove CA cert; remove it manually from Cert:\\LocalMachine\\Root if you no longer trust this monitor"
        }
    }

    Write-Host "==> removing files"
    # Reset ACLs first in case the install locked the data dir down — without
    # this, Remove-Item can fail with "Access denied" even from an admin shell.
    foreach ($dir in @($InstallDir, $DataDir)) {
        if (Test-Path $dir) {
            & takeown.exe /F $dir /R /A /D Y 2>$null | Out-Null
            & icacls.exe $dir "/reset" "/T" "/C" "/Q" 2>$null | Out-Null
            Remove-Item -Recurse -Force $dir
        }
    }

    Write-Host "==> done"
    Write-Host ""
    Write-Host "Note: this host will continue to appear on the monitor dashboard with"
    Write-Host "      'agent offline' until an operator removes it from the SQLite DB"
    Write-Host "      (see README, 'Removing a server')."
}
