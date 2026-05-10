# Bootstrap the server-monitor agent on Windows.
# Usage:
#   iwr http://<monitor>/install.ps1 -UseBasicParsing | iex
#   Install-MonitorAgent -Token <T> -MonitorUrl https://<monitor>
#
# Compatible with Windows PowerShell 5.1 (Server 2016/2019/2022/2025, Win10/11
# default) and PowerShell 7+ (`pwsh`). The cert-bypass mechanism for the one-time
# CA fetch differs between the two; see the branch in step 1 below.

function Install-MonitorAgent {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)] [string] $Token,
        [string] $Hostname = $env:COMPUTERNAME,
        [string] $MonitorUrl = "https://monitor.lan"
    )
    # Abort on first error so we don't print "==> done" after a half-finished install.
    $ErrorActionPreference = 'Stop'

    # PowerShell 7+ uses HttpClient under the hood for Invoke-WebRequest and offers
    # a native -SkipCertificateCheck. PowerShell 5.1 uses WebRequest and needs the
    # ICertificatePolicy workaround (see PS 5.1 branch in step 1).
    $isPs7Plus = $PSVersionTable.PSVersion.Major -ge 6

    # PowerShell 5.1 defaults to TLS 1.0/1.1; Caddy requires TLS 1.2+. This setting
    # affects WebRequest-based calls. PS 7's HttpClient ignores it and negotiates
    # protocols itself — harmless either way.
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
    $caFetchArgs = @{
        Uri             = "$MonitorUrl/ca.crt"
        UseBasicParsing = $true
        OutFile         = $CaPath
    }
    if ($isPs7Plus) {
        # PS 7+: native parameter, no global state mutation needed.
        Invoke-WebRequest @caFetchArgs -SkipCertificateCheck
    } else {
        # PS 5.1 workaround. ServerCertificateValidationCallback with a ScriptBlock
        # fails ("no Runspace available") because .NET invokes the callback on a
        # TLS background thread with no PowerShell runspace. ICertificatePolicy
        # implemented in inline C# is callable from any thread.
        if (-not ('SmTrustAllCertsPolicy' -as [type])) {
            Add-Type -TypeDefinition @"
                using System.Net;
                using System.Security.Cryptography.X509Certificates;
                public class SmTrustAllCertsPolicy : ICertificatePolicy {
                    public bool CheckValidationResult(
                        ServicePoint sp, X509Certificate cert, WebRequest req, int err) {
                        return true;
                    }
                }
"@
        }
        $priorCertPolicy = [System.Net.ServicePointManager]::CertificatePolicy
        [System.Net.ServicePointManager]::CertificatePolicy = New-Object SmTrustAllCertsPolicy
        try {
            Invoke-WebRequest @caFetchArgs
        } finally {
            [System.Net.ServicePointManager]::CertificatePolicy = $priorCertPolicy
        }
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

    # 5. Register and start the service via NSSM.
    #
    # Why NSSM: Windows Service Control Manager (SCM) expects a service binary to
    # implement the SCM protocol (call SetServiceStatus(SERVICE_RUNNING) within
    # ~30s of launch). Our agent is a plain console app, so SCM would time out
    # and report "service did not start in a timely manner" even though the
    # process is happily running.
    #
    # NSSM (https://nssm.cc) is a tiny service wrapper that fronts SCM, runs our
    # console binary as a subprocess, and translates SCM control signals into
    # process start/stop. Mature, single-file, MIT-licensed.
    #
    # Operator prerequisite: nssm.exe (win64 build) must be present in the
    # monitor's agents-dist/ directory. See README "Setup -> 3. Build the
    # Windows agent binary" for the one-time download instructions.
    Write-Host "==> downloading service wrapper (NSSM)"
    $NssmPath = "$InstallDir\nssm.exe"
    Invoke-WebRequest "$MonitorUrl/api/agent-helper/nssm.exe" `
                      -UseBasicParsing -OutFile $NssmPath

    Write-Host "==> registering service"
    if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
        & $NssmPath stop $ServiceName 2>$null | Out-Null
        & $NssmPath remove $ServiceName confirm 2>$null | Out-Null
        Start-Sleep -Seconds 2
    }

    & $NssmPath install $ServiceName $ExePath | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "nssm install failed with exit code $LASTEXITCODE" }

    $appParams = ('--monitor-url {0} --hostname {1} ' +
                  '--token-file "{2}" --ca-bundle "{3}" run') -f
                  $MonitorUrl, $Hostname, $TokenPath, $CaPath
    & $NssmPath set $ServiceName AppParameters $appParams | Out-Null
    & $NssmPath set $ServiceName DisplayName "Server Monitor Agent" | Out-Null
    & $NssmPath set $ServiceName Description "Reports RDP session activity to the server-monitor service." | Out-Null
    & $NssmPath set $ServiceName Start SERVICE_AUTO_START | Out-Null
    & $NssmPath set $ServiceName AppRestartDelay 5000 | Out-Null
    # Capture stdout/stderr to a log file with rotation. Useful for "the service
    # is running but not reporting" debugging.
    & $NssmPath set $ServiceName AppStdout "$DataDir\agent.log" | Out-Null
    & $NssmPath set $ServiceName AppStderr "$DataDir\agent.log" | Out-Null
    & $NssmPath set $ServiceName AppRotateFiles 1 | Out-Null
    & $NssmPath set $ServiceName AppRotateBytes 1048576 | Out-Null

    Start-Service -Name $ServiceName
    Get-Service -Name $ServiceName
    Write-Host "==> done"
}
