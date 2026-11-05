#requires -Version 5.1
# Runs in the signed-in interactive Windows session alongside Kahua.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$script:KahuaBoundingRectangleProperty=[Windows.Automation.AutomationElement]::BoundingRectangleProperty
$script:KahuaNameProperty=[Windows.Automation.AutomationElement]::NameProperty
if (-not ('KahuaMigration.NativeInput' -as [type])) {
    Add-Type @'
using System;
using System.Runtime.InteropServices;
namespace KahuaMigration {
    public static class NativeInput {
        [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
        [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
        [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
        [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint x, uint y, uint d, UIntPtr i);
    }
}
'@
}

function Get-KahuaWindow {
    $windows = @([Windows.Automation.AutomationElement]::RootElement.FindAll(
        [Windows.Automation.TreeScope]::Children,
        [Windows.Automation.PropertyCondition]::new(
            [Windows.Automation.AutomationElement]::NameProperty, 'Kahua for Windows')))
    if ($windows.Count -ne 1) { throw "Expected one Kahua desktop window; found $($windows.Count)." }
    $window = $windows[0]
    if ((Get-Process -Id $window.Current.ProcessId).ProcessName -ne 'kahua.windows') {
        throw 'The named window does not belong to the Kahua desktop process.'
    }
    return $window
}

function Select-KahuaUniqueElement {
    param([object[]]$Elements)
    $seen=@{}
    foreach ($element in $Elements) {
        $key=$element.GetRuntimeId() -join ','
        if (-not $seen.ContainsKey($key)) { $seen[$key]=$true; $element }
    }
}

function Invoke-KahuaReadRetry {
    param([Parameter(Mandatory=$true)][scriptblock]$Read, [int]$TimeoutMilliseconds=3000,
        [switch]$RequireResult, [string]$UnavailableMessage='Kahua did not expose the requested view before the read deadline.')
    # For read-only snapshots only. The caller must acquire fresh elements on
    # every attempt. Never wrap clicks, toggles, Save, or other writes here.
    $watch=[Diagnostics.Stopwatch]::StartNew()
    do {
        try {
            $result=@(& $Read)
            if (-not $RequireResult -or $result.Count) { return $result }
            if ($watch.ElapsedMilliseconds -ge $TimeoutMilliseconds) { throw $UnavailableMessage }
        } catch {
            $exception=$_.Exception; $stale=$false
            while ($exception) {
                if ($exception.GetType().Name -eq 'ElementNotAvailableException') { $stale=$true; break }
                $exception=$exception.InnerException
            }
            if (-not $stale -or $watch.ElapsedMilliseconds -ge $TimeoutMilliseconds) { throw }
        }
        Start-Sleep -Milliseconds 100
    } while ($true)
}

function Get-KahuaControlRectangle {
    param([Parameter(Mandatory=$true)]$Control, [int]$TimeoutMilliseconds=3000)
    # Use a method call so PowerShell surfaces provider exceptions instead of
    # silently turning a failed property getter into null followed by a
    # misleading Width/IsEmpty StrictMode error. Require a complete value;
    # never invent coordinates or click using an old value.
    $watch=[Diagnostics.Stopwatch]::StartNew()
    $reason='no rectangle returned'
    do {
        try {
            $rect=$Control.GetCurrentPropertyValue($script:KahuaBoundingRectangleProperty)
            if ($null -ne $rect) {
                $missing=@('X','Y','Width','Height','Left','Top','Right','Bottom','IsEmpty' | Where-Object {
                    -not $rect.PSObject.Properties[$_]
                })
                if (-not $missing.Count) { return $rect }
                $reason="rectangle type '$($rect.GetType().FullName)' is missing $($missing -join ', ')"
            } else { $reason='no rectangle returned' }
        } catch {
            # An expired element cannot be repaired by rereading its bounds.
            # Preserve the exception so a read-only caller can reacquire it.
            throw
        }
        if ($watch.ElapsedMilliseconds -ge $TimeoutMilliseconds) {
            throw "Kahua control geometry is unavailable after ${TimeoutMilliseconds}ms: $reason. Wait for the view to finish loading, then retry the same run."
        }
        Start-Sleep -Milliseconds 100
    } while ($true)
}

function Get-KahuaControlName {
    param([Parameter(Mandatory=$true)]$Control)
    # Method access preserves expired-element errors that PowerShell's nested
    # Current.Name getter can otherwise turn into a null value.
    return [string]$Control.GetCurrentPropertyValue($script:KahuaNameProperty)
}

function Get-KahuaActiveRoot {
    $window = Get-KahuaWindow
    $views = @($window.FindAll([Windows.Automation.TreeScope]::Descendants,
        [Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::AutomationIdProperty, 'ModalView')) |
        Where-Object { -not $_.Current.IsOffscreen -and (Get-KahuaControlRectangle $_).Width -gt 0 })
    $views=@(Select-KahuaUniqueElement $views)
    if ($views.Count -gt 1) { throw 'Multiple Kahua dialogs are open.' }
    if ($views.Count -eq 1) { return $views[0] }
    $modals = @($window.FindAll([Windows.Automation.TreeScope]::Descendants,
        [Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::AutomationIdProperty, 'ModalRoot')) |
        Where-Object { -not $_.Current.IsOffscreen -and (Get-KahuaControlRectangle $_).Width -gt 0 })
    $modals=@(Select-KahuaUniqueElement $modals)
    if ($modals.Count -gt 1) { throw 'Multiple Kahua dialogs are open; refusing ambiguous controls.' }
    if ($modals.Count -eq 1) { return $modals[0] }
    return $window
}

function Find-KahuaControl {
    param([string]$Id, [string]$Name, [string]$Type, $Root = (Get-KahuaActiveRoot), [switch]$All)
    $conditions = New-Object 'System.Collections.Generic.List[Windows.Automation.Condition]'
    if ($Id) { $conditions.Add([Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::AutomationIdProperty, $Id)) }
    if ($Name) { $conditions.Add([Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::NameProperty, $Name)) }
    if ($Type) {
        $ct = [Windows.Automation.ControlType]::$Type
        if (-not $ct) { throw "Unknown control type: $Type" }
        $conditions.Add([Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::ControlTypeProperty, $ct))
    }
    if ($conditions.Count -eq 0) { throw 'A control selector is required.' }
    $condition = $conditions[0]
    if ($conditions.Count -gt 1) { $condition = [Windows.Automation.AndCondition]::new($conditions.ToArray()) }
    $matches = @($Root.FindAll([Windows.Automation.TreeScope]::Descendants, $condition) |
        Where-Object {
            if ($_.Current.IsOffscreen) { return $false }
            $rect=Get-KahuaControlRectangle $_
            return $rect.Width -gt 0 -and $rect.Height -gt 0
        })
    $matches=@(Select-KahuaUniqueElement $matches)
    if ($All) { return $matches }
    if ($matches.Count -ne 1) { throw "Expected one control id='$Id' name='$Name' type='$Type'; found $($matches.Count)." }
    return $matches[0]
}

function Wait-KahuaControl {
    param([string]$Id, [string]$Name, [string]$Type, [int]$TimeoutSeconds = 30, $Root)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $args = @{Id=$Id; Name=$Name; Type=$Type; All=$true}
        if ($Root) { $args.Root = $Root }
        $matches = @(Find-KahuaControl @args)
        if ($matches.Count -gt 1) { throw "Ambiguous control id='$Id' name='$Name' type='$Type'." }
        if ($matches.Count -eq 1 -and $matches[0].Current.IsEnabled) { return $matches[0] }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Timed out waiting for id='$Id' name='$Name' type='$Type'."
}

function Invoke-KahuaControl {
    param([Parameter(Mandatory=$true)]$Control, [switch]$Click, [switch]$DoubleClick)
    $deadline=[DateTime]::UtcNow.AddSeconds(15)
    while (-not $Control.Current.IsEnabled -or $Control.Current.IsOffscreen) {
        if ([DateTime]::UtcNow -ge $deadline) {
            throw "The intended control did not become ready: id='$($Control.Current.AutomationId)' name='$($Control.Current.Name)'."
        }
        Start-Sleep -Milliseconds 250
    }
    $pattern = $null
    if (-not $Click -and -not $DoubleClick -and $Control.TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) {
        $pattern.Invoke()
        return
    }
    $window = Get-KahuaWindow
    $handle = [IntPtr]$window.Current.NativeWindowHandle
    [void][KahuaMigration.NativeInput]::SetForegroundWindow($handle)
    Start-Sleep -Milliseconds 250
    if ([KahuaMigration.NativeInput]::GetForegroundWindow() -ne $handle) { throw 'Cannot focus Kahua; interactive desktop is required.' }
    $expectedName = $Control.Current.Name
    $expectedId = $Control.Current.AutomationId
    $rect = Get-KahuaControlRectangle $Control
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    do {
        Start-Sleep -Milliseconds 350
        $settled = Get-KahuaControlRectangle $Control
        if ($settled -eq $rect -and -not $rect.IsEmpty -and $rect.Width -gt 0 -and $rect.Height -gt 0) { break }
        if ([DateTime]::UtcNow -ge $deadline) { throw 'The intended control did not settle before clicking.' }
        $rect = $settled
    } while ($true)
    if ($Control.Current.Name -cne $expectedName -or $Control.Current.AutomationId -cne $expectedId) {
        throw 'The intended control changed identity during navigation.'
    }
    if ($rect.IsEmpty -or $rect.Width -le 0 -or $rect.Height -le 0) { throw 'The control has no clickable bounds.' }
    $point = [Windows.Point]::new($rect.X + $rect.Width / 2, $rect.Y + $rect.Height / 2)
    $hit = [Windows.Automation.AutomationElement]::FromPoint($point)
    if ($hit.Current.ProcessId -ne $window.Current.ProcessId) { throw 'Another application covers the intended control.' }
    $matched = $false
    for ($level = 0; $hit -and $level -lt 8; $level++) {
        if ([Windows.Automation.Automation]::Compare($hit, $Control)) { $matched = $true; break }
        $hit = [Windows.Automation.TreeWalker]::RawViewWalker.GetParent($hit)
    }
    if (-not $matched) { throw 'The point no longer belongs to the intended control.' }
    [void][KahuaMigration.NativeInput]::SetCursorPos([int]$point.X, [int]$point.Y)
    [KahuaMigration.NativeInput]::mouse_event(2, 0, 0, 0, [UIntPtr]::Zero)
    [KahuaMigration.NativeInput]::mouse_event(4, 0, 0, 0, [UIntPtr]::Zero)
    if ($DoubleClick) {
        Start-Sleep -Milliseconds 100
        [KahuaMigration.NativeInput]::mouse_event(2, 0, 0, 0, [UIntPtr]::Zero)
        [KahuaMigration.NativeInput]::mouse_event(4, 0, 0, 0, [UIntPtr]::Zero)
    }
}

function Set-KahuaValue {
    param($Control, [string]$Value)
    if (-not $Control.Current.IsEnabled) { throw 'The input control is disabled.' }
    $pattern = $Control.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern)
    if ($pattern.Current.IsReadOnly) { throw 'The input control is read-only.' }
    $pattern.SetValue($Value)
    if ($pattern.Current.Value -cne $Value) { throw 'The input value did not persist.' }
}

function Get-KahuaSnapshot {
    param($Root = (Get-KahuaActiveRoot))
    $Root.FindAll([Windows.Automation.TreeScope]::Descendants, [Windows.Automation.Condition]::TrueCondition) |
        ForEach-Object {
            $current = $_.Current
            if (-not $current.IsOffscreen -and ($current.Name -or $current.AutomationId)) {
                [pscustomobject]@{id=$current.AutomationId; name=$current.Name;
                    type=$current.ControlType.ProgrammaticName.Replace('ControlType.', '');
                    enabled=$current.IsEnabled;
                    patterns=@($_.GetSupportedPatterns() | ForEach-Object ProgrammaticName)}
            }
        }
}

function Assert-KahuaAccount {
    param([string]$Environment, [string]$Domain)
    $window = Get-KahuaWindow
    $windowProcessId = $window.Current.ProcessId
    [void](Find-KahuaControl -Name $Environment -Type Text -Root $window)
    $settings = Find-KahuaControl -Id Shell_Settings -Type Button -Root $window
    $inlineDomain = @(Find-KahuaControl -Name $Domain -Type Text -Root $settings -All)
    if ($inlineDomain.Count -eq 1) { return }
    if ($inlineDomain.Count -gt 1) { throw 'The account domain is ambiguous.' }
    # Newer clients expose the domain only in the Account drawer. Its UIA
    # tree is flattened, so use the observed header and settings bounds to
    # exclude matching text in the project breadcrumbs behind the drawer.
    if (@(Find-KahuaControl -Id CloseModal -Type Button -Root $window -All).Count) {
        throw 'Close the existing dialog before verifying the account domain.'
    }
    if (-not @(Find-KahuaControl -Name Account -Type Text -Root $window -All).Count) {
        Invoke-KahuaControl $settings
    }
    $header = Wait-KahuaControl -Name Account -Type Text -Root $window
    $mySettings = Wait-KahuaControl -Id TitleText -Name 'My settings' -Type Text -Root $window
    # The drawer X has no accessible name or ID in this client. Identify the
    # unique unnamed button on the Account header row, then hit-test the click.
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    $previousBounds = ''
    do {
        $top = Get-KahuaControlRectangle $header
        $closes = @(Find-KahuaControl -Type Button -Root $window -All | Where-Object {
            $r = Get-KahuaControlRectangle $_
            -not $_.Current.Name -and -not $_.Current.AutomationId -and
                $r.Left -ge $top.Right -and $r.Top -lt $top.Bottom -and $r.Bottom -gt $top.Top
        })
        if ($closes.Count -gt 1) { throw 'The Account drawer close button is ambiguous.' }
        if ($closes.Count -eq 1) {
            $bounds = $top.ToString() + '|' + (Get-KahuaControlRectangle $closes[0]).ToString()
            if ($bounds -ceq $previousBounds) { break }
            $previousBounds = $bounds
        } else { $previousBounds = '' }
        if ([DateTime]::UtcNow -ge $deadline) { throw 'The Account drawer did not finish opening.' }
        Start-Sleep -Milliseconds 250
    } while ($true)
    $close = $closes[0]
    try {
        $bottom = Get-KahuaControlRectangle $mySettings
        $right = (Get-KahuaControlRectangle $close).Right
        $domains = @(Find-KahuaControl -Name $Domain -Type Text -Root $window -All | Where-Object {
            $r = Get-KahuaControlRectangle $_
            $r.Left -ge $top.Left -and $r.Right -le $right -and
                $r.Top -ge $top.Bottom -and $r.Bottom -le $bottom.Top
        })
        if ($domains.Count -ne 1) { throw "Account drawer does not show the expected domain '$Domain'." }
    } finally {
        # Click checks the element under the pointer. The underlying window's
        # CloseButton also advertises Invoke even while the drawer covers it.
        Invoke-KahuaControl $close -Click
        $deadline = [DateTime]::UtcNow.AddSeconds(15)
        do {
            # Closing the drawer can invalidate the previous window's UIA
            # object even though the same Kahua process remains open.
            try {
                $remaining = Get-KahuaWindow
                if ($remaining.Current.ProcessId -ne $windowProcessId) { throw 'The Kahua session changed during account verification.' }
                $drawerOpen = @(Find-KahuaControl -Name Account -Type Text -Root $remaining -All).Count
                if (-not $drawerOpen) {
                    [void](Find-KahuaControl -Id Shell_Settings -Type Button -Root $remaining)
                    break
                }
            } catch {
                if ($_.Exception.GetBaseException() -isnot [Windows.Automation.ElementNotAvailableException]) { throw }
            }
            if ([DateTime]::UtcNow -ge $deadline) { throw 'The Account drawer did not close.' }
            Start-Sleep -Milliseconds 250
        } while ($true)
    }
}

function Assert-KahuaProject {
    param([string]$Environment, [string]$Domain, [string]$Number, [string]$Name)
    Assert-KahuaAccount -Environment $Environment -Domain $Domain
    $window = Get-KahuaWindow
    $project = Find-KahuaControl -Id Shell_PartitionSelector -Type Button -Root $window
    if ($project.Current.Name -cne "Current Project $Number - $Name") {
        throw "Wrong project: '$($project.Current.Name)'; expected '$Number - $Name'."
    }
}

function Select-KahuaProject {
    param([string]$Environment, [string]$Domain, [string]$Number, [string]$Name)
    Assert-KahuaAccount -Environment $Environment -Domain $Domain
    if (@(Find-KahuaControl -Id CloseModal -Type Button -All).Count) { throw 'An existing dialog prevents project selection.' }
    $selectors = @(Find-KahuaControl -Id Shell_PartitionSelector -Type Button -All)
    if ($selectors.Count -eq 1 -and $selectors[0].Current.Name -ceq "Current Project $Number - $Name") {
        Assert-KahuaProject @PSBoundParameters
        return
    }
    if ($selectors.Count -eq 0) {
        $selectors = @(Find-KahuaControl -Id Shell_HomePartitionSelector -Name 'Select a Project' -Type Button)
    }
    if ($selectors.Count -ne 1) { throw 'Cannot uniquely identify the project selector.' }
    Invoke-KahuaControl $selectors[0]
    Set-KahuaValue (Wait-KahuaControl -Id SearchBox -Type Edit) $Number
    $target = Wait-KahuaControl -Name "$Number - $Name" -Type ListItem
    # Search is asynchronous; allow the result list to settle before checking uniqueness again.
    Start-Sleep -Milliseconds 600
    Invoke-KahuaControl (Wait-KahuaControl -Name "$Number - $Name" -Type ListItem) -Click
    $deadline=[DateTime]::UtcNow.AddSeconds(15)
    while (@(Find-KahuaControl -Id SearchBox -Type Edit -All).Count) {
        if ([DateTime]::UtcNow -ge $deadline) { throw 'The project picker did not close after selection.' }
        Start-Sleep -Milliseconds 250
    }
    [void](Wait-KahuaControl -Id Shell_PartitionSelector -Name "Current Project $Number - $Name" -Type Button)
    Assert-KahuaProject @PSBoundParameters
}

function Open-KahuaLocalFiles {
    param([string]$Environment, [string]$Domain, [string]$Number, [string]$Name,
        [switch]$Initialize, [string]$ExpectedSyncRoot)
    Assert-KahuaProject -Environment $Environment -Domain $Domain -Number $Number -Name $Name
    if (@(Find-KahuaControl -Id CloseModal -Type Button -All).Count) {
        throw 'Close the existing Kahua dialog before starting a sync operation.'
    }
    Invoke-KahuaControl (Find-KahuaControl -Id Shell_Settings -Type Button)
    Invoke-KahuaControl (Wait-KahuaControl -Id TitleText -Name 'My settings' -Type Text) -Click
    Invoke-KahuaControl (Wait-KahuaControl -Id ButtonText -Name 'Local Files' -Type Text) -Click
    $state = Wait-KahuaLocalFilesState
    if ($state -eq 'unconfigured') {
        if (-not $Initialize) {
            throw 'Local Files is not configured for this Windows installation. In My settings > Local Files, select Enable, then rerun the same CSV and StateRoot. Alternatively use Execute -InitializeLocalFiles. Do not use Reset.'
        }
        # Enable initializes account sync without selecting project folders.
        # Kahua uses the current Windows profile as its fixed base path.
        Assert-KahuaSyncPath -BasePath $env:USERPROFILE -Domain $Domain -Name $Name -ExpectedSyncRoot $ExpectedSyncRoot
        Invoke-KahuaControl (Wait-KahuaControl -Name Enable -Type Button) -Click
        $state = Wait-KahuaLocalFilesState -Configured
    }
    if ($state -eq 'paused') {
        throw 'Local Files is configured but paused. Review the existing selections and select Resume in My settings > Local Files, then rerun the same CSV and StateRoot.'
    }
    [void](Wait-KahuaControl -Name Change -Type Button)
    # This verifies the Local Files selection separately from the main project.
    $change = Find-KahuaControl -Name Change -Type Button
    $container = $change
    $matched = $false
    for ($level = 0; $level -lt 4; $level++) {
        $container = [Windows.Automation.TreeWalker]::RawViewWalker.GetParent($container)
        if (-not $container -or $container.Current.ControlType -eq [Windows.Automation.ControlType]::Window) { break }
        $labels = @(Find-KahuaControl -Name "$Number - $Name" -Type Text -Root $container -All)
        if ($labels.Count -eq 1) { $matched = $true; break }
    }
    if (-not $matched) { throw 'The Local Files project does not match the selected project.' }
    # On reopening, the updated client can briefly expose the previous rows
    # before replacing them with the root. Wait for that root and click it.
    Invoke-KahuaControl (Wait-KahuaControl -Name Folders -Type Button) -Click
    [void](Wait-KahuaControl -Name Documents -Type Button)
    [void](Wait-KahuaControl -Name Photos -Type Button)
    if ($ExpectedSyncRoot) { Assert-KahuaLocalFilesPath -Domain $Domain -Name $Name -ExpectedSyncRoot $ExpectedSyncRoot }
}

function Wait-KahuaLocalFilesState {
    param([switch]$Configured, [int]$TimeoutSeconds=30)
    $deadline=[DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $states=@()
        foreach ($entry in @(@('Enable','unconfigured'),@('Pause','running'),@('Resume','paused'))) {
            if (@(Find-KahuaControl -Name $entry[0] -Type Button -All).Count -eq 1) { $states += $entry[1] }
        }
        if ($states.Count -eq 1 -and (-not $Configured -or $states[0] -ne 'unconfigured')) { return $states[0] }
        if ([DateTime]::UtcNow -ge $deadline) { throw 'Cannot determine Local Files setup state. Expected Enable, Pause, or Resume; inspect My settings > Local Files.' }
        Start-Sleep -Milliseconds 250
    } while ($true)
}

function Assert-KahuaSyncPath {
    param([string]$BasePath,[string]$Domain,[string]$Name,[string]$ExpectedSyncRoot)
    if (-not $ExpectedSyncRoot) { throw 'An explicit CSV sync_root is required to initialize Local Files.' }
    $actual=Join-Path (Join-Path $BasePath ('Kahua - '+$Domain)) $Name
    if ([IO.Path]::GetFullPath($actual).TrimEnd('\') -ine [IO.Path]::GetFullPath($ExpectedSyncRoot).TrimEnd('\')) {
        throw "CSV sync_root does not match the Local Files path: $actual"
    }
}

function Assert-KahuaLocalFilesPath {
    param([string]$Domain,[string]$Name,[string]$ExpectedSyncRoot)
    $paths=@(Find-KahuaControl -Type Button -All | Where-Object { $_.Current.Name -match '^[A-Za-z]:\\' })
    if ($paths.Count -ne 1) { throw 'Cannot identify the Local Files base path.' }
    Assert-KahuaSyncPath -BasePath $paths[0].Current.Name -Domain $Domain -Name $Name -ExpectedSyncRoot $ExpectedSyncRoot
}

function Test-KahuaLocalFilesSetup {
    param([string]$Environment,[string]$Domain,[string]$Number,[string]$Name,
        [string]$ExpectedSyncRoot,[switch]$Initialize)
    $identity=@{Environment=$Environment;Domain=$Domain;Number=$Number;Name=$Name}
    Open-KahuaLocalFiles @identity -ExpectedSyncRoot $ExpectedSyncRoot -Initialize:$Initialize
    # Read setup twice without changing project selections.
    Close-KahuaSettings
    Open-KahuaLocalFiles @identity -ExpectedSyncRoot $ExpectedSyncRoot
    $selections=@{}
    foreach ($folder in @('Documents','Photos')) {
        $selection=(Get-KahuaSyncToggle $folder).Current.ToggleState
        if ($selection -eq [Windows.Automation.ToggleState]::Indeterminate) { throw "Partial sync selection for $folder needs review before continuing." }
        $selections[$folder]=$selection.ToString()
    }
    Close-KahuaSettings
    [pscustomobject]@{state='running';reopened=$true;sync_root=$ExpectedSyncRoot;
        folder_selections=$selections;verified_at_utc=[DateTime]::UtcNow.ToString('o')}
}

function Get-KahuaSyncToggle {
    param([ValidateSet('Documents','Photos')][string]$Folder)
    $button = Find-KahuaControl -Name $Folder -Type Button
    $row = $button
    for ($level = 0; $level -lt 8 -and $row.Current.ControlType -ne [Windows.Automation.ControlType]::DataItem; $level++) {
        $row = [Windows.Automation.TreeWalker]::ControlViewWalker.GetParent($row)
        if (-not $row) { throw "No sync row found for $Folder." }
    }
    if ($row.Current.ControlType -ne [Windows.Automation.ControlType]::DataItem) { throw "No sync row found for $Folder." }
    $toggles = @($row.FindAll([Windows.Automation.TreeScope]::Descendants, [Windows.Automation.Condition]::TrueCondition) |
        Where-Object { $_.GetSupportedPatterns() -contains [Windows.Automation.TogglePattern]::Pattern })
    if ($toggles.Count -ne 1) { throw "Expected one toggle for $Folder; found $($toggles.Count)." }
    return $toggles[0].GetCurrentPattern([Windows.Automation.TogglePattern]::Pattern)
}

function Close-KahuaSettings {
    Invoke-KahuaControl (Find-KahuaControl -Id CloseModal -Type Button)
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    while (@(Find-KahuaControl -Id CloseModal -Type Button -All).Count) {
        if ([DateTime]::UtcNow -ge $deadline) { throw 'Settings did not close.' }
        Start-Sleep -Milliseconds 250
    }
}

function Set-KahuaProjectSync {
    param([string]$Environment, [string]$Domain, [string]$Number, [string]$Name,
        [Parameter(Mandatory=$true)][bool]$Enabled, [string]$ExpectedSyncRoot)
    $identity = @{Environment=$Environment; Domain=$Domain; Number=$Number; Name=$Name}
    Open-KahuaLocalFiles @identity -ExpectedSyncRoot $ExpectedSyncRoot
    $desired = if ($Enabled) { [Windows.Automation.ToggleState]::On } else { [Windows.Automation.ToggleState]::Off }
    $changed = $false
    foreach ($folder in @('Documents','Photos')) {
        $toggle = Get-KahuaSyncToggle $folder
        if ($toggle.Current.ToggleState -eq [Windows.Automation.ToggleState]::Indeterminate) {
            throw "Partial sync selection for $folder needs review before continuing."
        }
        if ($toggle.Current.ToggleState -ne $desired) {
            $toggle.Toggle()
            if ($toggle.Current.ToggleState -ne $desired) { throw "The $folder toggle did not change to $desired." }
            $changed = $true
        }
    }
    if ($changed) {
        Invoke-KahuaControl (Wait-KahuaControl -Name Save -Type Button)
        $deadline = [DateTime]::UtcNow.AddSeconds(30)
        while ((Find-KahuaControl -Name Save -Type Button).Current.IsEnabled) {
            if ([DateTime]::UtcNow -ge $deadline) { throw 'Sync settings Save did not finish.' }
            Start-Sleep -Milliseconds 250
        }
    }
    Close-KahuaSettings
    Open-KahuaLocalFiles @identity -ExpectedSyncRoot $ExpectedSyncRoot
    foreach ($folder in @('Documents','Photos')) {
        if ((Get-KahuaSyncToggle $folder).Current.ToggleState -ne $desired) {
            throw "Saved $folder sync state did not persist."
        }
    }
    Close-KahuaSettings
    [pscustomobject]@{environment=$Environment; domain=$Domain; project_number=$Number; project_name=$Name;
        documents_enabled=$Enabled; photos_enabled=$Enabled; reopened=$true;
        verified_at_utc=[DateTime]::UtcNow.ToString('o')}
}

Export-ModuleMember -Function *-Kahua*
