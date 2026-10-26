# Verify a chunked source, run the isolated Spark JSONL/Parquet milestone, and save real footprint evidence.
param(
    [ValidatePattern('^[a-zA-Z0-9_-]{1,40}$')][string]$RunName = 'chunk-ingest',
    [string]$SourcePath = 'data/source/chunked-release/pilot',
    [string]$ReceiptPath = '',
    [string]$EvidenceDir = '',
    [switch]$SkipBuild,
    [ValidateRange(0,60)][int]$UiPauseSeconds = 0
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$savedTaskPath = $env:Path
Push-Location $repoRoot
try {
    $env:Path += ';'+[Environment]::GetEnvironmentVariable('Path','User')+';'+[Environment]::GetEnvironmentVariable('Path','Machine')
    $dockerExe = (Get-Command docker -ErrorAction Stop).Source
    $pythonExe = Join-Path $repoRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Create .venv using README first.' }
    $outputDir = "outputs/$RunName"
    $lakehouseDir = "data/lakehouse/$RunName"
    foreach ($path in @($outputDir,$lakehouseDir)) {
        if (Test-Path -LiteralPath $path) { throw 'Run exists; preserve it and choose a new name.' }
    }
    if ($EvidenceDir -and (Test-Path -LiteralPath $EvidenceDir)) { throw 'Evidence directory already exists.' }
    $os = Get-CimInstance Win32_OperatingSystem
    $diskBefore = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($repoRoot)).AvailableFreeSpace
    if ($os.FreePhysicalMemory*1024 -lt 2GB -or $diskBefore -lt 8GB) { throw 'Need 2 GiB free host RAM and 8 GiB disk for this bounded Spark check.' }
    New-Item -ItemType Directory -Force -Path $outputDir,data/ingest-events,data/lakehouse | Out-Null
    function Invoke-Checked {
        <# Record real command output, elapsed time and exit code; stop on the first failure. #>
        param([string]$Executable,[string[]]$CommandArgs,[string]$LogName)
        $timer = [Diagnostics.Stopwatch]::StartNew()
        & $Executable @CommandArgs 2>&1 | Tee-Object -FilePath "$outputDir/$LogName"
        $exitCode = $LASTEXITCODE
        $timer.Stop()
        $entry = [ordered]@{executable=[IO.Path]::GetFileName($Executable);arguments=$CommandArgs;exit_code=$exitCode;elapsed_seconds=$timer.Elapsed.TotalSeconds}
        $commandRecords.Add($entry)
        [IO.File]::WriteAllText((Join-Path $repoRoot "$outputDir/commands.json"),($commandRecords | ConvertTo-Json -Depth 7)+"`n")
        if ($exitCode -ne 0) { throw "Command failed; inspect $outputDir/$LogName" }
    }
    $commandRecords = [Collections.Generic.List[object]]::new()
    Invoke-Checked $pythonExe @('-m','unittest','discover','-s','tests','-v') 'unit-tests.txt'
    if ($ReceiptPath) {
        Copy-Item -LiteralPath $ReceiptPath -Destination "$outputDir/receipt.json"
    } else {
        Invoke-Checked $pythonExe @('-m','pipelines.chunk_source','--source',$SourcePath,'--report',"$outputDir/receipt.json") 'source-validation.txt'
    }
    if (-not $SkipBuild) {
        Invoke-Checked $dockerExe @('compose','-f','infra/ingest.compose.yaml','build','ingest') 'build.txt'
    }
    # This chmod is limited to the new project's Linux scratch volume; the normal job still runs as spark.
    Invoke-Checked $dockerExe @('compose','-f','infra/ingest.compose.yaml','run','--rm','--user','0','--entrypoint','chmod','ingest','1777','/scratch') 'scratch-init.txt'
    $containerName = "iot-ingest-$RunName".ToLowerInvariant()
    Invoke-Checked $dockerExe @('compose','-f','infra/ingest.compose.yaml','run','--rm','--service-ports','--name',$containerName,'ingest',
        '--master','local[2]','--driver-memory','1g','--conf','spark.sql.session.timeZone=UTC',
        '--conf','spark.driver.bindAddress=0.0.0.0','--conf','spark.driver.host=127.0.0.1',
        '--conf',"spark.local.dir=/scratch/$RunName",'--conf','spark.eventLog.enabled=true',
        '--conf','spark.eventLog.dir=file:///workspace/data/ingest-events',
        'pipelines/chunk_ingest.py','--source',$SourcePath,'--receipt',"$outputDir/receipt.json",'--output',$lakehouseDir,
        '--report',"$outputDir/ingest-report.json",'--partitions','8','--query-repetitions','3','--ui-pause-seconds',"$UiPauseSeconds") 'spark-console.txt'
    $report = Get-Content -LiteralPath "$outputDir/ingest-report.json" -Raw | ConvertFrom-Json
    if (-not $report.passed -or -not (Test-Path -LiteralPath "$lakehouseDir/_RUN_SUCCESS.json")) { throw 'Missing successful ingest report/marker.' }
    $eventLog = "data/ingest-events/$($report.application_id)"
    Invoke-Checked $pythonExe @('-m','pipelines.ingest_metrics','--log',$eventLog,'--report',"$outputDir/spark-metrics.json") 'metrics.txt'
    $context = [ordered]@{
        captured_utc=[DateTime]::UtcNow.ToString('o');machine=$env:COMPUTERNAME;cpu=(Get-CimInstance Win32_Processor).Name.Trim()
        base_commit=(git rev-parse HEAD).Trim();free_ram_bytes_before=$os.FreePhysicalMemory*1024;free_disk_bytes_before=$diskBefore
        source=$SourcePath;script_sha256=(Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLower()
        spark_image_id=(& $dockerExe image inspect iot-coursework-spark:3.5.9 --format '{{.Id}}')
        resource_limits=@{container='2560m';cpus=2;driver_heap='1g';local_master='local[2]';shuffle_partitions=8}
        ui_pause_seconds=$UiPauseSeconds;output_total_bytes=(Get-ChildItem -LiteralPath $lakehouseDir -Recurse -File | Measure-Object -Property Length -Sum).Sum
        source_sha256=@(Get-ChildItem generator,pipelines,tests,infra -Recurse -File | Where-Object { $_.Extension -in '.py','.yaml','.json' -or $_.Name -eq 'Dockerfile' } | Sort-Object FullName | ForEach-Object {
            @{path=[IO.Path]::GetRelativePath($repoRoot,$_.FullName).Replace('\','/');sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower()}
        })
        scope='One local ingest plus three same-query timing pairs; actual event metrics and cgroup memory, no large-scale/production/Airflow claim.'
    }
    [IO.File]::WriteAllText((Join-Path $repoRoot "$outputDir/run-context.json"),($context | ConvertTo-Json -Depth 6)+"`n")
    if ($EvidenceDir) {
        New-Item -ItemType Directory -Path $EvidenceDir | Out-Null
        Get-ChildItem -LiteralPath $outputDir -File | Where-Object { $_.Extension -in '.json','.txt' } | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $EvidenceDir }
    }
    Write-Output "PASS: $RunName; $outputDir/ingest-report.json"
} finally {
    $env:Path = $savedTaskPath
    Pop-Location
}
