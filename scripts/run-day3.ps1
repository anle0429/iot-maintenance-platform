# Verify Kafka/Flink late events and replay on known-answer and generator workloads, then stop coursework services.
param(
    [ValidatePattern('^[a-zA-Z0-9_-]{1,40}$')][string]$RunName = "day3",
    [string]$SourcePath = "data/source/day1",
    [string]$EvidenceDir = "",
    [switch]$SkipBuild,
    [switch]$FixtureOnly,
    [ValidateRange(0,60)][int]$UiPauseSeconds = 0
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$savedTaskPath = $env:Path
$startedSource = $false
Push-Location $repoRoot
try {
    $env:Path += ';' + [Environment]::GetEnvironmentVariable('Path','User') + ';' + [Environment]::GetEnvironmentVariable('Path','Machine')
    $dockerExe = (Get-Command docker -ErrorAction Stop).Source
    $pythonExe = Join-Path $repoRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Create the host .venv using README first.' }
    if ((Test-Path -LiteralPath "outputs/$RunName") -or (Test-Path -LiteralPath "data/stream/$RunName")) { throw 'Run exists; choose another name.' }
    if ($EvidenceDir -and (Test-Path -LiteralPath $EvidenceDir)) { throw 'Evidence exists; preserve it and choose a new folder.' }
    if (-not $FixtureOnly -and -not (Test-Path -LiteralPath "$SourcePath/manifest.json")) { throw 'Generate/inspect the small source using run-day1.ps1 first.' }
    $os = Get-CimInstance Win32_OperatingSystem
    if ($os.FreePhysicalMemory*1024 -lt 1GB) { throw 'Less than 1 GiB host RAM free; free memory before this bounded streaming run.' }
    New-Item -ItemType Directory -Path outputs -Force | Out-Null
    & $pythonExe -m unittest discover -s tests -v 2>&1 | Tee-Object -FilePath outputs/day3-unit-tests-latest.txt
    if ($LASTEXITCODE -ne 0) { throw 'Unit tests failed.' }
    if (-not $SkipBuild) {
        & $dockerExe compose -f infra/streaming.compose.yaml build flink
        if ($LASTEXITCODE -ne 0) { throw 'Flink image build failed.' }
    }
    $startedSource = $true
    & $dockerExe compose -f infra/streaming.compose.yaml up -d --wait kafka
    if ($LASTEXITCODE -ne 0) { throw 'Kafka start failed.' }
    $runArgs = @('-m','pipelines.stream_verification','--run-id',$RunName,'--docker',$dockerExe,'--source',$SourcePath,'--ui-pause-seconds',"$UiPauseSeconds")
    if ($FixtureOnly) { $runArgs += '--fixture-only' }
    & $pythonExe @runArgs
    if ($LASTEXITCODE -ne 0) { throw "Streaming failed; inspect outputs/$RunName. No successful milestone claimed." }
    Copy-Item -LiteralPath outputs/day3-unit-tests-latest.txt -Destination "outputs/$RunName/unit-tests.txt"
    $cpu = Get-CimInstance Win32_Processor
    $context = [ordered]@{
        captured_utc=[DateTime]::UtcNow.ToString('o');machine=$env:COMPUTERNAME;cpu=$cpu.Name.Trim()
        total_ram_bytes=$os.TotalVisibleMemorySize*1024;free_ram_bytes_before=$os.FreePhysicalMemory*1024
        base_commit=(git rev-parse HEAD).Trim();script_sha256=(Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLower()
        flink_image_id=(& $dockerExe image inspect iot-coursework-flink:1.20.3 --format '{{.Id}}')
        kafka_image_id=(& $dockerExe compose -f infra/streaming.compose.yaml images kafka --format json | ConvertFrom-Json).ID
        resource_limits=@{flink_memory='3g';flink_cpus=2;kafka_memory='768m';kafka_heap='256m'}
        scope='Local single-partition bounded-offset streaming; host preflight is a minimum guard, not a capacity guarantee. Dataset and SQLite stay local.'
    }
    [IO.File]::WriteAllText((Join-Path $repoRoot "outputs/$RunName/run-context.json"),($context | ConvertTo-Json -Depth 5)+"`n")
    if ($EvidenceDir) {
        New-Item -ItemType Directory -Path $EvidenceDir | Out-Null
        foreach ($file in @(Get-ChildItem -LiteralPath "outputs/$RunName" -File | Where-Object { $_.Extension -in '.json','.txt' })) {
            Copy-Item -LiteralPath $file.FullName -Destination $EvidenceDir
        }
    }
    Write-Output "PASS: $RunName; outputs/$RunName/stream-report.json; data/stream/$RunName/_RUN_SUCCESS.json"
} finally {
    if ($startedSource) {
        & $dockerExe compose -f infra/streaming.compose.yaml stop kafka | Out-Null
    }
    $env:Path = $savedTaskPath
    Pop-Location
}
