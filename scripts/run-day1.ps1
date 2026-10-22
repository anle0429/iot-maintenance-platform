# Run the small generator milestone end to end and retain real verification outputs.
param(
    [ValidatePattern('^[a-zA-Z0-9_-]+$')][string]$RunName = "day1",
    [string]$EvidenceDir = "",
    [string]$ConfigPath = "generator/configs/dev.json"
)
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    if (-not (Test-Path .venv/Scripts/python.exe)) {
        & python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "Could not create .venv" }
    }
    $pythonExe = Join-Path $repoRoot ".venv/Scripts/python.exe"
    if (-not $EvidenceDir) { $EvidenceDir = "outputs/$RunName" }
    $source = "data/source/$RunName"
    $repeat = "data/source/$RunName-repeat"
    foreach ($path in @($source, $repeat, $EvidenceDir)) {
        if (Test-Path -LiteralPath $path) { throw "Path already exists: $path. Choose a new -RunName and -EvidenceDir to preserve prior evidence." }
    }
    New-Item -ItemType Directory -Path $EvidenceDir -Force | Out-Null
    $logPath = Join-Path $EvidenceDir "commands.txt"
    function Invoke-Verification {
        <# Run one public Python command, record its output, and stop on failure. #>
        param([string[]]$CommandArgs)
        "python $($CommandArgs -join ' ')" | Tee-Object -FilePath $logPath -Append
        $timer = [Diagnostics.Stopwatch]::StartNew()
        & $pythonExe @CommandArgs 2>&1 | Tee-Object -FilePath $logPath -Append
        $exitCode = $LASTEXITCODE
        $timer.Stop()
        "exit_code=$exitCode elapsed_seconds=$($timer.Elapsed.TotalSeconds.ToString('F3', [Globalization.CultureInfo]::InvariantCulture))" | Tee-Object -FilePath $logPath -Append
        if ($exitCode -ne 0) { throw "Verification failed: $($CommandArgs -join ' ')" }
    }
    Invoke-Verification -CommandArgs @("-m", "unittest", "discover", "-s", "tests", "-v")
    Invoke-Verification -CommandArgs @("-m", "generator", "generate", "--config", $ConfigPath, "--output", $source)
    Invoke-Verification -CommandArgs @("-m", "generator", "inspect", "--source", $source, "--report", "$EvidenceDir/quality.json")
    Invoke-Verification -CommandArgs @("-m", "generator", "generate", "--config", $ConfigPath, "--output", $repeat)
    Invoke-Verification -CommandArgs @("-m", "generator", "compare", "--left", $source, "--right", $repeat, "--report", "$EvidenceDir/reproducibility.json")
    & "$PSScriptRoot/check-environment.ps1" -OutputPath "$EvidenceDir/environment.json"
    Write-Output "Day-1 verification passed. Evidence: $EvidenceDir; source: $source"
} finally {
    Pop-Location
}
