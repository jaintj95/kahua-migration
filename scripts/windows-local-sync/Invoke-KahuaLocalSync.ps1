#requires -Version 5.1
<#
Copies only Documents and Photos from one ZIP into an already-selected Kahua
project sync directory. Kahua must be running. Plan is the default.
This script verifies local bytes, NOT completion of the Kahua upload.
It never deletes destination files, overwrites conflicts, or resets sync.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$MappingPath,
    [ValidateSet('Plan','Copy','VerifyLocal')][string]$Mode = 'Plan'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Assert-PlainPath([string]$Path) {
    $part = [IO.Path]::GetFullPath($Path)
    while ($part) {
        if (Test-Path -LiteralPath $part) {
            if ((Get-Item -LiteralPath $part -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Refusing a symbolic link or junction: $part"
            }
        }
        $parent = [IO.Path]::GetDirectoryName($part)
        if ($parent -eq $part) { break }
        $part = $parent
    }
}
function File-Hash([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Canonical-Path([string]$Path) {
    # Resolve the nearest existing ancestor to expand Windows 8.3 aliases even
    # when the final state/staging directory has not yet been created.
    $existing = [IO.Path]::GetFullPath($Path)
    $tail = New-Object 'System.Collections.Generic.Stack[string]'
    while (-not (Test-Path -LiteralPath $existing)) {
        $tail.Push([IO.Path]::GetFileName($existing))
        $existing = [IO.Path]::GetDirectoryName($existing)
        if (-not $existing) { throw "No existing parent for path: $Path" }
    }
    $resolved = (Resolve-Path -LiteralPath $existing).ProviderPath
    foreach ($segment in $tail) { $resolved = Join-Path $resolved $segment }
    return $resolved
}
function Write-Json([string]$Path, $Value) {
    $temporary = "$Path.$([Guid]::NewGuid().ToString('N')).tmp"
    $Value | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

$mapping = Get-Content -LiteralPath $MappingPath -Raw | ConvertFrom-Json
foreach ($field in @('domain','project_number','project_name','source_zip','source_prefix','sync_root','state_root')) {
    if (-not $mapping.PSObject.Properties[$field] -or [string]::IsNullOrWhiteSpace([string]$mapping.$field)) {
        throw "Missing mapping field: $field"
    }
}
if ($Mode -ne 'Plan' -and (-not $mapping.PSObject.Properties['destination_confirmed'] -or
    $mapping.destination_confirmed -isnot [bool] -or -not $mapping.destination_confirmed)) {
    throw 'Confirm the domain, project number/name, and sync path in Kahua before setting destination_confirmed=true.'
}
$zipPath = (Resolve-Path -LiteralPath $mapping.source_zip).ProviderPath
$destination = (Canonical-Path $mapping.sync_root).TrimEnd('\')
$state = (Canonical-Path $mapping.state_root).TrimEnd('\')
if ($Mode -ne 'Plan' -and -not (Test-Path -LiteralPath $destination -PathType Container)) {
    throw 'Sync root must already exist before copying or local verification.'
}
if ($state -eq $destination -or $state.StartsWith($destination + '\', [StringComparison]::OrdinalIgnoreCase) -or
    $destination.StartsWith($state + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'State and staging must be outside the synced project directory.'
}
if ([IO.Path]::GetPathRoot($state) -ne [IO.Path]::GetPathRoot($destination)) {
    throw 'State and sync directories must be on the same volume for atomic publication.'
}
Assert-PlainPath $destination
Assert-PlainPath $state
[void][IO.Directory]::CreateDirectory($state)
$lock = [IO.File]::Open((Join-Path $state 'worker.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
$archive = $null
try {
    $zipHash = File-Hash $zipPath
    $prefix = $mapping.source_prefix.TrimEnd('/') + '/'
    $binding = [ordered]@{domain=$mapping.domain; project_number=$mapping.project_number; project_name=$mapping.project_name;
        source_zip=$zipPath; zip_sha256=$zipHash; source_prefix=$prefix; sync_root=$destination}
    $bindingPath = Join-Path $state 'binding.json'
    $bindingText = $binding | ConvertTo-Json -Compress
    if (Test-Path -LiteralPath $bindingPath) {
        $oldBinding = Get-Content -LiteralPath $bindingPath -Raw | ConvertFrom-Json | ConvertTo-Json -Compress
        if ($oldBinding -cne $bindingText) { throw 'This state directory belongs to a different ZIP or destination. Use a new state directory.' }
    } else { Write-Json $bindingPath $binding }
    $stage = Join-Path $state 'staged'
    Assert-PlainPath $stage
    [void][IO.Directory]::CreateDirectory($stage)
    $archive = [IO.Compression.ZipFile]::OpenRead($zipPath)
    $selected = @()
    $seen = @{}
    foreach ($entry in $archive.Entries) {
        if (-not $entry.FullName.StartsWith($prefix, [StringComparison]::Ordinal)) { continue }
        $relative = $entry.FullName.Substring($prefix.Length)
        if ($relative -cnotmatch '^(Documents|Photos)/') { continue }
        $isDirectory = $relative.EndsWith('/')
        $relative = $relative.TrimEnd('/')
        foreach ($segment in $relative.Split('/')) {
            if (-not $segment -or $segment -in @('.','..') -or $segment -match '[<>:"\\|?*\x00-\x1f]' -or
                $segment -match '[. ]$' -or $segment -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)') {
                throw "Unsafe Windows path in ZIP: $relative"
            }
        }
        if ($seen.ContainsKey($relative)) { throw "Duplicate or case-colliding ZIP path: $relative" }
        $seen[$relative] = $true
        $target = Join-Path $destination $relative.Replace('/', '\')
        if ($target.Length -ge 248) { throw "Path needs review before Windows sync: $relative" }
        Assert-PlainPath $target
        $selected += [pscustomobject]@{entry=$entry; relative=$relative; target=$target; directory=$isDirectory}
    }
    if (@($selected | Where-Object { -not $_.directory }).Count -eq 0) { throw 'No Documents or Photos files found under the exact source_prefix.' }
    # Validate file/directory collisions before any extraction or sync writes.
    foreach ($item in $selected) {
        $parent = $item.relative
        while ($parent.Contains('/')) {
            $parent = $parent.Substring(0, $parent.LastIndexOf('/'))
            if (@($selected | Where-Object { $_.relative -ieq $parent -and -not $_.directory }).Count) {
                throw "File/directory collision in ZIP: $parent"
            }
        }
    }
    $records = @()
    foreach ($item in $selected) {
        $staged = Join-Path $stage $item.relative.Replace('/', '\')
        Assert-PlainPath $staged
        if ($item.directory) { continue }
        [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($staged))
        $temporary = "$staged.$([Guid]::NewGuid().ToString('N')).tmp"
        [IO.Compression.ZipFileExtensions]::ExtractToFile($item.entry, $temporary, $false)
        if ((Get-Item -LiteralPath $temporary).Length -ne $item.entry.Length) { throw "Extraction length mismatch: $($item.relative)" }
        $hash = File-Hash $temporary
        if (Test-Path -LiteralPath $staged) {
            if ((File-Hash $staged) -ne $hash) { throw "Staging conflict: $staged" }
            Remove-Item -LiteralPath $temporary
        } else { [IO.File]::Move($temporary, $staged) }
        $status = 'missing'
        if (Test-Path -LiteralPath $item.target) {
            if (-not (Test-Path -LiteralPath $item.target -PathType Leaf) -or (File-Hash $item.target) -ne $hash) {
                throw "Destination conflict; no files will be copied: $($item.relative)"
            }
            $status = 'identical'
        }
        $parentPath = [IO.Path]::GetDirectoryName($item.target)
        while ($parentPath.Length -gt $destination.Length) {
            if ((Test-Path -LiteralPath $parentPath) -and -not (Test-Path -LiteralPath $parentPath -PathType Container)) {
                throw "A file blocks a destination directory: $parentPath"
            }
            $parentPath = [IO.Path]::GetDirectoryName($parentPath)
        }
        $records += [pscustomobject]@{relative_path=$item.relative; bytes=$item.entry.Length; sha256=$hash; local_status=$status}
    }
    $manifest = [ordered]@{binding=$binding; mode=$Mode; status='planned'; server_verified=$false; files=$records}
    Write-Json (Join-Path $state 'manifest.json') $manifest
    if ($Mode -eq 'VerifyLocal' -and @($records | Where-Object local_status -ne 'identical').Count) { throw 'Some expected files are missing from the local sync directory.' }
    if ($Mode -eq 'Copy') {
        foreach ($item in $selected | Where-Object directory) {
            Assert-PlainPath $item.target
            [void][IO.Directory]::CreateDirectory($item.target)
        }
        foreach ($record in $records) {
            $target = Join-Path $destination $record.relative_path.Replace('/', '\')
            Assert-PlainPath $target
            if (Test-Path -LiteralPath $target) {
                if ((File-Hash $target) -ne $record.sha256) { throw "Destination changed: $target" }
                $record.local_status = 'identical'
                continue
            }
            [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target))
            # Prepare complete bytes outside sync, then rename without replacement.
            $publish = Join-Path $state ('publish-' + [Guid]::NewGuid().ToString('N'))
            [IO.File]::Copy((Join-Path $stage $record.relative_path.Replace('/', '\')), $publish, $false)
            if ((File-Hash $publish) -ne $record.sha256) { throw "Publish hash mismatch: $target" }
            [IO.File]::Move($publish, $target)
            if ((File-Hash $target) -ne $record.sha256) { throw "Local verification failed: $target" }
            $record.local_status = 'copied'
            Write-Json (Join-Path $state 'manifest.json') $manifest
        }
        $manifest.status = 'copied-awaiting-kahua-verification'
    } elseif ($Mode -eq 'VerifyLocal') { $manifest.status = 'local-bytes-verified-only' }
    Write-Json (Join-Path $state 'manifest.json') $manifest
    $records | Format-Table relative_path,bytes,local_status -AutoSize
    Write-Output "Files: $($records.Count); status: $($manifest.status)"
    Write-Output "Evidence: $state"
    Write-Output 'Do not delete synced files. Verify the server and deselect this project in Kahua before cleanup.'
} finally {
    if ($archive) { $archive.Dispose() }
    $lock.Dispose()
}
