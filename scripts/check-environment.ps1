# Measure this machine's toolchain and resources without starting any services.
param([string]$OutputPath = "outputs/environment.json")
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
function Read-OptionalTool {
    <# Record an optional executable's status without failing local verification if absent. #>
    param([string]$Name, [string[]]$CommandArgs)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        return @{ output = "NOT FOUND in PATH"; exit_code = $null; available = $false }
    }
    $toolOutput = & $Name @CommandArgs 2>&1 | Out-String
    return @{ output = $toolOutput.Trim(); exit_code = $LASTEXITCODE; available = $true }
}
Push-Location $repoRoot
try {
    $machine = Get-CimInstance Win32_ComputerSystem
    $cpu = Get-CimInstance Win32_Processor
    $os = Get-CimInstance Win32_OperatingSystem
    $disk = Get-PSDrive -Name ([IO.Path]::GetPathRoot($repoRoot).Substring(0, 1))
    $gitHead = & git rev-parse --verify HEAD 2>$null
    if ($LASTEXITCODE -ne 0) { $gitHead = "UNBORN (no commit)" }
    $docker = Read-OptionalTool -Name docker -CommandArgs @("version")
    $dockerReady = $docker.available -and $docker.exit_code -eq 0
    $compose = Read-OptionalTool -Name docker -CommandArgs @("compose", "version")
    $wsl = Read-OptionalTool -Name wsl -CommandArgs @("--status")
    $wslState = $wsl.output -replace "`0", ""
    $gpu = Read-OptionalTool -Name nvidia-smi -CommandArgs @("--query-gpu=name,memory.total,driver_version", "--format=csv,noheader")
    $pythonVersion = & ./.venv/Scripts/python.exe --version 2>&1 | Out-String
    $sourceHashes = @(Get-ChildItem generator,tests,scripts -Recurse -File | Where-Object {
        $_.Extension -in ".py", ".ps1", ".json"
    } | Sort-Object FullName | ForEach-Object {
        @{ path = [IO.Path]::GetRelativePath($repoRoot, $_.FullName).Replace('\', '/'); sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower() }
    })
    $evidence = [ordered]@{
        captured_utc = [DateTime]::UtcNow.ToString("o")
        machine = $machine.Name
        os = $os.Caption
        cpu = $cpu.Name.Trim()
        cores = $cpu.NumberOfCores
        threads = $cpu.NumberOfLogicalProcessors
        ram_bytes = $machine.TotalPhysicalMemory
        free_ram_bytes = $os.FreePhysicalMemory * 1024
        workspace_disk_free_bytes = $disk.Free
        gpu = $gpu.output
        python = $pythonVersion.Trim()
        powershell = $PSVersionTable.PSVersion.ToString()
        git = (& git --version)
        git_head = $gitHead
        git_status = @(& git status --short --branch)
        remotes = @(& git remote -v)
        docker_engine_ready = $dockerReady
        docker_version = $docker.output
        docker_compose = $compose.output
        wsl_status = $wslState.Trim()
        source_sha256 = $sourceHashes
    }
    $resolvedOutput = [IO.Path]::GetFullPath((Join-Path $repoRoot $OutputPath))
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $resolvedOutput) | Out-Null
    [IO.File]::WriteAllText($resolvedOutput, ($evidence | ConvertTo-Json -Depth 6) + "`n", [Text.UTF8Encoding]::new($false))
    Write-Output "Environment snapshot: $OutputPath (Docker ready: $dockerReady)"
} finally {
    Pop-Location
}
