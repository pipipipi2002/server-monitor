# Bootstrap the server-monitor agent on Windows.
# Usage:
#   iwr https://<monitor>/install.ps1 -UseBasicParsing | iex
#   Install-MonitorAgent -Token <T> -Hostname <H> [-MonitorUrl https://<monitor>]
function Install-MonitorAgent {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)] [string] $Token,
        [string] $Hostname = $env:COMPUTERNAME,
        [string] $MonitorUrl = "https://monitor.lan"
    )
    if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
            [Security.Principal.WindowsBuiltInRole] "Administrator")) {
        throw "Must run from an elevated PowerShell."
    }

    $InstallDir = "$env:ProgramFiles\server-monitor-agent"
    $DataDir    = "$env:ProgramData\server-monitor-agent"
    $CaPath     = "$DataDir\ca.pem"
    $TokenPath  = "$DataDir\token"
    $ExePath    = "$InstallDir\agent-windows.exe"
    $ServiceName = "ServerMonitorAgent"

    New-Item -ItemType Directory -Force -Path $InstallDir, $DataDir | Out-Null

    # 1. Trust monitor CA (one-time, downloaded over insecure channel on the LAN).
    Write-Host "==> downloading monitor CA"
    [Net.ServicePointManager]::ServerCertificateValidationCallback = {$true}
    Invoke-WebRequest "$MonitorUrl/ca.crt" -UseBasicParsing -OutFile $CaPath
    [Net.ServicePointManager]::ServerCertificateValidationCallback = $null
    Import-Certificate -FilePath $CaPath -CertStoreLocation Cert:\LocalMachine\Root | Out-Null

    # 2. Download agent binary.
    Write-Host "==> downloading agent binary"
    Invoke-WebRequest "$MonitorUrl/api/agent-binary?os=windows" -UseBasicParsing -OutFile $ExePath

    # 3. Lock down ProgramData dir so only SYSTEM + Administrators can read the token.
    $acl = Get-Acl $DataDir
    $acl.SetAccessRuleProtection($true, $false)
    $rules = @(
        New-Object System.Security.AccessControl.FileSystemAccessRule("SYSTEM","FullControl","ContainerInherit,ObjectInherit","None","Allow"),
        New-Object System.Security.AccessControl.FileSystemAccessRule("Administrators","FullControl","ContainerInherit,ObjectInherit","None","Allow")
    )
    $acl.Access | ForEach-Object { $acl.RemoveAccessRule($_) | Out-Null }
    $rules | ForEach-Object { $acl.AddAccessRule($_) }
    Set-Acl $DataDir $acl

    # 4. Enroll.
    & $ExePath --monitor-url $MonitorUrl --hostname $Hostname --token-file $TokenPath enroll --enrollment-token $Token

    # 5. Register and start the service.
    if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
        sc.exe stop $ServiceName | Out-Null
        sc.exe delete $ServiceName | Out-Null
    }
    $binPath = "`"$ExePath`" --monitor-url $MonitorUrl --hostname $Hostname --token-file `"$TokenPath`" run"
    sc.exe create $ServiceName binPath= "$binPath" start= auto displayName= "Server Monitor Agent" | Out-Null
    sc.exe failure $ServiceName reset= 60 actions= restart/5000/restart/5000/restart/5000 | Out-Null
    Start-Service $ServiceName
    Get-Service $ServiceName
    Write-Host "==> done"
}
