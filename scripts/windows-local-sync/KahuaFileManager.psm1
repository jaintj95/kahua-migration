#requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not ('KahuaMigration.SaveDialog' -as [type])) {
    Add-Type @'
using System;
using System.Text;
using System.Runtime.InteropServices;
namespace KahuaMigration {
 public static class SaveDialog {
  [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  public static extern IntPtr SendMessageTimeout(IntPtr h, uint m, IntPtr w, string l, uint f, uint t, out IntPtr result);
  [DllImport("user32.dll", EntryPoint="SendMessageTimeoutW", CharSet=CharSet.Unicode, SetLastError=true)]
  public static extern IntPtr ReadText(IntPtr h, uint m, IntPtr w, StringBuilder l, uint f, uint t, out IntPtr result);
  [DllImport("user32.dll", SetLastError=true)]
  public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
 }
}
'@
}

function Get-KahuaGridInfo {
    $grid = Find-KahuaControl -Id LogDataGrid -Type DataGrid
    $pattern = $grid.GetCurrentPattern([Windows.Automation.GridPattern]::Pattern)
    $headers = @($grid.GetCurrentPattern([Windows.Automation.TablePattern]::Pattern).Current.GetColumnHeaders())
    if ($headers.Count -ne $pattern.Current.ColumnCount) { throw 'File grid columns are incomplete.' }
    $nameColumn = -1; $versionColumn = -1
    for ($i=0; $i -lt $headers.Count; $i++) {
        if ($headers[$i].Current.Name -ceq 'Name') { if ($nameColumn -ge 0) { throw 'Ambiguous Name column.' }; $nameColumn=$i }
        if ($headers[$i].Current.Name -ceq 'Version') { $versionColumn=$i }
    }
    if ($nameColumn -lt 0 -or $versionColumn -lt 0) { throw 'Use File Manager list layout with Name and Version columns.' }
    return @{element=$grid; pattern=$pattern; name_column=$nameColumn; version_column=$versionColumn}
}

function Wait-KahuaFileReady {
    param([int]$TimeoutSeconds=60)
    $deadline=[DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $quiet=0
    do {
        $window=Get-KahuaWindow
        $serviceErrors=@(Find-KahuaControl -Id DetailsTextBlock -Type Text -Root $window -All |
            Where-Object { $_.Current.Name -match '^Something went wrong\. Please try again\.' })
        if ($serviceErrors.Count) {
            throw ("Kahua service error; file absence cannot be established: " +
                (($serviceErrors | ForEach-Object { $_.Current.Name }) -join ' | '))
        }
        $busy=@(Find-KahuaControl -Id ShellBusy2 -Name Busy -Type ProgressBar -Root $window -All)
        $refresh=@(Find-KahuaControl -Id RefreshButton -Type Button -All)
        if (-not $busy.Count -and $refresh.Count -eq 1 -and $refresh[0].Current.IsEnabled) { $quiet++ }
        else { $quiet=0 }
        if ($quiet -ge 2) { return }
        if ([DateTime]::UtcNow -ge $deadline) { throw 'Kahua File Manager is still loading; file absence cannot be established. Retry after the client recovers.' }
        Start-Sleep -Milliseconds 300
    } while ($true)
}

function Get-KahuaFolderListing {
    # A loading grid can temporarily expose zero rows. Never treat that as
    # proof that a server file is absent and safe to publish again.
    Wait-KahuaFileReady
    $info = Get-KahuaGridInfo
    $count = $info.pattern.Current.RowCount
    $entries = @(); $seen=@{}
    for ($i=0; $i -lt $count; $i++) {
        $cell = $info.pattern.GetItem($i, $info.name_column)
        $name = $cell.Current.Name
        if ([string]::IsNullOrWhiteSpace($name)) { throw 'A file-grid row cannot be read; absence is not established.' }
        if ($seen.ContainsKey($name)) { throw "Duplicate or case-colliding Kahua name: $name" }
        $seen[$name]=$true
        $version=$info.pattern.GetItem($i, $info.version_column).Current.Name
        if ($version -and $version -notmatch '^[1-9][0-9]*$') { throw "Unrecognized version for $name : $version" }
        $entries += [pscustomobject]@{name=$name; version=$version; index=$i; is_folder=([string]::IsNullOrEmpty($version))}
    }
    if ($info.pattern.Current.RowCount -ne $count -or
        @(Find-KahuaControl -Id ShellBusy2 -Name Busy -Type ProgressBar -Root (Get-KahuaWindow) -All).Count -or
        -not (Find-KahuaControl -Id RefreshButton -Type Button).Current.IsEnabled) {
        throw 'File listing changed or started loading while being inspected; retry the run.'
    }
    return $entries
}

function Get-KahuaBreadcrumb {
    $bar = Find-KahuaControl -Id FolderNavigationBar -Type Group
    return @(Find-KahuaControl -Type Button -Root $bar -All | Where-Object {
        $_.Current.Name -and $_.Current.Name -cne 'Folders'
    } | ForEach-Object { $_.Current.Name })
}

function Assert-KahuaFolder {
    param([string]$ProjectName, [string[]]$Segments=@())
    $expected = @($ProjectName) + @($Segments)
    $actual = @(Get-KahuaBreadcrumb)
    if (($actual -join [char]31) -cne ($expected -join [char]31)) {
        throw "Wrong File Manager folder: '$($actual -join '/')'; expected '$($expected -join '/')'."
    }
}

function Get-KahuaGridContentStamp {
    $info=Get-KahuaGridInfo
    $rows=@()
    for ($i=0; $i -lt $info.pattern.Current.RowCount; $i++) {
        $rows += @($info.pattern.GetItem($i,$info.name_column).Current.Name,
            $info.pattern.GetItem($i,$info.version_column).Current.Name)
    }
    return ConvertTo-Json -InputObject $rows -Compress
}

function Assert-KahuaFileRootContent {
    $entries=@(Get-KahuaFolderListing)
    foreach ($name in @('Documents','Photos')) {
        $matches=@($entries | Where-Object name -ieq $name)
        if ($matches.Count -ne 1 -or $matches[0].name -cne $name -or -not $matches[0].is_folder) {
            throw "The project-root rows do not contain one exact '$name' folder. Navigation may be stale; file absence cannot be established."
        }
    }
}

function Wait-KahuaFolder {
    param([string]$ProjectName, [string[]]$Segments=@(), [string]$PreviousListing)
    $deadline=[DateTime]::UtcNow.AddSeconds(30)
    do {
        try { Assert-KahuaFolder $ProjectName $Segments; [void](Get-KahuaGridInfo); break } catch {
            if ([DateTime]::UtcNow -ge $deadline) { throw }
            Start-Sleep -Milliseconds 300
        }
    } while ($true)
    # Kahua can expose the new breadcrumb and enabled Refresh while retaining
    # the old rows. Require changed, stable contents after a folder transition.
    $deadline=[DateTime]::UtcNow.AddSeconds(30)
    do {
        Wait-KahuaFileReady
        Assert-KahuaFolder $ProjectName $Segments
        $stamp=Get-KahuaGridContentStamp
        if (-not $PreviousListing -or $stamp -cne $PreviousListing) {
            Start-Sleep -Seconds 2
            Wait-KahuaFileReady
            Assert-KahuaFolder $ProjectName $Segments
            if ((Get-KahuaGridContentStamp) -ceq $stamp) { return }
        }
        if ([DateTime]::UtcNow -ge $deadline) {
            throw 'Folder rows did not visibly change and settle after navigation; file absence cannot be established.'
        }
        Start-Sleep -Milliseconds 250
    } while ($true)
}

function Get-KahuaFileRow {
    param([string]$Name)
    $entries=@(Get-KahuaFolderListing | Where-Object name -ieq $Name)
    if ($entries.Count -ne 1 -or $entries[0].name -cne $Name) { throw "Expected one exact file-grid name: $Name" }
    $info=Get-KahuaGridInfo
    $cell=$info.pattern.GetItem($entries[0].index,$info.name_column)
    $row=$cell
    while ($row -and $row.Current.ControlType -ne [Windows.Automation.ControlType]::DataItem) {
        $row=[Windows.Automation.TreeWalker]::ControlViewWalker.GetParent($row)
    }
    if (-not $row) { throw "Cannot resolve file row: $Name" }
    $scroll=$null
    if ($row.TryGetCurrentPattern([Windows.Automation.ScrollItemPattern]::Pattern,[ref]$scroll)) { $scroll.ScrollIntoView() }
    return $row
}

function Clear-KahuaFileSelection {
    # Traverse every row, including virtualized rows; never download a stale multi-selection.
    foreach ($entry in @(Get-KahuaFolderListing)) {
        $row=Get-KahuaFileRow $entry.name
        $box=Find-KahuaControl -Name 'Select row' -Type CheckBox -Root $row
        $toggle=$box.GetCurrentPattern([Windows.Automation.TogglePattern]::Pattern)
        if ($toggle.Current.ToggleState -ne [Windows.Automation.ToggleState]::Off) { $toggle.Toggle() }
        if ($toggle.Current.ToggleState -ne [Windows.Automation.ToggleState]::Off) { throw 'Cannot clear the file selection.' }
    }
}

function Clear-KahuaViewFilters {
    if (-not @(Find-KahuaControl -Id FilterScroll -All).Count) {
        Invoke-KahuaControl (Find-KahuaControl -Id LogViewFilterButton -Type Button) -Click
    }
    $panel=Wait-KahuaControl -Id FilterScroll
    foreach ($section in @('Discussions Section','Lock State Section')) {
        (Find-KahuaControl -Name $section -Type Button -Root $panel).GetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern).Expand()
    }
    $expected=@('File Lock','Has Discussions','MS365 Lock','No Lock') | Sort-Object
    # Expand() returns before Uno has rendered the child checkboxes.
    # Wait for the complete known set, preserving the fail-closed schema check.
    $deadline=[DateTime]::UtcNow.AddSeconds(10)
    do {
        $panel=Wait-KahuaControl -Id FilterScroll
        $boxes=@(Find-KahuaControl -Type CheckBox -Root $panel -All)
        $actual=@($boxes | ForEach-Object { $_.Current.Name } | Sort-Object)
        if (($actual -join '|') -ceq ($expected -join '|')) { break }
        if ([DateTime]::UtcNow -ge $deadline) {
            throw "File Manager filter options did not match the expected set; observed: $($actual -join ', ')."
        }
        Start-Sleep -Milliseconds 250
    } while ($true)
    foreach ($box in $boxes) {
        $toggle=$box.GetCurrentPattern([Windows.Automation.TogglePattern]::Pattern)
        if ($toggle.Current.ToggleState -ne [Windows.Automation.ToggleState]::Off) { $toggle.Toggle() }
        if ($toggle.Current.ToggleState -ne [Windows.Automation.ToggleState]::Off) { throw 'Cannot clear a File Manager filter.' }
    }
    Invoke-KahuaControl (Find-KahuaControl -Id LogViewFilterButton -Type Button) -Click
    $deadline=[DateTime]::UtcNow.AddSeconds(10)
    while (@(Find-KahuaControl -Id FilterScroll -All).Count) {
        if ([DateTime]::UtcNow -ge $deadline) { throw 'File Manager filters did not close.' }
        Start-Sleep -Milliseconds 250
    }
}

function Open-KahuaFileRoot {
    param([hashtable]$Identity)
    Assert-KahuaProject @Identity
    if (@(Find-KahuaControl -Id CloseModal -All).Count) { throw 'Close the existing Kahua dialog before continuing.' }
    $app = Find-KahuaControl -Id Shell_NavigationBarSelectedItem -Type Button
    if ($app.Current.Name -cne 'Current App File Manager, Select Another App') {
        Invoke-KahuaControl (Find-KahuaControl -Id 'File Manager' -Name 'Open File Manager')
    }
    [void](Wait-KahuaControl -Id FolderNavigationBar -Type Group)
    Set-KahuaValue (Find-KahuaControl -Id searchText -Type Edit) ''
    $bar=Find-KahuaControl -Id FolderNavigationBar -Type Group
    $scope=Find-KahuaControl -Type ComboBox -Root $bar
    $expand=$scope.GetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern)
    $expand.Expand()
    $item=Wait-KahuaControl -Name 'Current Folder' -Type ListItem
    # Uno advertises SelectionItem but its Select() throws for this popup.
    Invoke-KahuaControl $item -Click
    Start-Sleep -Milliseconds 350
    # Uno only exposes the selected item's text while the list is expanded.
    $expand.Expand()
    Start-Sleep -Milliseconds 350
    $selected=@($scope.GetCurrentPattern([Windows.Automation.SelectionPattern]::Pattern).Current.GetSelection())
    if ($selected.Count -ne 1 -or $selected[0].Current.Name -cne 'Current Folder') { throw 'Cannot verify Current Folder scope.' }
    $expand.Collapse()
    $previousListing=$null
    if ((@(Get-KahuaBreadcrumb) -join [char]31) -cne $Identity.Name) {
        $previousListing=Get-KahuaGridContentStamp
    }
    Invoke-KahuaControl (Find-KahuaControl -Name $Identity.Name -Type Button -Root $bar) -Click
    Wait-KahuaFolder $Identity.Name -PreviousListing $previousListing
    Assert-KahuaFileRootContent
    # Select the named built-in view every time, then verify its label.
    Invoke-KahuaControl (Find-KahuaControl -Id LogViewSelectorButton -Type Button) -Click
    $deadline=[DateTime]::UtcNow.AddSeconds(15)
    do {
        $views=@(Find-KahuaControl -Id LogViewButton -Type Button -All | Where-Object {
            @(Find-KahuaControl -Name Default -Type Text -Root $_ -All).Count -eq 1
        })
        if ($views.Count -gt 1) { throw 'Cannot uniquely select the Default File Manager view.' }
        if ($views.Count -eq 1) { break }
        if ([DateTime]::UtcNow -ge $deadline) { throw 'The Default File Manager view did not finish rendering.' }
        Start-Sleep -Milliseconds 250
    } while ($true)
    Invoke-KahuaControl $views[0] -Click
    [void](Find-KahuaControl -Name Default -Type Text -Root (Find-KahuaControl -Id LogViewSelectorButton -Type Button))
    # Changing the view starts its own request. Do not overlap it with filter
    # changes or Refresh; the live client can return an error and retain old rows.
    Wait-KahuaFolder $Identity.Name
    Clear-KahuaViewFilters
    Wait-KahuaFolder $Identity.Name
    Invoke-KahuaControl (Find-KahuaControl -Id RefreshButton -Type Button) -Click
    Start-Sleep -Milliseconds 1200
    Assert-KahuaFolder $Identity.Name
    Assert-KahuaFileRootContent
}

function Open-KahuaFileFolder {
    param([hashtable]$Identity, [string[]]$Segments, [switch]$AllowMissing)
    Open-KahuaFileRoot $Identity
    $walked=@()
    foreach ($segment in $Segments) {
        $entries=@(Get-KahuaFolderListing | Where-Object name -ieq $segment)
        if (-not $entries.Count) {
            Invoke-KahuaControl (Find-KahuaControl -Id RefreshButton -Type Button) -Click
            Start-Sleep -Milliseconds 1200
            $entries=@(Get-KahuaFolderListing | Where-Object name -ieq $segment)
            if (-not $entries.Count -and $AllowMissing) { return $false }
        }
        if ($entries.Count -ne 1 -or $entries[0].name -cne $segment -or -not $entries[0].is_folder) {
            throw "Expected one exact folder at '$($walked -join '/')/$segment'."
        }
        $row=Get-KahuaFileRow $segment
        # Uno hit-tests the grid cell instead of its child Text element.
        # Bind the cell through the verified Name column and recheck its value.
        $info=Get-KahuaGridInfo
        $cell=$info.pattern.GetItem($entries[0].index,$info.name_column)
        if ($cell.Current.Name -cne $segment) { throw 'Folder row changed before navigation.' }
        $previousListing=Get-KahuaGridContentStamp
        Invoke-KahuaControl $cell -DoubleClick
        $walked += $segment
        Wait-KahuaFolder $Identity.Name $walked -PreviousListing $previousListing
        Start-Sleep -Milliseconds 700
    }
    Assert-KahuaProject @Identity
    Assert-KahuaFolder $Identity.Name $Segments
    return $true
}

function Get-KahuaSaveFieldText {
    param($Control)
    # The native dialog exposes no ValuePattern, and Name can be its label
    # during initialization. Read the Edit control's actual text with WM_GETTEXT.
    $buffer=[Text.StringBuilder]::new(32768)
    $result=[IntPtr]::Zero
    $sent=[KahuaMigration.SaveDialog]::ReadText([IntPtr]$Control.Current.NativeWindowHandle,13,[IntPtr]$buffer.Capacity,$buffer,2,5000,[ref]$result)
    if ($sent -eq [IntPtr]::Zero) { throw 'Cannot read the Save As filename input.' }
    return $buffer.ToString()
}

function Save-KahuaOriginal {
    param([string]$FileName, [string]$Destination, [int]$TimeoutSeconds=120)
    if (Test-Path -LiteralPath $Destination) { throw 'A fresh download path is required.' }
    [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($Destination))
    $window=Get-KahuaWindow
    $save=Wait-KahuaControl -Name 'Save As' -Type Window -Root $window
    if ($save.Current.ClassName -cne '#32770' -or $save.Current.ProcessId -ne $window.Current.ProcessId) {
        throw 'The Save As dialog does not belong to Kahua.'
    }
    $hosted=Wait-KahuaControl -Id FileNameControlHost -Root $save
    $field=Wait-KahuaControl -Id 1001 -Root $hosted
    $button=Wait-KahuaControl -Id 1 -Name Save -Root $save
    if ($field.Current.ClassName -cne 'Edit' -or $button.Current.ClassName -cne 'Button') { throw 'Save As controls changed.' }
    $deadline=[DateTime]::UtcNow.AddSeconds(10)
    do {
        $value=Get-KahuaSaveFieldText $field
        if ($value -ceq $FileName) { break }
        if ([DateTime]::UtcNow -ge $deadline) { throw "Download filename differs from the selected row: $value" }
        Start-Sleep -Milliseconds 250
    } while ($true)
    $result=[IntPtr]::Zero
    $sent=[KahuaMigration.SaveDialog]::SendMessageTimeout([IntPtr]$field.Current.NativeWindowHandle,12,[IntPtr]::Zero,$Destination,2,5000,[ref]$result)
    if ($sent -eq [IntPtr]::Zero -or (Get-KahuaSaveFieldText $field) -cne $Destination) { throw 'Cannot set the independent download destination.' }
    [void][KahuaMigration.NativeInput]::SetForegroundWindow([IntPtr]$save.Current.NativeWindowHandle)
    if (-not [KahuaMigration.SaveDialog]::PostMessage([IntPtr]$button.Current.NativeWindowHandle,245,[IntPtr]::Zero,[IntPtr]::Zero)) {
        throw 'Cannot activate the Save button.'
    }
    $deadline=[DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $dialogs=@(Find-KahuaControl -Id ModalView -Name 'Downloading Files' -Root (Get-KahuaWindow) -All)
        if ($dialogs.Count -eq 1 -and @(Find-KahuaControl -Id ProgressText -Name 'Downloading Complete' -Type Text -Root $dialogs[0] -All).Count) {
            $files=@(Find-KahuaControl -Id FileName -Type Text -Root $dialogs[0] -All)
            if ($files.Count -ne 1 -or $files[0].Current.Name -cne $FileName) { throw 'The download completion list is not the selected single file.' }
            if (-not (Test-Path -LiteralPath $Destination -PathType Leaf)) { throw 'Kahua reports completion but the original download is missing.' }
            $stream=[IO.File]::Open($Destination,'Open','Read','None'); $stream.Dispose()
            Invoke-KahuaControl (Find-KahuaControl -Name Ok -Type Button -Root $dialogs[0])
            Start-Sleep -Milliseconds 250
            return
        }
        if ([DateTime]::UtcNow -ge $deadline) { throw 'Original download did not finish within the timeout; no verification was recorded.' }
        Start-Sleep -Milliseconds 300
    } while ($true)
}

function Receive-KahuaManifest {
    param([hashtable]$Identity, $Manifest, [string]$DownloadRoot, [int]$TimeoutSeconds=120, [switch]$AllowMissing)
    if (Test-Path -LiteralPath $DownloadRoot) { throw 'Each server check requires a new download directory.' }
    [void][IO.Directory]::CreateDirectory($DownloadRoot)
    $receipts=@(); $parent=$null; $folderExists=$false
    foreach ($file in @($Manifest.files | Sort-Object relative_path)) {
        $parts=$file.relative_path.Split('/')
        $segments=@($parts[0..($parts.Length-2)])
        $folder=$segments -join '/'; $name=$parts[-1]
        if ($folder -cne $parent) {
            $folderExists=Open-KahuaFileFolder $Identity $segments -AllowMissing:$AllowMissing
            $parent=$folder
        }
        $entries=@()
        if ($folderExists) {
            Assert-KahuaProject @Identity
            Assert-KahuaFolder $Identity.Name $segments
            $entries=@(Get-KahuaFolderListing | Where-Object name -ieq $name)
            if (-not $entries.Count) {
                Invoke-KahuaControl (Find-KahuaControl -Id RefreshButton -Type Button) -Click
                Start-Sleep -Milliseconds 1200
                $entries=@(Get-KahuaFolderListing | Where-Object name -ieq $name)
            }
        }
        $receipt=[ordered]@{environment=$Identity.Environment;domain=$Identity.Domain;project_number=$Identity.Number;
            project_name=$Identity.Name;relative_path=$file.relative_path;found=($entries.Count -eq 1);action='Download';
            download_path=$null;version=$null;checked_at_utc=[DateTime]::UtcNow.ToString('o')}
        if (-not $entries.Count) {
            if (-not $AllowMissing) { throw "Missing server path: $($file.relative_path)" }
        } else {
            if ($entries.Count -ne 1 -or $entries[0].name -cne $name -or $entries[0].is_folder) { throw "Ambiguous server file: $($file.relative_path)" }
            $receipt.version=$entries[0].version
            Clear-KahuaFileSelection
            $row=Get-KahuaFileRow $name
            $toggle=(Find-KahuaControl -Name 'Select row' -Type CheckBox -Root $row).GetCurrentPattern([Windows.Automation.TogglePattern]::Pattern)
            $toggle.Toggle()
            if ($toggle.Current.ToggleState -ne [Windows.Automation.ToggleState]::On) { throw 'File was not selected for download.' }
            Assert-KahuaProject @Identity
            Assert-KahuaFolder $Identity.Name $segments
            # In the updated client Invoke can return without opening Save As.
            # Use a settled, hit-tested click on the visible Download control.
            Invoke-KahuaControl (Wait-KahuaControl -Id LogAction_FileDownloadMultiLogAction -Name Download -Type Button) -Click
            $destination=Join-Path $DownloadRoot $file.relative_path.Replace('/','\')
            Save-KahuaOriginal $name $destination $TimeoutSeconds
            $receipt.download_path=$destination
        }
        $receipts += [pscustomobject]$receipt
        Write-KahuaJson (Join-Path $DownloadRoot 'receipts.json') $receipts
        # Leave no checked file behind when the next folder is opened.
        if ($receipt.download_path) { Clear-KahuaFileSelection }
        Write-Host ("Server check {0}/{1}: {2}" -f $receipts.Count,@($Manifest.files).Count,$file.relative_path)
    }
    return $receipts
}
Export-ModuleMember -Function *-Kahua*
