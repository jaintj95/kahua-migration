#requires -Version 5.1
# Real launcher, input binding, runner and history; simulated desktop only.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$root=Join-Path ([IO.Path]::GetTempPath()) ('kahua-launcher-tests-'+[Guid]::NewGuid().ToString('N'))
$code=Join-Path $root 'scripts with spaces'
$history=Join-Path $root 'history'
[void][IO.Directory]::CreateDirectory($code)
foreach ($file in @('Start-KahuaSyncFlags.ps1','Invoke-KahuaSyncFlags.ps1','KahuaSyncFlags.psm1')) {
    Copy-Item (Join-Path $PSScriptRoot $file) (Join-Path $code $file)
}
$desktop=@'
Set-StrictMode -Version Latest
function Event([string]$Text) { [IO.File]::AppendAllText((Join-Path $PSScriptRoot 'events'),$Text+"`n") }
function Initialize-KahuaFlagDomain {
    param($Environment,$Domain,[switch]$Switch)
    Event "DOMAIN $Environment/$Domain"
    return $Domain
}
function Select-KahuaFlagProject {
    param($Environment,$Domain,$Name,$LogContext,[int]$RecoveryAttempts=2)
    Event "SELECT $Name"
    Event "NAV-RECOVERY $RecoveryAttempts"
    if (Test-Path (Join-Path $PSScriptRoot 'fail-check')) { throw 'Fixture check failed' }
    [pscustomobject]@{environment=$Environment;domain=$Domain;project_name=$Name;display="0001 - $Name"}
}
function Receipt($Identity,$Selection) {
    [pscustomobject]@{environment=$Identity.environment;domain=$Identity.domain;project_name=$Identity.project_name;
        selection=$Selection;scope='project-files';reopened=$true;account_sync_state='running'}
}
function Get-KahuaProjectFlag {
    param($Identity,$LogContext,$RecoveryAttempts)
    Event "READ $($Identity.project_name)"
    Receipt $Identity 'Off'
}
function Set-KahuaProjectFlag {
    param($Identity,[bool]$Enabled,$LogContext,$RecoveryAttempts)
    Event "SET $($Identity.project_name) $Enabled"
    Receipt $Identity $(if ($Enabled) { 'On' } else { 'Off' })
}
Export-ModuleMember -Function Initialize-KahuaFlagDomain,Select-KahuaFlagProject,Get-KahuaProjectFlag,Set-KahuaProjectFlag
'@
[IO.File]::WriteAllText((Join-Path $code 'KahuaSyncFlagDesktop.psm1'),$desktop)
Import-Module (Join-Path $code 'KahuaSyncFlags.psm1') -Force
$runner=Join-Path $code 'Invoke-KahuaSyncFlags.ps1'
$launcher=Join-Path $code 'Start-KahuaSyncFlags.ps1'
$csv=Join-Path $root 'projects with spaces.csv'
$events=Join-Path $code 'events'
$passed=0
$global:KahuaLauncherFixture=@{}
function Assert($Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
function Expect-Error([scriptblock]$Call,[string]$Pattern) {
    $message=''
    try { & $Call | Out-Null } catch { $message=$_.Exception.Message }
    Assert ($message -match $Pattern) "Expected '$Pattern', got '$message'"
}
function Csv([string]$Text="domain,project_name`nOnindus,One`n") {
    [IO.File]::WriteAllText($csv,$Text,[Text.UTF8Encoding]::new($true))
}
function Batch([string]$Environment='PRODUCTION',[string]$Action='Enable',[string]$Domain='') {
    $rows=@(Read-KahuaSyncFlagCsv $csv $Environment $Domain $Action)
    Resolve-KahuaFlagBatchState -Rows $rows -HistoryRoot $history
}
function Plan([string]$Environment='PRODUCTION',[string]$Action='Enable') {
    & $runner -CsvPath $csv -Environment $Environment -Action $Action -HistoryRoot $history -Mode Plan | Out-Null
}
function Events { if (Test-Path $events) { Get-Content $events } }
function Latest([string]$State) {
    $attempt=Get-ChildItem (Join-Path $State 'attempts') -Directory | Sort-Object CreationTimeUtc -Descending | Select-Object -First 1
    Get-Content (Join-Path $attempt.FullName 'result.json') -Raw -Encoding UTF8 | ConvertFrom-Json
}
function Answers([string[]]$Values) {
    $global:KahuaLauncherFixture.Answers=New-Object 'System.Collections.Generic.Queue[string]'
    foreach ($value in $Values) { $global:KahuaLauncherFixture.Answers.Enqueue($value) }
    $global:KahuaLauncherFixture.Prompts=New-Object 'System.Collections.Generic.List[string]'
    $global:KahuaLauncherFixture.ChangeAtApply=$false
}
function Read-Host([string]$Prompt) {
    $global:KahuaLauncherFixture.Prompts.Add($Prompt)
    if ($global:KahuaLauncherFixture.ChangeAtApply -and $Prompt -match 'Type APPLY') { Csv "domain,project_name`nOnindus,One`nOnindus,Unreviewed`n" }
    if (-not $global:KahuaLauncherFixture.Answers.Count) { throw "Unexpected prompt: $Prompt" }
    return $global:KahuaLauncherFixture.Answers.Dequeue()
}
try {
    Csv
    $first=Batch
    # Fixed cross-runtime contract: PS 5.1 and PS 7 must choose the same history.
    Assert ([IO.Path]::GetFileName($first) -ceq 'batch-f1cd3f862fd5211c596d683b5498c93be07a46c5b49e5e4e2fdb60d3acf9feea') 'Batch identity changed across PowerShell runtimes'
    Plan
    Assert (-not (Test-Path $events)) 'Plan imported or used the desktop'
    Assert ((Latest $first).state_selection -eq 'automatic') 'Automatic selection not recorded'
    Assert ((Latest $first).state_root -ceq $first) 'Selected history path missing from report'
    $passed++
    $originalBinding=[IO.File]::ReadAllText((Join-Path $first 'binding.json'))
    $oldLog=(Latest $first).progress_log
    $oldText=[IO.File]::ReadAllText($oldLog)
    Plan
    Assert (@(Get-ChildItem $history -Directory).Count -eq 1) 'Unchanged retry created another batch'
    Assert (@(Get-ChildItem (Join-Path $first 'attempts') -Directory).Count -eq 2) 'Retry overwrote an attempt'
    Assert ([IO.File]::ReadAllText($oldLog) -ceq $oldText) 'Retry overwrote earlier log'
    $passed++
    Csv "domain,project_name,project_uid`r`nOnindus,`"One`",ignored-new-value`r`n"
    Assert ((Batch) -ceq $first) 'CSV formatting or ignored UID changed batch identity'
    $moved=Join-Path $root 'renamed.csv'
    Copy-Item $csv $moved
    $rows=@(Read-KahuaSyncFlagCsv $moved PRODUCTION -Action Enable)
    Assert ((Resolve-KahuaFlagBatchState -Rows $rows -HistoryRoot $history) -ceq $first) 'Moving CSV changed batch identity'
    $passed++
    Csv "domain,project_name,sync_enabled`nOnindus,One,true`n"
    Assert ((Batch PRODUCTION FromCsv) -ceq $first) 'Equivalent FromCsv intent did not reuse history'
    $passed++
    Csv "domain,project_name`nOnindus,One`nOnindus,Two`n"
    $two=Batch
    Plan
    Assert ($two -cne $first -and (Test-Path (Join-Path $first 'binding.json'))) 'Changed project list reused or removed old history'
    Csv "domain,project_name`nOnindus,Two`nOnindus,One`n"
    Assert ((Batch) -cne $two) 'Changed project order did not get its own binding'
    $passed++
    Csv
    Assert ((Batch DEVPRODUCTION) -cne $first) 'Changed environment reused history'
    Assert ((Batch PRODUCTION Disable) -cne $first) 'Opposite action reused history'
    Csv "domain,project_name`nDifferent,One`n"
    Assert ((Batch) -cne $first) 'Changed domain reused history'
    Csv "project_name`nOne`n"
    Assert ((Batch PRODUCTION Enable Onindus) -cne (Batch PRODUCTION Enable Different)) 'Manual domain absent from batch key'
    $passed++
    Csv
    $rows=@(Read-KahuaSyncFlagCsv $csv PRODUCTION -Action Enable)
    $expectedBase=Join-Path (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'KahuaMigration') 'SyncFlags'
    Assert ((Get-KahuaFlagHistoryRoot) -ceq $expectedBase) 'Default history did not use local application data'
    Assert ((Resolve-KahuaFlagBatchState -Rows $rows).StartsWith($expectedBase+[IO.Path]::DirectorySeparatorChar)) 'Automatic state outside default history'
    $passed++
    # State files are UTF-8 without a BOM. Windows PowerShell 5.1 must explicitly
    # read UTF-8, or non-ASCII names produce a false input/domain mismatch.
    $unicodeName=[string][char]0x00C9+'cole, Phase [2]'
    $unicodeDomain='Doma'+[char]0x00EE+'n'
    Csv "domain,project_name`n$unicodeDomain,`"$unicodeName`"`n"
    $unicodeBatch=Batch
    Assert ([IO.Path]::GetFileName($unicodeBatch) -ceq 'batch-7bf52934b33b6f348c705c3fba2b995f60cc87e941c62c2d60455784721cb326') 'Unicode batch identity changed across PowerShell runtimes'
    Plan
    Plan
    Assert ((Latest $unicodeBatch).projects[0].project_name -ceq $unicodeName) 'Unicode batch binding did not survive a repeated Plan'
    $passed++
    1..2 | ForEach-Object {
        & $runner -CsvPath $csv -Environment PRODUCTION -Action Enable -HistoryRoot $history -Mode Check | Out-Null
    }
    Assert ((Latest $unicodeBatch).projects[0].domain -ceq $unicodeDomain) 'Unicode selected-domain binding did not survive resume'
    $passed++
    Csv
    $legacy=Join-Path $root 'legacy state'
    & $runner -CsvPath $csv -Environment PRODUCTION -Action Enable -StateRoot $legacy -Mode Plan | Out-Null
    & $runner -CsvPath $csv -Environment PRODUCTION -Action Enable -StateRoot $legacy -Mode Plan -RecoveryAttempts 0 | Out-Null
    Assert ((Latest $legacy).state_selection -eq 'explicit' -and (Latest $legacy).recovery_attempts -eq 0) 'Legacy explicit state or recovery option failed'
    $passed++
    Expect-Error { & $runner -CsvPath $csv -Environment PRODUCTION -Action Disable -StateRoot $first } 'different input values'
    Assert ([IO.File]::ReadAllText((Join-Path $first 'binding.json')) -ceq $originalBinding) 'Mismatch altered the saved binding'
    Expect-Error { Resolve-KahuaFlagBatchState -Rows $rows -StateRoot $first -HistoryRoot $history } 'not both'
    $passed++
    $reviewHash=Get-KahuaFlagInputHash -Rows $rows
    Csv "domain,project_name`nOnindus,Unreviewed`n"
    $uncreated=Join-Path $root 'must-not-create'
    Expect-Error {
        & $runner -CsvPath $csv -Environment PRODUCTION -Action Enable -StateRoot $uncreated -ExpectedInputHash $reviewHash -Mode Plan
    } 'changed after launcher review'
    Assert (-not (Test-Path $uncreated)) 'Input edit before first Plan created a mismatched binding'
    Csv
    $passed++
    $lock=[IO.File]::Open((Join-Path $first 'flags.lock'),'OpenOrCreate','ReadWrite','None')
    try { Expect-Error { Plan } 'used by another process|being used|cannot access' } finally { $lock.Dispose() }
    $passed++
    Answers @('')
    $start=@(Events).Count
    & $launcher -CsvPath $csv -Environment PRODUCTION -Action Enable -HistoryRoot $history | Out-Null
    Assert (-not @(Events | Select-Object -Skip $start | Where-Object { $_ -match '^SET ' }).Count) 'Declining APPLY changed a flag'
    Assert ($global:KahuaLauncherFixture.Prompts.Count -eq 1 -and $global:KahuaLauncherFixture.Prompts[0] -match 'Checks passed') 'Apply prompt did not follow successful checks'
    $passed++
    Answers @('APPLY')
    $start=@(Events).Count
    & $launcher -CsvPath ('"'+$csv+'"') -Environment PRODUCTION -Action Enable -HistoryRoot $history -RecoveryAttempts 0 | Out-Null
    Assert (@(Events | Select-Object -Skip $start | Where-Object { $_ -ceq 'SET One True' }).Count -eq 1) 'Approved launcher did not apply the reviewed project'
    Assert ((Latest $first).status -eq 'flags-verified') 'Launcher completion lacks verified result'
    $navigationOptions=@(Events | Select-Object -Skip $start | Where-Object { $_ -match '^NAV-RECOVERY ' })
    Assert ($navigationOptions.Count -eq 3 -and -not @($navigationOptions | Where-Object { $_ -cne 'NAV-RECOVERY 0' }).Count) 'Launcher recovery option was lost before Check, Execute preflight, or Execute selection'
    $passed++
    Answers @('APPLY')
    $global:KahuaLauncherFixture.ChangeAtApply=$true
    $start=@(Events).Count
    Expect-Error { & $launcher -CsvPath $csv -Environment PRODUCTION -Action Enable -HistoryRoot $history } 'changed after launcher review'
    Assert (-not @(Events | Select-Object -Skip $start | Where-Object { $_ -match '^SET ' }).Count) 'Editing the CSV during review applied unreviewed settings'
    $passed++
    Csv
    Answers @()
    [IO.File]::WriteAllText((Join-Path $code 'fail-check'),'fixture')
    $start=@(Events).Count
    Expect-Error { & $launcher -CsvPath $csv -Environment PRODUCTION -Action Enable -HistoryRoot $history } 'Fixture check failed'
    Assert ($global:KahuaLauncherFixture.Prompts.Count -eq 0) 'Failed Check still offered APPLY'
    Assert (-not @(Events | Select-Object -Skip $start | Where-Object { $_ -match '^SET ' }).Count) 'Failed Check reached Execute'
    Remove-Item (Join-Path $code 'fail-check')
    $passed++
    Csv "project_name`nOne`n"
    Answers @('Onindus','')
    & $launcher -CsvPath $csv -Environment PRODUCTION -Action Enable -HistoryRoot $history | Out-Null
    Assert ($global:KahuaLauncherFixture.Prompts[0] -match 'Exact domain') 'Names-only automatic batch did not ask for domain'
    Assert ((Latest (Batch PRODUCTION Enable Onindus)).status -eq 'checked') 'Manual domain launcher did not complete Check'
    $passed++
    Csv
    Answers @($csv,'','bad-action','Disable','')
    & $launcher -HistoryRoot $history | Out-Null
    Assert ($global:KahuaLauncherFixture.Answers.Count -eq 0) 'Interactive setup did not consume the expected answers'
    Assert ((Latest (Batch PRODUCTION Disable)).status -eq 'checked') 'Interactive action/environment setup failed'
    $passed++
    # A corrupt binding is not treated as a missing batch or silently replaced.
    $bindingPath=Join-Path $first 'binding.json'
    [IO.File]::WriteAllText($bindingPath,'invalid-json')
    Expect-Error { Plan } 'JSON|Unexpected|Invalid'
    Assert ([IO.File]::ReadAllText($bindingPath) -ceq 'invalid-json') 'Corrupt history was silently reset'
    $passed++
    Write-Output "$passed launcher and automatic history tests passed. No Kahua access."
} finally {
    Remove-Module KahuaSyncFlagDesktop,KahuaSyncFlags -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $root -Recurse -Force
    Remove-Variable KahuaLauncherFixture -Scope Global
}
