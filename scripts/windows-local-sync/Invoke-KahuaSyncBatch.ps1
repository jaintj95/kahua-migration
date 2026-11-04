#requires -Version 5.1
<#
Plan prepares every CSV row without touching Kahua. Check navigates and downloads
originals without uploading or changing sync. Execute reconciles, copies missing
files, verifies independent server downloads, and saves/reopens sync OFF.
Rerun with the same CSV/state to resume. No destructive reset or file replacement.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$CsvPath,
    [Parameter(Mandatory=$true)][string]$StateRoot,
    [ValidateSet('Plan','Check','Execute')][string]$Mode='Plan',
    [switch]$InitializeLocalFiles,
    [ValidateRange(30,86400)][int]$SyncTimeoutSeconds=1800,
    [ValidateRange(10,3600)][int]$DownloadTimeoutSeconds=120
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if ($InitializeLocalFiles -and $Mode -ne 'Execute') { throw '-InitializeLocalFiles is only valid with Execute.' }
Import-Module (Join-Path $PSScriptRoot 'KahuaSyncCore.psm1') -Force
$rows=@(Read-KahuaSyncCsv $CsvPath $StateRoot)
$state=Resolve-KahuaPath $StateRoot
[void][IO.Directory]::CreateDirectory($state)
$lock=[IO.File]::Open((Join-Path $state 'batch.lock'),'OpenOrCreate','ReadWrite','None')
$mutex=$null; $ownsMutex=$false; $current=$null; $phase='preflight'
$attempt=[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,8)
$report=[ordered]@{attempt=$attempt;mode=$Mode;status='preflight';started_at_utc=[DateTime]::UtcNow.ToString('o');projects=@()}
$attemptRoot=Join-Path (Join-Path $state 'attempts') $attempt
[void][IO.Directory]::CreateDirectory($attemptRoot)
function Save-Report { Write-KahuaJson (Join-Path $attemptRoot 'batch-result.json') $report }
function Save-Phase([string]$Name, $Extra=$null) {
    $script:phase=$Name
    Write-KahuaJson (Join-Path $current.state_root 'journal.json') ([ordered]@{
        attempt=$attempt;phase=$Name;status='running';server_verified=$false;
        updated_at_utc=[DateTime]::UtcNow.ToString('o');detail=$Extra})
}
function Identity($Row) { return @{Environment=$Row.environment;Domain=$Row.domain;Number=$Row.project_number;Name=$Row.project_name} }
function Server-Check($Row, $Manifest, [string]$Label) {
    $downloadRoot=Join-Path (Join-Path $attemptRoot $Row.run_id) ($Label+'-'+[Guid]::NewGuid().ToString('N').Substring(0,8))
    $identity=Identity $Row
    $receipts=@(Receive-KahuaManifest -Identity $identity -Manifest $Manifest -DownloadRoot $downloadRoot -AllowMissing -TimeoutSeconds $DownloadTimeoutSeconds)
    $result=Test-KahuaDownloadedFiles -Manifest $Manifest -Receipts $receipts -DownloadRoot $downloadRoot -AllowMissing -Identity $identity -StagingRoot (Join-Path $Row.state_root 'staged')
    Write-KahuaJson (Join-Path $downloadRoot 'verification.json') $result
    return $result
}
function Wait-ExistingLocal($Row, $Verified) {
    $deadline=[DateTime]::UtcNow.AddSeconds($SyncTimeoutSeconds)
    do {
        $pending=@()
        foreach ($file in @($Verified.files | Where-Object status -eq 'verified')) {
            $path=Join-Path $Row.sync_root $file.relative_path.Replace('/','\')
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { $pending += $file.relative_path; continue }
            # Incoming sync files may still be downloading. Never publish over them.
            try {
                $stream=[IO.File]::Open($path,'Open','Read','None'); $stream.Dispose()
                $hash=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
                if ($hash -cne $file.sha256 -or (Get-Item -LiteralPath $path).Length -ne $file.bytes) { $pending += $file.relative_path }
            } catch { $pending += $file.relative_path }
        }
        $rootsReady=(Test-Path -LiteralPath (Join-Path $Row.sync_root 'Documents') -PathType Container) -and
            (Test-Path -LiteralPath (Join-Path $Row.sync_root 'Photos') -PathType Container)
        if (-not $pending.Count -and $rootsReady) { return }
        if ([DateTime]::UtcNow -ge $deadline) { throw "Existing Kahua files did not reconcile locally: $($pending -join ', ')" }
        Write-Host "Waiting for Kahua local reconciliation: $($pending.Count) source paths pending."
        Start-Sleep -Seconds 3
    } while ($true)
}
try {
    $bindingPath=Join-Path $state 'batch-binding.json'
    $binding=[ordered]@{schema=1;projects=$rows}
    if (Test-Path -LiteralPath $bindingPath) {
        $previous=Get-Content -LiteralPath $bindingPath -Raw | ConvertFrom-Json
        if (($previous | ConvertTo-Json -Depth 20 -Compress) -cne ($binding | ConvertTo-Json -Depth 20 -Compress)) {
            throw 'CSV mappings changed for this batch state. Use the original CSV or a new state directory.'
        }
    } else { Write-KahuaJson $bindingPath $binding }
    # All ZIP extraction, source hashes, collisions, and local conflicts are
    # checked before any desktop navigation or sync-setting change.
    $manifests=@{}; $mappingPaths=@{}
    foreach ($row in $rows) {
        $current=$row
        [void][IO.Directory]::CreateDirectory($row.state_root)
        $mapping=Join-Path $row.state_root 'mapping.json'
        Write-KahuaJson $mapping $row
        & (Join-Path $PSScriptRoot 'Invoke-KahuaLocalSync.ps1') -MappingPath $mapping -Mode Plan | Out-Host
        $mappingPaths[$row.run_id]=$mapping
        $manifests[$row.run_id]=Get-Content -LiteralPath (Join-Path $row.state_root 'manifest.json') -Raw | ConvertFrom-Json
    }
    $report.status='planned'; Save-Report
    if ($Mode -eq 'Plan') { Write-Output "Planned $($rows.Count) project(s). Evidence: $attemptRoot"; return }
    Import-Module (Join-Path $PSScriptRoot 'KahuaDesktop.psm1') -Force
    Import-Module (Join-Path $PSScriptRoot 'KahuaFileManager.psm1') -Force
    $mutex=[Threading.Mutex]::new($false,'Local\KahuaMigrationDesktop')
    try { $ownsMutex=$mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $ownsMutex=$true }
    if (-not $ownsMutex) { throw 'Another Kahua desktop batch is running in this session.' }
    # Validate all exact project identities and required root folders before
    # enabling sync on the first project. Missing roots are an actionable error.
    foreach ($row in $rows) {
        $current=$row; $id=Identity $row
        Select-KahuaProject @id
        if ($Mode -eq 'Execute') {
            Save-Phase 'checking-local-files-setup'
            $setup=Test-KahuaLocalFilesSetup @id -ExpectedSyncRoot $row.sync_root -Initialize:$InitializeLocalFiles
            Write-KahuaJson (Join-Path $row.state_root 'local-files-setup.json') $setup
            $phase='preflight'
        }
        Open-KahuaFileRoot $id
        $listing=@(Get-KahuaFolderListing)
        foreach ($folder in @('Documents','Photos')) {
            $match=@($listing | Where-Object name -ieq $folder)
            if ($match.Count -ne 1 -or $match[0].name -cne $folder -or -not $match[0].is_folder) {
                throw "Project $($row.project_number) needs one root '$folder' folder in File Manager before this batch can run."
            }
        }
    }
    $report.status='running'; Save-Report
    foreach ($row in $rows) {
        $current=$row; $id=Identity $row; $manifest=$manifests[$row.run_id]
        Select-KahuaProject @id
        Save-Phase 'checking-server'
        $baseline=Server-Check $row $manifest 'baseline'
        Write-KahuaJson (Join-Path $row.state_root 'latest-server-check.json') $baseline
        if ($Mode -eq 'Check') {
            Write-KahuaJson (Join-Path $row.state_root 'journal.json') ([ordered]@{
                attempt=$attempt;phase='checked';status='checked';server_verified=$baseline.server_verified;
                sync_off_verified=$false;checked_at_utc=[DateTime]::UtcNow.ToString('o')})
            $report.projects += [pscustomobject]@{run_id=$row.run_id;status='checked';server_verified=$baseline.server_verified}
            Save-Report
            continue
        }
        $verified=$baseline
        if (-not $baseline.server_verified) {
            Save-Phase 'enabling-sync'
            $enabled=Set-KahuaProjectSync @id -Enabled $true -ExpectedSyncRoot $row.sync_root
            Write-KahuaJson (Join-Path $row.state_root 'sync-on.json') $enabled
            Save-Phase 'reconciling-local-files'
            Wait-ExistingLocal $row $baseline
            # Re-read the server after enabling sync; this also reconciles a
            # previous interrupted upload before a missing local file is copied.
            $beforeCopy=Server-Check $row $manifest 'before-copy'
            Wait-ExistingLocal $row $beforeCopy
            $row.destination_confirmed=$true
            Write-KahuaJson $mappingPaths[$row.run_id] $row
            Save-Phase 'copying'
            & (Join-Path $PSScriptRoot 'Invoke-KahuaLocalSync.ps1') -MappingPath $mappingPaths[$row.run_id] -Mode Copy | Out-Host
            & (Join-Path $PSScriptRoot 'Invoke-KahuaLocalSync.ps1') -MappingPath $mappingPaths[$row.run_id] -Mode VerifyLocal | Out-Host
            Save-Phase 'verifying-server'
            $deadline=[DateTime]::UtcNow.AddSeconds($SyncTimeoutSeconds)
            do {
                $verified=Server-Check $row $manifest 'after-copy'
                if ($verified.server_verified) { break }
                if ([DateTime]::UtcNow -ge $deadline) { throw 'Kahua upload verification timed out. Sync state is retained for reconciliation on resume.' }
                Start-Sleep -Seconds 3
            } while ($true)
            foreach ($prior in @($beforeCopy.files | Where-Object status -eq 'verified')) {
                $after=@($verified.files | Where-Object relative_path -ceq $prior.relative_path)
                if ($after.Count -ne 1 -or $after[0].version -cne $prior.version) { throw "An existing server version changed: $($prior.relative_path)" }
            }
        }
        if (-not $verified.server_verified) { throw 'A project cannot complete without every server hash verified.' }
        Write-KahuaJson (Join-Path $row.state_root 'server-verification.json') $verified
        Save-Phase 'disabling-sync'
        $off=Set-KahuaProjectSync @id -Enabled $false -ExpectedSyncRoot $row.sync_root
        if (-not $off.reopened -or $off.documents_enabled -or $off.photos_enabled) { throw 'Saved sync-off verification failed.' }
        Write-KahuaJson (Join-Path $row.state_root 'sync-off.json') $off
        $complete=[ordered]@{attempt=$attempt;phase='complete';status='complete';server_verified=$true;sync_off_verified=$true;
            files=@($manifest.files).Count;completed_at_utc=[DateTime]::UtcNow.ToString('o')}
        Write-KahuaJson (Join-Path $row.state_root 'journal.json') $complete
        $report.projects += [pscustomobject]@{run_id=$row.run_id;status='complete';server_verified=$true;sync_off_verified=$true}
        Save-Report
    }
    $report.status=if ($Mode -eq 'Check') {'checked'} else {'complete'}
    Save-Report
    Write-Output "Batch $($report.status): $($rows.Count) project(s). Evidence: $attemptRoot"
} catch {
    $report.status='failed'; $report.error=$_.Exception.Message
    $report.error_script_stack=$_.ScriptStackTrace
    $report.failed_phase=$phase
    if ($current) { $report.failed_run_id=$current.run_id }
    Save-Report
    if ($current -and (Test-Path -LiteralPath $current.state_root -PathType Container)) {
        Write-KahuaJson (Join-Path $current.state_root 'journal.json') @{attempt=$attempt;status='failed';phase=$phase;
            server_verified=$false;error=$_.Exception.Message;error_script_stack=$_.ScriptStackTrace;
            updated_at_utc=[DateTime]::UtcNow.ToString('o')}
    }
    throw
} finally {
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    if ($mutex) { $mutex.Dispose() }
    $lock.Dispose()
}
