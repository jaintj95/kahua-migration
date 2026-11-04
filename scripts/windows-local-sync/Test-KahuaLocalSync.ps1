#requires -Version 5.1
# Standalone Windows tests. All fixture writes stay in a unique TEMP directory.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.IO.Compression.FileSystem
$worker = Join-Path $PSScriptRoot 'Invoke-KahuaLocalSync.ps1'
$testRoot = Join-Path $env:TEMP ('kahua-sync-tests-' + [Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($testRoot)
$passed = 0
function Assert($Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Fixture([string]$Name, [hashtable]$Entries) {
    $root = Join-Path $testRoot $Name
    [void][IO.Directory]::CreateDirectory($root)
    $dest = Join-Path $root 'destination'
    [void][IO.Directory]::CreateDirectory($dest)
    $zip = Join-Path $root 'source.zip'
    $archive = [IO.Compression.ZipFile]::Open($zip, 'Create')
    try {
        foreach ($name in $Entries.Keys) {
            $entry = $archive.CreateEntry($name)
            $writer = New-Object IO.StreamWriter($entry.Open())
            try { $writer.Write([string]$Entries[$name]) } finally { $writer.Dispose() }
        }
    } finally { $archive.Dispose() }
    $mapping = [ordered]@{domain='Test';project_number='0001';project_name='Fixture';source_zip=$zip;
        source_prefix='Project';sync_root=$dest;state_root=(Join-Path $root 'state');destination_confirmed=$true}
    $path = Join-Path $root 'mapping.json'
    $mapping | ConvertTo-Json | Set-Content -LiteralPath $path -Encoding UTF8
    return [pscustomobject]@{mapping=$path;destination=$dest;root=$root}
}
function Expect-Failure($Fixture, [string]$Pattern, [string]$Mode='Copy') {
    $message = ''
    try { & $worker -MappingPath $Fixture.mapping -Mode $Mode | Out-Null } catch { $message = $_.Exception.Message }
    Assert ($message -match $Pattern) "Expected '$Pattern'; received '$message'"
    $script:passed++
}
$basic = @{'Project/Documents/sub/file.txt'='document';'Project/Photos/photo.jpg'='photo';'Project/Drawings/drawing.pdf'='excluded'}
$f = Fixture 'basic' $basic
& $worker -MappingPath $f.mapping -Mode Plan | Out-Null
Assert (@(Get-ChildItem -LiteralPath $f.destination -Recurse -File).Count -eq 0) 'Plan wrote into sync'
$passed++
& $worker -MappingPath $f.mapping -Mode Copy | Out-Null
Assert (@(Get-ChildItem -LiteralPath $f.destination -Recurse -File).Count -eq 2) 'Copy selected incorrect scope'
Assert (Test-Path -LiteralPath (Join-Path $f.destination 'Documents\sub\file.txt')) 'Nested hierarchy lost'
$passed++
$time = (Get-Item -LiteralPath (Join-Path $f.destination 'Photos\photo.jpg')).LastWriteTimeUtc
& $worker -MappingPath $f.mapping -Mode Copy | Out-Null
Assert ((Get-Item -LiteralPath (Join-Path $f.destination 'Photos\photo.jpg')).LastWriteTimeUtc -eq $time) 'Rerun replaced identical file'
$passed++
& $worker -MappingPath $f.mapping -Mode VerifyLocal | Out-Null
$passed++
'different' | Set-Content -LiteralPath (Join-Path $f.destination 'Photos\photo.jpg')
Expect-Failure $f 'Destination conflict'
$f = Fixture 'conflict-before-copy' $basic
[void][IO.Directory]::CreateDirectory((Join-Path $f.destination 'Photos'))
'existing' | Set-Content -LiteralPath (Join-Path $f.destination 'Photos\photo.jpg')
Expect-Failure $f 'Destination conflict'
Assert (-not (Test-Path -LiteralPath (Join-Path $f.destination 'Documents'))) 'Conflict caused partial copy'
$f = Fixture 'traversal' @{'Project/Documents/../../escape.txt'='bad'}
Expect-Failure $f 'Unsafe Windows path'
$f = Fixture 'reserved' @{'Project/Documents/CON.txt'='bad'}
Expect-Failure $f 'Unsafe Windows path'
$f = Fixture 'collision' @{'Project/Documents/a'='file';'Project/Documents/a/child.txt'='child'}
Expect-Failure $f 'File/directory collision'
$f = Fixture 'missing-local' $basic
Expect-Failure $f 'missing from the local sync directory' 'VerifyLocal'
$f = Fixture 'changed-binding' $basic
& $worker -MappingPath $f.mapping -Mode Plan | Out-Null
$m = Get-Content -LiteralPath $f.mapping -Raw | ConvertFrom-Json
$m.project_number = '0002'
$m | ConvertTo-Json | Set-Content -LiteralPath $f.mapping -Encoding UTF8
Expect-Failure $f 'different ZIP or destination'
$f = Fixture 'unsafe-state' $basic
$m = Get-Content -LiteralPath $f.mapping -Raw | ConvertFrom-Json
$m.state_root = Join-Path $m.sync_root 'state'
$m | ConvertTo-Json | Set-Content -LiteralPath $f.mapping -Encoding UTF8
Expect-Failure $f 'outside the synced project'
$f = Fixture 'preflight-before-ui' $basic
$m = Get-Content -LiteralPath $f.mapping -Raw | ConvertFrom-Json
$m.sync_root = Join-Path $f.root 'not-created-by-kahua-yet'
$m.destination_confirmed = $false
$m | ConvertTo-Json | Set-Content -LiteralPath $f.mapping -Encoding UTF8
& $worker -MappingPath $f.mapping -Mode Plan | Out-Null
Assert (-not (Test-Path -LiteralPath $m.sync_root)) 'Plan created the sync destination'
$passed++
Expect-Failure $f 'destination_confirmed=true'
$m.destination_confirmed = 'true'
$m | ConvertTo-Json | Set-Content -LiteralPath $f.mapping -Encoding UTF8
Expect-Failure $f 'destination_confirmed=true'
Write-Output "PASS: $passed checks. Fixtures retained at $testRoot"
