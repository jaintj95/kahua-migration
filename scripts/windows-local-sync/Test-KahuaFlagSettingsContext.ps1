#requires -Version 5.1
# Real settings-open/context/reconciliation functions with simulated controls.
# Run in a fresh PowerShell process; no UI Automation or Kahua is accessed.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
Import-Module (Join-Path $PSScriptRoot 'KahuaSyncFlags.psm1') -Force
if ('System.Windows.Automation.TreeWalker' -as [type]) {
    throw 'Run this fixture in a fresh PowerShell process without UI Automation loaded.'
}
Add-Type @'
namespace System.Windows.Automation {
    public class ControlType { public static readonly string Window="Window"; }
    public class TreeWalker { public static object RawViewWalker; }
    public class ElementNotAvailableException : System.Exception {
        public ElementNotAvailableException(string message) : base(message) { }
    }
}
'@
$walker=[pscustomobject]@{}
$walker | Add-Member ScriptMethod GetParent { param($Control) return $Control.parent }
[Windows.Automation.TreeWalker]::RawViewWalker=$walker
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'KahuaSyncFlagDesktop.psm1'),[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
foreach ($name in @('Open-KahuaFlagSettings','Read-KahuaFlagSettingsContext','Confirm-KahuaFlagAfterSave')) {
    $definition=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true)
    . ([scriptblock]::Create($definition.Extent.Text))
}
function Assert($Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
function Expect-Error([scriptblock]$Call,[string]$Pattern) {
    $failure=$null
    try { & $Call | Out-Null } catch { $failure=$_ }
    Assert ($null -ne $failure -and $failure.Exception.Message -match $Pattern) "Expected '$Pattern', got '$failure'"
}
function New-Control([string]$Role,$Parent=$null) {
    [pscustomobject]@{role=$Role;parent=$Parent;Current=[pscustomobject]@{ControlType='Group';IsEnabled=$true}}
}
function Reset-Fixture([string]$Fault='',[string]$View='project') {
    $script:fixture=@{fault=$Fault;view=$View;navigation=@();modalReads=0;guards=0;folderReads=0;closes=0;readbacks=0;waits=0;saveChecks=0;cancelChecks=0}
    $script:window=New-Control 'window'
    $script:window.Current.ControlType=[Windows.Automation.ControlType]::Window
    $script:modal=New-Control 'modal' $script:window
    $script:project=New-Control 'project' $script:modal
    $script:change=New-Control 'change' $script:project
}
function Find-KahuaControl {
    param($Id,$Name,$Type,$Root,[switch]$All)
    $matches=@()
    switch ($Id) {
        'Shell_Settings' {
            Assert ($script:fixture.view -eq 'project' -and $Type -eq 'Button') 'Account drawer opened from an unexpected view'
            $matches=@(New-Control 'account')
        }
        'TitleText' {
            Assert ($Name -ceq 'My settings' -and $Type -eq 'Text') 'Unexpected menu selector'
            # This entry belongs to the Account drawer; it disappears once clicked.
            if ($script:fixture.view -eq 'account') { $matches=@(New-Control 'settings') }
        }
        'ButtonText' {
            Assert ($Name -ceq 'Local Files' -and $Type -eq 'Text' -and $script:fixture.view -eq 'settings') 'Unexpected Local Files navigation'
            $matches=@(New-Control 'local-files')
        }
        'CloseModal' {
            Assert ($Type -eq 'Button') 'Wrong modal selector'
            if ($script:fixture.view -eq 'local-files') {
                $script:fixture.modalReads++
                if ($script:fixture.fault -eq 'always-stale' -or
                    ($script:fixture.fault -eq 'once-stale' -and $script:fixture.modalReads -eq 1)) {
                    throw [Windows.Automation.ElementNotAvailableException]::new('Settings fixture expired element')
                }
                if ($script:fixture.fault -ne 'missing-modal') { $matches=@(New-Control 'close') }
                if ($script:fixture.fault -eq 'duplicate-modal') { $matches+=New-Control 'extra-close' }
            }
        }
        default {
            if ($Name -ceq 'My settings') {
                Assert ($Type -eq 'Button' -and $All -and $script:fixture.view -eq 'account') 'Wrong Account drawer button selector'
                if ($script:fixture.fault -ne 'legacy-menu') { $matches=@(New-Control 'settings-button') }
                if ($script:fixture.fault -eq 'duplicate-menu') { $matches+=New-Control 'settings-button' }
            } elseif ($Name -in @('Save','Cancel')) {
                Assert ($script:fixture.view -eq 'local-files') 'Read occurred outside Local Files'
                Assert ($Type -eq 'Button') 'Wrong settings button type'
                $key=if ($Name -eq 'Save') { 'saveChecks' } else { 'cancelChecks' }
                $script:fixture[$key]++
                if ($script:fixture.fault -ne ('missing-'+$Name.ToLowerInvariant())) { $matches=@(New-Control $Name) }
            } elseif ($Name -eq 'Change') {
                Assert ($script:fixture.view -eq 'local-files') 'Read occurred outside Local Files'
                Assert ($Type -eq 'Button') 'Wrong project selector type'
                $matches=@($script:change)
            } else {
                Assert ($script:fixture.view -eq 'local-files') 'Read occurred outside Local Files'
                Assert ($Name -ceq '0002 - Exact project' -and $Type -eq 'Text' -and $Root -and $All) 'The requested project identity was lost'
                if ([object]::ReferenceEquals($Root,$script:project) -and $script:fixture.fault -ne 'wrong-local-project') {
                    $matches=@(New-Control 'exact-project')
                }
            }
        }
    }
    if ($All) { return $matches }
    if ($matches.Count -ne 1) { throw "Expected one control id='$Id' name='$Name' type='$Type'; found $($matches.Count)." }
    return $matches[0]
}
function Wait-KahuaControl {
    param($Id,$Name,$Type)
    Find-KahuaControl -Id $Id -Name $Name -Type $Type
}
function Invoke-KahuaControl {
    param($Control,[switch]$Click)
    $role=$Control.role
    if ($role -eq 'settings-button') {
        Assert (-not $Click) 'The My settings button must use Invoke instead of a coordinate click'
        $role='settings'
    } elseif ($role -eq 'settings') {
        Assert ($Click -and $script:fixture.fault -eq 'legacy-menu') 'Only the legacy text control should use a coordinate click'
    }
    if ($role -notin @('account','settings','local-files')) { throw 'Context verification must never toggle, Save, cancel, close or dismiss a dialog' }
    $script:fixture.navigation+=$role
    $script:fixture.view=$role
}
function Assert-KahuaFlagProject {
    param($Identity)
    $script:fixture.guards++
    Assert ($Identity.environment -ceq 'PRODUCTION' -and $Identity.domain -ceq 'Expected domain' -and $Identity.display -ceq '0002 - Exact project') 'Requested identity was not retained'
    if ($script:fixture.fault -in @('wrong-environment','wrong-domain','wrong-header')) { throw $script:fixture.fault }
}
function Assert-KahuaFlagNoSyncError {
    if ($script:fixture.fault -eq 'sync-warning') { throw 'Unable to sync: same name already synced' }
}
function Assert-KahuaFlagNoUnsavedWarning {
    if ($script:fixture.fault -eq 'unsaved-warning') { throw 'Unsaved Changes Alert' }
}
function Wait-KahuaLocalFilesState {
    Assert ($script:fixture.view -eq 'local-files') 'Account state read outside Local Files'
    if ($script:fixture.fault -in @('paused','unconfigured')) { return $script:fixture.fault }
    return 'running'
}
function Wait-KahuaFlagFolders {
    $script:fixture.folderReads++
    if ($script:fixture.fault -eq 'missing-folders') { throw 'Timed out waiting for the Local Files Folders control.' }
}
function Wait-KahuaFlagSave {
    param($Desired,$LogContext)
    $script:fixture.waits++
    Assert ($Desired -ceq 'On') 'Reconciliation lost the requested switch'
}
function Close-KahuaFlagSettings {
    $script:fixture.closes++
    Assert ($script:fixture.view -eq 'local-files') 'Reconciliation tried to close an unrelated view'
    $script:fixture.view='project'
}
function Read-KahuaFlagSelection {
    $script:fixture.readbacks++
    [pscustomobject]@{selection='On';account_sync_state='running';sync_root='fixture-root'}
}
$identity=[pscustomobject]@{environment='PRODUCTION';domain='Expected domain';display='0002 - Exact project'}
$passed=0
Reset-Fixture
$state=Open-KahuaFlagSettings -Identity $identity
Assert ($state -ceq 'running' -and ($script:fixture.navigation -join ',') -ceq 'account,settings,local-files') 'Settings did not open once with the menu entry absent afterwards'
Assert ($script:fixture.folderReads -eq 1 -and $script:fixture.saveChecks -eq 1 -and $script:fixture.cancelChecks -eq 1) 'Local Files controls were not verified'
$passed++
Reset-Fixture 'legacy-menu'
Assert ((Open-KahuaFlagSettings -Identity $identity) -ceq 'running') 'The legacy Account drawer did not open settings'
$passed++
Reset-Fixture 'duplicate-menu'
Expect-Error { Open-KahuaFlagSettings -Identity $identity } 'Ambiguous My settings buttons'
Assert (($script:fixture.navigation -join ',') -ceq 'account') 'An ambiguous menu button was activated'
$passed++
foreach ($fault in @('','paused')) {
    Reset-Fixture $fault 'local-files'
    $state=Read-KahuaFlagSettingsContext -Identity $identity
    $expected=if ($fault) { $fault } else { 'running' }
    Assert ($state -ceq $expected -and $script:fixture.navigation.Count -eq 0 -and $script:fixture.closes -eq 0) 'An open settings read navigated or changed account status'
    $passed++
}
foreach ($case in @(
    @('wrong-environment','wrong-environment'),@('wrong-domain','wrong-domain'),@('wrong-header','wrong-header'),
    @('sync-warning','same name already synced'),@('unsaved-warning','Unsaved Changes Alert'),
    @('missing-modal',"id='CloseModal'.*found 0"),@('duplicate-modal',"id='CloseModal'.*found 2"),
    @('missing-save',"name='Save'.*found 0"),@('missing-cancel',"name='Cancel'.*found 0"),
    @('unconfigured','Enable Local Files for the account manually'),@('wrong-local-project','different project'),
    @('missing-folders','Local Files Folders control')
)) {
    Reset-Fixture $case[0] 'local-files'
    $log=New-KahuaFlagLogContext Check
    Expect-Error { Read-KahuaFlagSettingsContext -Identity $identity -LogContext $log } $case[1]
    Assert ($script:fixture.navigation.Count -eq 0 -and $script:fixture.closes -eq 0 -and $log.Recoveries.Count -eq 0) 'Unsafe context caused navigation, closure or recovery'
    $passed++
}
Reset-Fixture 'once-stale'
$log=New-KahuaFlagLogContext Check
$state=Open-KahuaFlagSettings -Identity $identity -LogContext $log
Assert ($state -ceq 'running' -and $script:fixture.modalReads -eq 2 -and $script:fixture.guards -eq 3) 'Stale settings context was not fully reacquired'
Assert ($script:fixture.navigation.Count -eq 3 -and $log.Recoveries.Count -eq 1) 'Read recovery repeated a settings click'
$passed++
foreach ($attempts in @(0,2)) {
    Reset-Fixture 'always-stale'
    $log=New-KahuaFlagLogContext Check
    Expect-Error { Open-KahuaFlagSettings -Identity $identity -LogContext $log -RecoveryAttempts $attempts } 'expired element'
    Assert ($script:fixture.modalReads -eq $attempts+1 -and $log.Recoveries.Count -eq $attempts -and $script:fixture.navigation.Count -eq 3) 'Read failure exceeded recovery limit or repeated navigation'
    $passed++
}
Reset-Fixture '' 'local-files'
$receipt=Confirm-KahuaFlagAfterSave -Identity $identity -Desired On
Assert ($receipt.selection -ceq 'On' -and $script:fixture.waits -eq 1 -and $script:fixture.closes -eq 1 -and $script:fixture.readbacks -eq 1) 'Recognized settings did not reconcile and read back'
Assert ($script:fixture.navigation.Count -eq 0) 'Reconciliation repeated a settings click or Save'
$passed++
foreach ($fault in @('wrong-local-project','missing-save','missing-folders','unsaved-warning')) {
    Reset-Fixture $fault 'local-files'
    Expect-Error { Confirm-KahuaFlagAfterSave -Identity $identity -Desired On } 'different project|found 0|Local Files Folders control|Unsaved Changes Alert'
    Assert ($script:fixture.closes -eq 0 -and $script:fixture.readbacks -eq 0 -and $script:fixture.navigation.Count -eq 0) 'Reconciliation closed an unrecognized or unsafe dialog'
    $passed++
}
Write-Host "PASS: $passed settings context scenarios (simulated controls; no Kahua actions)."
