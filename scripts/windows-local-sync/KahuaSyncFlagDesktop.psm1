#requires -Version 5.1
# Windows UI adapter for the named project's own Folders switch.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
# A forced nested reload removes helpers already imported by the coordinator.
Import-Module (Join-Path $PSScriptRoot 'KahuaDesktop.psm1')
Import-Module (Join-Path $PSScriptRoot 'KahuaSyncFlags.psm1')

function Assert-KahuaFlagEnvironment {
    param([string]$Environment)
    $window = Get-KahuaWindow
    # The footer can be clipped on a smaller RDP viewport. Read the exact
    # environment text in this verified Kahua process even when offscreen.
    $condition = [Windows.Automation.AndCondition]::new(
        [Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::NameProperty,$Environment),
        [Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::ControlTypeProperty,[Windows.Automation.ControlType]::Text))
    $labels = @(Select-KahuaUniqueElement @($window.FindAll([Windows.Automation.TreeScope]::Descendants,$condition)))
    if ($labels.Count -ne 1) { throw "The Kahua window does not expose one exact environment label '$Environment'." }
}

function Get-KahuaFlagBreadcrumbDomain {
    $window = Get-KahuaWindow
    $home = Find-KahuaControl -Id HomeButton -Name Home -Type Button -Root $window
    $content = Find-KahuaControl -Id ShellContentFrame -Root $window
    $top = $home.Current.BoundingRectangle.Bottom
    $bottom = $content.Current.BoundingRectangle.Top
    $crumbs = @(Find-KahuaControl -Type Button -Root $window -All | Where-Object {
        $_.Current.Name.StartsWith('Open project descendants for ',[StringComparison]::Ordinal) -and
        $_.Current.BoundingRectangle.Top -ge $top -and $_.Current.BoundingRectangle.Bottom -le $bottom
    } | Sort-Object { $_.Current.BoundingRectangle.Left })
    if (-not $crumbs.Count) { throw 'Enable My settings > General > Enable Full Navigation Breadcrumb so the selected domain can be verified.' }
    $name = $crumbs[0].Current.Name.Substring('Open project descendants for '.Length)
    if ([string]::IsNullOrWhiteSpace($name)) { throw 'The domain breadcrumb is unreadable.' }
    return $name
}

function Confirm-KahuaFlagDomain {
    param([string]$Environment,[string]$Domain)
    Assert-KahuaFlagEnvironment -Environment $Environment
    $actual = Get-KahuaFlagBreadcrumbDomain
    if ($actual -cne $Domain) { throw "Selected domain '$actual' differs from expected '$Domain'." }
}

function Get-KahuaFlagHomeDomain {
    $root = Wait-KahuaControl -Id ShellContentFrame -Name 'Home Dashboard Main Content' -Type Group
    $help = Wait-KahuaControl -Id HelpCenterButton -Type Button -Root $root
    $bounds = $help.Current.BoundingRectangle
    # The current domain heading is the unique text on the Help Center row,
    # inside the home content. Exclude the separate project breadcrumb above it.
    $labels = @(Find-KahuaControl -Type Text -Root $root -All | Where-Object {
        $r=$_.Current.BoundingRectangle
        -not [string]::IsNullOrWhiteSpace($_.Current.Name) -and $r.Right -le $bounds.Left -and
            $r.Top -lt $bounds.Bottom -and $r.Bottom -gt $bounds.Top
    })
    if ($labels.Count -ne 1) { throw 'Cannot uniquely read the selected domain on the home page.' }
    return $labels[0].Current.Name
}

function Initialize-KahuaFlagDomain {
    param([string]$Environment,[string]$Domain,[switch]$Switch)
    Assert-KahuaFlagEnvironment -Environment $Environment
    if (@(Find-KahuaControl -Id CloseModal -Type Button -All).Count) { throw 'Close the existing Kahua dialog before domain selection.' }
    Invoke-KahuaControl (Find-KahuaControl -Id HomeButton -Name Home -Type Button)
    $actual = Get-KahuaFlagHomeDomain
    if ($Switch -and $actual -cne $Domain) {
        if ([string]::IsNullOrWhiteSpace($Domain)) { throw 'An exact CSV domain is required for automatic domain selection.' }
        $root = Find-KahuaControl -Id ShellContentFrame -Name 'Home Dashboard Main Content' -Type Group
        $change = @(Find-KahuaControl -Name 'Change Domain' -Type Button -Root $root -All)
        if ($change.Count -ne 1) { throw "The home page does not offer Change Domain for '$Domain'. Check this account's domain access." }
        Invoke-KahuaControl $change[0]
        $target = Wait-KahuaControl -Name $Domain -Type ListItem
        Invoke-KahuaControl $target -Click
        $deadline = [DateTime]::UtcNow.AddSeconds(30)
        do {
            $actual = Get-KahuaFlagHomeDomain
            if ($actual -ceq $Domain) { break }
            if ([DateTime]::UtcNow -ge $deadline) { throw 'The requested domain did not become selected.' }
            Start-Sleep -Milliseconds 250
        } while ($true)
    }
    if ($Domain -and $actual -cne $Domain) { throw "Doe's selected domain '$actual' differs from expected '$Domain'." }
    Confirm-KahuaFlagDomain -Environment $Environment -Domain $actual
    return $actual
}

function Get-KahuaSearchProjectRows {
    param($Search)
    $container = $Search
    for ($level=0; $level -lt 8; $level++) {
        $container = [Windows.Automation.TreeWalker]::RawViewWalker.GetParent($container)
        if (-not $container) { break }
        $lists = @(Find-KahuaControl -Type List -Root $container -All)
        if ($lists.Count -gt 1) { throw 'Project search exposes multiple result lists.' }
        if ($lists.Count -eq 1) {
            $list = $lists[0]
            # A virtualized or scrollable result list cannot prove uniqueness
            # from its visible rows alone. Refuse it instead of picking first.
            $scroll = $null
            $reason = ''
            if ($list.TryGetCurrentPattern([Windows.Automation.ScrollPattern]::Pattern,[ref]$scroll) -and
                $scroll.Current.VerticallyScrollable) {
                $reason = 'Project search results are scrollable; visible rows alone cannot prove uniqueness.'
            }
            $rows = @($list.FindAll([Windows.Automation.TreeScope]::Descendants,
                [Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::ControlTypeProperty,
                    [Windows.Automation.ControlType]::ListItem)))
            $rows = @(Select-KahuaUniqueElement $rows)
            if (@($rows | Where-Object { $_.Current.IsOffscreen }).Count) {
                $reason = 'Project search has offscreen results; cannot establish a unique exact name.'
            }
            # The documented recent-project list is capped at twenty. Until
            # search pagination is calibrated, conservatively reject this many
            # search rows too, even when no scrollbar is exposed.
            if ($rows.Count -ge 20) { $reason = 'Project search reached the conservative 20-result limit; use a more distinctive exact name.' }
            return [pscustomobject]@{rows=$rows;complete=(-not $reason);reason=$reason}
        }
        if ($container.Current.ControlType -eq [Windows.Automation.ControlType]::Window) { break }
    }
    # Search can briefly remove its old list while fetching new results.
    return [pscustomobject]@{rows=@();complete=$false;reason='Project search has not exposed its result list.'}
}

function Select-KahuaFlagProject {
    param([string]$Environment,[string]$Domain,[string]$Name)
    Confirm-KahuaFlagDomain -Environment $Environment -Domain $Domain
    if (@(Find-KahuaControl -Id CloseModal -Type Button -All).Count) { throw 'Close the existing Kahua dialog before project search.' }
    # The selected-project header can collapse so far that its picker opens
    # outside the usable viewport. Home provides the full-width search entry.
    Invoke-KahuaControl (Find-KahuaControl -Id HomeButton -Name Home -Type Button)
    [void](Wait-KahuaControl -Id ShellContentFrame -Name 'Home Dashboard Main Content' -Type Group)
    Confirm-KahuaFlagDomain -Environment $Environment -Domain $Domain
    $selectors = @(Find-KahuaControl -Id Shell_HomePartitionSelector -Name 'Select a Project' -Type Button -All)
    if ($selectors.Count -ne 1) { throw 'Cannot uniquely identify the project search control.' }
    Invoke-KahuaControl $selectors[0]
    $search = Wait-KahuaControl -Id SearchBox -Type Edit
    Set-KahuaValue $search $Name
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    $previous = ''; $stableSince = [DateTime]::UtcNow
    do {
        $search = Wait-KahuaControl -Id SearchBox -Type Edit
        $snapshot = Get-KahuaSearchProjectRows -Search $search
        $results = @($snapshot.rows)
        $candidates = @()
        foreach ($result in $results) {
            $resolved = Resolve-KahuaProjectDisplay -Display $result.Current.Name -Name $Name
            if ($resolved) { $candidates += [pscustomobject]@{control=$result;identity=$resolved} }
        }
        if ($candidates.Count -gt 1) { throw "More than one project has the exact name '$Name'. No project was selected." }
        $fingerprint = $snapshot.complete.ToString() + ':' + $snapshot.reason + ':' +
            (($results | ForEach-Object { ($_.GetRuntimeId() -join ',') + ':' + $_.Current.Name }) -join '|')
        if ($fingerprint -cne $previous) { $previous=$fingerprint; $stableSince=[DateTime]::UtcNow }
        if ($candidates.Count -eq 1 -and ([DateTime]::UtcNow-$stableSince).TotalSeconds -ge 1.5) {
            if (-not $snapshot.complete) { throw $snapshot.reason }
            break
        }
        if ([DateTime]::UtcNow -ge $deadline) { throw "No unique, stable project search result for exact name '$Name'. Enable My settings > General > Show Project Number; verify the exact name and access." }
        Start-Sleep -Milliseconds 250
    } while ($true)
    $chosen = $candidates[0]
    Invoke-KahuaControl $chosen.control -Click
    $display = $chosen.identity.display
    [void](Wait-KahuaControl -Id Shell_PartitionSelector -Name "Current Project $display" -Type Button)
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    while (@(Find-KahuaControl -Id SearchBox -Type Edit -All).Count) {
        if ([DateTime]::UtcNow -ge $deadline) { throw 'Project search did not close.' }
        Start-Sleep -Milliseconds 250
    }
    Confirm-KahuaFlagDomain -Environment $Environment -Domain $Domain
    [pscustomobject]@{environment=$Environment;domain=$Domain;project_name=$Name;
        project_number=$chosen.identity.project_number;display=$display}
}

function Assert-KahuaFlagProject {
    param($Identity)
    Confirm-KahuaFlagDomain -Environment $Identity.environment -Domain $Identity.domain
    [void](Find-KahuaControl -Id Shell_PartitionSelector -Name "Current Project $($Identity.display)" -Type Button)
}

function Get-KahuaRootFlagToggle {
    param([switch]$AllowDisabled)
    # The row named Folders represents only the selected project's file tree.
    # Never click Folders to drill down or Change to select a parent/child project.
    $buttons = @(Find-KahuaControl -Name Folders -Type Button -All)
    $toggles = @()
    foreach ($button in $buttons) {
        $row = $button
        for ($level=0; $row -and $level -lt 8; $level++) {
            if ($row.Current.ControlType -eq [Windows.Automation.ControlType]::DataItem) { break }
            $row = [Windows.Automation.TreeWalker]::ControlViewWalker.GetParent($row)
        }
        if (-not $row -or $row.Current.ControlType -ne [Windows.Automation.ControlType]::DataItem) { continue }
        $toggles += @($row.FindAll([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.Condition]::TrueCondition) |
            Where-Object { $_.GetSupportedPatterns() -contains [Windows.Automation.TogglePattern]::Pattern })
    }
    $toggles = @(Select-KahuaUniqueElement $toggles)
    if ($toggles.Count -ne 1) { throw "Expected the selected project's single Folders switch; found $($toggles.Count). Reopen Local Files at the project root." }
    if ($toggles[0].Current.IsOffscreen) { throw 'The project Folders switch is unavailable.' }
    if (-not $toggles[0].Current.IsEnabled) {
        # Save temporarily disables this control. Only its read-only waiter
        # accepts that state, as a null result which cannot count as settled.
        if ($AllowDisabled) { return $null }
        throw 'The project Folders switch is unavailable.'
    }
    return $toggles[0].GetCurrentPattern([Windows.Automation.TogglePattern]::Pattern)
}

function Open-KahuaFlagSettings {
    param($Identity)
    Assert-KahuaFlagProject -Identity $Identity
    if (@(Find-KahuaControl -Id CloseModal -Type Button -All).Count) { throw 'Close the existing settings/dialog before continuing.' }
    Invoke-KahuaControl (Find-KahuaControl -Id Shell_Settings -Type Button)
    Invoke-KahuaControl (Wait-KahuaControl -Id TitleText -Name 'My settings' -Type Text) -Click
    Invoke-KahuaControl (Wait-KahuaControl -Id ButtonText -Name 'Local Files' -Type Text) -Click
    $state = Wait-KahuaLocalFilesState
    if ($state -eq 'unconfigured') { throw 'Enable Local Files for the account manually once, then rerun. The project flag runner never initializes or resets account sync.' }
    $change = Wait-KahuaControl -Name Change -Type Button
    $container = $change; $matched = $false
    for ($level=0; $level -lt 4; $level++) {
        $container = [Windows.Automation.TreeWalker]::RawViewWalker.GetParent($container)
        if (-not $container -or $container.Current.ControlType -eq [Windows.Automation.ControlType]::Window) { break }
        if (@(Find-KahuaControl -Name $Identity.display -Type Text -Root $container -All).Count -eq 1) { $matched=$true; break }
    }
    if (-not $matched) { throw 'Local Files is showing a different project.' }
    [void](Wait-KahuaControl -Name Folders -Type Button)
    return $state
}

function Assert-KahuaFlagNoUnsavedWarning {
    $window = Get-KahuaWindow
    if (@(Find-KahuaControl -Name 'Unsaved Changes Alert' -Type Text -Root $window -All).Count) {
        throw 'Kahua reports unsaved changes after Save. The result is uncertain; inspect the settings and reopen them before resuming.'
    }
}

function Wait-KahuaFlagSave {
    param([string]$Desired, [int]$TimeoutSeconds = 30, [int]$SettledMilliseconds = 2000)
    # Save is disabled while the request is in flight as well as after it
    # finishes. One disabled sample is not evidence that closing is safe.
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $quietSince = $null
    do {
        Assert-KahuaFlagNoUnsavedWarning
        $save = Find-KahuaControl -Name Save -Type Button
        $cancel = Find-KahuaControl -Name Cancel -Type Button
        $close = Find-KahuaControl -Id CloseModal -Type Button
        $toggle = Get-KahuaRootFlagToggle -AllowDisabled
        if ($null -ne $toggle -and $toggle.Current.ToggleState.ToString() -cne $Desired) {
            throw 'Project sync selection changed during Save. The result is uncertain; inspect the settings before resuming.'
        }
        if ($null -ne $toggle -and -not $save.Current.IsEnabled -and -not $cancel.Current.IsEnabled -and $close.Current.IsEnabled) {
            if ($null -eq $quietSince) { $quietSince = $watch.ElapsedMilliseconds }
            if ($watch.ElapsedMilliseconds - $quietSince -ge $SettledMilliseconds) { return }
        } else { $quietSince = $null }
        if ($watch.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
            throw 'Project sync Save did not settle. The result is uncertain; inspect the settings before resuming.'
        }
        Start-Sleep -Milliseconds 250
    } while ($true)
}

function Close-KahuaFlagSettings {
    Assert-KahuaFlagNoUnsavedWarning
    Invoke-KahuaControl (Find-KahuaControl -Id CloseModal -Type Button)
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    do {
        # Leave a recognized dirty-state warning open. Never answer it by
        # saving again or discarding an outcome the runner has not reconciled.
        Assert-KahuaFlagNoUnsavedWarning
        if (-not @(Find-KahuaControl -Id CloseModal -Type Button -All).Count) { return }
        if ([DateTime]::UtcNow -ge $deadline) { throw 'Settings did not close. Inspect the dialog before resuming.' }
        Start-Sleep -Milliseconds 250
    } while ($true)
}

function Read-KahuaFlagSelection {
    param($Identity)
    $state = Open-KahuaFlagSettings -Identity $Identity
    $save = Find-KahuaControl -Name Save -Type Button
    if ($save.Current.IsEnabled) { throw 'Local Files has unsaved edits. Resolve them manually before running.' }
    $selection = (Get-KahuaRootFlagToggle).Current.ToggleState.ToString()
    $paths = @(Find-KahuaControl -Type Button -All | Where-Object { $_.Current.Name -match '^[A-Za-z]:\\' })
    if ($paths.Count -ne 1) { throw 'Cannot read the Local Files base directory.' }
    $syncRoot = Join-Path (Join-Path $paths[0].Current.Name ('Kahua - '+$Identity.domain)) $Identity.project_name
    Close-KahuaFlagSettings
    [pscustomobject]@{selection=$selection;account_sync_state=$state;sync_root=$syncRoot}
}

function Get-KahuaProjectFlag {
    param($Identity)
    [void](Read-KahuaFlagSelection -Identity $Identity)
    $fresh = Read-KahuaFlagSelection -Identity $Identity
    [pscustomobject]@{environment=$Identity.environment;domain=$Identity.domain;project_name=$Identity.project_name;
        project_number=$Identity.project_number;display=$Identity.display;scope='project-files';
        selection=$fresh.selection;account_sync_state=$fresh.account_sync_state;sync_root=$fresh.sync_root;
        reopened=$true;verified_at_utc=[DateTime]::UtcNow.ToString('o')}
}

function Set-KahuaProjectFlag {
    param($Identity,[Parameter(Mandatory=$true)][bool]$Enabled)
    $state = Open-KahuaFlagSettings -Identity $Identity
    $save = Find-KahuaControl -Name Save -Type Button
    if ($save.Current.IsEnabled) { throw 'Local Files has unsaved edits. Resolve them manually before running.' }
    if ($Enabled -and $state -eq 'paused') { throw 'Account sync is paused. Review and resume it manually before enabling project sync.' }
    $desired = if ($Enabled) { 'On' } else { 'Off' }
    $before = (Get-KahuaRootFlagToggle).Current.ToggleState.ToString()
    $changed = $before -cne $desired
    for ($step=0; $step -lt 3; $step++) {
        $toggle = Get-KahuaRootFlagToggle
        if ($toggle.Current.ToggleState.ToString() -ceq $desired) { break }
        $toggle.Toggle()
        Start-Sleep -Milliseconds 300
    }
    if ((Get-KahuaRootFlagToggle).Current.ToggleState.ToString() -cne $desired) { throw 'The project Folders switch did not reach the requested value. No Save was attempted.' }
    if ($changed) {
        Invoke-KahuaControl (Wait-KahuaControl -Name Save -Type Button)
        Wait-KahuaFlagSave -Desired $desired
    }
    Close-KahuaFlagSettings
    $fresh = Read-KahuaFlagSelection -Identity $Identity
    if ($fresh.selection -cne $desired) { throw 'The saved project sync flag did not persist after reopening.' }
    [pscustomobject]@{environment=$Identity.environment;domain=$Identity.domain;project_name=$Identity.project_name;
        project_number=$Identity.project_number;display=$Identity.display;scope='project-files';
        selection=$fresh.selection;account_sync_state=$fresh.account_sync_state;sync_root=$fresh.sync_root;
        changed=$changed;previous_selection=$before;reopened=$true;verified_at_utc=[DateTime]::UtcNow.ToString('o')}
}

Export-ModuleMember -Function Initialize-KahuaFlagDomain,Confirm-KahuaFlagDomain,Select-KahuaFlagProject,Get-KahuaProjectFlag,Set-KahuaProjectFlag
