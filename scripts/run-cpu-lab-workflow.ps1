<#
.SYNOPSIS
Run a private architecture verification workflow on the retained CPU lab.
.DESCRIPTION
The supplied Bash runner receives the reviewed archive and the remote receipt path.
The receipt must bind the archive SHA-256 and report drift, rag_search_scaler,
and prometheus_alerts as passed.
An instance started by this invocation is stopped after the run, including on failure.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^[a-z][a-z0-9-]{4,28}[a-z0-9]$')][string]$ProjectId,
    [Parameter(Mandatory)][string]$Runner,
    [Parameter(Mandatory)][string]$CandidateArchive,
    [Parameter(Mandatory)][string]$ReceiptDirectory,
    [ValidateRange(10, 480)][int]$MaxMinutes = 120
)

$ErrorActionPreference = 'Stop'
$cloudSdk = (Get-Command gcloud.cmd -ErrorAction Stop).Source
$vmName = 'iot-cw-vm'
$zone = 'us-central1-a'
$cloudFlags = @("--project=$ProjectId", "--zone=$zone", '--quiet')
$runnerPath = (Get-Item -LiteralPath $Runner).FullName
$archivePath = (Get-Item -LiteralPath $CandidateArchive).FullName
if (Test-Path -LiteralPath $ReceiptDirectory) { throw 'Use a fresh receipt directory.' }
$receipt = (New-Item -ItemType Directory -Path $ReceiptDirectory).FullName
$runId = [Guid]::NewGuid().ToString('N')
$remote = "/tmp/iot-architecture-$runId"
$record = [ordered]@{
    passed = $false
    runner_sha256 = (Get-FileHash -LiteralPath $runnerPath -Algorithm SHA256).Hash.ToLowerInvariant()
    candidate_sha256 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    cpu_started = $false
    cpu_stopped = $false
    error = $null
}

function Invoke-CloudSdk {
    param([string[]]$CommandArguments)
    & $cloudSdk @CommandArguments @cloudFlags
    if ($LASTEXITCODE -ne 0) { throw "Google Cloud command failed with exit $LASTEXITCODE." }
}

function Get-LabCpu {
    Invoke-CloudSdk @('compute', 'instances', 'describe', $vmName,
        '--format=json(id,name,status,lastStartTimestamp,disks,machineType,scheduling)') | ConvertFrom-Json
}

function Invoke-LabSsh {
    param([string]$Command)
    Invoke-CloudSdk @('compute', 'ssh', $vmName, '--tunnel-through-iap', '--ssh-flag=-batch', "--command=$Command")
}

$initial = $null
$startedAt = $null
$deadline = [DateTimeOffset]::UtcNow.AddMinutes($MaxMinutes)
try {
    $initial = Get-LabCpu
    if ($initial.name -ne $vmName -or $initial.machineType -notlike '*/e2-highmem-8' -or $initial.disks.Count -ne 3 -or
        $initial.scheduling.instanceTerminationAction -ne 'STOP' -or $initial.scheduling.maxRunDuration.seconds -ne '28800') {
        throw 'The retained CPU VM configuration differs from the lab configuration.'
    }
    if ($initial.status -eq 'TERMINATED') {
        Invoke-CloudSdk @('compute', 'instances', 'start', $vmName, '--async', '--format=value(status)') | Out-Null
        $record.cpu_started = $true
    } elseif ($initial.status -ne 'RUNNING') {
        throw "Cannot attach while the VM is $($initial.status)."
    }

    while ($true) {
        if ([DateTimeOffset]::UtcNow -ge $deadline) { throw 'The CPU session expired while waiting for IAP.' }
        $live = Get-LabCpu
        if ($live.id -ne $initial.id) { throw 'The CPU VM identity changed.' }
        if ($live.status -eq 'RUNNING') {
            $startedAt = $live.lastStartTimestamp
            try {
                Invoke-LabSsh 'sudo -n true' | Out-Null
                break
            } catch {
                Start-Sleep -Seconds 5
                continue
            }
        }
        Start-Sleep -Seconds 5
    }

    $seconds = [int][Math]::Floor(($deadline - [DateTimeOffset]::UtcNow).TotalSeconds)
    if ($seconds -le 15) { throw 'No time remains before the CPU deadline.' }
    Invoke-LabSsh "install -d -m 700 $remote" | Out-Null
    if ($record.cpu_started) {
        Invoke-LabSsh "sudo -n systemd-run --quiet --unit=iot-architecture-stop-$runId --on-active=${seconds}s /usr/bin/systemctl poweroff" | Out-Null
    }
    Invoke-CloudSdk @('compute', 'scp', $archivePath, "${vmName}:$remote/candidate.tar", '--tunnel-through-iap') | Out-Null
    Invoke-CloudSdk @('compute', 'scp', $runnerPath, "${vmName}:$remote/verify.sh", '--tunnel-through-iap') | Out-Null
    $runnerError = $null
    try {
        Invoke-LabSsh "/usr/bin/timeout ${seconds}s /bin/bash $remote/verify.sh $remote/candidate.tar $remote/verification.json"
    } catch {
        $runnerError = $_
    }
    try {
        Invoke-CloudSdk @('compute', 'scp', "${vmName}:$remote/verification.json", $receipt, '--tunnel-through-iap') | Out-Null
    } catch {
        if (-not $runnerError) { throw }
    }
    if ($runnerError) { throw $runnerError }
    $verification = Get-Content -Raw -LiteralPath (Join-Path $receipt 'verification.json') | ConvertFrom-Json
    if ($verification.candidate_sha256 -cne $record.candidate_sha256) {
        throw 'The verifier did not bind its receipt to the supplied archive.'
    }
    foreach ($check in @('drift', 'rag_search_scaler', 'prometheus_alerts')) {
        if ($verification.$check.passed -isnot [bool] -or -not $verification.$check.passed) {
            throw "Verification did not pass for $check."
        }
    }
    $record.passed = $true
} catch {
    $record.error = $_.Exception.Message
    throw
} finally {
    try {
        if ($record.cpu_started) {
            $live = Get-LabCpu
            if ($live.id -ne $initial.id -or ($startedAt -and $live.lastStartTimestamp -ne $startedAt)) {
                throw 'The instance or session changed. Leaving the different session in place.'
            }
            if ($live.status -ne 'TERMINATED') {
                Invoke-CloudSdk @('compute', 'instances', 'stop', $vmName, '--format=value(status)') | Out-Null
            }
            $record.cpu_stopped = (Get-LabCpu).status -eq 'TERMINATED'
            if (-not $record.cpu_stopped) { throw 'Google Cloud did not confirm the CPU stop.' }
        }
    } catch {
        $record.passed = $false
        $record.error = $_.Exception.Message
        throw
    } finally {
        $record | ConvertTo-Json | Set-Content -Encoding utf8 -LiteralPath (Join-Path $receipt 'workflow.json')
    }
}
