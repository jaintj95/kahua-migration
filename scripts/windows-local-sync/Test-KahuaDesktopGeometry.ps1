#requires -Version 5.1
# Exercise the real rectangle reader without loading UI Automation or Kahua.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$tokens=$null; $parseErrors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'KahuaDesktop.psm1'),[ref]$tokens,[ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
$function=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-KahuaControlRectangle'},$true)
. ([scriptblock]::Create($function.Extent.Text))
$retryFunction=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Invoke-KahuaReadRetry'},$true)
. ([scriptblock]::Create($retryFunction.Extent.Text))
$nameFunction=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-KahuaControlName'},$true)
. ([scriptblock]::Create($nameFunction.Extent.Text))
$script:KahuaBoundingRectangleProperty='fixture-rectangle-property'
$script:KahuaNameProperty='fixture-name-property'
function Assert($Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
function Expect-Error([scriptblock]$Call,[string]$Pattern) {
    $message=''
    try { & $Call | Out-Null } catch { $message=$_.Exception.Message }
    Assert ($message -match $Pattern) "Expected '$Pattern', got '$message'"
}
function New-Control([object[]]$Rectangles) {
    $control=[pscustomobject]@{rectangles=$Rectangles;reads=0}
    $control | Add-Member ScriptMethod GetCurrentPropertyValue {
        param($Property)
        if ($Property -cne 'fixture-rectangle-property') { throw 'Wrong UI Automation property requested' }
        $index=[Math]::Min($this.reads,$this.rectangles.Count-1)
        $this.reads++
        return $this.rectangles[$index]
    }
    return $control
}
$rect=[pscustomobject]@{X=10;Y=20;Width=30;Height=40;Left=10;Top=20;Right=40;Bottom=60;IsEmpty=$false}
$passed=0
$control=New-Control @($rect)
Assert ([object]::ReferenceEquals((Get-KahuaControlRectangle $control),$rect) -and $control.reads -eq 1) 'Valid coordinates were changed or reread'
$passed++
$control=New-Control @($null,$null,$rect)
Assert ((Get-KahuaControlRectangle $control).Width -eq 30 -and $control.reads -eq 3) 'A temporary missing rectangle did not recover'
$passed++
$control=New-Control @([pscustomobject]@{Height=40},$rect)
Assert ((Get-KahuaControlRectangle $control).IsEmpty -eq $false -and $control.reads -eq 2) 'An incomplete rectangle did not recover'
$passed++
$control=New-Control @([pscustomobject]@{Height=40})
Expect-Error { Get-KahuaControlRectangle $control -TimeoutMilliseconds 0 } 'geometry is unavailable.*missing.*Width'
Assert ($control.reads -eq 1) 'Permanent missing geometry did not respect the deadline'
$passed++
$control=New-Control @($null)
Expect-Error { Get-KahuaControlRectangle $control -TimeoutMilliseconds 0 } 'geometry is unavailable.*no rectangle'
$passed++
$empty=[pscustomobject]@{X=[double]::PositiveInfinity;Y=[double]::PositiveInfinity;Width=[double]::NegativeInfinity;Height=[double]::NegativeInfinity;Left=0;Top=0;Right=0;Bottom=0;IsEmpty=$true}
$control=New-Control @($empty)
Assert ((Get-KahuaControlRectangle $control).IsEmpty) 'An empty rectangle was replaced by invented coordinates'
$passed++
$broken=[pscustomobject]@{reads=0}
$broken | Add-Member ScriptMethod GetCurrentPropertyValue { param($Property) $this.reads++; throw 'Unexpected provider failure' }
Expect-Error { Get-KahuaControlRectangle $broken } 'Unexpected provider failure'
Assert ($broken.reads -eq 1) 'An unrelated provider error was silently retried'
$passed++

if (-not ('KahuaTest.ElementNotAvailableException' -as [type])) {
    Add-Type 'namespace KahuaTest { public class ElementNotAvailableException : System.Exception { public ElementNotAvailableException() : base("Expired fixture element") {} } }'
}
$expired=[pscustomobject]@{reads=0}
$expired | Add-Member ScriptMethod GetCurrentPropertyValue { param($Property) $this.reads++; throw [KahuaTest.ElementNotAvailableException]::new() }
Expect-Error { Get-KahuaControlRectangle $expired } 'Expired fixture element'
Assert ($expired.reads -eq 1) 'An expired control was reread instead of returning its original provider error'
$passed++
$script:readAttempts=0
$values=@(Invoke-KahuaReadRetry -Read {
    $script:readAttempts++
    if ($script:readAttempts -eq 1) { 'discard this partial snapshot'; throw [KahuaTest.ElementNotAvailableException]::new() }
    'fresh snapshot'
})
Assert ($script:readAttempts -eq 2 -and $values.Count -eq 1 -and $values[0] -ceq 'fresh snapshot') 'Retry leaked a partial snapshot or did not recover'
$passed++
$script:readAttempts=0
Expect-Error { Invoke-KahuaReadRetry -TimeoutMilliseconds 0 -Read { $script:readAttempts++; throw [KahuaTest.ElementNotAvailableException]::new() } } 'Expired fixture element'
Assert ($script:readAttempts -eq 1) 'Permanent stale read did not honor its deadline'
$passed++
$script:readAttempts=0
Expect-Error { Invoke-KahuaReadRetry -Read { $script:readAttempts++; throw 'Wrong domain' } } 'Wrong domain'
Assert ($script:readAttempts -eq 1) 'A domain or identity failure was retried'
$passed++
$script:readAttempts=0
$ready=Invoke-KahuaReadRetry -RequireResult -Read { $script:readAttempts++; if ($script:readAttempts -gt 1) { 'ready' } }
Assert ($ready -ceq 'ready' -and $script:readAttempts -eq 2) 'A temporarily empty required snapshot was accepted'
$passed++
Expect-Error { Invoke-KahuaReadRetry -RequireResult -TimeoutMilliseconds 0 -UnavailableMessage 'Required view missing' -Read {} } 'Required view missing'
$passed++

# Exercise the actual breadcrumb reader: a replaced descendant requires a
# fresh verified window and a complete new snapshot, not cached coordinates.
$flagAst=[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'KahuaSyncFlagDesktop.psm1'),[ref]$tokens,[ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
foreach ($name in @('Get-KahuaFlagHomeButton','Open-KahuaFlagHome','Get-KahuaFlagBreadcrumbDomain')) {
    $functionAst=$flagAst.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true)
    . ([scriptblock]::Create($functionAst.Extent.Text))
}
$script:windowReads=0
$script:expireAt='enumeration'
$script:useEarthButton=$false
$script:onHome=$false
$script:homeClicks=0
function Get-KahuaWindow { $script:windowReads++; return [pscustomobject]@{generation=$script:windowReads} }
function Get-KahuaActiveRoot { Get-KahuaWindow }
function Find-KahuaControl {
    param($Id,$Name,$Type,$Root,[switch]$All)
    if ($Id -eq 'HomeButton') {
        if (-not $script:useEarthButton -and $Name -ceq 'Home' -and $Type -eq 'Button') { return [pscustomobject]@{kind='home'} }
        return
    }
    if ($Id -eq 'EarthButton') {
        if ($script:useEarthButton -and $Name -ceq 'Navigate to Kahua Home' -and $Type -eq 'Button') { return [pscustomobject]@{kind='home'} }
        throw 'Wrong globe selector.'
    }
    if ($Id -eq 'ShellContentFrame') {
        if (-not $Name -or ($Name -ceq 'Home Dashboard Main Content' -and $script:onHome)) {
            return [pscustomobject]@{kind='content';Current=[pscustomobject]@{IsEnabled=$true}}
        }
        return
    }
    if ($Root.generation -eq 1 -and $All -and $script:expireAt -eq 'enumeration') { throw [KahuaTest.ElementNotAvailableException]::new() }
    if ($Root.generation -eq 1 -and $All -and $script:expireAt -eq 'empty') { return }
    $crumb=[pscustomobject]@{kind='crumb';generation=$Root.generation}
    $crumb | Add-Member ScriptMethod GetCurrentPropertyValue {
        param($Property)
        if ($Property -cne 'fixture-name-property') { throw 'Wrong name property' }
        if ($this.generation -eq 1 -and $script:expireAt -eq 'name') { throw [KahuaTest.ElementNotAvailableException]::new() }
        return 'Open project descendants for Onindus'
    }
    return $crumb
}
function Invoke-KahuaControl {
    param($Control)
    if ($Control.kind -ne 'home' -or $script:onHome) { throw 'Unexpected Home click.' }
    $script:homeClicks++
    $script:onHome=$true
}
function Wait-KahuaControl {
    param($Id,$Name,$Type)
    if ($Id -ne 'ShellContentFrame' -or $Name -cne 'Home Dashboard Main Content' -or $Type -ne 'Group' -or -not $script:onHome) {
        throw 'Wrong Home destination.'
    }
}
function Get-KahuaControlRectangle {
    param($Control)
    switch ($Control.kind) {
        'home' { [pscustomobject]@{Bottom=50} }
        'content' { [pscustomobject]@{Top=100} }
        'crumb' { [pscustomobject]@{Top=60;Bottom=90;Left=20} }
    }
}
Assert ((Get-KahuaFlagBreadcrumbDomain) -ceq 'Onindus' -and $script:windowReads -eq 2) 'Breadcrumb recovery reused the expired window snapshot'
$passed++
$script:windowReads=0; $script:expireAt='name'
Assert ((Get-KahuaFlagBreadcrumbDomain) -ceq 'Onindus' -and $script:windowReads -eq 2) 'Expired name read did not cause a fresh breadcrumb snapshot'
$passed++
$script:windowReads=0; $script:expireAt='empty'
Assert ((Get-KahuaFlagBreadcrumbDomain) -ceq 'Onindus' -and $script:windowReads -eq 2) 'A temporarily empty breadcrumb was mistaken for a disabled setting'
$passed++
$script:useEarthButton=$true; $script:expireAt=''
Assert ((Get-KahuaFlagBreadcrumbDomain) -ceq 'Onindus') 'The globe did not anchor the domain breadcrumb'
$passed++
Open-KahuaFlagHome
Assert ($script:onHome -and $script:homeClicks -eq 1) 'The globe did not open Kahua Home'
$passed++
Open-KahuaFlagHome
Assert ($script:homeClicks -eq 1) 'The runner clicked the globe while already on Home'
$passed++
Write-Output "PASS: $passed desktop geometry/read scenarios (simulated controls)."
