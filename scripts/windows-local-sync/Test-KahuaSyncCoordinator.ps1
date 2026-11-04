#requires -Version 5.1
# Actual coordinator and copy worker, simulated Kahua UI/server. No live app access.
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.IO.Compression.FileSystem
$testRoot=Join-Path $env:TEMP ('kahua-coordinator-tests-'+[Guid]::NewGuid().ToString('N'))
$runnerDir=Join-Path $testRoot 'scripts'
[void][IO.Directory]::CreateDirectory($runnerDir)
foreach($name in @('Invoke-KahuaSyncBatch.ps1','Invoke-KahuaLocalSync.ps1','KahuaSyncCore.psm1')) {
    Copy-Item (Join-Path $PSScriptRoot $name) (Join-Path $runnerDir $name)
}
$desktop=@'
Set-StrictMode -Version Latest
function Add-KahuaTestEvent([string]$Text) {
    [IO.File]::AppendAllText((Join-Path $PSScriptRoot 'events.txt'),$Text+[Environment]::NewLine)
}
function Select-KahuaProject {
    param($Environment,$Domain,$Number,$Name)
    Add-KahuaTestEvent "SELECT $Number"
}
function Test-KahuaLocalFilesSetup {
    param($Environment,$Domain,$Number,$Name,$ExpectedSyncRoot,[switch]$Initialize)
    Add-KahuaTestEvent "SETUP $Number"
    if (Test-Path (Join-Path $PSScriptRoot 'paused')) { throw 'Local Files is configured but paused.' }
    $marker=Join-Path $PSScriptRoot 'unconfigured'
    if (Test-Path $marker) {
        if (-not $Initialize) { throw 'Local Files is not configured.' }
        Add-KahuaTestEvent 'INITIALIZE'
        Remove-Item $marker
    }
    return [pscustomobject]@{state='running';reopened=$true;sync_root=$ExpectedSyncRoot}
}
function Set-KahuaProjectSync {
    param($Environment,$Domain,$Number,$Name,[bool]$Enabled,$ExpectedSyncRoot)
    Add-KahuaTestEvent "SYNC $Number $Enabled"
    if (-not $Enabled -and (Test-Path (Join-Path $PSScriptRoot 'fail-off'))) { throw 'Simulated uncertain sync-off save' }
    if($Enabled) {
        foreach($folder in @('Documents','Photos')) {[void][IO.Directory]::CreateDirectory((Join-Path $ExpectedSyncRoot $folder))}
    }
    return [pscustomobject]@{reopened=$true;documents_enabled=$Enabled;photos_enabled=$Enabled}
}
Export-ModuleMember -Function *-Kahua*
'@
[IO.File]::WriteAllText((Join-Path $runnerDir 'KahuaDesktop.psm1'),$desktop)
$files=@'
Set-StrictMode -Version Latest
function Open-KahuaFileRoot {param($Identity)}
function Get-KahuaFolderListing {
    @('Documents','Photos') | ForEach-Object {[pscustomobject]@{name=$_;is_folder=$true}}
}
function Receive-KahuaManifest {
    param($Identity,$Manifest,$DownloadRoot,[switch]$AllowMissing,$TimeoutSeconds)
    Add-KahuaTestEvent "DOWNLOAD $($Identity.Number)"
    [void][IO.Directory]::CreateDirectory($DownloadRoot)
    foreach($file in $Manifest.files) {
        # Simulate server propagation from the real copy worker's fixture destination.
        $source=Join-Path $Manifest.binding.sync_root $file.relative_path.Replace('/','\')
        $found=Test-Path -LiteralPath $source -PathType Leaf
        $destination=Join-Path $DownloadRoot $file.relative_path.Replace('/','\')
        if($found) {
            [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination))
            [IO.File]::Copy($source,$destination,$false)
        }
        [pscustomobject]@{environment=$Identity.Environment;domain=$Identity.Domain;project_number=$Identity.Number;
            project_name=$Identity.Name;relative_path=$file.relative_path;found=[bool]$found;action='Download';
            download_path=$destination;version='1'}
    }
}
Export-ModuleMember -Function *-Kahua*
'@
[IO.File]::WriteAllText((Join-Path $runnerDir 'KahuaFileManager.psm1'),$files)
$rows=@()
foreach($n in @('0001','0002')) {
    $zip=Join-Path $testRoot ($n+'.zip')
    $archive=[IO.Compression.ZipFile]::Open($zip,'Create')
    try {
        $writer=[IO.StreamWriter]::new($archive.CreateEntry('Project/Documents/sub/a.txt').Open())
        try {$writer.Write("source bytes $n")} finally {$writer.Dispose()}
    } finally {$archive.Dispose()}
    $rows += [pscustomobject]@{run_id=$n;environment='DEVPRODUCTION';domain='Fixture';project_number=$n;
        project_name=('Project '+$n);source_zip=$zip;source_prefix='Project';sync_root=(Join-Path $testRoot ('sync-'+$n))}
}
$csv=Join-Path $testRoot 'projects.csv'
$rows | Export-Csv $csv -NoTypeInformation -Encoding UTF8
$state=Join-Path $testRoot 'state'
$runner=Join-Path $runnerDir 'Invoke-KahuaSyncBatch.ps1'
$events=Join-Path $runnerDir 'events.txt'
$passed=0
function Assert($Condition,[string]$Message) {if(-not $Condition){throw $Message}}
function Read-Journal([string]$Number) {
    Get-Content (Join-Path $state "projects\$Number\journal.json") -Raw | ConvertFrom-Json
}
& $runner -CsvPath $csv -StateRoot $state -Mode Plan | Out-Null
Assert (-not (Test-Path $events)) 'Plan reached the desktop'
Assert (-not (Test-Path $rows[0].sync_root) -and -not (Test-Path $rows[1].sync_root)) 'Plan wrote a sync path'
$passed++
[IO.File]::WriteAllText((Join-Path $runnerDir 'unconfigured'),'new installation')
$message=''
try { & $runner -CsvPath $csv -StateRoot $state -Mode Execute | Out-Null } catch {$message=$_.Exception.Message}
Assert ($message -match 'not configured') 'Unconfigured setup was not reported'
Assert (-not @(Get-Content $events | Where-Object {$_ -match '^(DOWNLOAD|SYNC|INITIALIZE)'}).Count) 'Unconfigured setup reached download or changed sync'
Assert ((Read-Journal '0001').phase -eq 'checking-local-files-setup') 'Setup failure was reported as another phase'
$passed++
& $runner -CsvPath $csv -StateRoot $state -Mode Execute -InitializeLocalFiles | Out-Null
foreach($n in @('0001','0002')) {
    $journal=Read-Journal $n
    Assert ($journal.status -eq 'complete' -and $journal.server_verified -and $journal.sync_off_verified) "Project $n incomplete"
}
$firstEvents=@(Get-Content $events)
Assert (@($firstEvents | Where-Object {$_ -match '^SYNC .* True$'}).Count -eq 2) 'Fresh projects did not enable sync once each'
Assert (@($firstEvents | Where-Object {$_ -eq 'INITIALIZE'}).Count -eq 1) 'Initialization was not performed exactly once for the installation'
$passed++
& $runner -CsvPath $csv -StateRoot $state -Mode Execute | Out-Null
$allEvents=@(Get-Content $events)
Assert (@($allEvents | Where-Object {$_ -match '^SYNC .* True$'}).Count -eq 2) 'Resume recopied completed server content'
Assert (@($allEvents | Where-Object {$_ -match '^DOWNLOAD '}).Count -gt @($firstEvents | Where-Object {$_ -match '^DOWNLOAD '}).Count) 'Resume trusted the previous journal without server reconciliation'
Assert (@($allEvents | Where-Object {$_ -eq 'INITIALIZE'}).Count -eq 1) 'Resume reinitialized configured sync'
$passed++
$beforeCheck=@(Get-Content $events | Where-Object {$_ -match '^SYNC '}).Count
$setupBeforeCheck=@(Get-Content $events | Where-Object {$_ -match '^SETUP '}).Count
& $runner -CsvPath $csv -StateRoot $state -Mode Check | Out-Null
Assert (@(Get-Content $events | Where-Object {$_ -match '^SYNC '}).Count -eq $beforeCheck) 'Check changed sync'
Assert (@(Get-Content $events | Where-Object {$_ -match '^SETUP '}).Count -eq $setupBeforeCheck) 'Check reached sync setup'
Assert ((Read-Journal '0001').status -eq 'checked' -and (Read-Journal '0002').server_verified) 'Check left a running journal'
$passed++
[IO.File]::WriteAllText((Join-Path $runnerDir 'paused'),'paused installation')
$beforePause=@(Get-Content $events).Count
$message=''
try { & $runner -CsvPath $csv -StateRoot $state -Mode Execute -InitializeLocalFiles | Out-Null } catch {$message=$_.Exception.Message}
Assert ($message -match 'paused') 'Paused setup was not reported'
Assert (-not @(Get-Content $events | Select-Object -Skip $beforePause | Where-Object {$_ -match '^(DOWNLOAD|SYNC|INITIALIZE)'}).Count) 'Paused sync was resumed or reached copying'
Remove-Item (Join-Path $runnerDir 'paused')
$passed++
[IO.File]::WriteAllText((Join-Path $runnerDir 'fail-off'),'simulate uncertain save')
$beforeFailure=@(Get-Content $events).Count
$message=''
try {& $runner -CsvPath $csv -StateRoot $state -Mode Execute | Out-Null} catch {$message=$_.Exception.Message}
Assert ($message -match 'uncertain sync-off') 'Expected save failure was swallowed'
$journal=Read-Journal '0001'
Assert ($journal.status -eq 'failed' -and $journal.phase -eq 'disabling-sync' -and -not $journal.server_verified) 'Uncertain save was marked complete'
$laterEvents=@(Get-Content $events | Select-Object -Skip $beforeFailure)
Assert (-not @($laterEvents | Where-Object {$_ -match '^(DOWNLOAD|SYNC) 0002'}).Count) 'Queue continued past an uncertain sync state'
$passed++
Remove-Item (Join-Path $runnerDir 'fail-off')
& $runner -CsvPath $csv -StateRoot $state -Mode Execute | Out-Null
Assert ((Read-Journal '0001').status -eq 'complete' -and (Read-Journal '0002').status -eq 'complete') 'Resume did not recover the failed project and finish the queue'
$passed++
Write-Output "PASS: $passed coordinator scenarios (simulated desktop/server). Fixtures retained at $testRoot"
