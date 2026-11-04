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
    [Parameter(Mandatory=$true)][string]$StateRoot
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'KahuaSyncFlags.psm1') -Force
$rows = @(Read-KahuaSyncFlagCsv -CsvPath $CsvPath -Environment $Environment -Domain $Domain -Action $Action)
$state = Resolve-KahuaFlagStatePath $StateRoot
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
$lock = [IO.File]::Open((Join-Path $state 'flags.lock'),'OpenOrCreate','ReadWrite','None')
$mutex = $null; $ownsMutex = $false; $current = $null
$attempt = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
$attemptRoot = Join-Path (Join-Path $state 'attempts') $attempt
$report = [ordered]@{schema=1;attempt=$attempt;mode=$Mode;status='validating';
    started_at_utc=[DateTime]::UtcNow.ToString('o');scope='project-files';
    transfer_verified=$false;projects=@()}
function Save-FlagReport { Write-KahuaFlagJson (Join-Path $attemptRoot 'result.json') $report }
try {
    [void][IO.Directory]::CreateDirectory($attemptRoot)
    $binding = [ordered]@{schema=2;projects=$rows}
    $bindingPath = Join-Path $state 'binding.json'
    if (Test-Path -LiteralPath $bindingPath) {
        $previous = Get-Content -LiteralPath $bindingPath -Raw | ConvertFrom-Json
        if (($previous | ConvertTo-Json -Depth 20 -Compress) -cne ($binding | ConvertTo-Json -Depth 20 -Compress)) {
            throw 'This state directory belongs to different input values. Keep it for recovery; use a new StateRoot for a changed batch or opposite action.'
        }
    } else { Write-KahuaFlagJson $bindingPath $binding }
    $report.projects = @($rows | ForEach-Object {
        [pscustomobject][ordered]@{row=$_.row;domain=$_.domain;project_name=$_.project_name;
            desired_enabled=$_.sync_enabled;status='planned';receipt=$null}
    })
    if ($Mode -eq 'Plan') {
        $report.status = 'planned'; Save-FlagReport
        Write-Output "Planned $($rows.Count) project flag(s). Review $attemptRoot\result.json"
        return
    }
    Import-Module (Join-Path $PSScriptRoot 'KahuaSyncFlagDesktop.psm1') -Force
    $mutex = [Threading.Mutex]::new($false,'Local\KahuaMigrationDesktop')
    try { $ownsMutex = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
    if (-not $ownsMutex) { throw 'Another Kahua desktop worker is running in this session.' }
    # Only a domain supplied in the CSV authorizes an automatic switch.
    # Otherwise read Doe's selection and bind it for every later resume.
    $actualDomain = Initialize-KahuaFlagDomain -Environment $Environment -Domain $rows[0].domain -Switch:$rows[0].auto_select_domain
    if ($state -match ('(?i)(?:^|[\\/])Kahua-'+[regex]::Escape($actualDomain)+'(?:[\\/]|$)')) {
        throw 'StateRoot must be outside Kahua local sync directories.'
    }
    Set-KahuaFlagDomainBinding -StateRoot $state -Rows $rows -ActualDomain $actualDomain
    foreach ($entry in $report.projects) { $entry.domain=$actualDomain }
    $report.status = 'checking'; Save-FlagReport
    $identities = @{}
    # Resolve and inspect the entire list before the first setting change.
    # Lookup errors found here stop before any project flag is changed.
    for ($i=0; $i -lt $rows.Count; $i++) {
        $row = $rows[$i]; $current = $report.projects[$i]
        $current.status = 'checking'; Save-FlagReport
        $identity = Select-KahuaFlagProject -Environment $Environment -Domain $row.domain -Name $row.project_name
        $receipt = Get-KahuaProjectFlag -Identity $identity
        Assert-KahuaSyncFlagReceipt -Receipt $receipt -Row $row -RequireRunning:($Mode -eq 'Execute')
        $identities[$row.row] = $identity
        $current.receipt = $receipt; $current.status = 'checked'; Save-FlagReport
    }
    if ($Mode -eq 'Execute') {
        $report.status = 'applying'; Save-FlagReport
        for ($i=0; $i -lt $rows.Count; $i++) {
            $row = $rows[$i]; $current = $report.projects[$i]
            $current.status = 'applying'; Save-FlagReport
            # Re-resolve on every attempt; a previous receipt is never a reason
            # to skip a fresh UI read or to blindly invert the current switch.
            $identity = Select-KahuaFlagProject -Environment $Environment -Domain $row.domain -Name $row.project_name
            if ($identity.display -cne $identities[$row.row].display) { throw 'Project identity changed after preflight.' }
            $receipt = Set-KahuaProjectFlag -Identity $identity -Enabled $row.sync_enabled
            Assert-KahuaSyncFlagReceipt -Receipt $receipt -Row $row -RequireDesired -RequireRunning
            $current.receipt = $receipt; $current.status = 'verified'; Save-FlagReport
        }
        $report.status = 'flags-verified'
    } else { $report.status = 'checked' }
    $report.finished_at_utc = [DateTime]::UtcNow.ToString('o'); Save-FlagReport
    Write-Output "$($report.status): $($rows.Count) project(s). Transfer completion is not checked. Evidence: $attemptRoot"
} catch {
    $report.status = 'failed'; $report.error = $_.Exception.Message
    $report.error_script_stack = $_.ScriptStackTrace
    if ($current) {
        # A failed Save/readback remains uncertain. Keep earlier receipts as
        # historical observations, never as proof of this attempted write.
        $report.failed_project = $current.project_name
        $current.status = if ($current.status -eq 'applying') { 'uncertain' } else { 'failed' }
    }
    if (Test-Path -LiteralPath $attemptRoot -PathType Container) { Save-FlagReport }
    throw
} finally {
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    if ($mutex) { $mutex.Dispose() }
    $lock.Dispose()
}
