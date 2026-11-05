# Kahua Windows automation handover and risk register

Updated November 5, 2026: **supervised flag pilot passed; full file-migration Execute validation is incomplete**.

**Overall remaining risk is High for unattended or 100–200-project operation.**
Kahua File Manager has repeatedly failed during both manual and automated
navigation. A separate Local Files log records an HTTP synchronization failure.
The automation can stop, preserve evidence, and check downloaded bytes.
A post-reboot three-file check passed, but the following full run failed again.
Client, service, network, and VM resource causes have not been isolated. The
failure remains unresolved; it has not been established as unresolvable.

This handover covers the Windows documents and photos workflow in
**DEVPRODUCTION / Onindus / 0001 - 220 - Seattle Heights**. The initial client
was `1.9.56.393`; later troubleshooting used `1.9.56.438`. Drawings use a
separate browser workflow. Running commands and recovery steps are in
[LOCAL_FILE_SYNC.md](LOCAL_FILE_SYNC.md).

The newer [project flag runner](SYNC_FLAGS.md) has a narrower purpose: it sets
the named project's own Folders switch, including whatever content Doe places
in that directory. It verifies the setting, not completed transfers. It does
not select parent or child projects. Its 55 simulated flag scenarios and 16
geometry/read scenarios passed locally and in Windows PowerShell 5.1. After
the Cloud PC upgrade to 4 vCPUs / 16 GB,
all five projects passed a live read-only Check, including nested projects.
A separate test CSV corrected `222 - Bellevue Park` to Kahua's exact name
`221 - Bellevue Park`; Doe's original CSV is preserved.

After the user returned VM access, the corrected runner completed Seattle
**On to Off at 02:02:01 IST, then Off to On at 02:03:11 IST on November 5**.
Both runs saved, closed, reopened, and returned `flags-verified`. Seattle was
restored to its original On setting at that time. The runner now waits for Folders to
become available and Save/Cancel to stay inactive before closing settings.

Two earlier attempts remain failed in their original reports. Disable
`20261104T172739Z-80beaea1` stopped during close with an Unsaved Changes Alert,
but an independent reopened read confirmed Off at 23:04:27 IST on November 4.
Enable `20261104T202355Z-d085bd7b` stopped when its waiter rejected the
temporarily disabled Folders control, but a reopened read confirmed On at
01:57:42 IST on November 5. Resuming each saved run read the persisted value
and made no further toggle or Save. The prior user-requested pause and its
Off readback remain in `sync-flags-20261104\handoff-20261104-230427.json`;
they are historical observations.
The pilot's final five-project read-only Check completed at **02:04:49 IST on
November 5**, confirming Seattle On and the other four Off. Zero migration
workers were confirmed, and the remote connection was released. The completed
record is `sync-flags-20261104\handoff-20261105-completed.json`.

A later debugging Check completed at **18:36:52 IST on November 5**, attempt
`20261105T130539Z-12534cb0`, with exit code 0 in 73.5 seconds. **All five
projects were Off**, including Seattle; every receipt confirmed a reopened
read. This is the latest observed state. The debugging session only ran
Plan/Check and made no project flag changes; it does not establish when or why
Seattle changed after the earlier pilot. The initial run had failed with a
missing `Width` property. The fix adds complete rectangle reads and bounded
fresh breadcrumb reads, and the remote viewport was expanded to expose the
project header. Six deployed source/test hashes matched local files; no worker
remained active. The original CSV and failed attempts were preserved. A separate
corrected CSV uses `221 - Bellevue Park`. The saved VM record is
`Documents\KahuaDebug-20261105\debug-handoff-20261105.json`.

Existing transfer and session risks remain relevant; the simpler scope does
not repair Kahua's internal synchronization mechanism. This flag-only test
does not establish that the VM upgrade resolved earlier File Manager errors.

## What is verified

- The supervised pilot uploaded 9 JPG photos and 2 documents, preserving the
  nested document folders. The five pre-existing local files retained their
  hashes during that pilot.
- An earlier full CSV Check independently downloaded all 11 source files:
  lengths and SHA-256 matched, and all versions were 1.
- After reboot, a three-file check passed at 19:12:20 IST with all source hashes
  and versions unchanged. The following full Execute failed with `HU-917543`
  at 19:17:12 IST. This demonstrates temporary success, not a lasting fix.
- The latest Execute stopped during server checking, before enabling sync or
  copying. It is recorded as failed, not complete.
- A separate check reopened Local Files and confirmed Documents and Photos
  off at **19:20:06 IST on November 3**. This establishes the saved settings at
  that time; it does not turn the failed Execute into a completed run.
- 49 checks/scenarios passed: 15 copy checks, 26 CSV/download checks, and
  8 simulated coordinator scenarios. All eight deployed scripts matched local
  hashes and passed Windows PowerShell 5.1 parsing at 18:31 IST. Simulated
  failures are not proof of recovery from every live Kahua failure.

## How to read the ratings

These are qualitative engineering judgments for this migration, not measured
failure probabilities. **Impact** describes the plausible consequence if the
problem is mishandled. **Remaining risk** includes the current safeguards and
the limits of live validation. A guard that stops a run reduces exposure; it
does not fix the underlying application behavior.

| Rating | Meaning for this migration |
| --- | --- |
| Critical | Potential bulk loss or destructive propagation requiring immediate stop and preservation of recovery evidence. |
| High | Possible incorrect migration or uncertain sync outcome, repeated blockage, or a material gap preventing unattended operation. |
| Medium | Recoverable interruption or configuration issue that still needs a supervised check. |
| Low | Limited operational inconvenience with a verified recovery and little effect on correctness. |

No cloud file loss or corrupted original was established in this pilot.
Critical consequences below describe potential harm, not an observed loss.
The register distinguishes observed incidents, documented product behavior,
automation defects, and untested risks.

## Risk summary

| ID | Issue or dependency | Impact | Remaining risk | Responsibility boundary |
| --- | --- | --- | --- | --- |
| K01 | Local Files HTTP sync failure | High | High until live recovery is verified | Kahua client/service and connection; cause unconfirmed |
| K02 | File Manager errors and a stuck loading state | High | High | Kahua investigation; automation must stop |
| K03 | New breadcrumb with old file rows | High | High for unattended use | Kahua supplies the view; automation owns its checks |
| K04 | Local Files unexpectedly unconfigured | High | Medium with setup readback | Account/installation setup; cause unconfirmed |
| K05 | Deletion propagation and uncertain sync shutdown | Critical | High while sync is uncertain; Medium after verified off | Product behavior and operator handling |
| K06 | Changing or asynchronous desktop controls | High | Medium supervised; High unattended | Kahua UI changes and automation maintenance |
| K07 | Inactivity lock, restart, and session availability | High | High unattended | Kahua authentication and Windows session policies |
| K08 | Multiple app packages, missing folders, and RDP clipboard | High for wrong destination; Medium for interruption | Medium with identity checks | Installation, project preparation, and Windows administration |
| K09 | Concurrent edits, scale, and long or incomplete listings | High | High until validated | Shared project use, capacity, and untested automation limits |

## Observed Kahua incidents and external dependencies

### K01 Local Files HTTP synchronization failure

**Observed in the retained `FileSync.Log`:** line 3276 records
`Marker sync HTTP failure: status=n/a` at raw log time
`2026-11-03 12:13:56,220`. The exception chain contains
`System.Net.Http.HttpRequestException` and `System.Net.Http.HttpIOException`,
with `The response ended prematurely. (ResponseEnded)`. The preceding line
mentions possible connection issues. The timestamp does not identify its time
zone, so it must not be treated as a confirmed IST time.

Later entries show marker checks continuing, including a zero-marker,
zero-task result at `12:14:28,003`. This supports resumed background polling;
it does **not** establish successful transfer of any particular file. The log
does not identify a failed payload, prove data loss, explain the Local Files
setup change, or establish the cause of the separate File Manager errors.

**Risk and control:** a failed or interrupted sync request can leave the upload
outcome uncertain. A local copy, a running client, or an empty task count cannot
prove cloud completion. The runner requires independent downloads and matching
hashes before completion. It retains the source and run state when a check
fails. It does not currently parse every Local Files Status message or log
entry; supervision must cover serious sync notices as well as the run journal.

**Remaining action:** Kahua support and, if appropriate, the connection owner
must investigate the transport failure. A controlled live test must demonstrate
reconciliation after an interrupted sync request. A code retry or later polling
message alone is insufficient to close this High-risk item.

### K02 File Manager errors and stalled loading

Navigation and refresh produced repeated generic error dialogs. After some
errors, Refresh remained disabled and the grid exposed zero or stale rows.
The problem also occurred with manual Photos navigation and with no worker
running. Switching to thumbnails and back sometimes restored readability,
but subsequent failures disproved a permanent recovery.

| Reference | Recorded time or context on November 3 | Observed operation |
| --- | --- | --- |
| `HU-999CDF` | Execute attempt `20261103T071739Z-ebd32a3c` | Earlier navigation/download preparation failure |
| `HU-A180AB` | Execute attempt `20261103T104901Z-ebd9c5d3` | Server checking; journal also recorded an invalid click target |
| `HU-10C7EA` | 16:27:14 IST | Manual Photos navigation after reopening File Manager |
| `HU-20A974` | 18:18:19 IST | Root refresh after view and filter changes |
| `HU-09EF0B` | 18:22:26 IST | Documents navigation after settled root preparation |
| `HU-A2C812` | 18:23:48 IST | Manual single-click Photos tree navigation |
| `HU-9948E1` | 18:27:51 IST | Final saved-run check after the Test1 download |
| `HU-917543` | 19:17:12 IST | Post-reboot full Execute while opening Test1; no download receipt completed |

**Risk and control:** the batch cannot reliably inspect or download files.
Without a guard, an empty view could be mistaken for an empty server folder.
The runner detects the observed service dialog, loading indicator, and disabled
Refresh, stops the queue, and records the error and stack. It does not dismiss
dialogs to force uploads. The underlying cause remains outside the script's
control and is not proven to be exclusively server-side.

After reboot, the three-file check in
`post-reboot-smoke-20261103-190908` passed, including fresh original downloads
from Test1, Test2, and Photos. The full saved-state attempt
`20261103T134526Z-f8a1ed73` then failed again before sync enable or copying.
Reboot therefore did not remove the intermittent failure.

**Remaining action:** compare VM resources under the same workload as described
in K09, retain these references for Kahua investigation, and complete a fresh
saved-state Execute with download verification and saved sync off. Neither a
resource shortage nor an exclusively Kahua-side cause is established.
Remaining risk is High.

### K03 Breadcrumb and file rows disagree

Kahua displayed a new folder breadcrumb while retaining the previous folder's
rows. This occurred even when Refresh appeared enabled. An early automation
version trusted that combination and reported existing paths as missing.
**That inference was an automation bug**, even though the inconsistent view
came from the application. The affected attempt stopped before changing sync.

**Risk and control:** false absence could lead to duplicate uploads, unintended
new versions, or verification against the wrong folder. The current controller
requires matching breadcrumbs, changed and stable rows after navigation, both
required root folders, and no observed loading/service error. It clears file
selection after each download. The new guard rejected the live stale listing.

The checks use visible UI evidence, not an authoritative server snapshot.
Identical parent/child listings may conservatively stop, and not every possible
stale view has been tested. The guard is a mitigation, not a Kahua fix or a
guarantee that false absence is impossible. Remaining risk is High for
unattended use until the full workflow and relevant failure cases pass live.

### K04 Local Files became unconfigured

At 16:29 IST the client showed Local Files as not configured and displayed
Enable instead of the previously available folder settings. Troubleshooting
included restarts, a client update, and sign-in/account changes; their occurrence
does not prove which, if any, caused the setup change. No destructive Reset
was used to resolve it.

Enable was selected after checking the expected local path at 17:53 IST.
Reopening confirmed setup and both folder switches off at 17:57 IST, with a
further independent off check at 18:30 IST.

**Risk and control:** the old sync path or switches cannot be assumed to remain
valid after a session change. Execute now branches on the actual setup state:
unconfigured requires manual Enable or explicit `-InitializeLocalFiles`;
paused requires review and manual Resume; running proceeds to project checks.
It verifies the mapped path and reopens settings before server checking.
An update alone never triggers reinitialization. Remaining risk is Medium
under supervision; the original cause is unresolved.

### K05 Sync deletion and shutdown hazards

Kahua documents that deleting synchronized local files can delete them in
Kahua. Stopping selected folders through settings and using global Reset have
different effects; Reset clears all selections and local synced copies.
These are product behaviors, not demonstrated defects. They make ad hoc cleanup
unsafe while the sync outcome is uncertain. [Kahua Local Files Sync documentation](https://help.kahua.com/main/Content/4-DocumentationMngmt/File-Manager/local-files-sync.htm)

In the pilot, deselecting Documents and Photos removed the local project sync
directory while uploaded files remained in File Manager. This observed local
removal is not evidence of cloud deletion. Source ZIPs and staging were outside
the sync tree and remained available.

**Risk and control:** premature cleanup could propagate deletion; an error after
sync enable could leave sync active. The runner never deletes the destination
or invokes Reset. Failure preserves state rather than forcing sync shutdown
while transfer may be uncertain. Successful completion requires both switches
off after reopening settings. The uncertain-save scenario passed in simulation;
a full live interrupted-sync recovery remains unvalidated. The automation
cannot prevent a person or another process from deleting files in the sync tree.

Treat any serious sync notice or uncertain Save as High remaining risk with a
Critical potential consequence if cleanup is attempted. Preserve the ZIP,
staging, synced files, journal, and diagnostics. Reconcile server bytes and
verify saved off before cleanup. The 18:30 off receipt is specific to this
project and time, not blanket clearance for other projects or later activity.

For the separate flag runner, `flags-verified` means the requested boolean was
saved. Enable intentionally leaves the selected project On; it is not cleanup
clearance. The November 5 Seattle pilot restored On, its original setting.

### K06 Desktop controls change and load asynchronously

The update changed how the Account domain and drawer controls were exposed.
Other observed problems included duplicate accessibility objects, background
controls exposed beneath a modal, moving controls, stale objects after closing
the drawer, transient search results, delayed filter choices, and cached Local
Files rows while its root was loading. Invoke actions sometimes left a popup
open or failed to open Download's Save As dialog. The native filename field
did not support the value-reading method initially used.

The November 4 flag pilot also showed an Unsaved Changes Alert after Save,
despite a later reopened read confirming Off. During the November 5 retry,
Folders was temporarily disabled while Save completed. A single disabled Save
sample was insufficient to decide when to close, and the first replacement
waiter incorrectly rejected the disabled Folders control. These were automation
timing assumptions; the evidence does not establish a Kahua persistence defect.
The corrected flag waiter passed an actual Disable and Enable cycle with
reopened receipts. A future alert still stops the runner without automatically
discarding or saving again. This fix is scoped to the new flag adapter.

**Risk and control:** clicks can target the wrong surface, or operations can
fail despite a control being present. Current code scopes modals, deduplicates
controls, waits for stable targets, verifies the control under the click,
reacquires elements, and reads back the native filename. Positive and negative
account checks passed, as did repeated checks that kept the app open. The
corrected Download action produced a matching source hash in a direct check.

The automation maintainer owns these adaptations and regression checks.
Kahua controls future UI changes. Remaining risk is Medium when supervised and
High for unattended use on unvalidated builds, layouts, or long lists.

### K07 Inactivity locks and restarts

Kahua repeatedly reached its inactivity sign-in screen. Restarting a stalled
client required sign-in again. The previously observed account policy was
15 minutes; it is an account observation, not a universal Kahua default.

**Risk and control:** navigation and final saved-off checks can be interrupted.
Authentication and the interactive Windows session are dependencies; the worker
does not bypass them. Keep the desktop reserved and unlocked, supervise the
run, and resume the same CSV/state after authentication and reconciliation.
Disconnected and overnight operation remain unvalidated. Remaining unattended
risk is High.

### K08 Installation and environment issues

Two installed packages, Kahua for Windows and Kahua for Windows
(DEVPRODUCTION), created ambiguity during troubleshooting. The controller
requires a single Kahua window and checks environment, domain, exact project
number/name, the Local Files project, and the mapped path. An inspected second
test project had only Sheets; missing Documents/Photos is a preparation issue,
and the worker stops rather than creating or silently skipping required roots.

Mac-to-Cloud-PC clipboard transfer also failed or pasted stale content. An
effective Windows clipboard policy blocked redirection; local configuration
was changed under the user's authorization, and the user later confirmed a
fresh Mac-to-Windows paste. This is a Windows/RDP environment incident, not a
proven Kahua defect. It affects sign-in and deployment support. Future policy
or session changes remain an administrator dependency. Remaining risk is Medium
with identity and deployment-hash checks.

### K09 Concurrency and scale are not established

The post-reboot VM reported **2 vCPUs and 8536289280 bytes of physical RAM**
(the 8-GB Cloud PC configuration). The successful three-file test collected
46 system-counter samples: CPU averaged **46.95%** and peaked at **97.96%**;
at least **3164 MB** of memory remained available. Maximum committed memory
was **38.05%**. These are whole-VM measurements during the successful short
test, not CPU attribution to Kahua or measurements at the later failure.
They do not demonstrate memory exhaustion or prove a resource cause.

The next capacity comparison should use **4 vCPUs / 16 GB RAM**, the same
storage size, Kahua version, CSV/state, and dataset. Record resources and
timings through the three-file check and full Execute; preserve any errors.
Windows 365 supports resizing CPU and RAM, but the target license and an
appropriate cloud administrator role are required. Local Windows administrator
membership alone is insufficient. Available target licenses have not been
inspected and no resize or purchase has been performed.
[Microsoft Cloud PC resize requirements](https://learn.microsoft.com/en-us/windows-365/enterprise/resize-cloud-pc-single)

These are unvalidated risks, not observed Kahua data-loss incidents. Another
user could change a file between baseline checking and copying. The local
worker lock does not lock the Kahua project for other users. Download checks
cover the source manifest, not every unrelated server file or future change.
Large/virtualized listings, large archives, low disk space, long paths, and
100–200-project throughput have not been established by the 11-file pilot.

The worker rejects content conflicts and unsafe/overlapping paths, waits for
known server files to reconcile locally, copies without overwrite, and checks
existing source-path versions after copying. Those controls do not eliminate a
concurrent server edit or establish production capacity. Reserve a migration
window, validate representative sizes and complete listings, and grow through
small supervised batches. Remaining risk is High until those checks pass.

## Automation defects and development mistakes

These belong to the automation and its implementation process. They must not
be attributed to Kahua simply because they occurred while operating its UI.
Corrections and unresolved cases are recorded below; full live Execute is
still pending.

| Defect encountered | Consequence and correction | Remaining risk |
| --- | --- | --- |
| Trusted breadcrumb and apparent readiness before rows changed | Incorrect missing-file results. Added changed/stable row and root checks; live stale-view rejection passed. See K03. | High unattended |
| Selected the main window Close control while testing the Account drawer | Closed Kahua and required sign-in again. Corrected the drawer target, hit testing, and post-close window check. No upload occurred in that retry. | Medium |
| Used stale or ambiguous accessibility objects and an incorrect comparison API | Element-not-available or wrong-target failures. Added modal scoping, runtime-ID deduplication, fresh lookup, correct native comparison, and bounded waits. | Medium |
| Initial post-reboot control read had no `Width` property; repeated during November 5 Warehouse Check | Both attempts stopped before file work. The helper now uses UI Automation's property method, requires a complete rectangle, and briefly retries missing data. It preserves expired-control errors for fresh read-only lookup; 16 geometry/read fixtures and the 18:36 five-project Check passed. Persistent failures still stop. | Medium supervised; High unattended; original startup timing is not fully reproduced |
| November 4 pre-upgrade project-selection read had no `IsEmpty` property | The check stopped before selecting Seattle or changing sync. The shared complete-rectangle reader now covers this property as well as Width; incomplete/empty values are tested without invented coordinates. The original pre-upgrade startup sequence was not rerun. | Medium supervised; High unattended |
| November 5 breadcrumb enumeration used expired controls, a null name, or a temporarily absent breadcrumb | Read-only retries failed after project navigation with `ElementNotAvailableException`, a null-valued method call, or a misleading disabled-breadcrumb message. Reads now reacquire the entire snapshot for up to three seconds, use the property method for names, and wait for a nonempty breadcrumb. Partial snapshots are discarded; clicks and Save are not retried. Regression cases and the 18:36 five-project Check pass. | Medium; persistent UI provider failures still stop the queue |
| No visible progress while Check navigated and read settings | Operators could not distinguish waiting, failure, and activity. Added timestamped console output and a flushed per-attempt UTF-8 `progress.log`, project counts, step names, polling messages and failure context. A blocked native UI call cannot emit a heartbeat while blocked. | Low for tested logging; Medium for diagnosing a blocked provider call |
| Narrow remote viewport hid the selected-project header | A November 5 retry timed out although the header's exact identity remained in accessibility data with `IsOffscreen=True` and empty bounds. Expanding the remote window restored visible bounds. The guide requires both Kahua and the remote session to be large enough to show the project name. | Medium on untested layouts; identity checks deliberately stop |
| Assumed Invoke and immediate child-control availability were sufficient | Filter/search/folder/download actions failed or lagged. Added verified clicks, popup closure checks, and waits for refreshed controls. | Medium |
| Forced a nested module reload in the new flag adapter | Removed reporting helpers already imported by the coordinator. Removed the nested `-Force`; the helper-availability regression and 50-scenario suite passed in Windows PowerShell 5.1. | Low for the tested import order |
| Used a narrow selected-project picker for flag search | The picker could open outside the usable viewport. The flag runner now searches from Home; the corrected five-project Check included nested partitions. | Medium on untested layouts |
| Treated one disabled Save sample as completion | Close raised an Unsaved Changes Alert even though the selection later read back Off. Added a stable Save/Cancel/Close wait and explicit warning detection; the original failed report is retained. | Medium supervised; new warnings stop the queue |
| Save waiter rejected temporarily disabled Folders | The first Enable attempt stopped after Save had persisted On. The waiter now treats disabled Folders as pending while mutation still requires an enabled control. Real On-to-Off and Off-to-On runs passed with reopened receipts; recovery also avoided a repeated Save. | Low for the tested timing case; Medium on new client builds |
| Assumed the native Save As field exposed a value pattern | Could not verify or set the intended save path reliably. Added native text readback and exact filename/destination verification. | Medium |
| Windows short path aliases could defeat the staging/sync overlap check | Staging could be accepted inside sync. Canonicalized existing path ancestors; Windows safety fixtures passed. | Low for tested cases |
| PowerShell converted a null journal-replacement argument to an empty path | Atomic journal replacement failed in testing. Corrected the null argument representation; journal fixtures passed. | Low for tested cases |
| Development command path escaping and foreground focus mistakes | One trial used a wrong state-directory spelling; another intended test command did not run after focus changed. Preserved the separate trial, restored explicit paths/focus checks, and verified deployed hashes. Failed or unrun tests were not counted as passes. | Medium operational |
| Logging test assumed redirected console lines never wrap | Windows PowerShell 5.1 wrapped a long log path and caused a false fixture failure. The assertion now ignores inserted line breaks while requiring the complete path. All 55 flag scenarios passed afterward. | Low for the tested output formats |

## Evidence retained for support and handover

The Windows worker directory is
`C:\Users\MigrationEngineer\Documents\KahuaMigrationWorker-20261103`.
Names below are relative to it. The archive's `validation-summary.json` is a
portable summary; full live run files and the log snapshot remain on the VM.

| Evidence | Location and meaning |
| --- | --- |
| Source mapping and saved batch | `seattle-batch.csv` and `runs\seattle-batch` |
| Successful 11-file Check | Attempt `20261103T070300Z-b03e9da7` under `runs\seattle-batch\attempts`; independent downloaded originals and verification |
| Pre-reboot failed Execute | Attempt `20261103T125547Z-ff2dbc05`; `resume-guarded-20261103-182546.txt` |
| Successful post-reboot short check | `post-reboot-smoke-20261103-190908`: result, setup readback, sample manifest, original downloads, verification, and 46 resource samples |
| Post-reboot startup reader failure | `post-reboot-smoke-20261103-190503`: failed before file operations with missing `Width` property |
| Pre-upgrade preservation record | `pre-upgrade-20261104-194817.json`: idle worker and free state lock, preserved CSV/state/source inventory; no fresh Seattle saved-off verification because the selection reader stopped |
| Latest failed Execute | Attempt `20261103T134526Z-f8a1ed73`; `resume-post-reboot-20261103-191525.txt`; project journal records `HU-917543` and failed server checking |
| Pre-reboot saved-off readback | `final-restored-sync-off.json`, verified at `2026-11-03T13:00:02.6210244Z` |
| Latest saved-off readback | `post-reboot-final-sync-off.json`, verified at `2026-11-03T13:50:06.3510009Z`; both selections off after reopening |
| Reboot comparison summary | `post-reboot-comparison.json`; combines short-test resources, full failed attempt, and fresh off receipt |
| Final deployment checks | `final-script-validation.json`, eight matching hashes and no parse errors |
| Sync transport failure excerpt | `sync-error-evidence-20261103.json`; five matching log lines, including 3275–3278 |
| Preserved sync log | `FileSync-handover-20261103T130524Z.Log`, captured `2026-11-03T13:05:24.3164733Z`, 449651 bytes |
| Failed flag Disable and recovery | `sync-flags-20261104\state-seattle-disable\attempts`; original `20261104T172739Z-80beaea1` is still failed, with separate later readbacks and successful attempts |
| Failed flag Enable and recovery | `sync-flags-20261104\state-seattle-enable\attempts`; failed `20261104T202355Z-d085bd7b`, then no-change recovery `20261104T203042Z-c68caca7` |
| Successful flag Disable transition | `sync-flags-20261104\state-seattle-disable\attempts\20261104T203131Z-7c713f6e\result.json`; On to Off, saved and reopened |
| Successful flag Enable restoration | `sync-flags-20261104\state-seattle-enable\attempts\20261104T203241Z-ef26ed56\result.json`; Off to On, saved and reopened |
| Final five-project flag Check | `sync-flags-20261104\state-five-corrected-check\attempts\20261104T203346Z-9031f1d9\result.json`; all five reopened, Seattle On and other four Off |
| Completed flag handover | `sync-flags-20261104\handoff-20261105-completed.json`; parsed readback, source hashes, transition and recovery reports, final Check, 50 Windows fixture scenarios, and zero workers |

The saved log snapshot's SHA-256 was read back and verified:
`d0f566be1b5cdaab266bccafcc4a7658f621214d93cc9d2bdb30f4b630d6e5ca`.
Its source was the DEVPRODUCTION package's
`LocalCache\Local\Kahua Inc\Kahua.Windows\LocalState\settings\FileSync.Log`.
The live log remained open and continued growing; the checksum identifies the
saved snapshot, not the subsequently changing live file.

## Conditions for a full file-migration operational handover

These conditions apply to the older worker that copies and verifies files.
The narrower flag runner has a completed supervised flag pilot, with transfer
verification left to the operator as described in [SYNC_FLAGS.md](SYNC_FLAGS.md).

1. Investigate K01 and K02 using Kahua/connection evidence and a controlled
   capacity comparison. Prove live recovery and reconciliation; temporary
   readability or one successful short check cannot close these issues.
2. Complete a fresh Execute using the saved CSV/state: all expected downloads
   must match the source, and both folder selections must be off after reopening.
3. Exercise live interrupted sync, failed saved-off confirmation, changed
   content, and restart/re-authentication recovery without duplicate versions
   or destructive cleanup, in an authorized test scope.
4. Validate multiple representative projects, realistic file counts, long
   listings, storage headroom, and operational monitoring before increasing
   volume. If unattended operation is required, validate session loss and lock
   behavior explicitly.
5. Agree the remaining risk and operating responsibility. File and folder
   preservation is the tested scope; captions, albums, and other photo-field
   mappings remain a separate acceptance decision.

Carry this issue register into the final handover, including fixed issues.
For every new serious sync error or UI defect, record the version, account and
project context, time/time zone, exact message/reference, affected run phase,
observed effect, impact rating, control, remaining risk, and recovery evidence.
Keep product behavior, environmental problems, and automation defects distinct.
Close an issue only with relevant verification; preserve the original incident
and explain any change in rating.
