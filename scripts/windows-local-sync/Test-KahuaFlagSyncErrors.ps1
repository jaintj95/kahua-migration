#requires -Version 5.1
# Exercise error detection and asynchronous folder loading without Kahua.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$tokens=$null; $parseErrors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'KahuaSyncFlagDesktop.psm1'),[ref]$tokens,[ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
foreach ($name in @('Assert-KahuaFlagNoSyncError','Wait-KahuaFlagFolders','Initialize-KahuaFlagDomain','Open-KahuaFlagSettings')) {
    $definition=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true)
    . ([scriptblock]::Create($definition.Extent.Text))
}
function Assert($Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
function Expect-Error([scriptblock]$Call,[string]$Pattern) {
    $message=''
    try { & $Call | Out-Null } catch { $message=$_.Exception.Message }
    Assert ($message -match $Pattern) "Expected '$Pattern', got '$message'"
}
function New-Control([string]$Name,[bool]$Enabled=$true) {
    [pscustomobject]@{Current=[pscustomobject]@{Name=$Name;IsEnabled=$Enabled}}
}
function Reset-Fixture {
    $script:titles=@(); $script:details=@(); $script:folders=@()
    $script:windowReads=0; $script:folderReads=0; $script:detailReads=0
    $script:lateError=$false; $script:lateFolders=$false; $script:providerError=''
}
function Get-KahuaWindow { $script:windowReads++; [pscustomobject]@{verified=$true} }
function Find-KahuaControl {
    param($Id,$Name,$Type,$Root,[switch]$All)
    if ($script:providerError) { throw $script:providerError }
    Assert ($All -and ($Type -eq 'Text' -or $Type -eq 'Button')) 'Unexpected selector'
    if ($Id -eq 'CaptionTextBlock') {
        Assert ($Root.verified -and $Name -ceq 'Unable to sync') 'Warning not scoped to verified window/title'
        if ($script:lateError -and $script:windowReads -gt 1) { return (New-Control 'Unable to sync') }
        return $script:titles
    }
    if ($Id -eq 'DetailsTextBlock') {
        Assert $Root.verified 'Details not scoped to verified window'
        $script:detailReads++; return $script:details
    }
    Assert ($Name -ceq 'Folders') 'Unexpected navigation or control lookup'
    $script:folderReads++
    if ($script:lateFolders -and $script:folderReads -gt 1) { return (New-Control 'Folders') }
    return $script:folders
}
function Get-KahuaControlName { param($Control) $Control.Current.Name }
function Invoke-KahuaControl { throw 'Error handling must never click or dismiss a dialog' }
function Write-KahuaFlagProgress { }
function Assert-KahuaFlagEnvironment { }
function Assert-KahuaFlagProject { }
$duplicate='You currently have a project of the same name already synced. You cannot sync a second project with the same name at this time. Contact your administrator for more information.'
$passed=0
Reset-Fixture
Assert-KahuaFlagNoSyncError
Assert ($script:detailReads -eq 0) 'An absent warning should not read unrelated details'
$passed++
foreach ($count in @(1,2)) {
    Reset-Fixture
    $script:titles=@(1..$count | ForEach-Object { New-Control 'Unable to sync' })
    $script:details=@(1..$count | ForEach-Object { New-Control $duplicate })
    Expect-Error { Assert-KahuaFlagNoSyncError } 'same name is already synced.*same batch and state directory'
    $passed++
}
Reset-Fixture; $script:titles=@(New-Control 'Unable to sync')
Expect-Error { Assert-KahuaFlagNoSyncError } "reports 'Unable to sync'"
$passed++
$script:details=@(New-Control 'A different error')
Expect-Error { Assert-KahuaFlagNoSyncError } "reports 'Unable to sync'"
$passed++
Reset-Fixture; $script:details=@(New-Control $duplicate)
Assert-KahuaFlagNoSyncError
$passed++
Reset-Fixture; $script:providerError='Provider read failed'
Expect-Error { Assert-KahuaFlagNoSyncError } 'Provider read failed'
$passed++
Reset-Fixture; $script:folders=@(New-Control 'Folders')
Wait-KahuaFlagFolders -TimeoutSeconds 0
Assert ($script:folderReads -eq 1) 'Ready folder was not accepted'
$passed++
Reset-Fixture; $script:lateFolders=$true
Wait-KahuaFlagFolders -TimeoutSeconds 2
Assert ($script:folderReads -eq 2 -and $script:windowReads -eq 2) 'Delayed folder did not get a fresh warning check'
$passed++
Reset-Fixture; $script:lateError=$true; $script:details=@(New-Control $duplicate)
Expect-Error { Wait-KahuaFlagFolders -TimeoutSeconds 2 } 'same name is already synced'
Assert ($script:folderReads -eq 1) 'A control read occurred behind the new error'
$passed++
Reset-Fixture; $script:titles=@(New-Control 'Unable to sync'); $script:details=@(New-Control $duplicate)
$script:folders=@(New-Control 'Folders')
Expect-Error { Wait-KahuaFlagFolders } 'same name is already synced'
Assert ($script:folderReads -eq 0) 'Background Folders control hid the sync error'
$passed++
Expect-Error { Initialize-KahuaFlagDomain -Environment PRODUCTION -Domain Example } 'same name is already synced'
$passed++
Expect-Error { Open-KahuaFlagSettings -Identity ([pscustomobject]@{}) } 'same name is already synced'
$passed++
foreach ($count in @(0,1,2)) {
    Reset-Fixture
    $script:folders=@(1..2 | Select-Object -First $count | ForEach-Object { New-Control 'Folders' $false })
    $pattern=if ($count -eq 2) { 'Ambiguous Folders' } else { 'Timed out waiting' }
    Expect-Error { Wait-KahuaFlagFolders -TimeoutSeconds 0 } $pattern
    $passed++
}
Write-Host "Passed $passed Local Files error checks (simulated controls; no Kahua actions)."
