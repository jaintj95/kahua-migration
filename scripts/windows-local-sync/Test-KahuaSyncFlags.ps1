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
    param($Environment,$Domain,$Name)
    Add-Event "SELECT $Name"
    if ($Name -eq 'Missing') { throw 'Project not found' }
    if ($Name -eq 'Duplicate') { throw 'Duplicate exact project name' }
    [pscustomobject]@{environment=$Environment;domain=$Domain;project_name=$Name;display="0001 - $Name"}
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
        scope='project-files';selection=$Selection;reopened=$true;account_sync_state=$account}
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
    if ((Read-Selection $Identity) -cne $desired) {
        [IO.File]::WriteAllText((Join-Path $PSScriptRoot ($Identity.project_name+'.selection')),$desired)
        Add-Event "SAVE $($Identity.project_name) $desired"
    }
    if (Test-Path (Join-Path $PSScriptRoot 'uncertain-save')) { throw 'Uncertain Save: readback unavailable' }
    if (Test-Path (Join-Path $PSScriptRoot 'wrong-readback')) { return New-Receipt $Identity 'Indeterminate' }
    $receipt=New-Receipt $Identity $desired
    if (Test-Path (Join-Path $PSScriptRoot 'not-reopened')) { $receipt.reopened=$false }
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
function Run([string]$State,[string]$Mode='Plan',[string]$Action='Enable') {
    & $runner -CsvPath $csv -Environment DEVPRODUCTION -Action $Action -StateRoot $State -Mode $Mode | Out-Null
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
Assert ($checkLog -match 'checked: Folders=Off' -and $checkLog -match 'complete: checked: 2/2') 'Live log omitted the current selection or final summary'
Assert ($checkLog -match '\[\+\d+\.\d+s\]' -and $checkLog -match '\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{2}:\d{2}') 'Live log omitted elapsed time or timestamp offset'
# Windows PowerShell 5.1 formats redirected information records at the host
# width. Ignore its inserted line breaks when checking the complete path.
$consoleText=(Get-Content $consoleLog -Raw) -replace '[\r\n]',''
Assert ($consoleText.Contains($checkLogPath)) 'Console did not show where its durable log was written'
$passed++
Run $state Execute
$report=Read-Latest $state
Assert ($report.status -eq 'flags-verified' -and -not $report.transfer_verified) 'Flag completion incorrectly claims file transfer'
Assert (@($report.projects | Where-Object status -eq 'verified').Count -eq 2) 'Not all flags verified'
$trace=@(Events)
$firstSet=0
while ($firstSet -lt $trace.Count -and $trace[$firstSet] -notmatch '^SET ') { $firstSet++ }
Assert (@($trace[0..($firstSet-1)] | Where-Object { $_ -match '^READ ' }).Count -ge 2) 'Mutation began before all projects were checked'
$passed++
Assert ([IO.File]::ReadAllText($checkLogPath) -ceq $checkLog) 'A subsequent attempt modified the previous progress log'
Assert ((Read-Latest $state).progress_log -cne $checkLogPath) 'A subsequent attempt reused the previous log file'
$passed++
$saves=@(Events | Where-Object { $_ -match '^SAVE ' }).Count
$reads=@(Events | Where-Object { $_ -match '^READ ' }).Count
Run $state Execute
Assert (@(Events | Where-Object { $_ -match '^SAVE ' }).Count -eq $saves) 'Rerun inverted an already correct flag'
Assert (@(Events | Where-Object { $_ -match '^READ ' }).Count -gt $reads) 'Rerun trusted old receipts'
$passed++
Expect-Error { Run $state Execute Disable } 'different input values'
$passed++
$offState=Join-Path $testRoot 'disable'
Run $offState Execute Disable
Assert ((Read-Latest $offState).status -eq 'flags-verified' -and [IO.File]::ReadAllText((Join-Path $runnerDir 'One.selection')) -eq 'Off') 'Disable did not set false'
$passed++

foreach ($missing in @('Missing','Duplicate')) {
    Write-CsvText "domain,project_name`nOnindus,One`nOnindus,$missing`n"
    $count=@(Events).Count
    Expect-Error { Run (Join-Path $testRoot $missing) Execute } 'not found|Duplicate'
    Assert (-not @(Events | Select-Object -Skip $count | Where-Object { $_ -match '^(SET|SAVE) ' }).Count) 'Failed lookup allowed partial batch mutation'
    $failure=Read-Latest (Join-Path $testRoot $missing)
    $failureLog=[IO.File]::ReadAllText($failure.progress_log)
    Assert ($failure.failed_step -eq 'project' -and $failure.failed_phase -eq 'Preflight') 'Failed report lost the active step or phase'
    Assert ($failureLog -match ('2/2 \| '+[regex]::Escape($missing)) -and $failureLog -match '\[ERROR\].*STOPPED:') 'Failure log omitted the failing row or error'
    Assert ($failureLog -notmatch 'complete:') 'A failed lookup was logged as complete'
}
$passed++
$passed++ # Failed attempt progress remains useful without a success report.
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
Assert (-not @(Events | Select-Object -Skip $count | Where-Object { $_ -match '^SET ' }).Count) 'Paused account was detected only after mutations'
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
Assert ($report.status -eq 'failed' -and $report.projects[0].status -eq 'uncertain' -and $report.projects[1].status -eq 'checked') 'Uncertain save reported as complete or continued'
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
$setter=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Set-KahuaProjectFlag'},$true)
$adapterHarness=New-Module -Name KahuaFlagSetterFixture -ArgumentList $setter.Extent.Text -ScriptBlock {
    param($SetterText)
    . ([scriptblock]::Create($SetterText))
    function Reset-FlagFixture([string]$Selection='Off',[string]$Account='running') {
        $script:account=$Account; $script:persisted=$Selection; $script:saves=0; $script:closes=0
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
        [pscustomobject]@{selection=$(if ($script:badRead) { 'Indeterminate' } else { $script:persisted });
            account_sync_state=$script:account;sync_root='fixture-project-root'}
    }
    function Get-FlagFixture { [pscustomobject]@{saves=$script:saves;toggles=$script:toggle.Count;closes=$script:closes;persisted=$script:persisted;waits=$script:waits} }
    function Set-FlagFixtureFault([string]$Fault) {
        switch ($Fault) {
            'read' { $script:badRead=$true }
            'save' { $script:failSave=$true }
            'dirty' { $script:save.Current.IsEnabled=$true }
            'stuck' { $script:toggle.Stuck=$true }
            'wait' { $script:failWait=$true }
        }
    }
    Export-ModuleMember -Function Set-KahuaProjectFlag,Reset-FlagFixture,Get-FlagFixture,Set-FlagFixtureFault
}
Import-Module $adapterHarness -Force
$id=[pscustomobject]@{environment='DEVPRODUCTION';domain='Onindus';project_name='One';project_number='0001';display='0001 - One'}
foreach ($case in @(@('Off',$true,'On'),@('On',$false,'Off'),@('Indeterminate',$true,'On'),@('Indeterminate',$false,'Off'))) {
    Reset-FlagFixture $case[0]
    $receipt=Set-KahuaProjectFlag -Identity $id -Enabled $case[1]
    $detail=Get-FlagFixture
    Assert ($receipt.selection -eq $case[2] -and $receipt.reopened -and $detail.saves -eq 1 -and $detail.closes -eq 1 -and $detail.waits -eq 1) 'Actual setter did not wait, save and reopen the requested root state'
    Assert ($detail.toggles -ge 1 -and $detail.toggles -le 2) 'Unexpected toggle cycle'
    $passed++
}
foreach ($case in @(@('On',$true),@('Off',$false))) {
    Reset-FlagFixture $case[0]
    $receipt=Set-KahuaProjectFlag -Identity $id -Enabled $case[1]
    $detail=Get-FlagFixture
    Assert (-not $receipt.changed -and $receipt.reopened -and $detail.saves -eq 0 -and $detail.toggles -eq 0) 'Actual setter toggled an already correct state'
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

$selector=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Select-KahuaFlagProject'},$true)
$searchHarness=New-Module -Name KahuaFlagSearchFixture -ArgumentList $selector.Extent.Text -ScriptBlock {
    param($SelectorText)
    . ([scriptblock]::Create($SelectorText))
    function Reset-SearchFixture([string]$Mode) {
        $script:mode=$Mode; $script:reads=0; $script:picked=''; $script:query=''; $script:closed=$false
        $script:search=[pscustomobject]@{kind='search'}
        $script:picker=[pscustomobject]@{kind='picker'}
    }
    function Confirm-KahuaFlagDomain { param($Environment,$Domain) }
    function Find-KahuaControl {
        param($Id,$Name,$Type,[switch]$All)
        if ($Id -in @('HomeButton','Shell_HomePartitionSelector')) { return $script:picker }
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
        if ($Control.kind -eq 'result') { $script:picked=$Control.Current.Name; $script:closed=$true }
    }
    function New-SearchResult([string]$Name,[int]$Id) {
        $item=[pscustomobject]@{kind='result';Current=[pscustomobject]@{Name=$Name};runtime=$Id}
        $item | Add-Member ScriptMethod GetRuntimeId { return @($this.runtime) }
        return $item
    }
    function Get-KahuaSearchProjectRows {
        param($Search)
        $script:reads++
        if ($script:mode -eq 'refresh' -and $script:reads -eq 1) {
            return [pscustomobject]@{rows=@();complete=$false;reason='Loading results'}
        }
        $rows=@((New-SearchResult '0001 - One' 1),(New-SearchResult '0002 - Parent - One' 2))
        if ($script:mode -eq 'duplicate') { $rows += New-SearchResult '0003 - One' 3 }
        $complete=$script:mode -ne 'incomplete'
        [pscustomobject]@{rows=$rows;complete=$complete;reason=$(if ($complete) { '' } else { 'Incomplete result list' })}
    }
    function Get-SearchFixture { [pscustomobject]@{picked=$script:picked;reads=$script:reads;query=$script:query} }
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
Expect-Error { Select-KahuaFlagProject -Environment DEVPRODUCTION -Domain Onindus -Name One } 'Incomplete result list'
Assert (-not (Get-SearchFixture).picked) 'Incomplete search result list was selected'
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
