#requires -Version 5.1
# Exercise the actual environment decision without loading UI Automation/Kahua.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$tokens=$null; $parseErrors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'KahuaSyncFlagDesktop.psm1'),[ref]$tokens,[ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
foreach ($name in @('Assert-KahuaFlagProductionPackage','Assert-KahuaFlagEnvironment')) {
    $definition=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true)
    . ([scriptblock]::Create($definition.Extent.Text))
}
function Assert($Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
function Expect-Error([scriptblock]$Call,[string]$Pattern) {
    $message=''
    try { & $Call | Out-Null } catch { $message=$_.Exception.Message }
    Assert ($message -match $Pattern) "Expected '$Pattern', got '$message'"
}
function Reset-Fixture {
    $script:install=Join-Path ([IO.Path]::GetTempPath()) 'Kahua Windows production package'
    $script:processPath=Join-Path $script:install 'kahua.windows.exe'
    $script:packages=@([pscustomobject]@{Name='Kahua.Windows';PackageFamilyName='Kahua.Windows_3hchyjmf292n6';InstallLocation=$script:install})
    $script:windowError=''; $script:packageError=''; $script:pathError=''
    $script:windowReads=0; $script:packageReads=0; $script:labelReads=0
    $script:labels=@(); $script:labelRequested=''
}
function Get-KahuaWindow {
    $script:windowReads++
    if ($script:windowError) { throw $script:windowError }
    [pscustomobject]@{Current=[pscustomobject]@{ProcessId=42}}
}
function Get-Process {
    param($Id,$ErrorAction)
    Assert ($Id -eq 42) 'Package verification used a process unrelated to the verified window'
    if ($script:pathError) { throw $script:pathError }
    [pscustomobject]@{Path=$script:processPath}
}
function Get-AppxPackage {
    param($Name,$ErrorAction)
    Assert ($Name -ceq 'Kahua.Windows') 'Wrong registration query'
    $script:packageReads++
    if ($script:packageError) { throw $script:packageError }
    $script:packages
}
function Get-KahuaFlagEnvironmentLabels {
    param($Window,$Environment)
    Assert ($Window.Current.ProcessId -eq 42) 'Labels were read outside the verified window'
    $script:labelRequested=$Environment; $script:labelReads++
    $script:labels
}
$passed=0
Reset-Fixture
Assert-KahuaFlagEnvironment PRODUCTION
Assert ($script:windowReads -eq 1 -and $script:packageReads -eq 1 -and $script:labelReads -eq 0) 'Unlabelled production was not verified by its active package'
$passed++
# Windows paths are case-insensitive. Versions and install locations can vary.
$script:processPath=$script:processPath.ToUpperInvariant()
Assert-KahuaFlagEnvironment production
$passed++
foreach ($badPath in @('',(Join-Path ([IO.Path]::GetTempPath()) 'Kahua Windows devproduction/kahua.windows.exe'),
        (Join-Path $script:install 'copy/kahua.windows.exe'),(Join-Path ($script:install+'-other') 'kahua.windows.exe'))) {
    Reset-Fixture; $script:processPath=$badPath
    Expect-Error { Assert-KahuaFlagEnvironment PRODUCTION } 'Cannot read|not running from'
    Assert ($script:labelReads -eq 0) 'An invalid package fell back to a text label'
    $passed++
}
foreach ($fault in @('missing','duplicate','wrong-family','devproduction','missing-location','query-error','path-error','two-windows')) {
    Reset-Fixture
    switch ($fault) {
        missing { $script:packages=@() }
        duplicate { $script:packages=@($script:packages[0],$script:packages[0]) }
        wrong-family { $script:packages[0].PackageFamilyName='Kahua.Windows_otherpublisher' }
        devproduction { $script:packages[0].Name='Kahua.Windows.devproduction' }
        missing-location { $script:packages[0].InstallLocation='' }
        query-error { $script:packageError='Package query unavailable' }
        path-error { $script:pathError='Process path denied' }
        two-windows { $script:windowError='Expected one Kahua desktop window; found 2.' }
    }
    # Even a matching text label cannot rescue invalid production identity.
    $script:labels=@('PRODUCTION')
    Expect-Error { Assert-KahuaFlagEnvironment PRODUCTION } 'Cannot uniquely verify|unavailable|denied|found 2'
    Assert ($script:labelReads -eq 0) 'Invalid production identity fell back to a label'
    $passed++
}
Reset-Fixture
Assert-KahuaFlagEnvironment PRODUCTION
$script:processPath=Join-Path ([IO.Path]::GetTempPath()) 'different-process/kahua.windows.exe'
Expect-Error { Assert-KahuaFlagEnvironment PRODUCTION } 'not running from'
Assert ($script:windowReads -eq 2 -and $script:packageReads -eq 2) 'Production identity was cached across window changes'
$passed++
foreach ($labelCount in @(0,1,2)) {
    Reset-Fixture
    if ($labelCount -gt 0) { $script:labels=@(1..$labelCount) }
    if ($labelCount -eq 1) { Assert-KahuaFlagEnvironment DEVPRODUCTION }
    else { Expect-Error { Assert-KahuaFlagEnvironment DEVPRODUCTION } 'one exact environment label' }
    Assert ($script:packageReads -eq 0 -and $script:labelRequested -ceq 'DEVPRODUCTION') 'The exact nonproduction label check changed'
    $passed++
}
Write-Host "Passed $passed environment identity checks (simulated packages/UI; no Kahua actions)."
