# Verify continuous chunked generation, reproducibility and sampled RAM before planning a future 100 GB run.
param(
    [ValidatePattern('^[a-zA-Z0-9_-]{1,40}$')][string]$RunName = 'chunked-pilot',
    [string]$ConfigPath = 'generator/configs/chunked-pilot.json',
    [string]$EvidenceDir = ''
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $pythonExe = Join-Path $repoRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Create .venv using the README first.' }
    $outputDir = "outputs/$RunName"
    $sourceDir = "data/source/$RunName"
    foreach ($path in @($outputDir,$sourceDir)) {
        if (Test-Path -LiteralPath $path) { throw 'Run exists; choose a new RunName.' }
    }
    if ($EvidenceDir -and (Test-Path -LiteralPath $EvidenceDir)) { throw 'Evidence exists; preserve it and choose a new folder.' }
    $scaleConfig = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    $pilotRows = $scaleConfig.machine_count*$scaleConfig.duration_hours*3600/$scaleConfig.sample_seconds
    if ($scaleConfig.duration_hours -gt 192 -or $pilotRows -gt 1000000) { throw 'Use a small pilot of at most 192 hours and 1,000,000 unique readings.' }
    $os = Get-CimInstance Win32_OperatingSystem
    $freeDiskBefore = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($repoRoot)).AvailableFreeSpace
    if ($os.FreePhysicalMemory*1024 -lt 512MB -or $freeDiskBefore -lt 4GB) { throw 'Small verification needs at least 512 MiB free RAM and 4 GiB free disk.' }
    New-Item -ItemType Directory -Path $outputDir,$sourceDir | Out-Null
    & $pythonExe -m unittest discover -s tests -v 2>&1 | Tee-Object -FilePath "$outputDir/unit-tests.txt"
    if ($LASTEXITCODE -ne 0) { throw 'Unit tests failed.' }

    function Invoke-Measured {
        <# Sample the venv launcher plus discovered Python descendants; never infer RAM from launcher alone. #>
        param([string[]]$CommandArgs,[string]$LogStem)
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
        $nextDiscoveryMs = 0L
        while (-not $process.WaitForExit(50)) {
            if ($timer.ElapsedMilliseconds -ge $nextDiscoveryMs) {
                foreach ($parentId in @($processIds)) {
                    foreach ($child in @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$parentId")) {
                        [void]$processIds.Add([int]$child.ProcessId)
                    }
                }
                $nextDiscoveryMs = $timer.ElapsedMilliseconds+250
            }
            $sampleBytes = 0L
            foreach ($taskProcessId in @($processIds)) {
                try {
                    $member = [Diagnostics.Process]::GetProcessById($taskProcessId)
                    $sampleBytes += $member.WorkingSet64
                    $member.Dispose()
                } catch [ArgumentException] { } catch [InvalidOperationException] { }
            }
            $peakBytes = [Math]::Max($peakBytes,$sampleBytes)
        }
        $timer.Stop()
        $text = $stdoutTask.GetAwaiter().GetResult()+$stderrTask.GetAwaiter().GetResult()
        [IO.File]::WriteAllText((Join-Path $repoRoot "$outputDir/$LogStem.txt"),$text)
        $exitCode = $process.ExitCode
        $process.Dispose()
        $measurement = [ordered]@{arguments=$CommandArgs;exit_code=$exitCode;elapsed_seconds=$timer.Elapsed.TotalSeconds;
            observed_peak_process_tree_working_set_bytes=$peakBytes;observed_process_count=$processIds.Count}
        [IO.File]::WriteAllText((Join-Path $repoRoot "$outputDir/$LogStem-measurement.json"),($measurement | ConvertTo-Json -Depth 5)+"`n")
        if ($exitCode -ne 0) { throw "Command failed; inspect $outputDir/$LogStem.txt" }
        return $measurement
    }

    $commands = [ordered]@{}
    foreach ($spec in @(@('pilot',4096),@('repeat',4096),@('different-chunks',257))) {
        $name,$rows = $spec
        $commands["$name-generate"] = Invoke-Measured @('-m','generator.chunked','generate','--config',$ConfigPath,'--output',"$sourceDir/$name",'--chunk-rows',"$rows") "$name-generate"
        Write-Output "Generated $name; verifying persisted bytes and logical data."
        if ($name -eq 'pilot') {
            $commands['pilot-inspect'] = Invoke-Measured @('-m','generator.chunked','inspect','--source',"$sourceDir/pilot",'--report',"$outputDir/pilot-quality.json") 'pilot-inspect'
        } else {
            $args = @('-m','generator.chunked','compare','--left',"$sourceDir/pilot",'--right',"$sourceDir/$name",'--report',"$outputDir/$name-comparison.json")
            if ($name -eq 'different-chunks') { $args += '--logical-only' }
            $commands["$name-compare"] = Invoke-Measured $args "$name-compare"
        }
    }
    $scaleConfig.duration_hours *= 4
    [IO.File]::WriteAllText((Join-Path $repoRoot "$outputDir/scale-config.json"),($scaleConfig | ConvertTo-Json)+"`n")
    $commands['scale-generate'] = Invoke-Measured @('-m','generator.chunked','generate','--config',"$outputDir/scale-config.json",'--output',"$sourceDir/scale",'--chunk-rows','4096') 'scale-generate'
    $commands['scale-inspect'] = Invoke-Measured @('-m','generator.chunked','inspect','--source',"$sourceDir/scale",'--report',"$outputDir/scale-quality.json") 'scale-inspect'
    foreach ($plan in @('with-stream','offline-only')) {
        $args = @('-m','generator.chunked','plan','--source',"$sourceDir/pilot",'--target-offline-gb','100','--report',"$outputDir/100gb-$plan-plan.json")
        if ($plan -eq 'offline-only') { $args += '--offline-only' }
        $commands["plan-$plan"] = Invoke-Measured $args "plan-$plan"
    }

    # The shared physical iterator changed, so verify the capped command still writes all original day-1 bytes.
    $commands['legacy-generate'] = Invoke-Measured @('-m','generator','generate','--config','generator/configs/dev.json','--output',"$sourceDir/legacy") 'legacy-generate'
    if (Test-Path -LiteralPath data/source/day1/manifest.json) {
        $commands['legacy-compare'] = Invoke-Measured @('-m','generator','compare','--left','data/source/day1','--right',"$sourceDir/legacy",'--report',"$outputDir/legacy-regression.json") 'legacy-compare'
    }
    foreach ($name in @('pilot','scale')) { Copy-Item -LiteralPath "$sourceDir/$name/manifest.json" -Destination "$outputDir/$name-manifest.json" }
    $pilot = Get-Content -Raw "$outputDir/pilot-quality.json" | ConvertFrom-Json
    $scale = Get-Content -Raw "$outputDir/scale-quality.json" | ConvertFrom-Json
    $report = [ordered]@{
        passed=$true;captured_utc=[DateTime]::UtcNow.ToString('o');machine=$env:COMPUTERNAME
        cpu=(Get-CimInstance Win32_Processor).Name.Trim();python=(& $pythonExe --version)
        base_commit=(git rev-parse HEAD).Trim();free_ram_bytes_before=$os.FreePhysicalMemory*1024;free_disk_bytes_before=$freeDiskBefore
        format='JSONL';compression='none';pilot_unique_rows=$pilot.unique_telemetry_rows;pilot_offline_bytes=$pilot.offline_payload_bytes
        scale_unique_rows=$scale.unique_telemetry_rows;scale_offline_bytes=$scale.offline_payload_bytes
        workload_row_ratio=$scale.unique_telemetry_rows/$pilot.unique_telemetry_rows
        memory_measurement='Sampled sum of launcher/descendant working sets; wait 50ms, discover descendants >=250ms. Observed lower bound; shared pages may be counted twice. One run per workload, background apps/cache uncontrolled.'
        commands=$commands;script_sha256=(Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLower()
        source_sha256=@(Get-ChildItem generator,tests -Recurse -File -Filter *.py | Sort-Object FullName | ForEach-Object {
            @{path=[IO.Path]::GetRelativePath($repoRoot,$_.FullName).Replace('\','/');sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower()}
        })
        scope='Small continuous sources only; 100 GB plans are estimates, not generated data. No Spark/Flink performance benchmark or large-source ingestion claimed.'
    }
    [IO.File]::WriteAllText((Join-Path $repoRoot "$outputDir/chunked-report.json"),($report | ConvertTo-Json -Depth 12)+"`n")
    if ($EvidenceDir) {
        New-Item -ItemType Directory -Path $EvidenceDir | Out-Null
        Get-ChildItem -LiteralPath $outputDir -File | Where-Object { $_.Extension -in '.json','.txt' -and $_.Name -notlike '*-measurement.json' } | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $EvidenceDir
        }
    }
    Write-Output "PASS: chunked generation/readback/repeat/chunk-independence/4x workload; $outputDir/chunked-report.json"
} finally {
    Pop-Location
}
