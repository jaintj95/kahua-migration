# Upload documents and photos through Kahua local sync

`scripts/windows-local-sync/Invoke-KahuaSyncBatch.ps1` processes a CSV of existing Kahua projects.
It stages each ZIP, selects projects through the Windows desktop UI, reconciles
existing files through original-file downloads, copies missing content through
Local Files Sync, and compares downloaded server bytes with the ZIP by SHA-256.
A project is complete only after all files match and Documents/Photos sync has
been saved off and checked again after reopening settings.

Only `Documents/` and `Photos/` beneath the exact ZIP prefix are included.
Nested file paths are preserved. Drawings use the separate browser workflow.
The worker never resets local sync, deletes destination files, or replaces a
content conflict. It stops the queue on an error and keeps evidence for resume.

Read [KAHUA_WINDOWS_HANDOVER.md](KAHUA_WINDOWS_HANDOVER.md) for the observed
Kahua issues, confirmed sync transport failure, corrected automation defects,
and remaining risks. Unattended batch operation is currently **High risk**;
this issue register must accompany the final handover.

The original requirements are recorded in
[call_notes_photos_migration.md](call_notes_photos_migration.md).

## Requirements

- Windows PowerShell 5.1 and the Kahua Windows desktop app, signed in to the
  CSV's exact environment and domain. No Python or Codex installation is needed
  on Windows. The pilot uses Windows App from the Mac to control the Cloud PC;
  the same scripts can run on another suitable Windows computer.
- An unlocked, interactive desktop reserved for this worker. Do not use Kahua
  or move its dialogs while the script is running. Sign-in and MFA remain manual.
  Keep Kahua maximized so the environment label and navigation controls remain
  visible; a clipped identity label stops the run before uploads.
  Kahua's inactivity lock can stop a run; disconnected and overnight operation
  are not validated.
- Existing projects with exact project numbers and names, including leading
  zeros. Both root folders **Documents** and **Photos** must already exist in
  File Manager. The current controller reports missing roots before enabling
  sync; it does not create projects or configure missing root folders.
- Local Files Sync configured for the signed-in account on this Windows
  installation. Execute checks setup before downloading files. If it shows
  **Enable**, complete the initial setup manually, or explicitly use
  `-InitializeLocalFiles` with Execute. If it shows **Resume**, review existing
  selections and resume manually. **Pause** means sync is running. The controller
  then selects the individual Documents and Photos folders. A partial selection
  within either folder requires operator review before running.
- File Manager's **Default** list view with **Name** and **Version** columns.
  The controller clears its search and filters and uses **Current Folder**.
  The controller waits for File Manager to leave its loading state before
  reading rows. Folder transitions also require changed, stable rows; the root
  must contain both exact required folders. A busy, stale, or incomplete grid
  cannot establish that a file is absent. Identical parent/child listings may
  require manual review because their transition cannot be distinguished.
  Unreadable, duplicate, or ambiguous rows stop the run. The current selectors
  were initially calibrated against Kahua `1.9.56.393` in English. Account
  drawer compatibility was checked on `1.9.56.438`; a complete Execute on that
  updated client remains unvalidated.
- Local source ZIPs and enough space for staging every CSV row, local synced
  copies, and fresh verification downloads. Downloads and staging remain
  outside all sync directories. State and sync must be on the same drive.

## CSV and state directory

Copy `local-sync-projects.example.csv` and replace every example value:

| Column | Meaning |
| --- | --- |
| `run_id` | Stable unique identifier using letters, digits, `_`, and `-` |
| `environment` | Exact Kahua environment label, such as `DEVPRODUCTION` |
| `domain` | Exact domain currently signed in |
| `project_number` | Exact existing project number; keep it as text |
| `project_name` | Exact existing project name |
| `source_zip` | Absolute local Windows path to this project's ZIP |
| `source_prefix` | Exact top-level ZIP folder, with forward slashes for nested prefixes |
| `sync_root` | Exact project directory beneath the Kahua Local Files base path |

Use one CSV per environment/domain. Duplicate project numbers, project names,
run IDs, ZIP mappings, overlapping paths, and names requiring unsupported
Windows path transformations are rejected. The controller separately checks
the main project header, the project shown in Local Files, and the sync base
path before changing sync.

For the Seattle pilot, the sync root was
`C:\Users\MigrationEngineer\Kahua - Onindus\220 - Seattle Heights`.
Do not use that test mapping for a different project. The general path checked
by the controller is `<Local Files base>\Kahua - <domain>\<project_name>`.

Choose a new state directory for a new reviewed batch, for example
`C:\Migration\runs\documents-20261103`. Keep the CSV and ZIP files outside it
and outside every sync directory. The state binds the CSV mapping and each
ZIP hash. Changing the source, destination, or CSV rows requires a new state
directory and a fresh review. Preserve the existing state for resume.

## Run the batch

Keep the `scripts/windows-local-sync/` directory together when copying or
checking out the repository on Windows. Commands below run from the repository
root. The scripts locate sibling modules through `$PSScriptRoot`; Python and
Playwright are not needed for this workflow. First run the local fixture suites:

```powershell
.\scripts\windows-local-sync\Test-KahuaLocalSync.ps1
.\scripts\windows-local-sync\Test-KahuaSyncBatch.ps1
.\scripts\windows-local-sync\Test-KahuaSyncCoordinator.ps1
```

Plan all rows without opening or changing Kahua:

```powershell
.\scripts\windows-local-sync\Invoke-KahuaSyncBatch.ps1 `
  -CsvPath C:\Migration\projects.csv `
  -StateRoot C:\Migration\runs\documents-20261103 `
  -Mode Plan
```

Review each manifest's destination, paths, counts, and conflicts. Plan extracts
and hashes every selected source file outside sync. It permits a sync directory
that does not exist yet. No desktop code is reached until every source plan
passes. Before an upload, optionally inspect current server content:

```powershell
.\scripts\windows-local-sync\Invoke-KahuaSyncBatch.ps1 `
  -CsvPath C:\Migration\projects.csv `
  -StateRoot C:\Migration\runs\documents-20261103 `
  -Mode Check
```

`Check` selects projects and downloads originals into a new attempt directory.
It reports absent paths and rejects different content at an existing path.
It does not upload files or change sync settings. A successful check does not
claim that sync is off or that migration is complete.

Start or resume the upload with:

```powershell
.\scripts\windows-local-sync\Invoke-KahuaSyncBatch.ps1 `
  -CsvPath C:\Migration\projects.csv `
  -StateRoot C:\Migration\runs\documents-20261103 `
  -Mode Execute
```

On a fresh installation, or whenever Kahua reports Local Files as unconfigured,
use the same command with `-InitializeLocalFiles` to allow the initial **Enable**
action. This initializes account sync separately from project folder selection.
It first checks the CSV destination against Kahua's fixed Windows-profile path,
then checks the displayed path and reopens settings to verify configuration.
It does not reset an existing configuration or resume a paused installation.
Without that option, the runner stops with manual setup instructions. Plan and
Check never initialize sync. An update alone is not evidence that setup is lost;
the runner branches on the actual settings page.

Kahua documents the separate account Enable step and project folder selection
in [Using Local Files Sync](https://help.kahua.com/main/Content/4-DocumentationMngmt/File-Manager/local-files-sync.htm).

The runner validates every project identity and required root folder before
processing the first project. For each row it then:

1. Downloads existing source paths from Kahua to fresh independent locations
   and compares file lengths and SHA-256 hashes with the source manifest.
2. If any source paths are absent, enables Documents/Photos sync, saves and
   reopens settings to verify the switches, and waits for existing server files
   to arrive locally with matching bytes. It checks the server again before
   copying, so a resumed upload is reconciled first.
3. Copies complete files from staging into sync without replacement. Identical
   local files are skipped. Any differing local file stops the run.
4. Downloads originals until every source path matches. Existing source-file
   versions must remain unchanged across the copy stage.
5. Turns Documents and Photos off, saves, closes and reopens settings, and
   confirms both switches remain off before recording completion.

When every source path already exists with matching bytes, Execute skips the
sync-on and copy stages, then verifies sync is off. It does not trust an old
completion journal without downloading and checking server files again.

`-SyncTimeoutSeconds` defaults to 1800 for local reconciliation and upload
polling; `-DownloadTimeoutSeconds` defaults to 120 per original download.
A batch holds a state-directory lock and an interactive-session desktop mutex.

## Results and recovery

The state directory contains:

```text
batch-binding.json
projects/<run_id>/
  mapping.json, binding.json, manifest.json, staged/
  journal.json, latest-server-check.json
  server-verification.json, sync-on.json, sync-off.json
attempts/<attempt_id>/
  batch-result.json
  <run_id>/<check_id>/receipts.json, verification.json, downloaded originals
```

Some files exist only after their phase completes. `manifest.json` describes
local extraction/copying and always has `server_verified=false`. Server hash
evidence is in `server-verification.json`; final success is in `journal.json`
with `status=complete`, `server_verified=true`, and `sync_off_verified=true`.
Attempt reports retain prior results when a later attempt fails.

On any failure, the queue stops. Already completed projects retain their
results. The failed project is not marked complete, including when downloaded
bytes match but saving sync off is uncertain. Resolve sign-in, dialogs, source
conflicts, or missing folders, then rerun the **same CSV and StateRoot**. The
runner rechecks the actual server before copying. Do not edit a completion
journal to skip this step.

If Kahua remains busy or its Refresh control stays disabled after a service
error, wait for the client to recover or restart it normally and sign in again.
The worker does not dismiss unknown service errors or bypass a loading state.
The failure journal includes the control name and script stack where available.
An observed Kahua service-error dialog also stops folder inspection and includes
its reference in the error. A changed breadcrumb alone is insufficient: the
controller waits for changed, stable folder rows before further operations.
It also waits between view selection, filter changes, and Refresh, and clears
each downloaded file's selection before moving on.
If a service error leaves the breadcrumb and rows inconsistent, do not keep
retrying uploads. Restore a working client view and verify the original files
before resuming the same state. Do not dismiss errors to force progress.

An interrupted run may leave sync active. Do not delete files from its local
sync directory. Source ZIPs, staging, downloaded evidence, and the journal are
retained; there is no automatic cleanup. Cleanup is a separate operator step
after the saved sync selection is verified off. Do not use **Reset** as the
per-project stop action. Kahua may remove local synced copies when folders
are deselected; this was observed in the pilot.

## Single-project copy worker

`scripts/windows-local-sync/Invoke-KahuaLocalSync.ps1` remains available for supervised use with
`local-sync.example.json`. Its modes are `Plan`, `Copy`, and `VerifyLocal`.
Plan needs no destination assertion; Copy and VerifyLocal require
`destination_confirmed` to be the JSON boolean `true` and the sync root to exist.
For standalone use this is an operator assertion, not a desktop identity check.
This worker verifies local bytes only. Use the batch runner for automated
project selection, server downloads, and saved sync-off verification.

## Validation record and limits

On November 3, 2026, the initial supervised pilot uploaded **9 photos and
2 documents** into **DEVPRODUCTION → Onindus → 0001 - 220 - Seattle Heights**.
The user allowed this project for experiments. The source ZIP's top-level
folder was `Sample Project - Office Building`; this is an intentional test
mapping rather than a production name match. All 11 local copies matched the
ZIP by SHA-256, the five pre-existing local files retained their hashes, and
File Manager showed the new files at version 1. The documents remained in
`Documents/Test1` and `Documents/Test2`. Documents/Photos sync was saved off,
reopened, and verified off while the files remained in Kahua.

The desktop controller has exercised exact project search and selection and
automatic enable/save/reopen/disable. After live navigation fixes, the full
CSV runner completed **Check** for all 11 files on November 3: every original
download matched the ZIP's length and SHA-256, and all 11 server versions were
still 1. The successful check attempt is `20261103T070300Z-b03e9da7`.

A subsequent fresh-process Execute exposed additional UI timing issues in
filter rendering and the native Save As filename field. The controller now
waits for those controls and reads the native edit field's actual value.
Later Execute attempts stopped before uploading after a Kahua service error
left File Manager busy with Refresh disabled. The new loading guard was tested
against that live stalled state and correctly refused to establish file absence.
The client was restarted and updated. Its Account drawer now exposes the domain
outside the Settings button. The controller has been adapted to check that
drawer and to use a hit-tested drawer-close click; the underlying window-close
control can otherwise close the app. Live checks confirm that the expected
domain passes, a wrong domain is rejected, and repeated checks preserve the
signed-in window. All eight deployed scripts match the local SHA-256.

An earlier Execute attempt, `20261103T104901Z-ebd9c5d3`, stopped in
`checking-server` before enabling sync or copying files. Its journal recorded
`The point no longer belongs to the intended control.` A Kahua service error
(`HU-A180AB`) was visible during that attempt. Manual Photos navigation after
reopening File Manager also failed, with reference `HU-10C7EA` at
16:27:14 IST. The folder breadcrumb changed while the grid retained root rows.
The explicit service-error guard was then exercised against that live dialog
and rejected file absence with the same error reference.

At 16:29 IST, the signed-in `DEVPRODUCTION` client showed Local Files as
**not yet configured**, with an **Enable** button. It was initialized at
17:53 IST after checking the expected local path. Reopening settings confirmed
the configuration and both Documents/Photos switches off at 17:57 IST.
The controller now checks this setup explicitly before server downloads and
distinguishes unconfigured, running, and paused installations. It also waits
for the Local Files root to render before opening its folder list.

Photos became readable after switching to thumbnails and back to the list.
All nine uploaded photos and the pre-existing PDF were present at version 1.
This recovered the stuck view; the exact cause of the earlier service errors
and the unavailable sync configuration remains unconfirmed. No Reset, cleanup,
or re-upload was used for this recovery.

Resume attempt `20261103T123049Z-c1f54ab7` downloaded eight originals before
an Invoke call to Download failed to open Save As. A settled, hit-tested click
downloaded the affected photo successfully and its SHA-256 matched the source.
The runner now uses that click for Download. It still requires the expected
Save As filename and a completed download before recording a receipt.

Further diagnostics found that Kahua can expose a new breadcrumb and enabled
Refresh while retaining the previous folder's rows. The added root-content
guard rejected that live stale state. A slower sequence of view selection,
filter clearing, and refresh passed directly, but did not eliminate the service
failure during subsequent folder navigation. Recorded references include
`HU-20A974` at 18:18:19 IST and `HU-09EF0B` at 18:22:26 IST. Manual single-click
Photos navigation, with no worker running, also failed with `HU-A2C812` at
18:23:48 IST. Thumbnail navigation can restore a readable view temporarily;
it is not a confirmed permanent fix. The underlying service cause remains
unconfirmed.

The pre-reboot guarded resume, `20261103T125547Z-ff2dbc05`, stopped in
`checking-server` with `HU-9948E1` at 18:27:51 IST. It downloaded the Test1
document before the error and did not reach sync enable or copying. The journal
recorded that failure; the earlier successful 11-file Check is retained separately.
An independent settings check then reopened Local Files and verified both
Documents and Photos off at 18:30:02 IST. Its receipt is
`final-restored-sync-off.json` in the Windows worker directory.

After the user rebooted the Cloud PC, a three-file diagnostic passed from
19:09:08 to 19:12:20 IST. It reopened Local Files with Documents and Photos off,
downloaded one original from each of Test1, Test2, and Photos, and verified all
three lengths and SHA-256 hashes against the source; all versions remained 1.
The first diagnostic attempt had stopped before file operations because the
automation could not read a control's `Width` property. Manually selecting the
project and repeating from the settled view passed; that startup reader issue
has not been independently fixed.

The subsequent full Execute, `20261103T134526Z-f8a1ed73`, failed during
`checking-server` with `HU-917543` at **19:17:12 IST**, while opening Test1.
No original-download receipt was completed in that attempt, and it did not
reach sync enable or copying. The original state now records this latest
failure. The three-file success therefore does not establish a lasting reboot
fix or completion of the full workflow. A separate saved-off check then
confirmed both folder selections off after reopening at **19:20:06 IST**;
its receipt is `post-reboot-final-sync-off.json`.

The VM has **2 vCPUs and 8 GB RAM**. During the successful three-file test,
46 system-counter samples recorded average CPU **46.95%**, maximum CPU
**97.96%**, and minimum available memory **3164 MB**. These measurements were
not collected at the later service failure. They do not establish CPU or RAM
as the cause. A controlled 4-vCPU/16-GB comparison is a reasonable next test,
using the same client, CSV, saved state, and files, with resource measurements
throughout both the short check and full Execute.

Local Files setup is restored; the intermittent failure remains unresolved.
Client, service, connection, and VM resource causes have not been isolated.
Retain the error references for Kahua investigation and compare resource
capacity before concluding that the problem cannot be resolved. A fresh
successful Execute is still required. This build remains a supervised pilot,
not a validated unattended batch release.

The Windows copy suite passed **15 checks**, and the CSV/download suite passed
**26 checks**, including rejected mappings, conflicts, unsafe paths, independent
download validation, atomic journal replacement, and rejection of a bad later
ZIP before any desktop operation. The separate coordinator suite passed
**8 scenarios** with simulated Kahua: two-project execution, resume,
check-only mode, uncertain sync-off recovery, initial setup, and paused setup.
These are 49 passing checks
and scenarios in total; simulated results are not live service validation.

The copy and download fixture suites passed at 16:30 IST; the expanded
coordinator suite passed after the setup changes at 18:00 IST. After the
Download click fix, all eight PowerShell files passed parsing on Windows
PowerShell 5.1 and their hashes matched the local source at 18:08 IST. The final
deployed files were checked again at 18:31 IST: all eight hashes matched and
all eight parsed without errors. The real Seattle CSV completed Plan with all
11 source files staged. Plan records local
state only; the separate server check contains the downloaded-byte evidence.

The Windows worker and original pilot state are under
`C:\Users\MigrationEngineer\Documents\KahuaMigrationWorker-20261103`.
The original mapping is `seattle-heights.json`; the baseline is
`seattle-baseline.json`; original pilot evidence is in `runs\seattle-heights`.

Large archives, virtualized long file lists, concurrent server edits,
interruption recovery in the live client, disconnected sessions, and a
100–200-project production batch remain unvalidated. Run a small supervised
batch before increasing scale. Missing root folders and changed UI selectors
produce a stop requiring correction; they are not silently skipped.
