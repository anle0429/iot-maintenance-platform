# Run an isolated PostgreSQL-to-Spark data/feature/prediction milestone and keep technical evidence.
param(
    [ValidatePattern('^[a-zA-Z0-9_-]{1,64}$')][string]$RunName = "day2",
    [string]$SourcePath = "data/source/day1",
    [string]$EvidenceDir = "",
    [switch]$SkipBuild,
    [switch]$KeepSource
)
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$savedTaskPath = $env:Path
$savedCommit = $env:IOT_BASE_COMMIT
$sourceStarted = $false
Push-Location $repoRoot
try {
    $env:Path = [Environment]::GetEnvironmentVariable('Path','User') + ';' + [Environment]::GetEnvironmentVariable('Path','Machine')
    $dockerExe = (Get-Command docker -ErrorAction Stop).Source
    if (-not (Test-Path .venv/Scripts/python.exe)) {
        & python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "Could not create Python environment" }
    }
    $pythonExe = Join-Path $repoRoot ".venv/Scripts/python.exe"
    $outputDir = "outputs/$RunName"
    $lakehouseDir = "data/lakehouse/$RunName"
    foreach ($path in @($outputDir, $lakehouseDir)) {
        if (Test-Path -LiteralPath $path) { throw "Run already exists: $path. Choose another -RunName." }
    }
    if ($EvidenceDir -and (Test-Path -LiteralPath $EvidenceDir)) { throw "Evidence directory already exists; preserve prior results." }
    $os = Get-CimInstance Win32_OperatingSystem
    if ($os.FreePhysicalMemory * 1024 -lt 2GB) { throw "Less than 2 GiB RAM free; free memory before this small Spark job." }
    New-Item -ItemType Directory -Force -Path $outputDir,outputs/source-load,data/runtime/secrets,data/spark-events,data/lakehouse | Out-Null
    $secretPath = Join-Path $repoRoot "data/runtime/secrets/source_password"
    if (-not (Test-Path -LiteralPath $secretPath)) {
        $secretBytes = [Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
        [IO.File]::WriteAllText($secretPath, [Convert]::ToBase64String($secretBytes), [Text.UTF8Encoding]::new($false))
    }
    $env:IOT_BASE_COMMIT = (git rev-parse HEAD).Trim()
    $commands = Join-Path $outputDir "commands.txt"
    function Invoke-Checked {
        <# Execute one command, retain its real output, and stop on a nonzero exit status. #>
        param([string]$Executable, [string[]]$CommandArgs, [string]$LogName)
        "$([IO.Path]::GetFileName($Executable)) $($CommandArgs -join ' ')" | Add-Content -Encoding utf8 $commands
        $timer = [Diagnostics.Stopwatch]::StartNew()
        & $Executable @CommandArgs 2>&1 | Tee-Object -FilePath (Join-Path $outputDir $LogName)
        $exitCode = $LASTEXITCODE
        $timer.Stop()
        "exit_code=$exitCode elapsed_seconds=$($timer.Elapsed.TotalSeconds.ToString('F3',[Globalization.CultureInfo]::InvariantCulture))" | Add-Content -Encoding utf8 $commands
        if ($exitCode -ne 0) { throw "Command failed; inspect $outputDir/$LogName" }
    }
    Invoke-Checked $pythonExe @("-m", "unittest", "discover", "-s", "tests", "-v") "unit-tests.txt"
    if (-not (Test-Path -LiteralPath "$SourcePath/manifest.json")) {
        Invoke-Checked $pythonExe @("-m", "generator", "generate", "--output", $SourcePath) "generate.txt"
    }
    if (-not $SkipBuild) {
        Invoke-Checked $dockerExe @("compose", "-f", "infra/compose.yaml", "--profile", "batch", "build", "batch") "build.log"
    }
    Invoke-Checked $dockerExe @("compose", "-f", "infra/compose.yaml", "up", "-d", "--wait", "source") "source-start.txt"
    $sourceStarted = $true
    Invoke-Checked $pythonExe @("-m", "pipelines.source_loader", "--source", $SourcePath, "--run-id", $RunName, "--docker", $dockerExe) "source-load.txt"
    Invoke-Checked $dockerExe @("compose", "-f", "infra/compose.yaml", "run", "--rm", "batch", "--master", "local[2]", "--driver-memory", "768m",
        "--conf", "spark.sql.shuffle.partitions=4", "--conf", "spark.sql.session.timeZone=UTC",
        "--conf", "spark.eventLog.enabled=true", "--conf", "spark.eventLog.dir=file:///workspace/data/spark-events",
        "pipelines/batch_job.py", "--run-id", $RunName, "--output", $lakehouseDir, "--report", "$outputDir/batch-report.json") "spark.log"
    $report = Get-Content -Raw "$outputDir/batch-report.json" | ConvertFrom-Json
    if (-not $report.passed) { throw "Batch report did not pass" }
    $cpu = Get-CimInstance Win32_Processor
    $hashes = @(Get-ChildItem generator,pipelines,ml,tests,infra,scripts -Recurse -File | Where-Object {
        $_.Extension -in ".py", ".ps1", ".json", ".sql", ".yaml" -or $_.Name -eq "Dockerfile"
    } | Sort-Object FullName | ForEach-Object {
        @{path=[IO.Path]::GetRelativePath($repoRoot,$_.FullName).Replace('\','/');sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower()}
    })
    $context = [ordered]@{
        captured_utc=[DateTime]::UtcNow.ToString("o"); machine=$env:COMPUTERNAME; cpu=$cpu.Name.Trim()
        total_ram_bytes=$os.TotalVisibleMemorySize*1024; free_ram_bytes_before=$os.FreePhysicalMemory*1024
        base_commit=$env:IOT_BASE_COMMIT; source_sha256=$hashes
        spark_image_id=(& $dockerExe image inspect iot-coursework-spark:3.5.9 --format '{{.Id}}')
        source_image_id=(& $dockerExe compose -f infra/compose.yaml images source --format json | ConvertFrom-Json).ID
        resource_limits=@{spark_memory="2g";spark_cpus=2;driver_heap="768m";source_memory="192m"}
        source_network="Compose internal access; no published PostgreSQL port"
    }
    [IO.File]::WriteAllText((Join-Path $repoRoot "$outputDir/run-context.json"), (($context | ConvertTo-Json -Depth 6) + "`n"), [Text.UTF8Encoding]::new($false))
    if ($EvidenceDir) {
        New-Item -ItemType Directory -Path $EvidenceDir | Out-Null
        foreach ($name in @("batch-report.json", "run-context.json", "unit-tests.txt", "commands.txt")) {
            Copy-Item -LiteralPath "$outputDir/$name" -Destination (Join-Path $EvidenceDir $name)
        }
    }
    Write-Output "PASS: $RunName. Report: $outputDir/batch-report.json. Parquet/model: $lakehouseDir"
} finally {
    if ($sourceStarted -and -not $KeepSource) {
        & $dockerExe compose -f infra/compose.yaml stop source | Out-Null
    }
    $env:Path = $savedTaskPath
    $env:IOT_BASE_COMMIT = $savedCommit
    Pop-Location
}
