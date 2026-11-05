#requires -Version 5.1
# Exercise the real recovery and setter functions with simulated UI controls.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
Import-Module (Join-Path $PSScriptRoot 'KahuaSyncFlags.psm1') -Force
if (-not ('System.Windows.Automation.ElementNotAvailableException' -as [type])) {
    Add-Type @'
namespace System.Windows.Automation {
    public class ElementNotAvailableException : System.Exception {
        public ElementNotAvailableException() : base("Fixture expired element") {}
    }
}
'@
}
$passed=0
function Assert($Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
function Expect-Error([scriptblock]$Call,[string]$Pattern) {
    $message=''
    try { & $Call | Out-Null } catch { $message=$_.Exception.Message }
    Assert ($message -match $Pattern) "Expected '$Pattern', got '$message'"
}
$readState=@{count=0}
$log=New-KahuaFlagLogContext Check
$value=Invoke-KahuaFlagReadRecovery -LogContext $log -DelayMilliseconds 0 -Read {
    $readState.count++
    if ($readState.count -lt 3) { throw [System.Windows.Automation.ElementNotAvailableException]::new() }
    'fresh-value'
}
Assert ($value -ceq 'fresh-value' -and $readState.count -eq 3 -and $log.Recoveries.Count -eq 2) 'Stale snapshots were not bounded and retried with evidence'
$passed++
$readState.count=0
Expect-Error {
    Invoke-KahuaFlagReadRecovery -DelayMilliseconds 0 -RecoveryAttempts 2 -Read {
        $readState.count++
        throw [System.Windows.Automation.ElementNotAvailableException]::new()
    }
} 'expired element'
Assert ($readState.count -eq 3) 'Persistent stale read exceeded its retry limit'
$passed++
$readState.count=0
Expect-Error {
    Invoke-KahuaFlagReadRecovery -DelayMilliseconds 0 -RecoveryAttempts 0 -Read {
        $readState.count++
        throw [System.Windows.Automation.ElementNotAvailableException]::new()
    }
} 'expired element'
Assert ($readState.count -eq 1) 'RecoveryAttempts 0 still retried'
$passed++
foreach ($semanticFailure in @('Wrong domain','Duplicate exact name','Unable to sync','Unsaved Changes Alert','expired element')) {
    $readState.count=0
    Expect-Error {
        Invoke-KahuaFlagReadRecovery -DelayMilliseconds 0 -Read { $readState.count++; throw $semanticFailure }
    } ([regex]::Escape($semanticFailure))
    Assert ($readState.count -eq 1) "A semantic error was retried: $semanticFailure"
    $passed++
}
Assert (-not (Test-KahuaFlagTransientFailure ([TimeoutException]::new('Unclassified timeout')))) 'Generic timeout was considered recoverable'
$wrapped=[Exception]::new('Wrapped provider failure',[System.Windows.Automation.ElementNotAvailableException]::new())
Assert (Test-KahuaFlagTransientFailure $wrapped) 'Wrapped stale provider error was missed'
$passed++

$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'KahuaSyncFlagDesktop.psm1'),[ref]$tokens,[ref]$errors)
Assert (-not $errors.Count) 'Desktop adapter does not parse'
$source=@('Set-KahuaProjectFlag','Confirm-KahuaFlagAfterSave') | ForEach-Object {
    $name=$_
    $ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true).Extent.Text
}
$harness=New-Module -Name KahuaFlagRecoveryFixture -ArgumentList ($source -join "`n") -ScriptBlock {
    param($Source)
    . ([scriptblock]::Create($Source))
    function Reset-RecoveryFixture([string]$Fault='') {
        $script:fault=$Fault; $script:saves=0; $script:closes=0; $script:waits=0; $script:reads=0; $script:guards=0
        $script:persisted='Off'; $script:open=$true
        $script:save=[pscustomobject]@{Current=[pscustomobject]@{IsEnabled=$false}}
        $script:toggle=[pscustomobject]@{Current=[pscustomobject]@{ToggleState='Off'};Count=0;Save=$script:save}
        $script:toggle | Add-Member ScriptMethod Toggle {
            $this.Count++
            $this.Current.ToggleState=if ($this.Current.ToggleState -eq 'On') { 'Off' } else { 'On' }
            $this.Save.Current.IsEnabled=$true
        }
    }
    function Open-KahuaFlagSettings { 'running' }
    function Get-KahuaRootFlagToggle { $script:toggle }
    function Find-KahuaControl {
        param($Id,$Name,$Type,[switch]$All)
        if ($Id -eq 'CloseModal') {
            if ($script:open) { $script:save }
            return
        }
        return $script:save
    }
    function Wait-KahuaControl { $script:save }
    function Invoke-KahuaControl {
        $script:saves++
        if ($script:fault -eq 'save-not-sent') { throw [System.Windows.Automation.ElementNotAvailableException]::new() }
        $script:persisted=$script:toggle.Current.ToggleState
        $script:save.Current.IsEnabled=$false
        if ($script:fault -eq 'save-accepted-then-stale') { throw [System.Windows.Automation.ElementNotAvailableException]::new() }
    }
    function Wait-KahuaFlagSave {
        $script:waits++
        if ($script:save.Current.IsEnabled) { throw 'Unsaved settings remain; no additional Save allowed' }
        if ($script:waits -eq 1 -or $script:fault -eq 'never-settles') {
            $failure=[TimeoutException]::new('Fixture Save did not settle')
            $failure.Data['KahuaFlagReadTimeout']=$true
            throw $failure
        }
    }
    function Assert-KahuaFlagProject {
        $script:guards++
        if ($script:fault -eq 'identity') { throw 'Wrong project or domain' }
    }
    function Assert-KahuaFlagNoSyncError {
        if ($script:fault -eq 'sync-error') { throw 'Unable to sync: same name already synced' }
    }
    function Assert-KahuaFlagNoUnsavedWarning {
        if ($script:fault -eq 'warning') { throw 'Unsaved Changes Alert' }
    }
    function Read-KahuaFlagSettingsContext {
        if ($script:fault -eq 'unrecognized-dialog') { throw 'Unrecognized settings view' }
        'running'
    }
    function Close-KahuaFlagSettings { $script:closes++; $script:open=$false }
    function Read-KahuaFlagSelection {
        $script:reads++
        if ($script:fault -eq 'read-never-ready' -or ($script:fault -eq 'read-once-stale' -and $script:reads -eq 1)) {
            throw [System.Windows.Automation.ElementNotAvailableException]::new()
        }
        [pscustomobject]@{selection=$(if ($script:fault -eq 'wrong-readback') { 'Off' } else { $script:persisted });
            account_sync_state='running';sync_root='fixture-root'}
    }
    function Get-RecoveryFixture {
        [pscustomobject]@{saves=$script:saves;toggles=$script:toggle.Count;waits=$script:waits;reads=$script:reads;closes=$script:closes;guards=$script:guards}
    }
    function Start-Sleep { } # Keep fixtures fast; production has a bounded delay.
    Export-ModuleMember -Function Set-KahuaProjectFlag,Reset-RecoveryFixture,Get-RecoveryFixture
}
Import-Module $harness -Force
$identity=[pscustomobject]@{environment='PRODUCTION';domain='Onindus';project_name='One';project_number='0001';display='0001 - One'}
try {
    foreach ($fault in @('','read-once-stale','save-accepted-then-stale')) {
        Reset-RecoveryFixture $fault
        $log=New-KahuaFlagLogContext Execute
        $receipt=Set-KahuaProjectFlag -Identity $identity -Enabled $true -LogContext $log
        $state=Get-RecoveryFixture
        Assert ($receipt.reconciled -and $receipt.reopened -and $receipt.selection -ceq 'On') 'Recovery did not produce an independent saved readback'
        Assert ($state.saves -eq 1 -and $state.toggles -eq 1 -and $state.guards -ge 1) 'Recovery repeated a mutation or omitted identity verification'
        Assert ($log.Recoveries.Count -ge 1 -and $log.Recoveries.Count -le 2) 'Save reconciliation evidence missing or retry limit exceeded'
        $passed++
    }
    foreach ($case in @(
        @('never-settles','did not settle'),
        @('read-never-ready','expired element'),
        @('save-not-sent','Unsaved settings'),
        @('identity','Wrong project'),
        @('sync-error','same name already synced'),
        @('warning','Unsaved Changes Alert'),
        @('unrecognized-dialog','Unrecognized settings'),
        @('wrong-readback','did not confirm')
    )) {
        Reset-RecoveryFixture $case[0]
        $log=New-KahuaFlagLogContext Execute
        Expect-Error { Set-KahuaProjectFlag -Identity $identity -Enabled $true -LogContext $log } $case[1]
        $state=Get-RecoveryFixture
        Assert ($state.saves -eq 1 -and $state.toggles -eq 1) 'Failed recovery repeated a mutation'
        Assert ($log.Recoveries.Count -le 2) 'Failed recovery exceeded its limit'
        if ($case[0] -in @('identity','sync-error','warning','unrecognized-dialog','save-not-sent')) {
            Assert ($state.closes -eq 0) 'Recovery closed an unsafe or unrecognized dialog'
        }
        $passed++
    }
    Reset-RecoveryFixture
    $log=New-KahuaFlagLogContext Execute
    Expect-Error { Set-KahuaProjectFlag -Identity $identity -Enabled $true -LogContext $log -RecoveryAttempts 0 } 'did not settle'
    Assert ($log.Recoveries.Count -eq 0 -and (Get-RecoveryFixture).saves -eq 1) 'RecoveryAttempts 0 did not stop after the original Save failure'
    $passed++
} finally { Remove-Module KahuaFlagRecoveryFixture -ErrorAction SilentlyContinue }

# A stale snapshot must not re-run the navigation that opened settings.
$selectionSource=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Read-KahuaFlagSelection'},$true).Extent.Text
$readHarness=New-Module -Name KahuaFlagReadFixture -ArgumentList $selectionSource -ScriptBlock {
    param($Source)
    . ([scriptblock]::Create($Source))
    function Reset-ReadFixture { $script:opens=0; $script:closes=0; $script:reads=0 }
    function Open-KahuaFlagSettings { $script:opens++; 'running' }
    function Close-KahuaFlagSettings { $script:closes++ }
    function Read-KahuaFlagSettingsContext { 'running' }
    function Find-KahuaControl {
        param($Name,$Type,[switch]$All)
        if ($Name -eq 'Save') {
            $script:reads++
            if ($script:reads -eq 1) { throw [System.Windows.Automation.ElementNotAvailableException]::new() }
            return [pscustomobject]@{Current=[pscustomobject]@{IsEnabled=$false}}
        }
        [pscustomobject]@{Current=[pscustomobject]@{Name='C:\Fixture'}}
    }
    function Get-KahuaRootFlagToggle { [pscustomobject]@{Current=[pscustomobject]@{ToggleState='On'}} }
    function Join-Path { param($Path,$ChildPath) "$Path\$ChildPath" }
    function Get-ReadFixture { [pscustomobject]@{opens=$script:opens;closes=$script:closes;reads=$script:reads} }
    Export-ModuleMember -Function Read-KahuaFlagSelection,Reset-ReadFixture,Get-ReadFixture
}
Import-Module $readHarness -Force
try {
    Reset-ReadFixture
    $log=New-KahuaFlagLogContext Check
    $snapshot=Read-KahuaFlagSelection -Identity $identity -LogContext $log
    $state=Get-ReadFixture
    Assert ($snapshot.selection -eq 'On' -and $state.opens -eq 1 -and $state.closes -eq 1 -and $state.reads -eq 2) 'A stale snapshot repeated settings navigation'
    $passed++
    Reset-ReadFixture
    Expect-Error { Read-KahuaFlagSelection -Identity $identity -RecoveryAttempts 0 } 'expired element'
    Assert ((Get-ReadFixture).opens -eq 1 -and (Get-ReadFixture).closes -eq 0) 'Failed snapshot closed settings or retried navigation'
    $passed++
} finally { Remove-Module KahuaFlagReadFixture -ErrorAction SilentlyContinue }

# Reconciliation verifies the header outside the open settings modal.
$projectSource=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Assert-KahuaFlagProject'},$true).Extent.Text
$projectHarness=New-Module -Name KahuaFlagProjectFixture -ArgumentList $projectSource -ScriptBlock {
    param($Source)
    . ([scriptblock]::Create($Source))
    function Confirm-KahuaFlagDomain {
        param($Environment,$Domain)
        if ($Environment -cne 'PRODUCTION' -or $Domain -cne 'Onindus') { throw 'Wrong domain' }
    }
    function Get-KahuaWindow { 'verified-main-window' }
    function Find-KahuaControl {
        param($Id,$Name,$Type,$Root)
        if ($Root -cne 'verified-main-window' -or $Name -cne 'Current Project 0001 - One') { throw 'Header was not verified outside the settings modal' }
        'verified-header'
    }
    Export-ModuleMember -Function Assert-KahuaFlagProject
}
Import-Module $projectHarness -Force
try { Assert-KahuaFlagProject -Identity $identity; $passed++ }
finally { Remove-Module KahuaFlagProjectFixture -ErrorAction SilentlyContinue }
Write-Output "$passed bounded recovery tests passed. No Kahua access or repeated Save."
