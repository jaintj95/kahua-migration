#requires -Version 5.1
<#
Set the Folders switch for each exact named project. Doe handles file placement;
Kahua handles transfers. Completion means flag readback, not file verification.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$CsvPath,
    [Parameter(Mandatory=$true)][string]$Environment,
    [string]$Domain,
    [ValidateSet('Enable','Disable','FromCsv')][string]$Action = 'FromCsv',
    [ValidateSet('Plan','Check','Execute')][string]$Mode = 'Plan',
    [string]$StateRoot,
    [string]$HistoryRoot,
    [ValidateRange(0,3)][int]$RecoveryAttempts = 2,
    # The launcher pins its reviewed input before even the first Plan creates
    # state. This also prevents a concurrent CSV edit from poisoning a batch ID.
    [Parameter(DontShow=$true)][ValidatePattern('^[0-9a-f]{64}$')][string]$ExpectedInputHash
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'KahuaSyncFlags.psm1') -Force
$log = New-KahuaFlagLogContext -Mode $Mode
$lock = $null; $mutex = $null; $ownsMutex = $false; $current = $null
$attemptRoot = $null
$attempt = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
$report = [ordered]@{schema=1;attempt=$attempt;mode=$Mode;status='validating';
    started_at_utc=[DateTime]::UtcNow.ToString('o');scope='project-files';
    transfer_verified=$false;projects=@()}
function Save-FlagReport {
    $report.progress_log=$log.Path
    $report.last_step=$log.Step
    $report.last_progress_at_utc=$log.LastProgressAtUtc
    $report.recoveries=@($log.Recoveries.ToArray())
    Write-KahuaFlagJson (Join-Path $attemptRoot 'result.json') $report
}
try {
    Write-KahuaFlagProgress $log 'validate' "Starting $Mode / $Action in $Environment. CSV: $CsvPath"
    $rows = @(Read-KahuaSyncFlagCsv -CsvPath $CsvPath -Environment $Environment -Domain $Domain -Action $Action)
    $inputHash=Get-KahuaFlagInputHash -Rows $rows
    if ($ExpectedInputHash -and $inputHash -cne $ExpectedInputHash) {
        throw 'The CSV or requested inputs changed after launcher review. No settings were changed by this attempt. Start the launcher again to review the current batch.'
    }
    $report.input_hash=$inputHash
    $log.Total=$rows.Count
    $state = Resolve-KahuaFlagBatchState -Rows $rows -StateRoot $StateRoot -HistoryRoot $HistoryRoot
    $stateKind = if ($StateRoot) { 'explicit' } else { 'automatic' }
    $stateAction = if (Test-Path -LiteralPath (Join-Path $state 'binding.json')) { 'Reusing' } else { 'Creating' }
    Write-KahuaFlagProgress $log 'validate' "CSV accepted: $($rows.Count) project(s). $stateAction $stateKind batch history: $state"
    $report.state_root=$state; $report.state_selection=$stateKind; $report.recovery_attempts=$RecoveryAttempts
    $csvFull = (Resolve-Path -LiteralPath $CsvPath).ProviderPath
    if ($csvFull.StartsWith($state.TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) {
        throw 'Keep the source CSV outside the state directory.'
    }
    # Do not put logs inside any Kahua directory that could itself be synchronized.
    $domainFolder = [regex]::Escape($rows[0].domain)
    if ($state -match '(?i)(?:^|[\\/])Kahua - [^\\/]+(?:[\\/]|$)' -or
        $state -match "(?i)(?:^|[\\/])Kahua-$domainFolder(?:[\\/]|$)") {
        throw 'StateRoot must be outside Kahua local sync directories.'
    }
    [void][IO.Directory]::CreateDirectory($state)
    Write-KahuaFlagProgress $log 'state-lock' 'Acquiring exclusive access to this run state.'
    $lock = [IO.File]::Open((Join-Path $state 'flags.lock'),'OpenOrCreate','ReadWrite','None')
    $attemptRoot = Join-Path (Join-Path $state 'attempts') $attempt
    [void][IO.Directory]::CreateDirectory($attemptRoot)
    $log.Path=Join-Path $attemptRoot 'progress.log'
    Write-KahuaFlagProgress $log 'log' "Progress log: $($log.Path)"
    Write-KahuaFlagProgress $log 'binding' 'Checking saved input binding before continuing.'
    $binding = [ordered]@{schema=2;projects=$rows}
    $bindingPath = Join-Path $state 'binding.json'
    if (Test-Path -LiteralPath $bindingPath) {
        $previous = Get-Content -LiteralPath $bindingPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if (($previous | ConvertTo-Json -Depth 20 -Compress) -cne ($binding | ConvertTo-Json -Depth 20 -Compress)) {
            throw 'This state directory belongs to different input values. Keep it for recovery. For a changed batch or opposite action, omit -StateRoot to use automatic history, or supply a new StateRoot. If inputs changed during launcher review, start the launcher again to review them.'
        }
    } else { Write-KahuaFlagJson $bindingPath $binding }
    $report.projects = @($rows | ForEach-Object {
        [pscustomobject][ordered]@{row=$_.row;domain=$_.domain;project_name=$_.project_name;
            desired_enabled=$_.sync_enabled;status='planned';receipt=$null}
    })
    if ($Mode -eq 'Plan') {
        $log.Phase='Plan'
        for ($i=0; $i -lt $rows.Count; $i++) {
            $log.Index=$i+1; $log.Project=$rows[$i].project_name
            $desired=if ($rows[$i].sync_enabled) { 'On' } else { 'Off' }
            Write-KahuaFlagProgress $log 'planned' "Requested Folders=$desired. No desktop action."
        }
        $log.Index=0; $log.Project=''
        Write-KahuaFlagProgress $log 'complete' "Planned $($rows.Count) project(s). Report: $attemptRoot\result.json"
        $report.status = 'planned'; Save-FlagReport
        Write-Output "Planned $($rows.Count) project flag(s). Review $attemptRoot\result.json"
        return
    }
    Write-KahuaFlagProgress $log 'desktop-lock' 'Loading desktop adapter and acquiring the Kahua desktop lock.'
    Import-Module (Join-Path $PSScriptRoot 'KahuaSyncFlagDesktop.psm1') -Force
    $mutex = [Threading.Mutex]::new($false,'Local\KahuaMigrationDesktop')
    try { $ownsMutex = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
    if (-not $ownsMutex) { throw 'Another Kahua desktop worker is running in this session.' }
    # Only a domain supplied in the CSV authorizes an automatic switch.
    # Otherwise read Doe's selection and bind it for every later resume.
    $actualDomain = Initialize-KahuaFlagDomain -Environment $Environment -Domain $rows[0].domain -Switch:$rows[0].auto_select_domain -LogContext $log
    if ($state -match ('(?i)(?:^|[\\/])Kahua-'+[regex]::Escape($actualDomain)+'(?:[\\/]|$)')) {
        throw 'StateRoot must be outside Kahua local sync directories.'
    }
    Write-KahuaFlagProgress $log 'domain' "Verified environment=$Environment; domain=$actualDomain. Checking saved domain binding."
    Set-KahuaFlagDomainBinding -StateRoot $state -Rows $rows -ActualDomain $actualDomain
    foreach ($entry in $report.projects) { $entry.domain=$actualDomain }
    $report.status = 'checking'; Save-FlagReport
    $log.Phase='Preflight'
    $identities = @{}
    # Resolve and inspect the entire list before the first setting change.
    # Lookup errors found here stop before any project flag is changed.
    for ($i=0; $i -lt $rows.Count; $i++) {
        $row = $rows[$i]; $current = $report.projects[$i]
        $log.Index=$i+1; $log.Project=$row.project_name
        Write-KahuaFlagProgress $log 'project' 'Checking identity and current Folders switch before any changes.'
        $current.status = 'checking'; Save-FlagReport
        $identity = Select-KahuaFlagProject -Environment $Environment -Domain $row.domain -Name $row.project_name -LogContext $log
        Write-KahuaFlagProgress $log 'read' 'Reading Local Files and reopening settings to confirm the current switch.'
        $receipt = Get-KahuaProjectFlag -Identity $identity -LogContext $log -RecoveryAttempts $RecoveryAttempts
        Assert-KahuaSyncFlagReceipt -Receipt $receipt -Row $row -RequireRunning:($Mode -eq 'Execute')
        $identities[$row.row] = $identity
        Write-KahuaFlagProgress $log 'checked' "Folders=$($receipt.selection); account sync=$($receipt.account_sync_state); reopened=$($receipt.reopened)."
        $current.receipt = $receipt; $current.status = 'checked'; Save-FlagReport
    }
    if ($Mode -eq 'Execute') {
        $log.Index=0; $log.Project=''; $log.Phase='Apply'
        Write-KahuaFlagProgress $log 'preflight-complete' "All $($rows.Count) projects passed preflight. Applying requested switches."
        $report.status = 'applying'; Save-FlagReport
        for ($i=0; $i -lt $rows.Count; $i++) {
            $row = $rows[$i]; $current = $report.projects[$i]
            $log.Index=$i+1; $log.Project=$row.project_name
            $desired=if ($row.sync_enabled) { 'On' } else { 'Off' }
            Write-KahuaFlagProgress $log 'project' "Requested Folders=$desired. Rechecking exact project identity."
            $current.status = 'applying'; Save-FlagReport
            # Re-resolve on every attempt; a previous receipt is never a reason
            # to skip a fresh UI read or to blindly invert the current switch.
            $identity = Select-KahuaFlagProject -Environment $Environment -Domain $row.domain -Name $row.project_name -LogContext $log
            if ($identity.display -cne $identities[$row.row].display) { throw 'Project identity changed after preflight.' }
            Write-KahuaFlagProgress $log 'apply' "Setting Folders=$desired, then verifying after reopening settings."
            $receipt = Set-KahuaProjectFlag -Identity $identity -Enabled $row.sync_enabled -LogContext $log -RecoveryAttempts $RecoveryAttempts
            Assert-KahuaSyncFlagReceipt -Receipt $receipt -Row $row -RequireDesired -RequireRunning
            Write-KahuaFlagProgress $log 'verified' "Folders=$($receipt.selection); account sync=$($receipt.account_sync_state); reopened=$($receipt.reopened)."
            $current.receipt = $receipt; $current.status = 'verified'; Save-FlagReport
        }
        $report.status = 'flags-verified'
    } else { $report.status = 'checked' }
    $log.Index=0; $log.Project=''; $log.Phase='Summary'
    Write-KahuaFlagProgress $log 'complete' "$($report.status): $($rows.Count)/$($rows.Count) project(s). Transfer completion is not checked. Report: $attemptRoot\result.json"
    $report.finished_at_utc = [DateTime]::UtcNow.ToString('o'); Save-FlagReport
    Write-Output "$($report.status): $($rows.Count) project(s). Transfer completion is not checked. Evidence: $attemptRoot"
} catch {
    $failure=$_
    $report.status = 'failed'; $report.error = $_.Exception.Message
    $report.error_script_stack = $_.ScriptStackTrace
    $report.failed_step=$log.Step; $report.failed_phase=$log.Phase
    $report.finished_at_utc=[DateTime]::UtcNow.ToString('o')
    if ($current) {
        # A failed Save/readback remains uncertain. Keep earlier receipts as
        # historical observations, never as proof of this attempted write.
        $report.failed_project = $current.project_name
        $current.status = if ($current.status -eq 'applying') { 'uncertain' } else { 'failed' }
    }
    $uncertain=if ($current -and $current.status -eq 'uncertain') { ' Outcome is uncertain; inspect settings before resuming.' } else { '' }
    try { Write-KahuaFlagProgress $log $log.Step ("STOPPED: $($report.error)$uncertain") -Level ERROR }
    catch { Write-Warning "Could not append progress.log: $($_.Exception.Message). Original failure: $($failure.Exception.Message)" }
    if ($attemptRoot -and (Test-Path -LiteralPath $attemptRoot -PathType Container)) {
        try { Save-FlagReport }
        catch { Write-Warning "Could not save result.json: $($_.Exception.Message). Original failure: $($failure.Exception.Message)" }
    }
    throw $failure
} finally {
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    if ($mutex) { $mutex.Dispose() }
    if ($lock) { $lock.Dispose() }
}
