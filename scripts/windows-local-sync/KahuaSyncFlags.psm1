#requires -Version 5.1
# Input and evidence helpers for the project-level flag runner. No desktop access.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function New-KahuaFlagLogContext {
    param([string]$Mode)
    # Pass the context explicitly so nested module imports and subsequent runs
    # cannot accidentally reuse a previous attempt's file or project identity.
    return @{
        Path=''; Mode=$Mode; Phase='Startup'; Index=0; Total=0; Project=''; Step='startup'
        Watch=[Diagnostics.Stopwatch]::StartNew()
        SinceMessage=[Diagnostics.Stopwatch]::StartNew()
        LastProgressAtUtc=''
        Pending=(New-Object 'System.Collections.Generic.List[string]')
        Recoveries=(New-Object 'System.Collections.Generic.List[object]')
    }
}

function Write-KahuaFlagProgress {
    param([hashtable]$Context, [string]$Step, [string]$Message,
        [ValidateSet('INFO','WARN','ERROR')][string]$Level='INFO', [switch]$Heartbeat)
    if ($null -eq $Context) { return }
    # Polling remains on the UI thread; a wait message means a fresh polling
    # iteration occurred, not that a background timer thinks Kahua is healthy.
    if ($Heartbeat -and $Context.SinceMessage.Elapsed.TotalSeconds -lt 5) { return }
    if ($Step) { $Context.Step=$Step }
    $Context.LastProgressAtUtc=[DateTime]::UtcNow.ToString('o')
    $scope="$($Context.Mode)/$($Context.Phase)"
    if ($Context.Index -gt 0) { $scope+=" $($Context.Index)/$($Context.Total) | $($Context.Project)" }
    $messageLine=$Message -replace '[\r\n]+',' '
    $line=('{0} [+{1:0.0}s] [{2}] [{3}] {4}: {5}' -f
        [DateTimeOffset]::Now.ToString('yyyy-MM-dd HH:mm:ss zzz'),$Context.Watch.Elapsed.TotalSeconds,
        $Level,$scope,$Context.Step,$messageLine)
    if ($Context.Path) {
        # Append/close on every event so another PowerShell window can tail it.
        # Persist before the next UI action; a failed log write stops the run.
        $pendingText=if ($Context.Pending.Count) { ($Context.Pending -join [Environment]::NewLine)+[Environment]::NewLine } else { '' }
        [IO.File]::AppendAllText($Context.Path,$pendingText+$line+[Environment]::NewLine,[Text.UTF8Encoding]::new($false))
        $Context.Pending.Clear()
    } else { $Context.Pending.Add($line) }
    # Never put diagnostic strings on the success stream used for receipts.
    Write-Host $line
    $Context.SinceMessage.Restart()
}

function Read-KahuaSyncFlagCsv {
    param([string]$CsvPath, [string]$Environment, [string]$Domain,
        [ValidateSet('Enable','Disable','FromCsv')][string]$Action = 'FromCsv')
    foreach ($entry in @(@('Environment',$Environment),@('Domain',$Domain))) {
        if (($entry[0] -eq 'Environment' -or $entry[1]) -and
            ([string]::IsNullOrWhiteSpace($entry[1]) -or $entry[1] -cne $entry[1].Trim() -or $entry[1] -match '[\x00-\x1f]')) {
            throw "Supply an exact, nonempty $($entry[0]) without leading/trailing whitespace."
        }
    }
    $rows = @(Import-Csv -LiteralPath $CsvPath -Encoding UTF8)
    if (-not $rows.Count) { throw 'The CSV contains no project rows.' }
    $csvHasDomain = [bool]$rows[0].PSObject.Properties['domain']
    $filledDomains = if ($csvHasDomain) { @($rows | Where-Object { -not [string]::IsNullOrWhiteSpace($_.domain) }).Count } else { 0 }
    if ($filledDomains -gt 0 -and $filledDomains -ne $rows.Count) {
        throw 'Fill domain for every CSV row, or omit/leave it blank for every row so Doe selects the domain.'
    }
    $autoSelectDomain = $filledDomains -gt 0
    $seen = @{}; $result = @(); $batchDomain = $Domain
    foreach ($row in $rows) {
        $line = $result.Count + 2
        $name = if ($row.PSObject.Properties['project_name']) { [string]$row.project_name } else { '' }
        if ([string]::IsNullOrWhiteSpace($name) -or $name -cne $name.Trim() -or $name -match '[\x00-\x1f]') {
            throw "Row ${line}: project_name must be an exact, nonempty name without leading/trailing whitespace."
        }
        $rowDomain = if ($autoSelectDomain) { [string]$row.domain } else { $Domain }
        if ($rowDomain -and ($rowDomain -cne $rowDomain.Trim() -or $rowDomain -match '[\x00-\x1f]')) {
            throw "Row ${line}: domain must be exact, without surrounding whitespace or control characters."
        }
        if (-not $batchDomain) { $batchDomain = $rowDomain }
        if ($rowDomain -cne $batchDomain) { throw 'Use one exact domain per batch; -Domain must agree with every CSV row.' }
        # These names form Kahua's fixed local directory. Reject an ambiguous
        # Windows mapping even though this runner never writes into that directory.
        foreach ($part in @($rowDomain,$name)) {
            if ($part -match '[<>:"/\\|?*]' -or $part -match '[. ]$' -or
                $part -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)') {
                throw "Row ${line}: the domain/project name has an unsupported Windows directory mapping."
            }
        }
        if ($seen.ContainsKey($name)) { throw "Duplicate project_name in CSV: $name" }
        $seen[$name] = $true
        $csvEnabled = $null
        if ($row.PSObject.Properties['sync_enabled']) {
            $value = [string]$row.sync_enabled
            if ($value -notmatch '^(?i:true|false)$') { throw "Row ${line}: sync_enabled must be true or false." }
            $csvEnabled = $value -ieq 'true'
        }
        if ($Action -eq 'FromCsv') {
            if ($null -eq $csvEnabled) { throw 'Add a sync_enabled column (true/false), or choose -Action Enable or -Action Disable.' }
            $enabled = $csvEnabled
        } else {
            $enabled = $Action -eq 'Enable'
            if ($null -ne $csvEnabled -and $csvEnabled -ne $enabled) { throw "Row ${line}: sync_enabled conflicts with -Action $Action." }
        }
        # project_uid is deliberately not an identifier: Doe's supplied values
        # are placeholders. Resolve identity from the exact name in Kahua.
        $result += [pscustomobject][ordered]@{row=$line;environment=$Environment;domain=$rowDomain;auto_select_domain=[bool]$autoSelectDomain;
            project_name=$name;sync_enabled=[bool]$enabled;scope='project-files'}
    }
    return $result
}

function Set-KahuaFlagDomainBinding {
    param([string]$StateRoot, [object[]]$Rows, [string]$ActualDomain)
    if ([string]::IsNullOrWhiteSpace($ActualDomain) -or $ActualDomain -cne $ActualDomain.Trim() -or
        $ActualDomain -match '[<>:"/\\|?*\x00-\x1f]' -or $ActualDomain -match '[. ]$') {
        throw 'Cannot bind an unreadable or unsupported selected domain.'
    }
    if ($Rows[0].domain -and $Rows[0].domain -cne $ActualDomain) {
        throw 'The selected Kahua domain does not match the requested domain.'
    }
    $binding = [ordered]@{schema=1;environment=$Rows[0].environment;domain=$ActualDomain}
    $path = Join-Path $StateRoot 'selected-domain.json'
    if (Test-Path -LiteralPath $path) {
        $previous = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
        if (($previous | ConvertTo-Json -Compress) -cne ($binding | ConvertTo-Json -Compress)) {
            throw 'The selected domain differs from this saved run. Select the original domain, or supply -Domain with the intended exact domain to select separate automatic history. For an explicit StateRoot, use a new folder for the changed domain.'
        }
    } else { Write-KahuaFlagJson $path $binding }
    foreach ($row in $Rows) { $row.domain = $ActualDomain }
}

function Resolve-KahuaProjectDisplay {
    param([string]$Display, [string]$Name)
    # Split only the first separator. Suffix matching would mistake
    # '001 - West - Park' for a project named 'Park'.
    # Require Show Project Number in Kahua. Accepting a bare display name
    # could confuse project 'Seattle Heights' numbered 220 with the distinct
    # project whose actual name is '220 - Seattle Heights'.
    $separator = $Display.IndexOf(' - ', [StringComparison]::Ordinal)
    if ($separator -gt 0 -and $Display.Substring($separator + 3) -ceq $Name) {
        return [pscustomobject]@{display=$Display;project_name=$Name;project_number=$Display.Substring(0,$separator)}
    }
    return $null
}

function Assert-KahuaSyncFlagReceipt {
    param($Receipt, $Row, [switch]$RequireDesired, [switch]$RequireRunning)
    foreach ($key in @('environment','domain','project_name','scope')) {
        if (-not $Receipt.PSObject.Properties[$key] -or $Receipt.$key -cne $Row.$key) {
            throw "Sync receipt does not match the requested $key."
        }
    }
    if (-not $Receipt.PSObject.Properties['reopened'] -or $Receipt.reopened -isnot [bool] -or -not $Receipt.reopened) {
        throw 'Sync receipt must confirm a fresh read after reopening settings.'
    }
    if (-not $Receipt.PSObject.Properties['selection'] -or $Receipt.selection -cnotin @('On','Off','Indeterminate')) {
        throw 'Sync receipt has no recognized project selection state.'
    }
    if (-not $Receipt.PSObject.Properties['account_sync_state'] -or $Receipt.account_sync_state -cnotin @('running','paused')) {
        throw 'Sync receipt has no recognized account sync state.'
    }
    if ($RequireRunning -and $Row.sync_enabled -and $Receipt.account_sync_state -ne 'running') {
        throw 'Account sync is paused. Review and resume it manually before enabling project sync.'
    }
    if ($RequireDesired) {
        $desired = if ($Row.sync_enabled) { 'On' } else { 'Off' }
        if ($Receipt.selection -cne $desired) { throw 'Saved project sync flag differs from the requested value.' }
    }
}

function Write-KahuaFlagJson {
    param([string]$Path, $Value)
    $temporary = "$Path.$([Guid]::NewGuid().ToString('N')).tmp"
    [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth 20), [Text.UTF8Encoding]::new($false))
    if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($temporary,$Path,[NullString]::Value) }
    else { [IO.File]::Move($temporary,$Path) }
}

function Get-KahuaFlagHistoryRoot {
    $base = [Environment]::GetFolderPath('LocalApplicationData')
    if ([string]::IsNullOrWhiteSpace($base)) {
        throw 'Cannot locate local application data. Supply -HistoryRoot with a local folder outside Kahua sync.'
    }
    return (Join-Path (Join-Path $base 'KahuaMigration') 'SyncFlags')
}

function Get-KahuaFlagInputHash {
    param([object[]]$Rows)
    # Use the same normalized intent as binding.json. Filenames, CSV formatting,
    # and ignored UIDs do not identify a batch. Row order remains significant.
    # Keep the full digest and still validate binding.json under the state lock.
    $binding = [ordered]@{schema=2;projects=@($Rows)} | ConvertTo-Json -Depth 20 -Compress
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $digest = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($binding))).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
    return $digest
}

function Resolve-KahuaFlagBatchState {
    param([object[]]$Rows, [string]$StateRoot, [string]$HistoryRoot)
    if ($StateRoot -and $HistoryRoot) { throw 'Choose -StateRoot for an existing batch or -HistoryRoot for automatic history, not both.' }
    if ($StateRoot) { return (Resolve-KahuaFlagStatePath $StateRoot) }
    if (-not $HistoryRoot) { $HistoryRoot = Get-KahuaFlagHistoryRoot }
    $digest=Get-KahuaFlagInputHash -Rows $Rows
    return (Resolve-KahuaFlagStatePath (Join-Path $HistoryRoot ('batch-'+$digest)))
}

function Test-KahuaFlagTransientFailure {
    param([Exception]$Exception)
    # Do not infer recoverability from an error message. Identity, permissions,
    # duplicate names, dirty settings and application warnings must stop.
    while ($null -ne $Exception) {
        if ($Exception.GetType().FullName -eq 'System.Windows.Automation.ElementNotAvailableException') { return $true }
        if ($Exception -is [TimeoutException] -and $Exception.Data['KahuaFlagReadTimeout'] -eq $true) { return $true }
        $Exception = $Exception.InnerException
    }
    return $false
}

function Invoke-KahuaFlagReadRecovery {
    param([Parameter(Mandatory=$true)][scriptblock]$Read,
        [ValidateRange(0,3)][int]$RecoveryAttempts=2, [hashtable]$LogContext,
        [string]$Operation='Reading Kahua', [ValidateRange(0,5000)][int]$DelayMilliseconds=500)
    # Only wrap fresh, read-only snapshots. Never wrap navigation, toggle or Save.
    for ($retry=0; ; $retry++) {
        try { return (& $Read) }
        catch {
            if ($retry -ge $RecoveryAttempts -or -not (Test-KahuaFlagTransientFailure $_.Exception)) { throw }
            $event=[pscustomobject]@{operation=$Operation;retry=$retry+1;error=$_.Exception.Message;at_utc=[DateTime]::UtcNow.ToString('o')}
            if ($LogContext) { $LogContext.Recoveries.Add($event) }
            Write-KahuaFlagProgress $LogContext 'read-recovery' "$Operation`: temporary UI read failure; retry $($retry+1)/$RecoveryAttempts." -Level WARN
            Start-Sleep -Milliseconds $DelayMilliseconds
        }
    }
}

function Resolve-KahuaFlagStatePath {
    param([string]$Path)
    $existing = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
    $tail = New-Object 'System.Collections.Generic.Stack[string]'
    while (-not (Test-Path -LiteralPath $existing)) {
        $tail.Push([IO.Path]::GetFileName($existing))
        $existing = [IO.Path]::GetDirectoryName($existing)
        if (-not $existing) { throw 'StateRoot needs an existing local parent directory.' }
    }
    $resolved = (Resolve-Path -LiteralPath $existing).ProviderPath
    if ($env:OS -eq 'Windows_NT') {
        $ancestor = $resolved
        while ($ancestor) {
            if ((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'StateRoot must not use a symbolic link or junction.'
            }
            $ancestor = [IO.Path]::GetDirectoryName($ancestor)
        }
    }
    foreach ($part in $tail) { $resolved = Join-Path $resolved $part }
    return $resolved
}

Export-ModuleMember -Function *-Kahua*
