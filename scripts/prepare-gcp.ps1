# Validate local Terraform or inspect the authorized project; never apply or create cloud resources.
param(
    [ValidateSet('Validate','Inspect','Plan')][string]$Action = 'Validate',
    [ValidateSet('','basic','standard','accelerated')][string]$Profile = '',
    [string]$ProjectId = '',
    [ValidatePattern('^[a-zA-Z0-9_-]{1,40}$')][string]$RunName = 'gcp-prepare'
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$outputDir = Join-Path $repoRoot "outputs/$RunName"
$moduleDir = Join-Path $repoRoot 'infra/gcp/vm'
$savedTaskPath = $env:Path
$savedSdkPython = $env:CLOUDSDK_PYTHON
$savedCredentialsPath = $env:GOOGLE_APPLICATION_CREDENTIALS
Push-Location $repoRoot
try {
    $env:Path += ';'+[Environment]::GetEnvironmentVariable('Path','User')+';'+[Environment]::GetEnvironmentVariable('Path','Machine')
    $sdkBin = Join-Path $env:LOCALAPPDATA 'Programs/EDAICoursework/google-cloud-sdk/bin'
    if (Test-Path -LiteralPath "$sdkBin/gcloud.cmd") { $env:Path += ";$sdkBin" }
    $pythonExe = Join-Path $repoRoot '.venv/Scripts/python.exe'
    $profiles = Get-Content -LiteralPath 'infra/gcp/capacity-profiles.json' -Raw | ConvertFrom-Json
    $privateVarsPath = Join-Path $moduleDir 'terraform.tfvars.json'
    $privateVars = if (Test-Path -LiteralPath $privateVarsPath) { Get-Content -LiteralPath $privateVarsPath -Raw | ConvertFrom-Json } else { $null }
    if (-not $Profile) { $Profile = if ($privateVars.profile) { $privateVars.profile } else { 'basic' } }
    $capacity = $profiles.$Profile
    if (-not $capacity) { throw 'Local profile is not one of the reviewed configurations.' }
    $env:CLOUDSDK_PYTHON = $pythonExe
    if (Test-Path -LiteralPath $outputDir) { throw 'Choose a new RunName to retain earlier evidence.' }
    New-Item -ItemType Directory -Path $outputDir | Out-Null
    function Invoke-Recorded {
        <# Keep real local output/exit status, and do not continue after failed checks. #>
        param([string]$Executable,[string[]]$CommandArgs,[string]$LogName)
        & $Executable @CommandArgs 2>&1 | Tee-Object -FilePath (Join-Path $outputDir $LogName)
        if ($LASTEXITCODE -ne 0) { throw "Command failed; inspect outputs/$RunName/$LogName" }
    }
    if ($Action -eq 'Validate') {
        Invoke-Recorded 'terraform' @("-chdir=$moduleDir",'init','-backend=false','-input=false','-no-color') 'terraform-init.txt'
        Invoke-Recorded 'terraform' @("-chdir=$moduleDir",'fmt','-check','-recursive') 'terraform-fmt.txt'
        Invoke-Recorded 'terraform' @("-chdir=$moduleDir",'validate','-no-color') 'terraform-validate.txt'
        Invoke-Recorded 'terraform' @("-chdir=$moduleDir",'test','-no-color') 'terraform-test.txt'
        Invoke-Recorded $pythonExe @('infra/gcp/estimate.py','--profile',$Profile,'--report',"$outputDir/cost-estimate.json") 'cost-estimate.txt'
        return
    }
    if ($ProjectId -notmatch '^[a-z][a-z0-9-]{4,28}[a-z0-9]$') { throw 'Supply the existing coursework Project ID.' }
    $gcloudExe = (Get-Command gcloud -ErrorAction Stop).Source
    $operatorAccount = (& $gcloudExe auth list --filter=status:ACTIVE --format='value(account)' --quiet)
    if ($LASTEXITCODE -ne 0 -or -not $operatorAccount) { throw 'Run gcloud auth login --update-adc in your own PowerShell first; send no credentials to chat.' }
    Invoke-Recorded $gcloudExe @('projects','describe',$ProjectId,'--format=json(projectId,lifecycleState)','--quiet') 'project.json'
    Invoke-Recorded $gcloudExe @('billing','projects','describe',$ProjectId,'--format=json(billingEnabled)','--quiet') 'billing.json'
    Invoke-Recorded $gcloudExe @('services','list','--enabled',"--project=$ProjectId",'--format=json(config.name)','--quiet') 'enabled-services.json'
    $billing = Get-Content -LiteralPath "$outputDir/billing.json" -Raw | ConvertFrom-Json
    if (-not $billing.billingEnabled) { throw 'Billing is not linked to this project; do not provision resources.' }
    $services = Get-Content -LiteralPath "$outputDir/enabled-services.json" -Raw | ConvertFrom-Json
    if ('compute.googleapis.com' -notin $services.config.name) {
        Write-Output 'Compute API is not enabled. Inspection stopped without enabling it or creating resources.'
        if ($Action -eq 'Plan') { throw 'Enable required APIs explicitly before a real plan; local mock validation is still available.' }
        return
    }
    Invoke-Recorded $gcloudExe @('compute','regions','describe','us-central1',"--project=$ProjectId",'--format=json(name,status,quotas)','--quiet') 'region-quota.json'
    Invoke-Recorded $gcloudExe @('compute','machine-types','describe',$capacity.machine_type,'--zone=us-central1-a',"--project=$ProjectId",'--format=json(name,guestCpus,memoryMb)','--quiet') 'machine-type.json'
    Invoke-Recorded $gcloudExe @('compute','project-info','describe',"--project=$ProjectId",'--format=json(quotas)','--quiet') 'global-quota.json'
    $regionQuota = Get-Content -LiteralPath "$outputDir/region-quota.json" -Raw | ConvertFrom-Json
    $globalQuota = Get-Content -LiteralPath "$outputDir/global-quota.json" -Raw | ConvertFrom-Json
    $quotaChecks = @()
    # Existing Terraform-owned resources consume quota already; credit them when reviewing their own updates.
    $credits = @{CPUS=0;E2_CPUS=0;CPUS_ALL_REGIONS=0;DISKS_TOTAL_GB=0;SSD_TOTAL_GB=0;IN_USE_ADDRESSES=0;INSTANCES=0}
    $statePath = Join-Path $moduleDir 'terraform.tfstate'
    if (Test-Path -LiteralPath $statePath) {
        $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        $ownedVm = @($state.resources | Where-Object { $_.type -eq 'google_compute_instance' -and $_.name -eq 'coursework' }).instances.attributes
        if ($ownedVm -and $ownedVm.project -eq $ProjectId) {
            Invoke-Recorded $gcloudExe @('compute','instances','list',"--project=$ProjectId",'--format=json(name,status,machineType,networkInterfaces.accessConfigs.natIP)','--quiet','--verbosity=error') 'managed-vm-snapshot.json'
            Invoke-Recorded $gcloudExe @('compute','disks','list',"--project=$ProjectId",'--format=json(name,sizeGb,type)','--quiet','--verbosity=error') 'managed-disk-snapshot.json'
            $live = @(Get-Content -LiteralPath "$outputDir/managed-vm-snapshot.json" -Raw | ConvertFrom-Json | Where-Object name -eq $ownedVm.name)
            if ($live.Count -eq 1) {
                $credits.INSTANCES = 1
                $oldShape = $live[0].machineType.Split('/')[-1]
                $oldCapacity = @($profiles.PSObject.Properties.Value | Where-Object machine_type -eq $oldShape)
                if ($live[0].status -eq 'RUNNING' -and $oldCapacity.Count -eq 1) {
                    $credits.CPUS = $oldCapacity[0].vcpus; $credits.E2_CPUS = $credits.CPUS; $credits.CPUS_ALL_REGIONS = $credits.CPUS
                    $credits.IN_USE_ADDRESSES = @($live[0].networkInterfaces.accessConfigs.natIP | Where-Object { $_ }).Count
                }
                $ownedDiskNames = @($ownedVm.boot_disk.source | ForEach-Object { $_.Split('/')[-1] })
                $ownedDiskNames += @($state.resources | Where-Object { $_.type -eq 'google_compute_disk' -and $_.name -in 'data','scratch' }).instances.attributes.name
                $liveDisks = Get-Content -LiteralPath "$outputDir/managed-disk-snapshot.json" -Raw | ConvertFrom-Json
                foreach ($disk in $liveDisks) {
                    if ($disk.name -in $ownedDiskNames) {
                        if ($disk.type.EndsWith('/pd-standard')) { $credits.DISKS_TOTAL_GB += [double]$disk.sizeGb }
                        elseif ($disk.type.EndsWith('/pd-balanced')) { $credits.SSD_TOTAL_GB += [double]$disk.sizeGb }
                    }
                }
            }
        }
    }
    $standardDiskGib = $capacity.data_gib
    $ssdDiskGib = $capacity.scratch_gib
    if ($capacity.boot_type -eq 'pd-standard') { $standardDiskGib += $capacity.boot_gib } else { $ssdDiskGib += $capacity.boot_gib }
    foreach ($metric in @('CPUS','E2_CPUS','DISKS_TOTAL_GB','SSD_TOTAL_GB','IN_USE_ADDRESSES','INSTANCES')) {
        $need = switch ($metric) { 'CPUS' {$capacity.vcpus} 'E2_CPUS' {$capacity.vcpus} 'DISKS_TOTAL_GB' {$standardDiskGib} 'SSD_TOTAL_GB' {$ssdDiskGib} default {1} }
        $quota = @($regionQuota.quotas | Where-Object metric -eq $metric)
        if ($quota.Count -ne 1) { throw "Cannot verify quota $metric for the initial footprint." }
        $available = $quota[0].limit - $quota[0].usage
        $quotaChecks += @{metric=$metric;required=$need;available=$available;managed_existing=$credits[$metric];additional_needed=[Math]::Max(0,$need-$credits[$metric]);passed=($available+$credits[$metric] -ge $need)}
    }
    $globalCpu = @($globalQuota.quotas | Where-Object metric -eq 'CPUS_ALL_REGIONS')
    if ($globalCpu.Count -eq 1) {
        $available = $globalCpu[0].limit - $globalCpu[0].usage
        $quotaChecks += @{metric='CPUS_ALL_REGIONS';required=$capacity.vcpus;available=$available;managed_existing=$credits.CPUS_ALL_REGIONS;additional_needed=[Math]::Max(0,$capacity.vcpus-$credits.CPUS_ALL_REGIONS);passed=($available+$credits.CPUS_ALL_REGIONS -ge $capacity.vcpus)}
    }
    $quotaChecks | ConvertTo-Json | Set-Content -LiteralPath "$outputDir/quota-checks.json" -Encoding utf8NoBOM
    if ($false -in $quotaChecks.passed) { throw 'The reviewed footprint exceeds current quota. Do not apply.' }
    if ($Action -eq 'Plan') {
        if (-not $env:GOOGLE_APPLICATION_CREDENTIALS) {
            $adcPath = Join-Path $env:APPDATA 'gcloud/application_default_credentials.json'
            if (-not (Test-Path -LiteralPath $adcPath)) {
                # Microsoft Store Python can redirect ADC writes; Go-based Terraform needs the real path.
                $storeCredentials = @(Get-ChildItem -Path "$env:LOCALAPPDATA/Packages/PythonSoftwareFoundation.Python.*/LocalCache/Roaming/gcloud/application_default_credentials.json" -File -ErrorAction SilentlyContinue)
                if ($storeCredentials.Count -ne 1) { throw 'Cannot resolve one ADC file; select its path locally without sharing credentials.' }
                $adcPath = $storeCredentials[0].FullName
            }
            $env:GOOGLE_APPLICATION_CREDENTIALS = $adcPath
        }
        if (-not (Test-Path -LiteralPath $env:GOOGLE_APPLICATION_CREDENTIALS)) { throw 'The selected local ADC file is missing.' }
        $localConfig = Join-Path $moduleDir 'terraform.tfvars.json'
        $expectedMember = "user:$($operatorAccount.Trim())"
        if (Test-Path -LiteralPath $localConfig) {
            $existingConfig = Get-Content -LiteralPath $localConfig -Raw | ConvertFrom-Json
            if ($existingConfig.project_id -ne $ProjectId -or $existingConfig.operator_member -ne $expectedMember) {
                throw 'Existing local project/operator differs; retain it and review before replacing.'
            }
        } else {
            @{project_id=$ProjectId;operator_member=$expectedMember} | ConvertTo-Json | Set-Content -LiteralPath $localConfig -Encoding utf8NoBOM
        }
        Invoke-Recorded 'terraform' @("-chdir=$moduleDir",'init','-backend=false','-input=false','-no-color') 'terraform-init.txt'
        Invoke-Recorded 'terraform' @("-chdir=$moduleDir",'plan','-input=false','-no-color',"-var=profile=$Profile","-out=$outputDir/review.tfplan") 'terraform-plan.txt'
        Write-Output "Plan saved to outputs/$RunName/review.tfplan. No apply was run."
    }
} finally {
    $env:Path = $savedTaskPath
    $env:CLOUDSDK_PYTHON = $savedSdkPython
    $env:GOOGLE_APPLICATION_CREDENTIALS = $savedCredentialsPath
    Pop-Location
}
