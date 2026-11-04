#requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-KahuaJson {
    param([string]$Path, $Value)
    $temporary = "$Path.$([Guid]::NewGuid().ToString('N')).tmp"
    [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth 20), [Text.UTF8Encoding]::new($false))
    if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($temporary, $Path, [NullString]::Value) }
    else { [IO.File]::Move($temporary, $Path) }
}

function Resolve-KahuaPath {
    param([string]$Path)
    if ($Path -notmatch '^[A-Za-z]:\\' -or $Path -match '[\x00-\x1f]') {
        throw "Use an absolute local Windows path: $Path"
    }
    $existing = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $tail = New-Object 'System.Collections.Generic.Stack[string]'
    while (-not (Test-Path -LiteralPath $existing)) {
        $tail.Push([IO.Path]::GetFileName($existing))
        $existing = [IO.Path]::GetDirectoryName($existing)
        if (-not $existing) { throw "No existing parent for $Path" }
    }
    $resolved = (Resolve-Path -LiteralPath $existing).ProviderPath
    $ancestor = $resolved
    while ($ancestor) {
        if ((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Refusing a symbolic link or junction: $ancestor"
        }
        $ancestor = [IO.Path]::GetDirectoryName($ancestor)
    }
    foreach ($segment in $tail) { $resolved = Join-Path $resolved $segment }
    return $resolved.TrimEnd('\')
}

function Test-KahuaPathOverlap {
    param([string]$First, [string]$Second)
    return ($First -ieq $Second -or $First.StartsWith($Second + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $Second.StartsWith($First + '\', [StringComparison]::OrdinalIgnoreCase))
}

function Read-KahuaSyncCsv {
    param([string]$CsvPath, [string]$StateRoot)
    $state = Resolve-KahuaPath $StateRoot
    $rows = @(Import-Csv -LiteralPath $CsvPath)
    if (-not $rows.Count) { throw 'The CSV contains no project rows.' }
    $columns = @('run_id','environment','domain','project_number','project_name','source_zip','source_prefix','sync_root')
    $identities = @{}; $names = @{}; $runs = @{}; $sources = @{}
    $result = @()
    foreach ($row in $rows) {
        foreach ($column in $columns) {
            if (-not $row.PSObject.Properties[$column] -or [string]::IsNullOrWhiteSpace($row.$column)) {
                throw "Missing CSV value: $column (row $($result.Count + 2))."
            }
            if ($row.$column -cne $row.$column.Trim()) { throw "Leading/trailing whitespace in $column." }
        }
        if ($row.run_id -notmatch '^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$') { throw "Unsafe run_id: $($row.run_id)" }
        foreach ($field in @('domain','project_name')) {
            if ($row.$field -match '[<>:"/\\|?*\x00-\x1f]' -or $row.$field -match '[. ]$' -or
                $row.$field -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)') {
                throw "The $field needs an explicitly supported Windows sync-path mapping."
            }
        }
        $key = @($row.environment,$row.domain,$row.project_number) -join '|'
        $nameKey = @($row.environment,$row.domain,$row.project_name) -join '|'
        if ($identities.ContainsKey($key)) { throw "Duplicate project number in CSV: $key" }
        if ($names.ContainsKey($nameKey)) { throw "Duplicate project name would share a sync path: $nameKey" }
        if ($runs.ContainsKey($row.run_id)) { throw "Duplicate run_id: $($row.run_id)" }
        $identities[$key]=$true; $names[$nameKey]=$true; $runs[$row.run_id]=$true
        $source = Resolve-KahuaPath $row.source_zip
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Source ZIP missing: $source" }
        if (Test-KahuaPathOverlap $source $state) { throw 'Source ZIP must be outside batch state.' }
        if ($sources.ContainsKey($source)) { throw "The same source ZIP is mapped more than once: $source" }
        $sources[$source]=$true
        $sync = Resolve-KahuaPath $row.sync_root
        if (Test-KahuaPathOverlap $state $sync) { throw 'Batch state must be outside every sync directory.' }
        if (Test-KahuaPathOverlap $source $sync) { throw 'Source ZIP must be outside every sync directory.' }
        if ([IO.Path]::GetPathRoot($state) -ine [IO.Path]::GetPathRoot($sync)) { throw 'Batch state and sync must be on the same volume.' }
        foreach ($prior in $result) {
            if (Test-KahuaPathOverlap $prior.sync_root $sync) { throw 'CSV sync directories overlap.' }
            if (Test-KahuaPathOverlap $prior.source_zip $sync) { throw 'A source ZIP is inside another sync directory.' }
            if (Test-KahuaPathOverlap $source $prior.sync_root) { throw 'A source ZIP is inside another sync directory.' }
        }
        $result += [pscustomobject][ordered]@{run_id=$row.run_id; environment=$row.environment; domain=$row.domain;
            project_number=$row.project_number; project_name=$row.project_name; source_zip=$source;
            source_prefix=$row.source_prefix; sync_root=$sync; state_root=(Join-Path (Join-Path $state 'projects') $row.run_id);
            destination_confirmed=$false}
    }
    if (@($result | ForEach-Object { $_.environment + '|' + $_.domain } | Select-Object -Unique).Count -ne 1) {
        throw 'Use a separate CSV for each environment and domain; sign into that destination before running.'
    }
    return $result
}

function Test-KahuaDownloadedFiles {
    param($Manifest, [object[]]$Receipts, [string]$DownloadRoot, [switch]$AllowMissing,
        [string]$StagingRoot, [hashtable]$Identity)
    $root = Resolve-KahuaPath $DownloadRoot
    foreach ($forbidden in @($Manifest.binding.sync_root, $Manifest.binding.source_zip)) {
        if (Test-KahuaPathOverlap $root (Resolve-KahuaPath $forbidden)) { throw 'Independent downloads overlap the source or sync path.' }
    }
    if ($StagingRoot -and (Test-KahuaPathOverlap $root (Resolve-KahuaPath $StagingRoot))) {
        throw 'Independent downloads overlap staging.'
    }
    $byPath = @{}
    $downloadPaths = @{}
    foreach ($receipt in $Receipts) {
        if ($Identity) {
            foreach ($pair in @(@('Environment','environment'),@('Domain','domain'),@('Number','project_number'),@('Name','project_name'))) {
                if (-not $receipt.PSObject.Properties[$pair[1]] -or $receipt.($pair[1]) -cne $Identity[$pair[0]]) {
                    throw 'Download receipt identity does not match the selected project.'
                }
            }
        }
        if ($byPath.ContainsKey($receipt.relative_path)) { throw "Duplicate download receipt: $($receipt.relative_path)" }
        $byPath[$receipt.relative_path] = $receipt
    }
    if ($byPath.Count -ne @($Manifest.files).Count) { throw 'Download receipts do not cover the complete manifest.' }
    $verified = @()
    foreach ($file in $Manifest.files) {
        if (-not $byPath.ContainsKey($file.relative_path)) { throw "Missing download receipt: $($file.relative_path)" }
        $receipt = $byPath[$file.relative_path]
        if ($receipt.relative_path -cne $file.relative_path) { throw 'Downloaded path casing differs from the source.' }
        if ($receipt.found -isnot [bool]) { throw 'Download receipt needs an explicit found boolean.' }
        if (-not $receipt.found) {
            if (-not $AllowMissing) { throw "File missing from Kahua: $($file.relative_path)" }
            $verified += [pscustomobject]@{relative_path=$file.relative_path; status='absent'; bytes=$null; sha256=$null}
            continue
        }
        if ($receipt.action -cne 'Download') { throw 'An original-file Download receipt is required.' }
        $path = Resolve-KahuaPath $receipt.download_path
        if ($downloadPaths.ContainsKey($path)) { throw 'The same downloaded file cannot verify multiple server paths.' }
        $downloadPaths[$path]=$true
        if (-not $path.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Downloaded file is outside this attempt directory.' }
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Downloaded file missing: $path" }
        $length = (Get-Item -LiteralPath $path).Length
        $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($length -ne $file.bytes -or $hash -cne $file.sha256) { throw "Kahua content conflict: $($file.relative_path)" }
        $verified += [pscustomobject]@{relative_path=$file.relative_path; status='verified'; bytes=$length; sha256=$hash;
            download_path=$path; version=$receipt.version}
    }
    return [pscustomobject]@{server_verified=(@($verified | Where-Object status -ne 'verified').Count -eq 0);
        checked_at_utc=[DateTime]::UtcNow.ToString('o'); files=$verified}
}

Export-ModuleMember -Function *-Kahua*
