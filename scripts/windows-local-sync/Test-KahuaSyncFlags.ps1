#requires -Version 5.1
# Real CSV/coordinator code, simulated desktop. Never opens or changes Kahua.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('kahua-flags-tests-'+[Guid]::NewGuid().ToString('N'))
$runnerDir = Join-Path $testRoot 'scripts'
[void][IO.Directory]::CreateDirectory($runnerDir)
foreach ($file in @('Invoke-KahuaSyncFlags.ps1','KahuaSyncFlags.psm1')) {
    Copy-Item (Join-Path $PSScriptRoot $file) (Join-Path $runnerDir $file)
}
$desktop = @'
Set-StrictMode -Version Latest
function Add-Event([string]$Text) { [IO.File]::AppendAllText((Join-Path $PSScriptRoot 'events.txt'),$Text+[Environment]::NewLine) }
function Confirm-KahuaFlagDomain {
    param($Environment,$Domain)
    Add-Event "DOMAIN $Environment/$Domain"
    if (Test-Path (Join-Path $PSScriptRoot 'wrong-domain')) { throw 'Wrong domain' }
}
function Initialize-KahuaFlagDomain {
    param($Environment,$Domain,[switch]$Switch)
    $currentPath=Join-Path $PSScriptRoot 'current-domain.txt'
    $current=if (Test-Path $currentPath) { [IO.File]::ReadAllText($currentPath) } else { 'Onindus' }
    if ($Switch) { Add-Event "SWITCH $Domain"; $current=$Domain }
    if ($Domain -and $Domain -cne $current) { throw 'Wrong domain' }
    Confirm-KahuaFlagDomain -Environment $Environment -Domain $current
    return $current
}
function Select-KahuaFlagProject {
    param($Environment,$Domain,$Name,$LogContext,[int]$RecoveryAttempts=2)
    Add-Event "SELECT $Name"
    Add-Event "NAV-RECOVERY $RecoveryAttempts"
    if ($Name -eq 'Missing' -and -not (Test-Path (Join-Path $PSScriptRoot 'missing-now-exists'))) {
        $failure=[InvalidOperationException]::new('No exact project named Missing')
        $failure.Data['KahuaFlagMissingProject']=$true
        throw $failure
    }
    if ($Name -eq 'Duplicate') { throw 'Duplicate exact project name' }
    if ($Name -eq 'Timeout') { throw 'Timed out waiting for results' }
    $number=if (Test-Path (Join-Path $PSScriptRoot 'renumbered')) { '0002' } else { '0001' }
    [pscustomobject]@{environment=$Environment;domain=$Domain;project_name=$Name;project_number=$number;display="$number - $Name"}
}
function Read-Selection($Identity) {
    $path=Join-Path $PSScriptRoot ($Identity.project_name+'.selection')
    if (Test-Path $path) { return [IO.File]::ReadAllText($path) }
    return 'Off'
}
function New-Receipt($Identity,[string]$Selection) {
    $name=$Identity.project_name
    if (Test-Path (Join-Path $PSScriptRoot 'bad-identity')) { $name='Wrong project' }
    $account=if (Test-Path (Join-Path $PSScriptRoot 'paused')) { 'paused' } else { 'running' }
    [pscustomobject]@{environment=$Identity.environment;domain=$Identity.domain;project_name=$name;
        project_number=$Identity.project_number;display=$Identity.display;
        scope='project-files';selection=$Selection;reopened=$false;account_sync_state=$account}
}
function Get-KahuaProjectFlag {
    param($Identity)
    Add-Event "READ $($Identity.project_name)"
    New-Receipt $Identity (Read-Selection $Identity)
}
function Set-KahuaProjectFlag {
    param($Identity,[bool]$Enabled)
    Add-Event "SET $($Identity.project_name) $Enabled"
    $desired=if ($Enabled) { 'On' } else { 'Off' }
    $receipt=New-Receipt $Identity $desired
    if ($Enabled -and $receipt.account_sync_state -eq 'paused') {
        $failure=[InvalidOperationException]::new('Account sync is paused')
        $failure.Data['KahuaFlagBeforeWrite']=$true
        throw $failure
    }
    $changed=(Read-Selection $Identity) -cne $desired
    if ($changed) {
        [IO.File]::WriteAllText((Join-Path $PSScriptRoot ($Identity.project_name+'.selection')),$desired)
        Add-Event "SAVE $($Identity.project_name) $desired"
    }
    if (Test-Path (Join-Path $PSScriptRoot 'uncertain-save')) { throw 'Uncertain Save: readback unavailable' }
    Add-Event "REOPEN $($Identity.project_name)"
    if (Test-Path (Join-Path $PSScriptRoot 'wrong-readback')) { $receipt.selection='Indeterminate' }
    $receipt.reopened=-not (Test-Path (Join-Path $PSScriptRoot 'not-reopened'))
    $receipt | Add-Member NoteProperty changed $changed
    return $receipt
}
Export-ModuleMember -Function Initialize-KahuaFlagDomain,Confirm-KahuaFlagDomain,Select-KahuaFlagProject,Get-KahuaProjectFlag,Set-KahuaProjectFlag
'@
# Keep the real adapter's dependency imports in the simulated adapter. This
# catches nested module reloads that remove the coordinator's JSON helpers.
$imports = @(Get-Content (Join-Path $PSScriptRoot 'KahuaSyncFlagDesktop.psm1') |
    Where-Object { $_ -match '^Import-Module ' }) -join [Environment]::NewLine
[IO.File]::WriteAllText((Join-Path $runnerDir 'KahuaDesktop.psm1'),'# Simulated UI dependency')
$desktop = $imports + [Environment]::NewLine + $desktop
[IO.File]::WriteAllText((Join-Path $runnerDir 'KahuaSyncFlagDesktop.psm1'),$desktop)
Import-Module (Join-Path $runnerDir 'KahuaSyncFlags.psm1') -Force
$runner=Join-Path $runnerDir 'Invoke-KahuaSyncFlags.ps1'
$events=Join-Path $runnerDir 'events.txt'
$csv=Join-Path $testRoot 'projects.csv'
$passed=0
function Assert($Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
function Expect-Error([scriptblock]$Call,[string]$Pattern) {
    $message=''
    try { & $Call | Out-Null } catch { $message=$_.Exception.Message }
    Assert ($message -match $Pattern) "Expected '$Pattern', got '$message'"
}
function Write-CsvText([string]$Text) { [IO.File]::WriteAllText($csv,$Text,[Text.UTF8Encoding]::new($true)) }
function Events { if (Test-Path $events) { Get-Content $events } }
function Read-Latest([string]$State) {
    $path=Get-ChildItem (Join-Path $State 'attempts') -Directory | Sort-Object CreationTimeUtc -Descending | Select-Object -First 1
    Get-Content (Join-Path $path.FullName 'result.json') -Raw | ConvertFrom-Json
}
function Run([string]$State,[string]$Mode='Plan',[string]$Action='Enable',[switch]$CheckFirst,[string]$OnMissing='Skip') {
    & $runner -CsvPath $csv -Environment DEVPRODUCTION -Action $Action -StateRoot $State -Mode $Mode -CheckFirst:$CheckFirst -OnMissing $OnMissing | Out-Null
}

# Doe's file is accepted unchanged; opaque placeholder UIDs are not identity.
Write-CsvText "domain,project_name,project_uid`nOnindus,220 - Seattle Heights,aaa-111`nOnindus,777-Warehouse Building,bbb-222`n"
$rows=@(Read-KahuaSyncFlagCsv $csv DEVPRODUCTION -Action Enable)
Assert ($rows.Count -eq 2 -and $rows[0].sync_enabled -eq $true -and $rows[1].scope -eq 'project-files') 'Doe CSV was not accepted'
Assert ($rows[0].auto_select_domain) 'CSV domain did not request automatic selection'
$passed++
Expect-Error { Read-KahuaSyncFlagCsv $csv DEVPRODUCTION } 'sync_enabled column'
$passed++
Write-CsvText "project_name,sync_enabled`nOne,false`nTwo,TRUE`n"
$rows=@(Read-KahuaSyncFlagCsv $csv DEVPRODUCTION Onindus)
Assert ($rows[0].sync_enabled -is [bool] -and -not $rows[0].sync_enabled -and $rows[1].sync_enabled) 'String false became true'
$passed++
Expect-Error { Read-KahuaSyncFlagCsv $csv DEVPRODUCTION Onindus -Action Enable } 'conflicts'
$passed++
foreach ($value in @('1','yes',' true','false ','', 'maybe')) {
    Write-CsvText "domain,project_name,sync_enabled`nOnindus,One,`"$value`"`n"
    Expect-Error { Read-KahuaSyncFlagCsv $csv DEVPRODUCTION } 'must be true or false'
}
$passed++
foreach ($content in @(
    "domain,project_name`nOnindus,One`nOnindus,one`n",
    "domain,project_name`nOnindus,One`nOther,Two`n",
    "domain,project_name`nOnindus,`" One`"`n",
    "domain,project_name`nOnindus,`n",
    "domain,project_name`nOnindus,CON`n",
    "domain,project_name`nOnindus,Parent/Child`n"
)) {
    Write-CsvText $content
    Expect-Error { Read-KahuaSyncFlagCsv $csv DEVPRODUCTION -Action Enable } 'Duplicate|one exact domain|project_name|domain in the CSV|unsupported'
}
$passed++
Write-CsvText "project_name`nOne`n"
$rows=@(Read-KahuaSyncFlagCsv $csv DEVPRODUCTION -Action Enable)
Assert (-not $rows[0].domain -and -not $rows[0].auto_select_domain) 'Names-only CSV did not defer to the selected domain'
$manualState=Join-Path $testRoot 'manual-domain'
$start=@(Events).Count
Run $manualState Check
$newEvents=@(Events | Select-Object -Skip $start)
Assert (-not @($newEvents | Where-Object {$_ -match '^SWITCH '}).Count) 'Names-only CSV switched domains'
Assert ((Read-Latest $manualState).projects[0].domain -ceq 'Onindus') 'Selected domain was not recorded'
Assert ((Get-Content (Join-Path $manualState 'selected-domain.json') -Raw | ConvertFrom-Json).domain -ceq 'Onindus') 'Selected domain was not durably bound'
$passed++
[IO.File]::WriteAllText((Join-Path $runnerDir 'current-domain.txt'),'Different')
$start=@(Events).Count
Expect-Error { Run $manualState Execute } 'differs from this saved run'
Assert (-not @(Events | Select-Object -Skip $start | Where-Object {$_ -match '^(SELECT|SET|SAVE) '}).Count) 'A changed manual domain reached a project'
Remove-Item (Join-Path $runnerDir 'current-domain.txt')
$passed++
Write-CsvText "domain,project_name`n,One`n,Two`n"
$rows=@(Read-KahuaSyncFlagCsv $csv DEVPRODUCTION -Action Enable)
Assert (-not $rows[0].auto_select_domain -and -not $rows[1].domain) 'Blank domain column did not defer to Doe'
$passed++
Write-CsvText "domain,project_name`nOnindus,One`n,Two`n"
Expect-Error { Read-KahuaSyncFlagCsv $csv DEVPRODUCTION -Action Enable } 'every CSV row'
$passed++
Write-CsvText "project_name`nOne`n"
$rows=@(Read-KahuaSyncFlagCsv $csv DEVPRODUCTION Onindus -Action Enable)
Assert ($rows[0].domain -ceq 'Onindus' -and -not $rows[0].auto_select_domain) 'Expected manual domain was mistaken for an automatic switch'
$passed++
Write-CsvText "domain,project_name`nOnindus,One`n"
Expect-Error { Read-KahuaSyncFlagCsv $csv DEVPRODUCTION Wrong -Action Enable } 'one exact domain'
$passed++
# Names with separators, Unicode, commas, and wildcard characters are literal.
$resolved=Resolve-KahuaProjectDisplay '0001 - 220 - Seattle Heights' '220 - Seattle Heights'
Assert ($resolved.project_number -ceq '0001') 'Project number was not derived from full exact name'
Assert ($null -eq (Resolve-KahuaProjectDisplay '0001 - West - Park' 'Park')) 'Unsafe suffix match accepted'
Assert ($null -eq (Resolve-KahuaProjectDisplay '0001 - one' 'One')) 'Case mismatch accepted'
Assert ($null -eq (Resolve-KahuaProjectDisplay '0001 - One Annex' 'One')) 'Prefix match accepted'
Assert ($null -eq (Resolve-KahuaProjectDisplay '220 - Seattle Heights' '220 - Seattle Heights')) 'Bare label was mistaken for a project name'
$passed++
$unicodeName=[string][char]0x00C9 + 'cole, Phase [2]'
Write-CsvText "domain,project_name`nOnindus,`"$unicodeName`"`n"
$rows=@(Read-KahuaSyncFlagCsv $csv DEVPRODUCTION -Action Disable)
Assert ($rows[0].project_name -ceq $unicodeName -and -not $rows[0].sync_enabled) 'CSV literal name was changed'
$passed++

Write-CsvText "domain,project_name`nOnindus,One`nOnindus,Two`n"
$state=Join-Path $testRoot 'enable'
$eventsBeforePlan=@(Events).Count
Run $state
Assert (@(Events).Count -eq $eventsBeforePlan) 'Plan touched desktop'
Assert ((Read-Latest $state).status -eq 'planned') 'Plan report missing'
$passed++
$consoleLog=Join-Path $testRoot 'check-console.log'
$successOutput=@(& $runner -CsvPath $csv -Environment DEVPRODUCTION -Action Enable -StateRoot $state -Mode Check 6> $consoleLog)
Assert (-not @(Events | Where-Object { $_ -match '^(SET|SAVE) ' }).Count) 'Check changed a flag'
Assert (@(Events | Where-Object { $_ -ceq 'SWITCH Onindus' }).Count -eq 1) 'CSV domain was not selected before project search'
Assert ((Read-Latest $state).projects.Count -eq 2) 'Check omitted a row'
$passed++
$checkReport=Read-Latest $state
$checkLogPath=$checkReport.progress_log
$checkLog=[IO.File]::ReadAllText($checkLogPath)
Assert ($successOutput.Count -eq 1 -and $successOutput[0] -match '^checked:') 'Progress messages polluted the success output stream'
Assert ($checkLog -match '\[Check/Preflight 1/2 \| One\]' -and $checkLog -match '\[Check/Preflight 2/2 \| Two\]') 'Live log omitted project names or queue positions'
Assert ($checkLog -match 'checked: Folders=Off' -and $checkLog -match 'complete: checked: 2 checked, 0 skipped') 'Live log omitted the current selection or final summary'
Assert ($checkLog -match '\[\+\d+\.\d+s\]' -and $checkLog -match '\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{2}:\d{2}') 'Live log omitted elapsed time or timestamp offset'
# Windows PowerShell 5.1 formats redirected information records at the host
# width. Ignore its inserted line breaks when checking the complete path.
$consoleText=(Get-Content $consoleLog -Raw) -replace '[\r\n]',''
Assert ($consoleText.Contains($checkLogPath)) 'Console did not show where its durable log was written'
$passed++
$start=@(Events).Count
Run $state Execute
$report=Read-Latest $state
Assert ($report.status -eq 'flags-verified' -and -not $report.transfer_verified) 'Flag completion incorrectly claims file transfer'
Assert (@($report.projects | Where-Object status -eq 'verified').Count -eq 2) 'Not all flags verified'
$trace=@(Events | Select-Object -Skip $start | Where-Object { $_ -match '^(SELECT|SET|READ|REOPEN) ' })
Assert (($trace -join ',') -ceq 'SELECT One,SET One True,REOPEN One,SELECT Two,SET Two True,REOPEN Two') 'Execute repeated Check or failed to apply row by row'
Assert ($report.check_attempt -ceq $checkReport.attempt -and $report.counts.changed -eq 2) 'Execute lost the source Check or changed count'
$passed++
Assert ([IO.File]::ReadAllText($checkLogPath) -ceq $checkLog) 'A subsequent attempt modified the previous progress log'
Assert ((Read-Latest $state).progress_log -cne $checkLogPath) 'A subsequent attempt reused the previous log file'
$passed++
$saves=@(Events | Where-Object { $_ -match '^SAVE ' }).Count
$reads=@(Events | Where-Object { $_ -match '^REOPEN ' }).Count
Run $state Execute
Assert (@(Events | Where-Object { $_ -match '^SAVE ' }).Count -eq $saves) 'Rerun inverted an already correct flag'
Assert (@(Events | Where-Object { $_ -match '^REOPEN ' }).Count -eq $reads+2 -and (Read-Latest $state).counts.already_correct -eq 2) 'Rerun trusted old receipts'
$passed++
Expect-Error { Run $state Execute Disable } 'different input values'
$passed++
$offState=Join-Path $testRoot 'disable'
Run $offState Execute Disable
Assert ((Read-Latest $offState).status -eq 'flags-verified' -and [IO.File]::ReadAllText((Join-Path $runnerDir 'One.selection')) -eq 'Off') 'Disable did not set false'
$passed++

foreach ($fault in @('Duplicate','Timeout')) {
    Write-CsvText "domain,project_name`nOnindus,One`nOnindus,$fault`nOnindus,Two`n"
    $count=@(Events).Count
    Expect-Error { Run (Join-Path $testRoot $fault) Execute } 'Duplicate|Timed out'
    $failure=Read-Latest (Join-Path $testRoot $fault)
    Assert ($failure.projects[0].status -eq 'verified' -and $failure.projects[1].status -eq 'failed' -and $failure.projects[2].status -eq 'planned') 'Unexpected row outcome after failed lookup'
    $failureLog=[IO.File]::ReadAllText($failure.progress_log)
    Assert ($failure.failed_step -eq 'project' -and $failure.failed_phase -eq 'Apply') 'Failed report lost the active step or phase'
    Assert ($failureLog -match ('2/3 \| '+[regex]::Escape($fault)) -and $failureLog -match '\[ERROR\].*STOPPED:') 'Failure log omitted the failing row or error'
    Assert ($failureLog -notmatch 'complete:') 'A failed lookup was logged as complete'
}
$passed++
$passed++ # Failed attempt progress remains useful without a success report.

Write-CsvText "domain,project_name`nOnindus,One`nOnindus,Missing`nOnindus,Two`n"
$mixed=Join-Path $testRoot 'mixed'
$start=@(Events).Count
Run $mixed Execute
$trace=@(Events | Select-Object -Skip $start | Where-Object { $_ -match '^(SELECT|SET|REOPEN|READ) ' })
Assert (($trace -join ',') -ceq 'SELECT One,SET One True,REOPEN One,SELECT Missing,SELECT Two,SET Two True,REOPEN Two') 'Fresh Execute did not finish each exact match in order'
$report=Read-Latest $mixed
Assert ($report.status -ceq 'completed-with-skips' -and $report.counts.verified -eq 2 -and $report.counts.skipped -eq 1) 'Partial match claimed full verification'
Assert ($report.projects[1].row -eq 3 -and $report.projects[1].reason -ceq 'missing-project' -and $null -eq $report.projects[1].receipt) 'Missing row lacks evidence'
$passed++

$start=@(Events).Count
Expect-Error { Run $mixed Execute -OnMissing Stop } 'row 3.*Missing'
$trace=@(Events | Select-Object -Skip $start | Where-Object { $_ -match '^SELECT ' })
Assert (($trace -join ',') -ceq 'SELECT One,SELECT Missing' -and (Read-Latest $mixed).projects[0].status -ceq 'verified') 'Row-by-row Stop crossed the missing row'
$passed++

$start=@(Events).Count
Expect-Error { Run $mixed Execute -CheckFirst -OnMissing Stop } 'row 3.*Missing'
Assert (-not @(Events | Select-Object -Skip $start | Where-Object { $_ -match '^SET ' }).Count) 'Whole-batch Stop reached the setter'
$passed++

$start=@(Events).Count
Run $mixed Execute -CheckFirst
$trace=@(Events | Select-Object -Skip $start | Where-Object { $_ -match '^(READ|SELECT|SET) ' })
Assert (($trace -join ',') -ceq 'SELECT One,READ One,SELECT Missing,SELECT Two,READ Two,SELECT One,SET One True,SELECT Two,SET Two True') 'CheckFirst did not check the batch then apply only matches'
$checkedAttempt=(Read-Latest $mixed).check_attempt
$start=@(Events).Count
Run $mixed Execute -CheckFirst
$trace=@(Events | Select-Object -Skip $start | Where-Object { $_ -match '^(READ|SELECT) ' })
Assert (($trace -join ',') -ceq 'SELECT One,SELECT Two' -and (Read-Latest $mixed).check_attempt -ceq $checkedAttempt) 'A completed CheckFirst pass was repeated'
$passed++

$start=@(Events).Count
Expect-Error { Run $mixed Execute -CheckFirst -OnMissing Stop } 'no exact project.*Missing'
Assert (-not @(Events | Select-Object -Skip $start | Where-Object { $_ -match '^(SELECT|SET) ' }).Count) 'A cached skipped row escaped strict whole-batch Stop'
Run $mixed Check
$checkReport=Read-Latest $mixed
Assert ($checkReport.status -ceq 'checked-with-skips' -and $checkReport.counts.checked -eq 2) 'Read-only Check failed to record skips'
$start=@(Events).Count
Expect-Error { Run $mixed Execute -OnMissing Stop } 'row 3.*Missing'
$trace=@(Events | Select-Object -Skip $start | Where-Object { $_ -match '^SET ' })
Assert (($trace -join ',') -ceq 'SET One True') 'Row-by-row Stop ignored the cached missing row or rejected prior rows'
$passed++

[IO.File]::WriteAllText((Join-Path $runnerDir 'missing-now-exists'),'fixture')
Run $mixed Check
$newCheck=Read-Latest $mixed
Assert ($newCheck.status -ceq 'checked' -and $newCheck.projects[1].status -ceq 'checked') 'Explicit Check did not refresh a formerly missing project'
Run $mixed Execute -CheckFirst -OnMissing Stop
Assert ((Read-Latest $mixed).counts.verified -eq 3 -and (Read-Latest $mixed).check_attempt -ceq $newCheck.attempt) 'Refreshed Check did not replace a cached skip'
Remove-Item (Join-Path $runnerDir 'missing-now-exists')
$passed++

[IO.File]::WriteAllText((Join-Path $runnerDir 'renumbered'),'fixture')
$start=@(Events).Count
Expect-Error { Run $mixed Execute } 'identity changed since Check'
Assert (-not @(Events | Select-Object -Skip $start | Where-Object { $_ -match '^SET ' }).Count) 'A reused name with a new project number was applied'
Remove-Item (Join-Path $runnerDir 'renumbered')
$passed++

Write-CsvText "domain,project_name`nOnindus,Missing`n"
$zero=Join-Path $testRoot 'zero'
Run $zero Check
Run $zero Execute
$report=Read-Latest $zero
Assert ($report.status -ceq 'completed-with-skips' -and $report.counts.verified -eq 0 -and $report.counts.skipped -eq 1) 'An all-missing batch claimed verified flags'
$passed++

# Use a genuine completed report to exercise legacy status and malformed
# history. Its immutable batch binding alone cannot certify an unfinished row.
Write-CsvText "domain,project_name`nOnindus,One`n"
$historyState=Join-Path $testRoot 'check-history'
Run $historyState Check
$legacyCheck=Read-Latest $historyState
$legacyCheck.schema=1
$legacyCheck.projects[0].PSObject.Properties.Remove('reason')
$legacyCheck.projects[0].receipt.reopened=$true
$savedPath=Join-Path (Join-Path (Join-Path $historyState 'attempts') $legacyCheck.attempt) 'result.json'
Write-KahuaFlagJson $savedPath $legacyCheck
Run $historyState Execute
Assert ((Read-Latest $historyState).check_attempt -ceq $legacyCheck.attempt) 'A matching legacy completed Check was not reused'
$hash=$legacyCheck.input_hash
$legacyCheck.PSObject.Properties.Remove('input_hash')
Write-KahuaFlagJson $savedPath $legacyCheck
Run $historyState Execute
Assert ((Read-Latest $historyState).check_attempt -ceq $legacyCheck.attempt) 'Original pre-hash Check did not use its binding and numbered receipts'
$legacyCheck | Add-Member NoteProperty input_hash $hash
foreach ($bad in @('row','hash','unfinished','domain','flag','failed')) {
    $broken=$legacyCheck | ConvertTo-Json -Depth 20 | ConvertFrom-Json
    switch ($bad) {
        'row' { $broken.projects=@() }
        'hash' { $broken.input_hash='another batch' }
        'unfinished' { $broken.projects[0].status='planned' }
        'domain' { $broken.projects[0].domain='Other' }
        'flag' { $broken.projects[0].desired_enabled=$false }
        'failed' { $broken.status='failed' }
    }
    Write-KahuaFlagJson $savedPath $broken
    Run $historyState Execute
    Assert ($null -eq (Read-Latest $historyState).check_attempt) "Reused incomplete or mismatched Check: $bad"
}
$passed++
Write-CsvText "domain,project_name`nOnindus,One`nOnindus,Two`n"
[IO.File]::WriteAllText((Join-Path $runnerDir 'wrong-domain'),'fixture')
$count=@(Events).Count
Expect-Error { Run (Join-Path $testRoot 'domain-failure') Execute } 'Wrong domain'
Assert (-not @(Events | Select-Object -Skip $count | Where-Object { $_ -match '^(SELECT|SET|SAVE) ' }).Count) 'Wrong domain reached project lookup'
Remove-Item (Join-Path $runnerDir 'wrong-domain')
$passed++
[IO.File]::WriteAllText((Join-Path $runnerDir 'paused'),'fixture')
$count=@(Events).Count
Expect-Error { Run (Join-Path $testRoot 'paused-enable') Execute } 'paused'
Assert (-not @(Events | Select-Object -Skip $count | Where-Object { $_ -match '^SAVE ' }).Count) 'Paused account was detected only after Save'
Assert ((Read-Latest (Join-Path $testRoot 'paused-enable')).projects[0].status -ceq 'failed') 'Read-only refusal was recorded as an uncertain write'
Run (Join-Path $testRoot 'paused-disable') Execute Disable
Remove-Item (Join-Path $runnerDir 'paused')
$passed++
[IO.File]::WriteAllText((Join-Path $runnerDir 'bad-identity'),'fixture')
Expect-Error { Run (Join-Path $testRoot 'bad-receipt') Execute } 'requested project_name'
Remove-Item (Join-Path $runnerDir 'bad-identity')
$passed++
$uncertainState=Join-Path $testRoot 'uncertain'
[IO.File]::WriteAllText((Join-Path $runnerDir 'uncertain-save'),'fixture')
$count=@(Events).Count
Expect-Error { Run $uncertainState Execute } 'Uncertain Save'
$report=Read-Latest $uncertainState
Assert ($report.status -eq 'failed' -and $report.projects[0].status -eq 'uncertain' -and $report.projects[1].status -eq 'planned') 'Uncertain save reported as complete or continued'
Assert (-not @(Events | Select-Object -Skip $count | Where-Object { $_ -match '^SET Two ' }).Count) 'Queue continued after uncertain Save'
Assert ((Get-Content $report.progress_log -Raw) -match 'Outcome is uncertain; inspect settings before resuming') 'Log hid an uncertain Save'
$passed++
Remove-Item (Join-Path $runnerDir 'uncertain-save')
$saves=@(Events | Where-Object { $_ -eq 'SAVE One On' }).Count
Run $uncertainState Execute
Assert ((Read-Latest $uncertainState).status -eq 'flags-verified') 'Resume did not reconcile and finish'
Assert (@(Events | Where-Object { $_ -eq 'SAVE One On' }).Count -eq $saves) 'Resume repeated a Save that already persisted'
$passed++
foreach ($marker in @('wrong-readback','not-reopened')) {
    [IO.File]::WriteAllText((Join-Path $runnerDir $marker),'fixture')
    $failureState=Join-Path $testRoot $marker
    Expect-Error { Run $failureState Execute } 'differs|reopening'
    Assert ((Read-Latest $failureState).status -eq 'failed') 'Invalid readback accepted'
    Remove-Item (Join-Path $runnerDir $marker)
}
$passed++
$locked=Join-Path $testRoot 'locked'
[void][IO.Directory]::CreateDirectory($locked)
$lock=[IO.File]::Open((Join-Path $locked 'flags.lock'),'OpenOrCreate','ReadWrite','None')
try { Expect-Error { Run $locked } 'used by another process|being used|cannot access' } finally { $lock.Dispose() }
$passed++
Expect-Error { Run (Join-Path $testRoot 'Kahua - Onindus/One') } 'outside Kahua'
$passed++

# Exercise the actual UI adapter's Set function against fake UI primitives.
# This covers the setter's decision logic, not UI selectors or real UIA calls.
$tokens=$null; $parseErrors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $PSScriptRoot 'KahuaSyncFlagDesktop.psm1'),[ref]$tokens,[ref]$parseErrors)
Assert (-not $parseErrors.Count) 'Desktop adapter does not parse'
$flagMethods=@('Get-KahuaProjectFlag','Set-KahuaProjectFlag') | ForEach-Object {
    $name=$_
    $ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true).Extent.Text
}
$adapterHarness=New-Module -Name KahuaFlagSetterFixture -ArgumentList ($flagMethods -join "`n") -ScriptBlock {
    param($SetterText)
    . ([scriptblock]::Create($SetterText))
    function Reset-FlagFixture([string]$Selection='Off',[string]$Account='running') {
        $script:account=$Account; $script:persisted=$Selection; $script:saves=0; $script:closes=0; $script:reads=0
        $script:badRead=$false; $script:failSave=$false; $script:failWait=$false; $script:waits=0
        $script:save=[pscustomobject]@{Current=[pscustomobject]@{IsEnabled=$false}}
        $script:toggle=[pscustomobject]@{Current=[pscustomobject]@{ToggleState=$Selection};Count=0;Stuck=$false;SaveControl=$script:save}
        $script:toggle | Add-Member ScriptMethod Toggle {
            $this.Count++
            if (-not $this.Stuck) {
                $this.Current.ToggleState=if ($this.Current.ToggleState -eq 'Off') { 'On' } else { 'Off' }
                $this.SaveControl.Current.IsEnabled=$true
            }
        }
    }
    function Open-KahuaFlagSettings { param($Identity) return $script:account }
    function Find-KahuaControl { param($Name,$Type) return $script:save }
    function Wait-KahuaControl { param($Name,$Type) return $script:save }
    function Get-KahuaRootFlagToggle { return $script:toggle }
    function Invoke-KahuaControl {
        param($Control)
        $script:saves++
        if ($script:failSave) { throw 'Fixture Save failure' }
        $script:persisted=$script:toggle.Current.ToggleState
        $script:save.Current.IsEnabled=$false
    }
    function Wait-KahuaFlagSave {
        param($Desired)
        $script:waits++
        if ($script:failWait) { throw 'Fixture Save did not settle' }
        if ($script:persisted -cne $Desired) { throw 'Fixture unexpected selection' }
    }
    function Close-KahuaFlagSettings { $script:closes++ }
    function Read-KahuaFlagSelection {
        param($Identity)
        $script:reads++
        [pscustomobject]@{selection=$(if ($script:badRead) { 'Indeterminate' } else { $script:persisted });
            account_sync_state=$script:account;sync_root='fixture-project-root'}
    }
    function Get-FlagFixture { [pscustomobject]@{saves=$script:saves;toggles=$script:toggle.Count;closes=$script:closes;persisted=$script:persisted;waits=$script:waits;reads=$script:reads} }
    function Set-FlagFixtureFault([string]$Fault) {
        switch ($Fault) {
            'read' { $script:badRead=$true }
            'save' { $script:failSave=$true }
            'dirty' { $script:save.Current.IsEnabled=$true }
            'stuck' { $script:toggle.Stuck=$true }
            'wait' { $script:failWait=$true }
        }
    }
    Export-ModuleMember -Function Get-KahuaProjectFlag,Set-KahuaProjectFlag,Reset-FlagFixture,Get-FlagFixture,Set-FlagFixtureFault
}
Import-Module $adapterHarness -Force
$id=[pscustomobject]@{environment='DEVPRODUCTION';domain='Onindus';project_name='One';project_number='0001';display='0001 - One'}
Reset-FlagFixture Off
$preview=Get-KahuaProjectFlag -Identity $id
Assert ((Get-FlagFixture).reads -eq 1 -and -not $preview.reopened -and $preview.selection -ceq 'Off') 'Check did not report its single observation'
$passed++
foreach ($case in @(@('Off',$true,'On'),@('On',$false,'Off'),@('Indeterminate',$true,'On'),@('Indeterminate',$false,'Off'))) {
    Reset-FlagFixture $case[0]
    $receipt=Set-KahuaProjectFlag -Identity $id -Enabled $case[1]
    $detail=Get-FlagFixture
    Assert ($receipt.selection -eq $case[2] -and $receipt.reopened -and $detail.reads -eq 1 -and $detail.saves -eq 1 -and $detail.closes -eq 1 -and $detail.waits -eq 1) 'Actual setter did not wait, save and reopen exactly once'
    Assert ($detail.toggles -ge 1 -and $detail.toggles -le 2) 'Unexpected toggle cycle'
    $passed++
}
foreach ($case in @(@('On',$true),@('Off',$false))) {
    Reset-FlagFixture $case[0]
    $receipt=Set-KahuaProjectFlag -Identity $id -Enabled $case[1]
    $detail=Get-FlagFixture
    Assert (-not $receipt.changed -and $receipt.reopened -and $detail.reads -eq 1 -and $detail.saves -eq 0 -and $detail.toggles -eq 0) 'Already-correct flag was modified or not reopened once'
}
$passed++
Reset-FlagFixture Off
Set-FlagFixtureFault read
Expect-Error { Set-KahuaProjectFlag -Identity $id -Enabled $true } 'did not persist'
$passed++
Reset-FlagFixture Off
Set-FlagFixtureFault dirty
Expect-Error { Set-KahuaProjectFlag -Identity $id -Enabled $true } 'unsaved edits'
Assert ((Get-FlagFixture).toggles -eq 0 -and (Get-FlagFixture).saves -eq 0) 'Unrelated edits were saved'
$passed++
Reset-FlagFixture Off paused
Expect-Error { Set-KahuaProjectFlag -Identity $id -Enabled $true } 'paused'
Assert ((Get-FlagFixture).toggles -eq 0) 'Paused enable changed a selection'
Reset-FlagFixture On paused
[void](Set-KahuaProjectFlag -Identity $id -Enabled $false)
Assert ((Get-FlagFixture).persisted -eq 'Off') 'Paused account could not disable its project'
$passed++
Reset-FlagFixture Off
Set-FlagFixtureFault stuck
Expect-Error { Set-KahuaProjectFlag -Identity $id -Enabled $true } 'No Save was attempted'
Assert ((Get-FlagFixture).saves -eq 0) 'Setter saved a failed toggle'
$passed++
Reset-FlagFixture Off
Set-FlagFixtureFault save
Expect-Error { Set-KahuaProjectFlag -Identity $id -Enabled $true } 'Save failure'
Assert ((Get-FlagFixture).closes -eq 0) 'Uncertain Save was hidden by closing settings'
$passed++
Reset-FlagFixture Off
Set-FlagFixtureFault wait
Expect-Error { Set-KahuaProjectFlag -Identity $id -Enabled $true } 'did not settle'
Assert ((Get-FlagFixture).closes -eq 0 -and (Get-FlagFixture).saves -eq 1) 'Unsettled Save was closed or repeated'
$passed++
Remove-Module $adapterHarness

# A disabled Save can be a transient in-flight state. Exercise the real wait
# and close guards with a pending/dirty/settled sequence and a late warning.
$saveFunctions=@('Assert-KahuaFlagNoUnsavedWarning','Wait-KahuaFlagSave','Close-KahuaFlagSettings') | ForEach-Object {
    $name=$_
    $ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true).Extent.Text
}
$saveHarness=New-Module -Name KahuaFlagSaveFixture -ArgumentList ($saveFunctions -join "`n") -ScriptBlock {
    param($Source)
    . ([scriptblock]::Create($Source))
    function Reset-SaveFixture([string]$Mode) { $script:mode=$Mode; $script:reads=0; $script:clicks=0 }
    function Assert-KahuaFlagNoSyncError { }
    function Get-KahuaWindow { 'fixture-window' }
    function Find-KahuaControl {
        param($Name,$Type,$Id,$Root,[switch]$All)
        if ($Name -eq 'Unsaved Changes Alert') {
            if ($script:mode -eq 'warning' -and $script:clicks -gt 0) { return 'fixture-warning' }
            return
        }
        if ($Name -eq 'Save') { $script:reads++ }
        $enabled=$false
        if ($Name -eq 'Save') { $enabled=$script:mode -eq 'pending' -or ($script:mode -eq 'transient' -and $script:reads -eq 2) }
        if ($Name -eq 'Cancel') { $enabled=$script:mode -eq 'cancel-pending' -or ($script:mode -eq 'transient' -and $script:reads -le 2) }
        if ($Id -eq 'CloseModal') { $enabled=$true }
        [pscustomobject]@{Current=[pscustomobject]@{IsEnabled=$enabled}}
    }
    function Get-KahuaRootFlagToggle {
        param([switch]$AllowDisabled)
        if ($script:mode -eq 'disabled' -or ($script:mode -eq 'disabled-then-ready' -and $script:reads -le 2)) {
            if ($AllowDisabled) { return $null }
            throw 'The project Folders switch is unavailable.'
        }
        [pscustomobject]@{Current=[pscustomobject]@{ToggleState=$(if ($script:mode -eq 'wrong-selection') { 'Off' } else { 'On' })}}
    }
    function Invoke-KahuaControl { param($Control) $script:clicks++ }
    function Get-SaveFixture { [pscustomobject]@{reads=$script:reads;clicks=$script:clicks} }
    Export-ModuleMember -Function Wait-KahuaFlagSave,Close-KahuaFlagSettings,Reset-SaveFixture,Get-SaveFixture
}
Import-Module $saveHarness -Force
Reset-SaveFixture transient
Wait-KahuaFlagSave -Desired On -TimeoutSeconds 5 -SettledMilliseconds 500
Assert ((Get-SaveFixture).reads -ge 5) 'Transient Save disabled state was accepted as settled'
$passed++
Reset-SaveFixture disabled-then-ready
Wait-KahuaFlagSave -Desired On -TimeoutSeconds 5 -SettledMilliseconds 500
Assert ((Get-SaveFixture).reads -ge 5) 'Temporarily disabled Folders was accepted as settled or failed the wait'
$passed++
foreach ($mode in @('pending','cancel-pending','disabled')) {
    Reset-SaveFixture $mode
    Expect-Error { Wait-KahuaFlagSave -Desired On -TimeoutSeconds 0 -SettledMilliseconds 500 } 'did not settle'
    Assert ((Get-SaveFixture).clicks -eq 0) 'Unsettled Save caused another UI action'
    $passed++
}
Reset-SaveFixture wrong-selection
Expect-Error { Wait-KahuaFlagSave -Desired On } 'selection changed'
$passed++
Reset-SaveFixture warning
Expect-Error { Close-KahuaFlagSettings } 'unsaved changes after Save'
Assert ((Get-SaveFixture).clicks -eq 1) 'Close warning was silently dismissed or saved again'
$passed++
Remove-Module $saveHarness

if (-not ('System.Windows.Automation.ElementNotAvailableException' -as [type])) {
    Add-Type 'namespace System.Windows.Automation { public class ElementNotAvailableException : System.Exception { public ElementNotAvailableException() : base("Unrecognized error.") {} } }'
}
# Model the picker tree so the tests exercise the production result reader as
# well as its consumer. Use a fresh PowerShell process, as in the other UI fixtures.
if ('System.Windows.Automation.TreeWalker' -as [type]) { throw 'Run the project flag fixtures in a fresh PowerShell process.' }
Add-Type @'
namespace System.Windows.Automation {
    public class TreeWalker { public static object RawViewWalker; }
    public class ControlType { public static string Window="Window", ListItem="ListItem"; }
    public class ValuePattern { public static string Pattern="Value"; }
    public class ScrollPattern { public static string Pattern="Scroll"; }
    public class TreeScope { public static string Descendants="Descendants"; }
    public class AutomationElement { public static string ControlTypeProperty="Type"; }
    public class PropertyCondition { public PropertyCondition(object property, object value) {} }
}
'@
$walker=[pscustomobject]@{}
$walker | Add-Member ScriptMethod GetParent { param($Control) $Control.parent }
[Windows.Automation.TreeWalker]::RawViewWalker=$walker
$selectorSource=@('Get-KahuaSearchProjectRows','Select-KahuaFlagProject','Wait-KahuaFlagProjectOpen') | ForEach-Object {
    $name=$_
    $ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true).Extent.Text
}
$searchHarness=New-Module -Name KahuaFlagSearchFixture -ArgumentList ($selectorSource -join "`n") -ScriptBlock {
    param($SelectorText)
    . ([scriptblock]::Create($SelectorText))
    function Reset-SearchFixture([string]$Mode) {
        $script:mode=$Mode; $script:reads=0; $script:picked=''; $script:query=''; $script:closed=$false
        $script:projectClicks=0; $script:navigationReads=0
        $shell=[pscustomobject]@{Current=[pscustomobject]@{ControlType='Window';AutomationId=''}}
        $panel=[pscustomobject]@{kind='panel';Current=[pscustomobject]@{ControlType='Group';AutomationId=''};parent=$shell}
        $script:search=[pscustomobject]@{kind='search';parent=$panel}
        $script:search | Add-Member ScriptMethod GetCurrentPattern {
            $script:reads++
            $value=if ($script:mode -eq 'wrong-query') { 'Other' } else { $script:query }
            [pscustomobject]@{Current=[pscustomobject]@{Value=$value}}
        }
        $script:picker=[pscustomobject]@{kind='picker'}
    }
    function Confirm-KahuaFlagDomain { param($Environment,$Domain) }
    function Assert-KahuaFlagNoSyncError { }
    function Get-KahuaControlName($Control) { $Control.Current.Name }
    function Select-KahuaUniqueElement { param([object[]]$Elements) $Elements }
    function Get-KahuaActiveRoot {
        $script:navigationReads++
        [pscustomobject]@{generation=$script:navigationReads}
    }
    function Find-KahuaControl {
        param($Id,$Name,$Type,$Root,[switch]$All)
        if ($Root -and $Root.PSObject.Properties['kind'] -and $Root.kind -eq 'panel') {
            if ($Id -eq 'HomeButton' -and $script:mode -eq 'outside-picker') { return $script:picker }
            if ($Name -ceq 'Find a Project') { return $script:picker }
            if ($Type -eq 'List' -and $script:mode -notin @('no-list','outside-picker')) { return (New-SearchList) }
            if ($Type -eq 'ProgressBar' -and $script:mode -eq 'busy') { return $script:picker }
            if ($Type -eq 'Text' -and $script:mode -in @('empty','no-list','busy','outside-picker')) {
                return [pscustomobject]@{Current=[pscustomobject]@{Name='No results found.'}}
            }
            return
        }
        if ($Id -eq 'HomeButton') { return [pscustomobject]@{kind='home'} }
        if ($Id -eq 'Shell_HomePartitionSelector') { return $script:picker }
        if ($Root -and $Root.generation -eq 1 -and
            (($script:mode -eq 'header-stale' -and $Id -eq 'Shell_PartitionSelector') -or
            ($script:mode -eq 'picker-stale' -and $Id -eq 'SearchBox'))) {
            throw [System.Windows.Automation.ElementNotAvailableException]::new()
        }
        if ($Id -eq 'Shell_PartitionSelector') {
            if ($Name -cne ('Current Project '+$script:picked)) { throw 'Wrong header selection' }
            return [pscustomobject]@{Current=[pscustomobject]@{IsEnabled=$true}}
        }
        if ($Id -eq 'SearchBox' -and -not $script:closed) { return $script:search }
    }
    function Wait-KahuaControl {
        param($Id,$Name,$Type)
        if ($Id -eq 'SearchBox') { return $script:search }
        if ($Id -eq 'Shell_PartitionSelector') {
            if ($Name -cne ('Current Project '+$script:picked)) { throw 'Wrong header selection' }
            return $script:picker
        }
    }
    function Set-KahuaValue { param($Control,$Value) $script:query=$Value }
    function Invoke-KahuaControl {
        param($Control,[switch]$Click)
        if ($Control.kind -eq 'home') { $script:closed=$true }
        if ($Control.kind -eq 'picker') { $script:closed=$false }
        if ($Control.kind -eq 'result') {
            $script:projectClicks++; $script:picked=$Control.Current.Name; $script:closed=$true
            if ($script:mode -eq 'click-stale') { throw [System.Windows.Automation.ElementNotAvailableException]::new() }
        }
    }
    function New-SearchResult([string]$Name,[int]$Id) {
        $item=[pscustomobject]@{kind='result';Current=[pscustomobject]@{Name=$Name;IsOffscreen=$false};runtime=$Id}
        $item | Add-Member ScriptMethod GetRuntimeId { return @($this.runtime) }
        return $item
    }
    function New-SearchList {
        $rows=@((New-SearchResult '0001 - One' 1),(New-SearchResult '0002 - Parent - One' 2))
        if ($script:mode -eq 'related') { $rows=@((New-SearchResult '0002 - One Annex' 2)) }
        if ($script:mode -in @('empty','loading') -or ($script:mode -eq 'refresh' -and $script:reads -le 8)) { $rows=@() }
        if ($script:mode -eq 'unfiltered') { $rows=@((New-SearchResult '0001 - Stale result' 1)) }
        if ($script:mode -eq 'bare') { $rows=@((New-SearchResult 'One' 1)) }
        if ($script:mode -eq 'duplicate') { $rows += New-SearchResult '0003 - One' 3 }
        if ($script:mode -eq 'incomplete') { $rows[1].Current.IsOffscreen=$true }
        $list=[pscustomobject]@{Current=[pscustomobject]@{IsEnabled=$true};rows=$rows}
        $list | Add-Member ScriptMethod FindAll { param($Scope,$Condition) $this.rows }
        $list | Add-Member ScriptMethod TryGetCurrentPattern { param($Pattern,$Result) return $false }
        return $list
    }
    function Get-SearchFixture { [pscustomobject]@{picked=$script:picked;reads=$script:reads;query=$script:query;project_clicks=$script:projectClicks;navigation_reads=$script:navigationReads;closed=$script:closed} }
    Export-ModuleMember -Function Select-KahuaFlagProject,Reset-SearchFixture,Get-SearchFixture
}
Import-Module $searchHarness -Force
Reset-SearchFixture refresh
$identity=Select-KahuaFlagProject -Environment DEVPRODUCTION -Domain Onindus -Name One
$observed=Get-SearchFixture
Assert ($identity.project_name -ceq 'One' -and $identity.project_number -ceq '0001' -and $observed.picked -ceq '0001 - One') 'Search selected a parent/suffix match'
Assert ($observed.reads -gt 1 -and $observed.query -ceq 'One') 'Search did not recover after asynchronous list replacement'
$passed++
Reset-SearchFixture duplicate
Expect-Error { Select-KahuaFlagProject -Environment DEVPRODUCTION -Domain Onindus -Name One } 'More than one'
Assert (-not (Get-SearchFixture).picked) 'Ambiguous exact name was selected'
$passed++
Reset-SearchFixture incomplete
Expect-Error { Select-KahuaFlagProject -Environment DEVPRODUCTION -Domain Onindus -Name One } 'offscreen results'
Assert (-not (Get-SearchFixture).picked) 'Incomplete search result list was selected'
$passed++
Reset-SearchFixture wrong-query
Expect-Error { Select-KahuaFlagProject -Environment DEVPRODUCTION -Domain Onindus -Name One } 'search text changed'
$passed++
foreach ($case in @('empty','no-list','related','unfiltered','bare','loading','busy','outside-picker')) {
    Reset-SearchFixture $case
    $failure=$null
    try { Select-KahuaFlagProject -Environment DEVPRODUCTION -Domain Onindus -Name One -SearchTimeoutSeconds 3 }
    catch { $failure=$_ }
    $observed=Get-SearchFixture
    Assert ($null -ne $failure -and $observed.project_clicks -eq 0) 'Negative search selected a project'
    if ($case -in @('empty','no-list','related')) {
        Assert ($failure.Exception.Data['KahuaFlagMissingProject'] -eq $true -and $observed.closed) 'Confirmed miss did not close the picker and return a missing-name outcome'
    } else {
        Assert ($failure.Exception.Message -match 'No unique, stable' -and -not $failure.Exception.Data['KahuaFlagMissingProject']) "Unconfirmed search was treated as missing: $case"
    }
    $passed++
}
foreach ($fault in @('header-stale','picker-stale')) {
    Reset-SearchFixture $fault
    $log=New-KahuaFlagLogContext Check
    $identity=Select-KahuaFlagProject -Environment PRODUCTION -Domain Onindus -Name One -LogContext $log
    $observed=Get-SearchFixture
    Assert ($identity.display -ceq '0001 - One' -and $observed.project_clicks -eq 1 -and $observed.navigation_reads -eq 2) 'Project-open recovery clicked again or reused the expired root'
    Assert ($log.Recoveries.Count -eq 1) 'Project-open recovery was absent from the report context'
    $passed++
}
Reset-SearchFixture 'header-stale'
Expect-Error { Select-KahuaFlagProject -Environment PRODUCTION -Domain Onindus -Name One -RecoveryAttempts 0 } 'Unrecognized error'
Assert ((Get-SearchFixture).project_clicks -eq 1 -and (Get-SearchFixture).navigation_reads -eq 1) 'Disabled navigation recovery was ignored'
$passed++
Reset-SearchFixture 'click-stale'
Expect-Error { Select-KahuaFlagProject -Environment PRODUCTION -Domain Onindus -Name One } 'Unrecognized error'
Assert ((Get-SearchFixture).project_clicks -eq 1 -and (Get-SearchFixture).navigation_reads -eq 0) 'A possibly completed project click was retried'
$passed++
Remove-Module $searchHarness

# Logging remains readable for literal Unicode names, flushes every event for
# live readers, and emits a bounded heartbeat rather than a line per UI poll.
$logContext=New-KahuaFlagLogContext -Mode Check
$logContext.Path=Join-Path $testRoot 'logger.log'
$logContext.Phase='Preflight'; $logContext.Index=1; $logContext.Total=1; $logContext.Project=$unicodeName
$loggerOutput=@(Write-KahuaFlagProgress $logContext 'search-results' "First line`nsecond line" 6> (Join-Path $testRoot 'logger-console.log'))
Assert ($loggerOutput.Count -eq 0) 'Logger returned a receipt-like success value'
$lines=@(Get-Content $logContext.Path -Encoding UTF8)
Assert ($lines.Count -eq 1 -and $lines[0].Contains($unicodeName) -and $lines[0].Contains('First line second line')) 'Log corrupted a Unicode name or split one event over lines'
$passed++
Write-KahuaFlagProgress $logContext 'search-results' 'Still waiting for exact match.' -Heartbeat
Assert (@(Get-Content $logContext.Path).Count -eq 1) 'Heartbeat flooded the log immediately after a step message'
Start-Sleep -Milliseconds 5100
Write-KahuaFlagProgress $logContext 'search-results' 'Still waiting for exact match.' -Heartbeat
Write-KahuaFlagProgress $logContext 'search-results' 'Still waiting for exact match.' -Heartbeat
Assert (@(Get-Content $logContext.Path).Count -eq 2) 'Heartbeat was missing after the interval or repeated without delay'
$passed++

Write-Output "PASS: $passed project flag scenarios (simulated desktop). Fixtures: $testRoot"
