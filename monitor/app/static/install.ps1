# Bootstrap the server-monitor agent on Windows.
# Usage:
#   iwr http://<monitor>/install.ps1 -UseBasicParsing | iex
#   Install-MonitorAgent -Token <T> -MonitorUrl https://<monitor>
function Install-MonitorAgent {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)] [string] $Token,
        [string] $Hostname = $env:COMPUTERNAME,
        [string] $MonitorUrl = "https://monitor.lan"
    )
    # Abort on first error so we don't print "==> done" after a half-finished install.
    $ErrorActionPreference = 'Stop'

    # PowerShell 5.1 (Windows Server 2016/2019) defaults to TLS 1.0/1.1; Caddy
    # requires TLS 1.2+. Set it before any HTTPS call.
    try {
        [Net.ServicePointManager]::SecurityProtocol = `
            [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13
    } catch {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    }

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

    # 1. Trust monitor CA (one-time, fetched with verification disabled — TOFU).
    Write-Host "==> downloading monitor CA"
    [Net.ServicePointManager]::ServerCertificateValidationCallback = {$true}
    try {
        Invoke-WebRequest "$MonitorUrl/ca.crt" -UseBasicParsing -OutFile $CaPath
    } finally {
        [Net.ServicePointManager]::ServerCertificateValidationCallback = $null
    }
    Import-Certificate -FilePath $CaPath -CertStoreLocation Cert:\LocalMachine\Root | Out-Null

    # 2. Download agent binary (verified against the just-pinned CA).
    Write-Host "==> downloading agent binary"
    Invoke-WebRequest "$MonitorUrl/api/agent-binary?os=windows" -UseBasicParsing -OutFile $ExePath

    # 3. Lock down ProgramData dir so only SYSTEM + Administrators can read the token.
    #    SetAccessRuleProtection($true, $false) disables inheritance AND drops inherited
    #    rules — the manual loop that used to be here was redundant and fragile.
    $acl = Get-Acl $DataDir
    $acl.SetAccessRuleProtection($true, $false)
    $systemRule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        "SYSTEM", "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")
    $adminsRule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        "Administrators", "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")
    $acl.AddAccessRule($systemRule)
    $acl.AddAccessRule($adminsRule)
    Set-Acl $DataDir $acl

    # 4. Enroll — exchange one-shot enrollment token for a long-lived agent token.
    Write-Host "==> enrolling"
    & $ExePath --monitor-url $MonitorUrl --hostname $Hostname --token-file $TokenPath `
               --ca-bundle $CaPath enroll --enrollment-token $Token

    # 5. Register and start the service.
    Write-Host "==> registering service"
    if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
        sc.exe stop $ServiceName | Out-Null
        sc.exe delete $ServiceName | Out-Null
    }
    $binPath = "`"$ExePath`" --monitor-url $MonitorUrl --hostname $Hostname " +
               "--token-file `"$TokenPath`" --ca-bundle `"$CaPath`" run"
    sc.exe create $ServiceName binPath= "$binPath" start= auto displayName= "Server Monitor Agent" | Out-Null
    sc.exe failure $ServiceName reset= 60 actions= restart/5000/restart/5000/restart/5000 | Out-Null
    Start-Service $ServiceName
    Get-Service $ServiceName
    Write-Host "==> done"
}
