# Measure sequential bounded generator shards, persisted offline bytes and independent validation.
param(
    [ValidatePattern('^[a-zA-Z0-9_-]{1,64}$')][string]$RunName = "throughput2",
    [ValidateRange(1,8)][int]$Shards = 4
)
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $pythonExe = Join-Path $repoRoot ".venv/Scripts/python.exe"
    if (-not (Test-Path -LiteralPath $pythonExe)) { throw "Create .venv using the README first." }
    $outputDir = "outputs/$RunName"
    $sourceDir = "data/source/$RunName"
    if ((Test-Path -LiteralPath $outputDir) -or (Test-Path -LiteralPath $sourceDir)) { throw "Run exists; choose another -RunName." }
    $os = Get-CimInstance Win32_OperatingSystem
    $disk = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($repoRoot))
    $freeDiskBefore = $disk.AvailableFreeSpace
    if ($os.FreePhysicalMemory * 1024 -lt 1GB -or $disk.AvailableFreeSpace -lt 3GB) {
        throw "Need at least 1 GiB free RAM and 3 GiB disk for this bounded measurement."
    }
    New-Item -ItemType Directory -Path $outputDir,$sourceDir | Out-Null
    function Invoke-Measured {
        <# Measure the venv launcher and its Python descendants, retaining actual command output. #>
        param([string[]]$CommandArgs, [string]$LogStem)
        $info = [Diagnostics.ProcessStartInfo]::new($pythonExe)
        $info.WorkingDirectory = $repoRoot
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.RedirectStandardOutput = $true
        $info.RedirectStandardError = $true
        foreach ($argument in $CommandArgs) { $info.ArgumentList.Add($argument) }
        $process = [Diagnostics.Process]::new()
        $process.StartInfo = $info
        $timer = [Diagnostics.Stopwatch]::StartNew()
        [void]$process.Start()
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $peakBytes = 0L
        $processIds = [Collections.Generic.HashSet[int]]::new()
        [void]$processIds.Add($process.Id)
        $nextDiscoveryMs = 0
        while (-not $process.WaitForExit(50)) {
            if ($timer.ElapsedMilliseconds -ge $nextDiscoveryMs) {
                foreach ($parentId in @($processIds)) {
                    foreach ($child in @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$parentId")) {
                        [void]$processIds.Add([int]$child.ProcessId)
                    }
                }
                $nextDiscoveryMs = $timer.ElapsedMilliseconds + 250
            }
            $sampleBytes = 0L
            foreach ($taskProcessId in @($processIds)) {
                try {
                    $member = [Diagnostics.Process]::GetProcessById($taskProcessId)
                    $sampleBytes += $member.WorkingSet64
                    $member.Dispose()
                } catch [ArgumentException] { } catch [InvalidOperationException] { }
            }
            $peakBytes = [Math]::Max($peakBytes, $sampleBytes)
        }
        $timer.Stop()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        [IO.File]::WriteAllText((Join-Path $repoRoot "$outputDir/$LogStem.txt"), $stdout + $stderr)
        $exitCode = $process.ExitCode
        $process.Dispose()
        if ($exitCode -ne 0) { throw "Measurement failed; inspect $outputDir/$LogStem.txt" }
        return [ordered]@{arguments=$CommandArgs;exit_code=$exitCode;elapsed_seconds=$timer.Elapsed.TotalSeconds;observed_peak_process_tree_working_set_bytes=$peakBytes;observed_process_count=$processIds.Count}
    }
    $runs = @()
    for ($index = 0; $index -lt $Shards; $index++) {
        $shard = "shard-{0:D2}" -f ($index + 1)
        $config = Get-Content -Raw generator/configs/dev.json | ConvertFrom-Json
        $config.machine_count = 32
        $config.seed = 42 + $index
        $configPath = "$outputDir/$shard-config.json"
        [IO.File]::WriteAllText((Join-Path $repoRoot $configPath), ($config | ConvertTo-Json) + "`n")
        $generation = Invoke-Measured @("-m","generator","generate","--config",$configPath,"--output","$sourceDir/$shard") "$shard-generate"
        $inspection = Invoke-Measured @("-m","generator","inspect","--source","$sourceDir/$shard","--report","$outputDir/$shard-quality.json") "$shard-inspect"
        $quality = Get-Content -Raw "$outputDir/$shard-quality.json" | ConvertFrom-Json
        if (-not $quality.passed) { throw "Persisted shard validation failed." }
        $files = @(Get-ChildItem -LiteralPath "$sourceDir/$shard" -Recurse -File)
        $offline = @($files | Where-Object { $_.Extension -eq '.jsonl' -and $_.FullName -notmatch '[\\/]stream[\\/]' })
        $offlineBytes = ($offline | Measure-Object -Property Length -Sum).Sum
        $runs += [ordered]@{
            shard=$shard;config=$config;unique_readings=$quality.unique_telemetry_rows
            offline_bytes=$offlineBytes;total_source_bytes=($files | Measure-Object -Property Length -Sum).Sum
            quality_checks=@($quality.checks.PSObject.Properties).Count;generation=$generation;inspection=$inspection
        }
        Write-Output "PASS: $shard; offline_bytes=$offlineBytes; generation_seconds=$($generation.elapsed_seconds)"
    }
    $totalSeconds = ($runs | ForEach-Object { $_.generation.elapsed_seconds } | Measure-Object -Sum).Sum
    $offlineTotal = ($runs | ForEach-Object { $_.offline_bytes } | Measure-Object -Sum).Sum
    $report = [ordered]@{
        passed=$true;captured_utc=[DateTime]::UtcNow.ToString('o');machine=$env:COMPUTERNAME
        cpu=(Get-CimInstance Win32_Processor).Name.Trim();base_commit=(git rev-parse HEAD).Trim()
        python=(& $pythonExe --version);free_ram_bytes_before=$os.FreePhysicalMemory*1024;free_disk_bytes_before=$freeDiskBefore
        format='JSONL';compression='none';runs=$runs;unique_readings=($runs | ForEach-Object { $_.unique_readings } | Measure-Object -Sum).Sum
        offline_bytes=$offlineTotal;generation_seconds=$totalSeconds;offline_bytes_per_generation_second=$offlineTotal/$totalSeconds
        memory_measurement='Maximum sampled sum of working sets for the venv launcher and discovered descendants. Wait 50ms between samples, discover child PIDs every >=250ms. Observed lower bound, shared pages may be counted twice; excludes other applications.'
        script_sha256=(Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLower()
        source_sha256=@(Get-ChildItem generator -Recurse -File -Filter *.py | Sort-Object FullName | ForEach-Object {
            @{path=[IO.Path]::GetRelativePath($repoRoot,$_.FullName);sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower()}
        })
        scope='Sequential independent 48h simulations with distinct seeds, not one continuous simulation or a chunked generator. One run per seed, filesystem cache uncontrolled. Generation time includes stream fixture creation; offline byte numerator excludes stream and manifest. No 100GB or Spark optimization claim.'
    }
    [IO.File]::WriteAllText((Join-Path $repoRoot "$outputDir/generator-throughput.json"), ($report | ConvertTo-Json -Depth 10) + "`n")
    Write-Output "PASS: $RunName; measured offline_bytes=$offlineTotal; report=$outputDir/generator-throughput.json"
} finally {
    Pop-Location
}
