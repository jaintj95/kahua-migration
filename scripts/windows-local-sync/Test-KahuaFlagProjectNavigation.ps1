#requires -Version 5.1
# Real project-open wait and FindAll reader, simulated UI provider only.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
Import-Module (Join-Path $PSScriptRoot 'KahuaSyncFlags.psm1') -Force
if ($env:OS -eq 'Windows_NT') {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
} else {
    # Minimal selector types let macOS exercise the real Find-KahuaControl.
    Add-Type @'
namespace System.Windows.Automation {
    public enum TreeScope { Descendants }
    public class Condition { }
    public class PropertyCondition : Condition {
        public object Property, Value;
        public PropertyCondition(object property, object value) {
            Property=property;
            Value=value is ControlType ? (object)((ControlType)value).Id : value;
        }
    }
    public class AndCondition : Condition {
        private Condition[] conditions;
        public AndCondition(params Condition[] values) { conditions=values; }
        public Condition[] GetConditions() { return conditions; }
    }
    public class ControlType {
        public readonly int Id;
        private ControlType(int id) { Id=id; }
        public static readonly ControlType Button=new ControlType(50000), Edit=new ControlType(50004);
    }
    public class AutomationElement {
        public static readonly string AutomationIdProperty="id", NameProperty="name", ControlTypeProperty="type";
    }
    public class ElementNotAvailableException : System.Exception {
        public ElementNotAvailableException(string message) : base(message) { }
    }
}
'@
}
foreach ($entry in @(
    @('KahuaDesktop.psm1',@('Find-KahuaControl','Select-KahuaUniqueElement')),
    @('KahuaSyncFlagDesktop.psm1',@('Wait-KahuaFlagProjectOpen'))
)) {
    $tokens=$null; $errors=$null
    $ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot $entry[0]),[ref]$tokens,[ref]$errors)
    if ($errors.Count) { throw ($errors | Out-String) }
    foreach ($name in $entry[1]) {
        $definition=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true)
        . ([scriptblock]::Create($definition.Extent.Text))
    }
}
function Assert($Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
function Expect-Error([scriptblock]$Call,[string]$Pattern) {
    $failure=$null
    try { & $Call | Out-Null } catch { $failure=$_ }
    Assert ($null -ne $failure -and $failure.Exception.Message -match $Pattern) "Expected '$Pattern', got '$failure'"
}
function Reset-Fixture([string]$Fault='') {
    $script:fixture=@{fault=$Fault;roots=0;headerReads=0;pickerReads=0;domainReads=0}
}
function Get-KahuaActiveRoot {
    $script:fixture.roots++
    if ($script:fixture.fault -eq 'root-stale' -and $script:fixture.roots -eq 1) {
        throw [System.Windows.Automation.ElementNotAvailableException]::new('Unrecognized error.')
    }
    $root=[pscustomobject]@{state=$script:fixture;generation=$script:fixture.roots}
    $root | Add-Member ScriptMethod FindAll {
        param($Scope,$Condition)
        if ($Scope -ne [Windows.Automation.TreeScope]::Descendants) { throw 'Wrong lookup scope' }
        $conditions=if ($Condition -is [Windows.Automation.AndCondition]) { $Condition.GetConditions() } else { @($Condition) }
        $id=''; $name=''; $type=$null
        foreach ($part in $conditions) {
            if ($part.Property -eq [Windows.Automation.AutomationElement]::AutomationIdProperty) { $id=$part.Value }
            if ($part.Property -eq [Windows.Automation.AutomationElement]::NameProperty) { $name=$part.Value }
            if ($part.Property -eq [Windows.Automation.AutomationElement]::ControlTypeProperty) { $type=$part.Value }
        }
        $fault=$this.state.fault
        if ($fault -eq 'deadline-stale') { Start-Sleep -Milliseconds 1100 }
        if ($fault -in @('always-stale','deadline-stale') -or
            ($this.generation -eq 1 -and (($fault -eq 'header-stale' -and $id -eq 'Shell_PartitionSelector') -or
            ($fault -eq 'picker-stale' -and $id -eq 'SearchBox')))) {
            # ScriptMethod invocation wraps this in MethodInvocationException,
            # reproducing the reported FindAll/ElementNotAvailable failure.
            throw [System.Windows.Automation.ElementNotAvailableException]::new('Unrecognized error.')
        }
        if ($fault -eq 'untyped-error') { throw 'Unrecognized error.' }
        if ($fault -eq 'slow-success') { Start-Sleep -Milliseconds 1100 }
        if ($id -eq 'Shell_PartitionSelector') {
            $this.state.headerReads++
            # Windows PropertyCondition.Value stores a ControlType's numeric Id.
            # Match that contract in both the real UIA types and the macOS stub.
            if ($type -ne [Windows.Automation.ControlType]::Button.Id -or $name -cne 'Current Project 0002 - Exact project') {
                throw 'The project identity selector was weakened'
            }
            if ($fault -in @('missing-header','wrong-header') -or ($fault -eq 'delayed-header' -and $this.generation -eq 1)) { return }
            $control=New-Header -Enabled:($fault -ne 'disabled-header' -or $this.generation -gt 1) -Runtime 1
            if ($fault -eq 'duplicate-object') { return @($control,$control) }
            if ($fault -eq 'ambiguous-header') { return @($control,(New-Header -Enabled $true -Runtime 2)) }
            return $control
        }
        if ($id -eq 'SearchBox') {
            $this.state.pickerReads++
            if ($type -ne [Windows.Automation.ControlType]::Edit.Id) { throw 'Wrong project picker selector' }
            if ($fault -eq 'open-picker' -or ($fault -eq 'delayed-picker' -and $this.generation -eq 1)) {
                return (New-Header -Enabled $true -Runtime 3)
            }
            if ($fault -eq 'ambiguous-picker') { return @((New-Header $true 3),(New-Header $true 4)) }
            return
        }
        throw "Unexpected selector: $id"
    }
    return $root
}
function New-Header([bool]$Enabled,[int]$Runtime) {
    $control=[pscustomobject]@{Current=[pscustomobject]@{IsEnabled=$Enabled;IsOffscreen=$false};runtime=$Runtime}
    $control | Add-Member ScriptMethod GetRuntimeId { return @($this.runtime) }
    return $control
}
function Get-KahuaControlRectangle { param($Control) [pscustomobject]@{Width=100;Height=30} }
function Confirm-KahuaFlagDomain {
    param($Environment,$Domain)
    $script:fixture.domainReads++
    Assert ($Environment -ceq 'PRODUCTION' -and $Domain -ceq 'Expected domain') 'Domain/environment guard lost its requested identity'
    if ($script:fixture.fault -eq 'wrong-domain') { throw 'Wrong domain' }
    if ($script:fixture.fault -eq 'wrong-environment') { throw 'Wrong environment' }
}
function Assert-KahuaFlagNoSyncError {
    if ($script:fixture.fault -eq 'sync-warning') { throw 'Kahua reports Unable to sync' }
}
function Invoke-KahuaControl { throw 'A read recovery must not click, toggle or Save' }
$identity=[pscustomobject]@{environment='PRODUCTION';domain='Expected domain';display='0002 - Exact project'}
$passed=0
foreach ($fault in @('','header-stale','picker-stale','root-stale','delayed-header','delayed-picker','disabled-header','duplicate-object')) {
    Reset-Fixture $fault
    $log=New-KahuaFlagLogContext Check
    $output=@(Wait-KahuaFlagProjectOpen -Identity $identity -LogContext $log -TimeoutSeconds 5)
    Assert ($output.Count -eq 0 -and $script:fixture.domainReads -eq 1) 'Partial UI data escaped or the final identity was not checked'
    if ($fault -like '*-stale') {
        Assert ($script:fixture.roots -eq 2 -and $log.Recoveries.Count -eq 1) "Stale $fault did not reacquire the root with logged recovery"
    }
    if ($fault -eq 'picker-stale') { Assert ($script:fixture.headerReads -eq 2) 'Picker recovery reused a previous header result' }
    if ($fault -in @('delayed-header','delayed-picker','disabled-header')) {
        Assert ($script:fixture.roots -eq 2) 'A loading or disabled view was accepted without another fresh read'
    }
    $passed++
}
foreach ($attempts in @(0,2,3)) {
    Reset-Fixture 'always-stale'
    $log=New-KahuaFlagLogContext Check
    Expect-Error { Wait-KahuaFlagProjectOpen -Identity $identity -LogContext $log -RecoveryAttempts $attempts } 'Exception calling "FindAll".*Unrecognized error'
    Assert ($script:fixture.roots -eq $attempts+1 -and $log.Recoveries.Count -eq $attempts) 'The configured recovery limit was ignored'
    Assert ($script:fixture.domainReads -eq 0) 'A failed snapshot was accepted'
    $passed++
}
foreach ($case in @(
    @('untyped-error','Unrecognized error'),@('ambiguous-header','Ambiguous selected-project'),
    @('ambiguous-picker','Ambiguous project search'),@('wrong-domain','Wrong domain'),
    @('wrong-environment','Wrong environment'),@('sync-warning','Unable to sync')
)) {
    Reset-Fixture $case[0]
    $log=New-KahuaFlagLogContext Check
    Expect-Error { Wait-KahuaFlagProjectOpen -Identity $identity -LogContext $log } $case[1]
    Assert ($log.Recoveries.Count -eq 0 -and $script:fixture.roots -le 1) "Unsafe failure was retried: $($case[0])"
    $passed++
}
foreach ($fault in @('missing-header','wrong-header','open-picker','deadline-stale','slow-success')) {
    Reset-Fixture $fault
    $log=New-KahuaFlagLogContext Check
    Expect-Error { Wait-KahuaFlagProjectOpen -Identity $identity -LogContext $log -TimeoutSeconds 1 } 'Timed out verifying opened project'
    Assert ($script:fixture.domainReads -eq 0) 'Unready or overdue navigation was accepted'
    if ($fault -eq 'deadline-stale') { Assert ($script:fixture.roots -eq 1) 'Recovery reset the navigation deadline' }
    $passed++
}
Write-Host "PASS: $passed project-open recovery scenarios (real FindAll reader, simulated provider; no Kahua actions)."
