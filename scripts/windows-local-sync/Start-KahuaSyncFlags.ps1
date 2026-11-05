#requires -Version 5.1
<#
Interactive launcher. Plan and Check run first; APPLY authorizes the reviewed
batch. The low-level runner remains available for noninteractive use.
#>
[CmdletBinding()]
param(
    [string]$CsvPath,
    [string]$Environment,
    [string]$Domain,
    [ValidateSet('Enable','Disable','FromCsv')][string]$Action,
    [string]$StateRoot,
    [string]$HistoryRoot,
    [ValidateRange(0,3)][int]$RecoveryAttempts=2
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
Import-Module (Join-Path $PSScriptRoot 'KahuaSyncFlags.psm1') -Force
$runner=Join-Path $PSScriptRoot 'Invoke-KahuaSyncFlags.ps1'

try {
    Write-Host 'Kahua project sync settings'
    Write-Host 'Sign in to Kahua and leave its window maximized. This verifies switches; it does not verify file transfers.'
    if (-not $CsvPath) { $CsvPath=Read-Host 'CSV file (full path)' }
    $CsvPath=$CsvPath.Trim()
    if ($CsvPath.Length -ge 2 -and (($CsvPath.StartsWith('"') -and $CsvPath.EndsWith('"')) -or
        ($CsvPath.StartsWith("'") -and $CsvPath.EndsWith("'")))) { $CsvPath=$CsvPath.Substring(1,$CsvPath.Length-2) }
    if ([string]::IsNullOrWhiteSpace($CsvPath)) { throw 'Choose a CSV file to continue.' }
    $CsvPath=(Resolve-Path -LiteralPath $CsvPath).ProviderPath
    if (-not $Environment) {
        $Environment=Read-Host 'Environment [PRODUCTION]'
        if ([string]::IsNullOrWhiteSpace($Environment)) { $Environment='PRODUCTION' }
    }
    if (-not $Action) {
        do {
            $choice=Read-Host 'Action: Enable, Disable, or FromCsv'
        } while ($choice -notin @('Enable','Disable','FromCsv'))
        $Action=$choice
    }
    $rows=@(Read-KahuaSyncFlagCsv -CsvPath $CsvPath -Environment $Environment -Domain $Domain -Action $Action)
    if (-not $rows[0].domain -and -not $StateRoot) {
        $Domain=Read-Host 'Exact domain already selected in Kahua'
        if ([string]::IsNullOrWhiteSpace($Domain)) { throw 'A domain is required here so the launcher can find the correct history on later runs.' }
        $rows=@(Read-KahuaSyncFlagCsv -CsvPath $CsvPath -Environment $Environment -Domain $Domain -Action $Action)
    }
    $batchState=Resolve-KahuaFlagBatchState -Rows $rows -StateRoot $StateRoot -HistoryRoot $HistoryRoot
    Write-Host "CSV: $CsvPath"
    Write-Host "Environment: $Environment"
    $displayDomain=if ($rows[0].domain) { $rows[0].domain } else { '(read from Kahua and checked against this saved batch)' }
    Write-Host "Domain: $displayDomain"
    Write-Host "Run history: $batchState"
    # Freeze the reviewed intent's state directory through Plan, Check and
    # Execute. A CSV edited during a prompt must fail binding, not silently
    # select a new batch and apply unreviewed rows.
    $run=@{CsvPath=$CsvPath;Environment=$Environment;Action=$Action;
        StateRoot=$batchState;RecoveryAttempts=$RecoveryAttempts;
        ExpectedInputHash=(Get-KahuaFlagInputHash -Rows $rows)}
    if ($Domain) { $run.Domain=$Domain }
    & $runner @run -Mode Plan
    & $runner @run -Mode Check
    Write-Host "Requested settings for $Environment / $displayDomain`:"
    foreach ($row in $rows) {
        $desired=if ($row.sync_enabled) { 'On' } else { 'Off' }
        Write-Host "  $($row.project_name): Folders = $desired"
    }
    $answer=Read-Host "Checks passed for $($rows.Count) project(s). Type APPLY to apply the settings shown above, or press Enter to finish without changes"
    if ($answer -cne 'APPLY') {
        Write-Host 'Finished after checking. No project switches were changed.'
        return
    }
    & $runner @run -Mode Execute
    Write-Host 'Project settings verified. Check file-transfer completion separately in Kahua.'
} catch {
    Write-Host "Stopped: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'Keep the run history. Resolve the reported issue, then start the launcher again with the same inputs to resume.'
    throw
}
