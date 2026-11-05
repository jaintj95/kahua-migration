# Enable or disable sync for a CSV of Kahua projects

Double-click **`Start-SyncFlags.cmd`** in the repository folder for the guided
workflow, or use `scripts/windows-local-sync/Invoke-KahuaSyncFlags.ps1` from
PowerShell. It searches for each exact `project_name`, opens that project, sets
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
  The guided launcher also uses `Start-KahuaSyncFlags.ps1` in that directory
  and `Start-SyncFlags.cmd` at the repository root. Keep the repository's folder
  structure intact when downloading an update.
  No Python, Playwright, ZIP, project UID, or source-file manifest is needed.
  After updating these files, open a fresh PowerShell session before running
  them so a previously imported helper cannot remain cached in memory.

## CSV

**Before using Doe's five-project test CSV:** change `222 - Bellevue Park` to
Kahua's exact name, `221 - Bellevue Park`, in a reviewed copy. The original
file was preserved during testing. Automatic history gives corrected inputs a
separate batch. If supplying an explicit `-StateRoot`, use a new state directory
for corrected inputs and retain the original one.

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

## Guided launcher

1. Sign in to the intended Kahua Windows client and prepare the desktop above.
2. Double-click `Start-SyncFlags.cmd` in the downloaded repository folder.
3. Enter the CSV path, environment, and `Enable`, `Disable`, or `FromCsv`.
   The environment prompt defaults to `PRODUCTION`. For a names-only CSV, enter
   the exact domain already selected in Kahua; this is checked before any change.
4. The launcher runs Plan and checks every project, then shows the requested
   settings for review. After successful checks, type **`APPLY`** to apply the displayed
   settings, or press Enter to finish without changing switches.
5. Read the final result. `flags-verified` confirms settings only; check Kahua
   separately to establish that files finished transferring.

The same launcher works for subsequent batches and retries. It manages history
automatically; you do not need to invent timestamps or supply `StateRoot`.
After an interruption, resolve any reported application issue and start it
again with the same inputs. It checks the actual switches again before writing.
The command window stays open so you can read errors and report paths.

You can also start the guided flow from PowerShell:

```powershell
.\scripts\windows-local-sync\Start-KahuaSyncFlags.ps1
```

Or prefill the answers:

```powershell
.\scripts\windows-local-sync\Start-KahuaSyncFlags.ps1 `
  -CsvPath "$env:USERPROFILE\Downloads\sameProjectsEnableSync.csv" `
  -Environment PRODUCTION -Action Enable
```

If Windows blocks downloaded scripts, use your organization's approved script
execution policy. The launcher does not change that policy. It starts a fresh
Windows PowerShell process when launched through the `.cmd` file.

## Automatic history and retries

`StateRoot` is **optional**. By default, each batch's history lives under
`%LOCALAPPDATA%\KahuaMigration\SyncFlags\batch-<id>`. The runner prints the exact
folder and stores it in `result.json`. It derives the batch ID from normalized
project rows, requested switch values, environment, and domain inputs.

| What changes? | What the script does |
| --- | --- |
| Plan, Check, Execute, or retry with the same inputs | Reuses the batch history; creates a new attempt log/report |
| Add/remove/reorder projects, change environment/domain, or reverse the requested switches | Selects a different batch folder automatically; keeps earlier history |
| Rename/move the CSV, change line endings, or change ignored `project_uid` values | Reuses history when the normalized requests are identical |
| A switch already has the requested value | Reopens settings to verify it; does not toggle or Save again |
| The CSV changes while the launcher waits for APPLY | Stops before applying the changed inputs; start again to review the new batch |

For names-only CSVs, the launcher asks for the expected domain. In direct
commands, supply `-Domain 'Exact domain'` to distinguish automatic histories
by domain. If you omit it, the first live read binds the selected domain;
later runs in a different domain stop. The script never guesses which domain
you intended or silently discards that binding.

Advanced options:

- `-HistoryRoot C:\Migration\flag-history` relocates automatically managed
  batches. Keep using the same location on subsequent runs.
- `-StateRoot C:\Migration\runs\existing-batch` resumes a previously chosen
  folder, including legacy batches. Its inputs must still match. Existing
  manual folders are not automatically searched or moved into the new history.
- Use either option, or neither; do not combine them. Keep history outside
  every Kahua sync directory. Never delete or edit `binding.json` to bypass an
  input mismatch, and never delete history to force a retry.

## Run from PowerShell

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
  -Environment DEVPRODUCTION -Action Enable -Mode Plan
```

Search all projects and read their current flags without changing them:

```powershell
.\scripts\windows-local-sync\Invoke-KahuaSyncFlags.ps1 `
  -CsvPath C:\Migration\sameProjectsEnableSync.csv `
  -Environment DEVPRODUCTION -Action Enable -Mode Check
```

After Check succeeds, enable:

```powershell
.\scripts\windows-local-sync\Invoke-KahuaSyncFlags.ps1 `
  -CsvPath C:\Migration\sameProjectsEnableSync.csv `
  -Environment DEVPRODUCTION -Action Enable -Mode Execute
```

Once the operator has verified that Kahua finished syncing, disable using the
same CSV. Automatic history chooses a separate batch for the opposite action:

```powershell
.\scripts\windows-local-sync\Invoke-KahuaSyncFlags.ps1 `
  -CsvPath C:\Migration\sameProjectsEnableSync.csv `
  -Environment DEVPRODUCTION -Action Disable -Mode Execute
```

For a `sync_enabled` CSV, use `-Action FromCsv`. Plan is the default mode when
`-Mode` is omitted; a mode must be changed to Execute before flags are written.

### Production commands

Use the current repository version containing `Start-SyncFlags.cmd` and the
production-aware adapter. Keep `Environment = 'PRODUCTION'`, even when the
client shows no environment label. Sign in to the production client manually.

For new batches and all retries started with automatic history:

```powershell
Set-Location -LiteralPath "$env:USERPROFILE\Desktop\Projects\kahua-migration"
.\scripts\windows-local-sync\Start-KahuaSyncFlags.ps1 `
    -CsvPath "$env:USERPROFILE\Downloads\sameProjectsEnableSync.csv" `
    -Environment PRODUCTION -Action Enable
```

Run the same command again after an interruption, or after updating the CSV
for another batch. The launcher chooses the history and checks projects before
offering APPLY. You do not need to edit a state-folder name.

**Resuming an older batch with a manually chosen folder:** pass its existing
`-StateRoot`. This example belongs to the original **one-project** supervised
production batch. It is suitable only for that batch's unchanged inputs:

```powershell
.\scripts\windows-local-sync\Start-KahuaSyncFlags.ps1 `
    -CsvPath "$env:USERPROFILE\Downloads\sameProjectsEnableSync.csv" `
    -Environment PRODUCTION -Action Enable `
    -StateRoot "$env:USERPROFILE\Documents\KahuaRuns\production-enable-20261105-204341"
```

The later CSV containing an additional `Unit 4923` is a different batch. Use
automatic history for that changed list and preserve the older folder. The
same rule applies to any added/removed projects or changed switch values.

The latest supervised production Check remains blocked by Kahua's **Unable to
sync / project of the same name already synced** warning. Resolve the existing
sync mapping conflict with the Kahua administrator, then close the dialog and
rerun. Clearing the message alone does not resolve the conflict. Automatic
history and recovery do not repair Kahua's mapping or prove file transfers.

Read the report path printed by each invocation. Check success is `checked`;
Execute success is `flags-verified`, with reopened readbacks in its project
receipts. `failed` or an `uncertain` row requires inspecting the reported issue.
Keep all attempts and their `progress.log` files.

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

### Bounded recovery

The default `-RecoveryAttempts 2` permits up to two additional attempts per
read or Save-reconciliation step for recognized temporary UI failures.
`-RecoveryAttempts 0` disables these
additional attempts; values from 0 through 3 are accepted by both entry points.
Existing waits and short read polling still apply. The runner does not restart
Kahua or sign in automatically.

Recovery retries fresh, read-only Local Files snapshots after expired UI
elements or explicitly recognized read timeouts. It also covers the
**project-open read after selecting a search result**: an
`ElementNotAvailableException`, including one wrapped by a `FindAll` call,
causes a fresh root, exact project header, and picker-closure read. The project
click is sent once. The header must be enabled, the picker must be closed, and
the environment/domain must match before continuing. One 45-second wait budget
covers those observations and all retries; recovery does not restart the clock.
An in-progress native UI call cannot be interrupted by this polling deadline.
Earlier Home/search navigation is not automatically replayed if it fails.

After a transient Save/wait/
readback failure, it can recheck the exact project, wait for a recognized
settings view to settle, close it, and reopen settings to verify the requested
value. **It never repeats a toggle or Save during that recovery.** A reopened
readback must match before the batch continues. Reconciled receipts include
`reconciled: true`; all recovery attempts and their errors are retained in
`result.json` under `recoveries` and in `progress.log`.

Changed identity/domain, duplicate or missing project names, sync errors,
unsaved-change warnings, unrecognized dialogs, and mismatched readbacks stop
the run. They are not classified as temporary based on message text. If an
outcome cannot be confirmed within the limit, it stays uncertain.

On an unrecovered failure, the queue stops. An error during Save or readback marks that row
`uncertain`, with the error retained in the report. Previously verified rows
remain recorded. Close or resolve the failed dialog, then rerun the same CSV,
action and state directory. The runner freshly reads the current state before
setting it; it does not blindly toggle the switch or trust an old success.
Do not delete state to retry. Automatic history selects a separate folder for
changed inputs; with an explicit `-StateRoot`, select a new folder yourself.

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

- **November 6 project-open recovery fix:** the supplied production Check log
  stopped at `project-open` when `KahuaDesktop.psm1`'s `FindAll` raised a wrapped
  `ElementNotAvailableException`. Build `30725ef` had Local Files recovery but
  did not cover this read. The local fix passes 22 navigation scenarios using
  the real control reader with a simulated provider, plus four full-selector
  regressions for stale header/picker reads, disabled recovery and an uncertain
  click. All 175 checks across seven platform-neutral suites pass on macOS
  PowerShell 7.6.9; all 19 PowerShell files parse. The guided launcher also
  verifies that `-RecoveryAttempts 0` reaches Check and both Execute selections.
  [Windows CI run 42094873358](https://github.com/jaintj95/kahua-migration/actions/runs/42094873358)
  passed all **224 checks across ten suites** and parsed **19 scripts** in
  Windows PowerShell `5.1.26100.33648` at commit `5c8bc8a`, including the new
  navigation suite. Initial CI exposed two fixture assumptions about UIA
  control-type values and default exception text; the fixture now uses numeric
  control IDs and explicitly reproduces the supplied error message.
  The recovery fix has not been deployed to the VM or checked against live
  Kahua. The log does not establish why Kahua invalidated the original element.
- The launcher/history suite passes 20 simulated scenarios on macOS PowerShell
  7.6.9 and Windows PowerShell 5.1, including
  automatic reuse, changed batches, preserved logs, legacy state, locks,
  corrupt bindings, interactive prompts, refusal after a failed Check, and a
  CSV changed during the APPLY prompt and Unicode history across retries.
  The recovery suite passes 24 scenarios,
  including a Save that persisted before its UI read failed, bounded stale
  reads, disabled recovery, unconfirmed outcomes, identity changes, warnings,
  and no repeated toggle/Save. [Windows CI run 42080880432](https://github.com/jaintj95/kahua-migration/actions/runs/42080880432)
  passed all **198 checks across nine suites** and parsed all **18 PowerShell
  files** at implementation commit `c098bfe`, using Windows PowerShell
  `5.1.26100.33648`. Twelve PowerShell documentation examples also parsed locally.
  Archive `30725ef` was extracted on the supervised VM on November 6 and the
  launcher reached its CSV prompt. A completed live Check/Execute of that
  version has not been verified. Hosted CI uses simulated controls and does
  not validate the client's actual UI selectors or repair its sync-name conflict.
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
  It runs the launcher/history, recovery, environment identity and Local Files error suites as well as
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
