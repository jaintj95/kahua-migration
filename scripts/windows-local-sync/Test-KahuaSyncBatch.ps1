#requires -Version 5.1
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'KahuaSyncCore.psm1') -Force
$testRoot = Join-Path $env:TEMP ('kahua-batch-tests-' + [Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($testRoot)
$csv = Join-Path $testRoot 'projects.csv'
$state = Join-Path $testRoot 'state'
$source = Join-Path $testRoot 'source.zip'
[IO.File]::WriteAllText($source, 'fixture source')
$source2 = Join-Path $testRoot 'source2.zip'
[IO.File]::WriteAllText($source2, 'fixture source two')
$sync = Join-Path $testRoot 'sync-one'
$passed = 0
function Assert($Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Check-Failure([scriptblock]$Action, [string]$Pattern) {
    $message = ''
    try { & $Action | Out-Null } catch { $message = $_.Exception.Message }
    Assert ($message -match $Pattern) "Expected '$Pattern'; received '$message'"
    $script:passed++
}
function Row([string]$Run='one', [string]$Number='0001', [string]$Name='First') {
    return [pscustomobject][ordered]@{run_id=$Run;environment='DEVPRODUCTION';domain='Fixture';project_number=$Number;
        project_name=$Name;source_zip=$source;source_prefix='Project';sync_root=$sync}
}
function Save-Rows($Rows) { @($Rows) | Export-Csv -LiteralPath $csv -NoTypeInformation -Encoding UTF8 }
Save-Rows (Row)
$rows = @(Read-KahuaSyncCsv $csv $state)
Assert ($rows.Count -eq 1 -and $rows[0].project_number -ceq '0001') 'CSV lost the exact project number'
Assert (-not (Test-Path $sync) -and -not (Test-Path $state)) 'CSV validation wrote migration directories'
$passed++
Save-Rows @((Row),(Row 'two' '0001' 'Different'))
Check-Failure {Read-KahuaSyncCsv $csv $state} 'Duplicate project number'
Save-Rows @((Row),(Row 'two' '0002' 'First'))
Check-Failure {Read-KahuaSyncCsv $csv $state} 'Duplicate project name'
Save-Rows @((Row),(Row 'one' '0002' 'Second'))
Check-Failure {Read-KahuaSyncCsv $csv $state} 'Duplicate run_id'
Save-Rows @((Row),(Row 'two' '0002' 'Second'))
Check-Failure {Read-KahuaSyncCsv $csv $state} 'same source ZIP'
$r=Row; $r.run_id='../escape'; Save-Rows $r
Check-Failure {Read-KahuaSyncCsv $csv $state} 'Unsafe run_id'
$r=Row; $r.project_number=' 0001'; Save-Rows $r
Check-Failure {Read-KahuaSyncCsv $csv $state} 'whitespace'
$r=Row; $r.project_name='CON'; Save-Rows $r
Check-Failure {Read-KahuaSyncCsv $csv $state} 'Windows sync-path'
Save-Rows (Row)
Check-Failure {Read-KahuaSyncCsv $csv (Join-Path $sync 'state')} 'outside every sync'
$r=Row 'two' '0002' 'Second'; $r.source_zip=$source2; $r.sync_root=Join-Path $sync 'nested'; Save-Rows @((Row),$r)
Check-Failure {Read-KahuaSyncCsv $csv $state} 'overlap'
$r.sync_root=Join-Path $testRoot 'sync-two'; $r.domain='Other'; Save-Rows @((Row),$r)
Check-Failure {Read-KahuaSyncCsv $csv $state} 'separate CSV'
$r.domain='Fixture'; Save-Rows @((Row),$r)
Assert (@(Read-KahuaSyncCsv $csv $state).Count -eq 2) 'Valid two-project CSV rejected'
$passed++

$downloads=Join-Path $testRoot 'independent-downloads'
[void][IO.Directory]::CreateDirectory($downloads)
$download=Join-Path $downloads 'original.txt'
[IO.File]::WriteAllText($download, 'original bytes')
$hash=(Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash.ToLowerInvariant()
$manifest=[pscustomobject]@{binding=[pscustomobject]@{sync_root=$sync;source_zip=$source};
    files=@([pscustomobject]@{relative_path='Documents/sub/original.txt';bytes=(Get-Item $download).Length;sha256=$hash})}
$receipt=[pscustomobject]@{relative_path='Documents/sub/original.txt';found=$true;action='Download';download_path=$download;version='1'}
Assert (Test-KahuaDownloadedFiles $manifest @($receipt) $downloads).server_verified 'Correct independent download rejected'
$passed++
Check-Failure {Test-KahuaDownloadedFiles $manifest @() $downloads} 'complete manifest'
Check-Failure {Test-KahuaDownloadedFiles $manifest @($receipt,$receipt) $downloads} 'Duplicate download receipt'
$receipt.action='Open'
Check-Failure {Test-KahuaDownloadedFiles $manifest @($receipt) $downloads} 'original-file Download'
$receipt.action='Download'; $receipt.download_path=$source
Check-Failure {Test-KahuaDownloadedFiles $manifest @($receipt) $downloads} 'outside this attempt'
$receipt.download_path=$download
[IO.File]::WriteAllText($download, 'different bytes')
Check-Failure {Test-KahuaDownloadedFiles $manifest @($receipt) $downloads} 'content conflict'
$receipt.found=$false
Check-Failure {Test-KahuaDownloadedFiles $manifest @($receipt) $downloads} 'missing from Kahua'
Assert (-not (Test-KahuaDownloadedFiles $manifest @($receipt) $downloads -AllowMissing).server_verified) 'Missing file was marked verified'
$passed++
Check-Failure {Test-KahuaDownloadedFiles $manifest @($receipt) $downloads -AllowMissing -StagingRoot $downloads} 'overlap staging'
Check-Failure {Test-KahuaDownloadedFiles $manifest @($receipt) $downloads -AllowMissing -Identity @{Domain='Wrong'}} 'identity'
$journal=Join-Path $testRoot 'journal.json'
Write-KahuaJson $journal @{status='planned'}
Write-KahuaJson $journal @{status='failed';server_verified=$false}
Assert ((Get-Content $journal -Raw | ConvertFrom-Json).status -eq 'failed') 'Atomic journal replacement failed'
$passed++

# Exercise the actual coordinator with an invalid later ZIP. Its desktop module
# is an explicit trap: no UI code may be reached before all source plans pass.
Add-Type -AssemblyName System.IO.Compression.FileSystem
$validZip=Join-Path $testRoot 'valid.zip'
$archive=[IO.Compression.ZipFile]::Open($validZip,'Create')
try {
    $writer=[IO.StreamWriter]::new($archive.CreateEntry('Project/Documents/a.txt').Open())
    try { $writer.Write('source bytes') } finally { $writer.Dispose() }
} finally { $archive.Dispose() }
$runnerDir=Join-Path $testRoot 'runner'
[void][IO.Directory]::CreateDirectory($runnerDir)
foreach($name in @('Invoke-KahuaSyncBatch.ps1','Invoke-KahuaLocalSync.ps1','KahuaSyncCore.psm1')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $runnerDir $name)
}
[IO.File]::WriteAllText((Join-Path $runnerDir 'KahuaDesktop.psm1'),"throw 'DESKTOP REACHED BEFORE PREFLIGHT FINISHED'")
$r1=Row; $r1.source_zip=$validZip
$r2=Row 'two' '0002' 'Second'; $r2.source_zip=$source2; $r2.sync_root=Join-Path $testRoot 'sync-two'
Save-Rows @($r1,$r2)
$batchState=Join-Path $testRoot 'batch-state'
Check-Failure {& (Join-Path $runnerDir 'Invoke-KahuaSyncBatch.ps1') -CsvPath $csv -StateRoot $batchState -Mode Execute} 'Central Directory|archive'
Assert (-not (Test-Path $sync) -and -not (Test-Path $r2.sync_root)) 'Preflight failure wrote a sync destination'
Assert ((Get-Content (Join-Path $batchState 'projects\two\journal.json') -Raw | ConvertFrom-Json).status -eq 'failed') 'Failure was not journaled'
$passed++
$r2.project_name='Changed'; Save-Rows @($r1,$r2)
Check-Failure {& (Join-Path $runnerDir 'Invoke-KahuaSyncBatch.ps1') -CsvPath $csv -StateRoot $batchState -Mode Plan} 'mappings changed'
Write-Output "PASS: $passed batch checks. Fixtures retained at $testRoot"
