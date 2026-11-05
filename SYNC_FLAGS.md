# Enable or disable sync for a CSV of Kahua projects

Use `scripts/windows-local-sync/Invoke-KahuaSyncFlags.ps1` for Doe's simpler
workflow. It searches for each exact `project_name`, opens that project, sets
its **Local Files > Folders** switch, saves when needed, then closes and reopens
settings to verify the saved value.

The switch applies to that project's file directory. Documents, Photos, and
other content placed there are included by Kahua. The script does not select
parent or child projects or toggle their switches. It does not drill into
individual file folders. Kahua documents the project-level Folders switch in
[Using Local Files Sync](https://help.kahua.com/main/Content/4-DocumentationMngmt/File-Manager/local-files-sync.htm).

Doe extracts and places the files. Kahua performs the sync. This runner only
changes and verifies the flag; `flags-verified` **does not mean that files have
finished uploading**. Enabling leaves the flag on. Disabling is a separate run
after the operator checks transfer completion. Never remove local content while
it is still selected for sync: Kahua can propagate local deletions to the server.

## Prepare the desktop

- Run on Windows with Windows PowerShell 5.1 and Kahua for Windows. A Cloud PC
  is optional; an appropriate Windows computer with Kahua installed is enough.
- Sign in manually. If the CSV supplies `domain`, the runner selects that exact
  domain before searching projects. If `domain` is absent or blank for every
  row, Doe selects the domain first and the runner reads that selection.
  The environment is always checked. Use one domain per batch.
- Keep one Kahua window open, maximized and unlocked, and reserve the desktop
  while the runner works. Close existing dialogs and unsaved settings.
  Expand the remote desktop window too: Kahua's blue header must show the
  selected project name. A narrow viewport can hide that control and cause
  the identity check to time out. Do not resize the session during a run.
- Under **My settings > General**, enable **Show Project Number**. The CSV
  still needs only project names. The runner derives the displayed number to
  distinguish the complete project name from a similar name or numeric prefix.
- Also enable **Enable Full Navigation Breadcrumb** so the runner can verify
  the selected domain while working inside a project.
- Configure **My settings > Local Files > Enable** once if necessary. Review
  and resume a paused account before enabling any project. The runner does not
  click account Enable, Resume, Pause, or Reset. It can disable a project's flag
  while the account is paused.
- Keep all four runtime files together: `Invoke-KahuaSyncFlags.ps1`,
  `KahuaSyncFlags.psm1`, `KahuaSyncFlagDesktop.psm1`, and `KahuaDesktop.psm1`.
  No Python, Playwright, ZIP, project UID, or source-file manifest is needed.
  After updating these files, open a fresh PowerShell session before running
  them so a previously imported helper cannot remain cached in memory.

## CSV

**Before using Doe's five-project test CSV:** change `222 - Bellevue Park` to
Kahua's exact name, `221 - Bellevue Park`, in a reviewed copy. The original
file was preserved during testing. Use a new state directory for the corrected
input if the original CSV was already planned or checked.

Doe's supplied format works unchanged with `-Action Enable` or `-Action Disable`:

```csv
domain,project_name,project_uid
Onindus,220 - Seattle Heights,aaa-111
Onindus,777-Warehouse Building,bbb-222
```

Only `domain` and `project_name` identify the requested destination.
`project_uid` is ignored; the sample IDs are placeholders. If Doe supplies only
`project_name`, the runner uses the domain already selected in Kahua. Optional
`-Domain Onindus` adds a check against Doe's selection; it does not request a
switch. Fill `domain` on every row to request automatic selection. Partially
filled domain columns are rejected.

For different values in one batch, use `-Action FromCsv` with:

```csv
domain,project_name,sync_enabled
Onindus,220 - Seattle Heights,true
Onindus,777-Warehouse Building,false
```

`true` means enable and `false` means disable. Values such as `yes`, `1`, blank,
or a value conflicting with `-Action` are rejected. Use UTF-8 CSV and quote names
that contain commas. Keep exact spelling and case. Duplicate names, mixed
domains, and unsupported Windows directory names are rejected before any
desktop action. A duplicate exact name in Kahua also stops the run; the script
does not guess based on folder location or a placeholder UID.

## Run

Commands below run from the repository root. `Environment` is mandatory and
cannot be blank. It declares the intended environment and binds the saved run
state to it; it does not launch or switch environments. Use `DEVPRODUCTION` for
the test client and `PRODUCTION` for the production client (`launch.kahua.com`).
Production has no environment footer: the runner verifies that the open
window's executable belongs to the registered `Kahua.Windows_3hchyjmf292n6`
package. Missing labels, unknown packages, and a running devproduction client
are not accepted as proof of production. Other environments still require one
exact matching text label. Keep state outside every Kahua sync directory.

Plan the supplied CSV without touching Kahua:

```powershell
.\scripts\windows-local-sync\Invoke-KahuaSyncFlags.ps1 `
  -CsvPath C:\Migration\sameProjectsEnableSync.csv `
  -Environment DEVPRODUCTION -Action Enable -Mode Plan `
  -StateRoot C:\Migration\runs\enable-20261104
```

Search all projects and read their current flags without changing them:

```powershell
.\scripts\windows-local-sync\Invoke-KahuaSyncFlags.ps1 `
  -CsvPath C:\Migration\sameProjectsEnableSync.csv `
  -Environment DEVPRODUCTION -Action Enable -Mode Check `
  -StateRoot C:\Migration\runs\enable-20261104
```

After Check succeeds, enable:

```powershell
.\scripts\windows-local-sync\Invoke-KahuaSyncFlags.ps1 `
  -CsvPath C:\Migration\sameProjectsEnableSync.csv `
  -Environment DEVPRODUCTION -Action Enable -Mode Execute `
  -StateRoot C:\Migration\runs\enable-20261104
```

Once the operator has verified that Kahua finished syncing, disable using the
same CSV and a **new state directory** for the opposite action:

```powershell
.\scripts\windows-local-sync\Invoke-KahuaSyncFlags.ps1 `
  -CsvPath C:\Migration\sameProjectsEnableSync.csv `
  -Environment DEVPRODUCTION -Action Disable -Mode Execute `
  -StateRoot C:\Migration\runs\disable-20261104
```

For a `sync_enabled` CSV, use `-Action FromCsv`. Plan is the default mode when
`-Mode` is omitted; a mode must be changed to Execute before flags are written.

For a new production batch, retain one state directory for Plan, Check, Execute,
and retries with the unchanged CSV:

```powershell
$runner = '.\scripts\windows-local-sync\Invoke-KahuaSyncFlags.ps1'
$kahuaRun = @{
    CsvPath = "$env:USERPROFILE\Downloads\sameProjectsEnableSync.csv"
    Environment = 'PRODUCTION'
    Action = 'Enable'
    StateRoot = Join-Path $env:USERPROFILE (
        'Documents\KahuaRuns\production-enable-' + (Get-Date -Format yyyyMMdd-HHmmss)
    )
}
& $runner @kahuaRun -Mode Plan
& $runner @kahuaRun -Mode Check
# After Check succeeds:
& $runner @kahuaRun -Mode Execute
```

Assign the timestamped `StateRoot` once and retain its value. A state-directory
mismatch means that environment, project list, domain, or desired switch values
differ from its saved binding. Preserve that directory for recovery and choose
a new one for the changed batch. Do not delete or edit `binding.json` to bypass
the check. Plan validates inputs and state only; it does not prove desktop or
production compatibility.

## Results and recovery

The console prints timestamped progress with elapsed time, mode, phase,
project name, queue position, and the current step. For example:

```text
[+4.8s] [INFO] [Check/Preflight 1/5 | 220 - Seattle Heights] project-open: Opening ...
[+18.2s] [INFO] [Check/Preflight 1/5 | 220 - Seattle Heights] checked: Folders=On; account sync=running; reopened=True.
```

Each attempt also writes a flushed UTF-8 `progress.log` beside `result.json`.
Its path is printed near startup. Search, domain-switch, picker-close and Save
polling loops report that they are still waiting about every five seconds.
Other desktop calls print their step before entering the call; a blocked UI
Automation call cannot emit a heartbeat while it is blocked. A waiting message
is evidence that polling continued, not proof that file transfers are healthy.

To follow an existing attempt in a second PowerShell window, paste its printed
path and run:

```powershell
Get-Content -LiteralPath 'C:\Migration\runs\enable-20261104\attempts\<attempt>\progress.log' -Tail 30 -Wait -Encoding UTF8
```

Press Ctrl+C in that second window to stop following the log. Each retry gets
a new attempt folder, so use the new log path. Validation failures before a
safe state directory and exclusive lock are established appear on the console
only. Failures later in a run record `failed_step`, `failed_phase`, the project,
and a stack trace in `result.json`, with a final `STOPPED` line in the log.

Each attempt writes `StateRoot\attempts\<attempt>\result.json`, including the
requested value, exact project identity, reopened flag readback, account sync
state, and derived local directory. `binding.json` binds the state directory to
the requested projects and values. `selected-domain.json` also binds the actual
domain read from Kahua, including when Doe selected it manually. A later resume
in another domain stops before project lookup. Old attempt files are retained.

The runner checks every project's identity and current flag before the first
flag change. It returns to Home and uses **Select a Project > Search** for each
lookup, including projects in nested partitions. It rejects scrollable, capped,
or ambiguous result lists because their visible rows alone cannot prove that
the name is unique.

After changing a flag, the runner waits for Folders to become available and
for Save and Cancel to stay disabled while Close is available. It then closes
and reopens settings to read the saved value. A temporarily disabled Folders
control during Save is treated as pending, never as a completed save. An
Unsaved Changes Alert stops the run and remains open for inspection.

On a failure, the queue stops. An error during Save or readback marks that row
`uncertain`, with the error retained in the report. Previously verified rows
remain recorded. Close or resolve the failed dialog, then rerun the same CSV,
action and state directory. The runner freshly reads the current state before
setting it; it does not blindly toggle the switch or trust an old success.
Do not delete state to retry. A changed CSV or opposite action needs new state.

If Kahua displays **Unable to sync** because a project with the same name is
already synced, the runner reports that conflict and stops. A warning already
open at startup is also detected. Have the Kahua administrator reconcile the
existing project/sync mapping before retrying; do not use account Reset or
delete/rename synced folders to bypass it. The warning alone does not identify
the conflicting mapping. Preserve the same CSV and state directory, resolve
and close the dialog, and rerun Check before Execute.

The runner and the full migration worker share a desktop mutex and must not run
at the same time. It never copies, moves, deletes, downloads, hashes, or cleans
up project files and never claims to diagnose Kahua's transfer mechanism.

## Validation and current limits

- Doe's five-row CSV passed Plan for both Enable and Disable.
- The suite passes 55 scenarios under local PowerShell 7.6.9 and Windows
  PowerShell 5.1, covering input validation, strict boolean
  handling, exact-name matching, preflight, reruns, Disable, uncertain saves,
  mismatched receipts, state locking, partial selections, paused accounts, and
  the real setter's Save/readback decisions, asynchronous search refresh, and
  refusal of ambiguous or incomplete search results, transient disabled
  controls during Save, unsettled saves, and late unsaved-change warnings.
  Logging checks cover console/file output, failure context, preserved prior
  logs, Unicode names, and rate-limited wait messages. A separate suite passes
  16 geometry/read scenarios covering incomplete rectangles, expired controls,
  fresh breadcrumb lookup, temporarily absent breadcrumbs, deadline handling,
  and unrelated errors. Another 18 environment identity checks cover unlabelled
  production, wrong or missing package registration, another executable path,
  unavailable process/package reads, multiple windows, fresh identity checks,
  and unchanged exact-label checks for nonproduction environments.
  A further 16 Local Files error checks cover same-name and generic warnings,
  duplicate accessibility objects, startup/open-settings refusal, delayed
  warnings, background Folders controls, delayed readiness, and ambiguous or
  disabled controls.
  These fixtures use a simulated desktop; live observations are recorded
  separately below.
- The Windows PowerShell 5.1 workflow parses all PowerShell source/test files.
  It runs the environment identity and Local Files error suites as well as
  the flag suite alongside the existing copy, batch, and coordinator fixtures.
  See [Windows fixture runs](https://github.com/jaintj95/kahua-migration/actions/workflows/windows-local-sync.yml)
  for the result at a particular published commit.
- **Supervised flag pilot passed November 5.** The upgraded Cloud PC connected
  successfully with 4 vCPUs / 16 GB RAM. All five projects passed a live
  read-only Check, including nested projects, using a separate corrected CSV.
  Doe's original CSV says
  `222 - Bellevue Park`; Kahua shows `221 - Bellevue Park`. The original CSV
  was preserved and its exact-name mismatch stopped the earlier Check.
- Seattle completed both real transitions with the corrected runner:
  On to Off at **02:02:01 IST**, then Off to On at **02:03:11 IST on November 5**.
  Both runs returned `flags-verified` with `changed: true` and `reopened: true`.
  Seattle was restored to its original **On** state. The other four projects
  were only read, not enabled or disabled by the pilot.
- A final five-project Check completed at **02:04:49 IST on November 5**:
  Seattle On; Warehouse, Bellevue, 123Test and 456 Off. Every receipt confirmed
  a reopened read. No migration worker remained running, and the remote
  connection was released after saving the handover record.
- **Later debugging Check passed at 18:36:52 IST on November 5**, attempt
  `20261105T130539Z-12534cb0`, in 73.5 seconds. All five projects were Off,
  including Seattle, and all receipts confirmed reopened reads. This is the
  latest observed state; the earlier On receipt remains historical. This
  debugging session ran Plan/Check only and did not change project switches.
  The initial run had already failed with a missing `Width` property. Recovery
  also exposed expired and temporarily absent breadcrumbs and a project header
  hidden by a narrow remote viewport. The corrected reader and expanded window
  completed the five-project Check with exit code 0. The six deployed source
  and test files matched local SHA-256 hashes, and no worker remained active.
  The VM record is `Documents\KahuaDebug-20261105\debug-handoff-20261105.json`;
  the corrected CSV and original failed reports remain available there and
  in their respective run directories.
- **Production Check on November 5:** the production client exposed no
  `PRODUCTION` footer, so the original environment-label check failed. The
  updated adapter verified the active executable against the registered
  production package, verified the CSV domain, found the exact project, and
  read the account state as running. Kahua then blocked Local Files with
  **Unable to sync**, stating that a project of the same name was already
  synced. The fixed error handler also detected the still-open warning during
  a subsequent Check and recorded it in `result.json` rather than a generic
  Folders timeout. Production Enable was **not executed**; no project flag,
  existing local folder, or account sync setting was changed by these checks.
  The previous DEVPRODUCTION batch state was retained; the new production
  state is `Documents\KahuaRuns\production-enable-20261105-204341` on the VM.
  All **154 fixture checks** passed in Windows PowerShell 5.1 after deployment
  (15 local-copy, 26 batch, 8 coordinator, 55 flag, 16 geometry, 18 environment,
  and 16 sync-error checks). Source parsing and hashes are saved under that
  state directory in `final-checks-20261105-210422\validation.json`; these
  fixtures simulate UI/server behavior and do not establish live Enable.
  Production flag writes remain unvalidated until the existing sync conflict
  is resolved and Check passes.
- Earlier failed attempts remain recorded. The first Disable stopped during
  close with an Unsaved Changes Alert even though Off persisted. The first
  Enable stopped because the Save waiter rejected a temporarily disabled
  Folders control, even though On persisted. After independent reopened reads,
  both runs resumed without another toggle or Save. The new waiter requires a
  stable completed state before closing and explicitly stops on a late warning.
  This corrects the tested automation timing cases; it does not prove that
  Kahua can never show another warning or that its internal sync is repaired.
- Domain initialization passed for both an explicit Onindus CSV domain and
  the already-selected domain. Switching to a different domain has not been
  exercised. Duplicate-name and search-completeness guards deliberately stop
  when the UI does not provide enough evidence.
- Live writes were tested on Seattle only. Five successful project lookups do
  not establish unattended operation or 100-200-project capacity.
- The shared helper now reads complete rectangles through the UI Automation
  property method, retries missing data briefly, and preserves provider errors.
  Breadcrumb reads reacquire controls after a transient expired-element error
  and wait briefly when navigation temporarily removes the breadcrumb.
  These address the observed automation failures (`Width`, `IsEmpty`, and stale
  breadcrumb enumeration); they do not establish that Kahua's underlying sync
  or all desktop layouts are reliable. Persistent failures still stop the run.

Run fixtures without Kahua access:

```powershell
.\scripts\windows-local-sync\Test-KahuaSyncFlags.ps1
.\scripts\windows-local-sync\Test-KahuaDesktopGeometry.ps1
```

The historical [Windows handover](KAHUA_WINDOWS_HANDOVER.md) records known
client/UI and sync problems. The previous full migration runner remains
available in [LOCAL_FILE_SYNC.md](LOCAL_FILE_SYNC.md), with its own state and
validation requirements.
