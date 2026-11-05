# CSV-driven automatic migration

The `browser migrate` command reads the CSV, opens each mapped project,
creates missing projects and folders when enabled, and processes every version
set in order. You do not select destinations in the browser.

Kahua sign-in and MFA remain manual when Kahua requires them.
The command waits for sign-in automatically; no terminal Enter or destination
selection is required. The worker uses a local Chrome profile and no LLM or
Kahua API credentials.

## Prepare the inputs

Use Python 3.10+ and installed Google Chrome on macOS or native Windows.
The worker requires a 1600 × 1000 browser viewport and English Kahua labels.
OCR reads the **browser screenshot** for controls and independent readback;
CSV files and drawing logs supply the input metadata. It does not OCR PDFs.
OCR runs locally, without a cloud service or runtime model download.

### macOS installation (Terminal)

Start in the repository directory. Install Python, Chrome and Xcode Command
Line Tools if absent (`xcode-select --install` supplies `swiftc`). Then:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[browser]'
mkdir -p output/tools/swift-cache state
swiftc -module-cache-path output/tools/swift-cache \
  scripts/ocr_screenshot.swift -o output/tools/ocr-screenshot
```

The existing Apple Vision helper is preserved. `--ocr-backend vision` uses
`output/tools/ocr-screenshot` by default. An existing invocation such as
`--ocr-executable output/tools/ocr-screenshot` still uses the JSON helper.

### Native Windows installation (PowerShell)

Start in the repository directory, for example `Set-Location 'C:\Migration\kahua-migration'`.
Install 64-bit Python (3.12 is used by the hosted fixture job) and Google Chrome.
Install Tesseract 5 with its English (`eng`) language data:

```powershell
winget install --exact --id UB-Mannheim.TesseractOCR
py -3 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e ".[browser,ocr]"
$ocr = Join-Path $env:ProgramFiles 'Tesseract-OCR\tesseract.exe'
& $ocr --version
& $ocr --list-langs
```

If installed elsewhere, set `$ocr` to that executable's full path. The language
list must contain `eng`. The [Tesseract installation guide](https://tesseract-ocr.github.io/tessdoc/Installation.html)
links the [UB Mannheim Windows installer](https://github.com/UB-Mannheim/tesseract/wiki)
if WinGet is unavailable. Installation needs network access; OCR execution does
not. Neither WSL, Swift nor the Kahua Windows desktop application is required.
Invoking the venv Python directly avoids PowerShell activation-policy changes.

Tesseract is a separate native install; the Python `ocr` extra supplies Pillow
for 2× screenshot scaling. Word coordinates are mapped back to original CSS
pixels. PNG bytes go through a local binary pipe, avoiding native Windows
Unicode filename and temporary-file locking problems. A label's confidence
is the **minimum measured word confidence / 100**,
with the existing 0.95 threshold unchanged. An uncertain label stops the action;
no perfect scores or expected text are substituted. Tesseract and Vision scores
are engine-specific, so synthetic success does not calibrate a live tenant.

### Backend selection

Without OCR options, `auto` selects Vision on macOS and Tesseract elsewhere.
Tesseract is found on `PATH` or at the standard Windows Program Files location.
An explicit `--ocr-executable` alone preserves the legacy JSON-helper protocol.
For a Tesseract override use **both** `--ocr-backend tesseract` and
`--ocr-executable "path with spaces\tesseract.exe"`. Supply one executable path,
not a shell command containing flags. `audit` and `pilot` accept the same OCR
options. Custom native helpers can use `--ocr-backend external`; they must emit
UTF-8 JSON with `image_width`, `image_height`, and `lines` containing `text`,
`confidence` (0–1), `x`, `y`, `width`, `height` in original screenshot pixels.

Planning needs only core Python. It does not import Playwright/Pillow, locate
an OCR executable, open Chrome or create a migration journal. Execution checks
the selected engine before launching Chrome.

### First-run settings

Run the drawings command without a configuration argument. It creates
`migration.local.json` locally if that file is missing. In a terminal it asks
for these settings; press Enter at any prompt to accept its default:

| Setting | Default |
|---|---|
| Kahua environment URL | `https://launch.kahua.com/` |
| Kahua domain | `TDOT.ECPCS` |
| Destination folder | `Sheets` |
| Create missing projects | Yes |
| Create missing folders | Yes |
| Drawing log filename | `drawing_log.csv` |
| Current-set log filename | `current_set_log.csv` |

The revision rule remains per-sheet occurrence, starting at 1. The source
comparison folder remains `Drawings/Current`. Project-specific CSV fields can
override the shared defaults, including environment and domain.

Later invocations reuse the saved settings without prompting or replacing the
file. `--config` can select another settings file; a missing file is created
there instead. `--no-input`, or running without an interactive terminal, accepts
the defaults without prompting. End-of-input accepts defaults for the remaining
questions. Cancelling setup leaves no partial configuration.

The script also manages its own generated files:

| File or directory | Behavior |
|---|---|
| `migration.local.json` | Created once and reused; ignored by Git |
| `state/drawings.sqlite` | Created on execution and reused for resume; override with `--journal` |
| `output/drawings/<timestamp>-<id>/` | A fresh report directory for each invocation; override with `--out` |
| `state/browser-profile/` | Created when Chrome opens and reused for sign-in |

All paths above are relative to the directory where you invoke the command.
Settings, journal and report paths are printed at startup. No configuration
template or generated state/report file needs to be copied or created manually.

### Source inputs

Supply the actual export CSV, drawing/current logs and PDFs. If they already
live in another parent directory, use `--source-root` to read them in place.
`examples/automatic-queue` is a synthetic reference layout, not required setup.
For an existing migration, keep its unchanged inputs and existing journal.
JSON path values can use forward slashes on Windows; literal backslashes in
JSON must be doubled. Quote command-line paths that contain spaces.

The default input parent is `drawings/` in the directory where you invoke the
command. Put the shared `version_sets.csv` and all project directories directly
under that parent:

```text
migration.local.json  (created automatically)
drawings/
  version_sets.csv
  Project A/
    Project A_drawing_log.csv
    Project A_current_set_log.csv
    Drawings/
      First Set/
      Later Revision/
      Current/
  Project B/
    Project B_drawing_log.csv
    Project B_current_set_log.csv
    Drawings/
      First Set/
      Current/
```

Set `source_directory` to the project directory name, such as `Project A`,
without a `drawings/` prefix. The `drawing_log`, `current_log`, `version_folder`
and `current_folder` mappings remain relative to that project directory. For
project-prefixed log filenames, set `drawing_log` and `current_log` to their
exact filenames in the CSV or per-project JSON mapping. The parent directory
can be changed with `--source-root`; no project mapping edits are needed when
only that parent moves.

For a new queue using the generated settings, the CSV must supply the exact
`source_directory`, `version_folder`, `target_project_number` and
`target_project_name` mappings. These identify the actual source files and
Kahua destinations; the first-run setup does not invent them. Add per-project
`drawing_log` and `current_log` values when filenames differ from the shared
defaults. Existing files can keep their names.

`version_sets.csv` retains the existing version-set columns and adds explicit
project/destination columns. Repeat project fields consistently on every row
for that project. Preserve leading zeros in project numbers.

| Column | Meaning |
|---|---|
| `project_uid` | Stable source project identity |
| `project_name` | Source project name |
| `project_sequence` | Project queue order: one positive integer repeated on every row for that project |
| `project_kahua_entity_id` | Retained export field; `0` does not identify a browser destination |
| `version_set_uid` | Stable version-set identity |
| `version_name` | Exact batch name |
| `published_at` | Batch date, `YYYY-MM-DD` |
| `upload_sequence` | Version-set order within each project, covering 1 through N |
| `source_directory` | Project source directory, relative to `source_root` |
| `version_folder` | This batch's PDF directory, relative to its source directory |
| `target_project_number` | Exact destination number |
| `target_project_name` | Exact destination name |
| `target_folder_path` | Optional override of the shared destination folder (`Sheets` by default) |

### Multiple projects in one queue

Put all projects in the same `version_sets.csv`. The worker processes projects
in ascending numeric `project_sequence`, completing each project's version sets
in ascending `upload_sequence` before opening the next project. For example:

```csv
project_uid,project_sequence,version_set_uid,upload_sequence
project-b,1,b-original,1
project-b,1,b-revision,2
project-a,2,a-original,1
project-a,2,a-revision,2
```

This is an ordering illustration; the real CSV also needs the other columns
listed above. CSV row order and project names do not override either sequence.
Every project must have a unique positive `project_sequence`; repeat it on all
that project's rows. Gaps between project numbers are allowed. Blank, invalid,
conflicting or duplicate project sequence values stop the audit before Chrome
opens. `--project-uid` filters the queue without changing its configured order.
Check the project array and sequence values in `run-plan.json` before execution.
Older CSVs with no `project_sequence` column keep their prior `project_uid`
sorting. For an existing migration, preserve source inputs and the journal;
use the new column when preparing a new queue.

Shared settings are saved in `project_defaults` in `migration.local.json`.
CSV fields such as
`target_domain_name`, `target_environment`, `drawing_log`, `current_log`,
`current_folder`, `target_project_policy`, `target_folder_policy`, and
`target_record_type` can override defaults. Existing per-project JSON mappings
also work; a conflicting explicit CSV/JSON mapping stops the audit.

Set each creation policy to `find_or_create` or `existing_only`. Creation
requires an explicit project record type. This contract creates blank projects
at the configured domain root, and Controlled folders. Project templates and
nested project creation are not supported. Existing nested projects can be
found by their exact number and name. Ambiguous exact matches stop the project.
Use one Kahua environment and domain per invocation.

The drawing log still supplies the exact sheet number, title and revision date.
Revisions increase per sheet occurrence, starting at 1. `upload_sequence` never
becomes the revision number. `Current` remains a comparison set and is never
uploaded. Every input PDF is hashed before execution.

## Plan and run

Close any earlier migration process that owns the same browser profile. Never
run two queues against the same profile or journal. First write a local audit
and plan. Use fresh output directory names for every attempt; examples below
assume they do not yet exist.

### macOS plan, execution and result

```sh
.venv/bin/python -m kahua_migration browser migrate
```

Review `manifest.json`, `issues.json`, and `run-plan.json` in the printed report
directory. The plan does not open a browser or change Kahua. Add `--execute`
to run, using a new output
directory and the same journal. The examples below give `--out` explicitly so
the result-check commands have a known report path; the directory is created
automatically. Omitting `--out` uses a fresh timestamped directory instead.

```sh
.venv/bin/python -m kahua_migration browser migrate \
  --ocr-backend vision \
  --out output/my-run-01 --execute
```

Check the report (success requires the literal boolean `true`):

```sh
.venv/bin/python -c 'import json; from pathlib import Path; r=json.loads(Path("output/my-run-01/run-report.json").read_text(encoding="utf-8")); print(r); assert r["passed"] is True'
```

### Windows plan, execution and result

```powershell
& .\.venv\Scripts\python.exe -m kahua_migration browser migrate
```

Review `manifest.json`, `issues.json` and `run-plan.json`. Resolve audit errors
and review warnings before executing. In the same PowerShell session, with
`$ocr` set as above:

```powershell
& .\.venv\Scripts\python.exe -m kahua_migration browser migrate `
  --ocr-backend tesseract --ocr-executable "$ocr" `
  --out .\output\my-run-01 --execute
$migrationExit = $LASTEXITCODE
$report = Get-Content -Raw -Encoding UTF8 .\output\my-run-01\run-report.json | ConvertFrom-Json
$report | Format-List
if ($migrationExit -ne 0 -or $report.passed -ne $true) { throw 'Migration did not pass; inspect the reports and preserve the journal.' }
```

Exit 0 means success, 2 means audit errors or a queue that did not fully pass,
and 1 means setup/execution stopped with an error. If setup stops before a
`run-report.json` exists, inspect the terminal error and source audit reports.
For either OS, also inspect each project's report and linked metadata and
original-file integrity reports; a successful process start is not a pass.

### Override the input parent

To use a different input parent, pass its path on the command line:

```sh
.venv/bin/python -m kahua_migration browser migrate \
  --source-root "/path/to/customer drawings"
```

On Windows, add `--source-root "D:\Migration\drawings"` to either PowerShell
command above to select that parent instead.

`--source-root` accepts an absolute path or a path relative to the working
directory and overrides `source_root` in the JSON configuration. The command
reads `<source-root>/version_sets.csv` unless you also supply `--version-sets`.
An explicit `--version-sets` path is used as given, relative to the working
directory when needed. For existing runs, supplying `--version-sets` without
`--source-root` retains the configuration's original source-root behavior
(relative to the JSON file). With neither option, the browser command uses
`./drawings` for both the CSV and project folders. A missing parent or CSV stops
the command before it opens a browser.

The standalone `audit` command also accepts `--source-root`; it still requires
an explicit `--version-sets` path.

The worker checks for a usable signed-in session. If Kahua shows its login
screen, sign in in the worker's Chrome window and acknowledge any environment
notice. The worker then finds
or creates the mapped destinations and starts the queue itself. Leave that
browser under the worker's control until the command finishes.

## Batch boundaries and verification

Each PDF is uploaded individually with its original batch name/date and its
calculated sheet revision. All files in a batch must finish metadata and
original-file hash verification before any submission begins. The worker then
submits every revision, accepts every revision, and releases every revision in
that batch. Each workflow dialog selects only the exact batch and sheet.
The next batch waits for the entire preceding batch's final history audit.

The full Kahua XLSX export and the unfiltered UI count establish complete folder
membership. Exact batch/sheet filters isolate each revision for independent UI
readback and original-file download. This avoids trusting the recycled cells
in Kahua's virtualized large log. No expected source value fills a missing
target field. Final verification checks all original hashes again, together
with titles, dates, batch names, revision history, current flags, workflow
status and one file version per revision.

The automatic command has no four-record/two-file batch limit. It intentionally
uses one file per upload and one revision per workflow action. Its throughput
across large projects has not been benchmarked. This is a correctness-first
implementation; local fixtures are not proof of production-scale reliability.

## Resume and reports

Read the top-level `run-report.json` and each project's batch report. A project
failure stops its remaining files and batches. The worker records that project
as `paused`, preserves its pending journal entries, and dismisses a recognized
upload form before continuing to the next project. It first tries Cancel, then
the form's Close action if needed. The exact observed "Upload Failed" notice
can arrive after cancellation; the navigator acknowledges it during recovery
or navigation to the next project. It does not select Retry or repeat Save.

The report distinguishes `completed`, `completed_with_pauses`, and `blocked`
queues. It lists projects not started and records whether a paused upload had
already reached its final Save intent. A queue with paused projects exits with
code 2 even when every other project completed.

If the UI cannot dismiss the form, the worker attempts one replacement tab,
preserving journal intents. **This tenant did not restore authentication in a
new tab during live testing.** Authentication expiry, a dead browser, or a form
that cannot close and requires a new sign-in can therefore still stop the
queue. Such a stop is reported explicitly; remaining projects are not marked
completed. Do not operate the worker's tabs while it runs.

Run again with unchanged CSV/configuration/source files, the same journal, and
a new output directory. The worker finds destinations again and reconciles any
pending outcome. A timed-out project/folder creation or upload is never blindly
repeated. If its outcome cannot be established, that project remains stopped.
Closing a dialog is not evidence that Save failed. A saved revision must pass
metadata and original-file verification before its pending intent is resolved.
An absent revision after an uncertain Save remains paused. A failure before
Save also stays pending until the existing evidence-based precommit
reconciliation establishes that retry is safe; this is not an automatic retry.
Do not delete a journal or start a new one to force a populated folder to replay.
Keep all evidence and SQLite files.

Resume commands reuse the exact CSV/configuration/source files and SQLite
journal. Only the output directory changes. On macOS:

```sh
.venv/bin/python -m kahua_migration browser migrate \
  --ocr-backend vision \
  --out output/my-run-02 --execute
```

In Windows PowerShell:

```powershell
& .\.venv\Scripts\python.exe -m kahua_migration browser migrate `
  --ocr-backend tesseract --ocr-executable "$ocr" `
  --out .\output\my-run-02 --execute
```

If the original run used `--source-root`, `--version-sets`, `--config` or
`--journal`, include the same options when resuming. This also applies to runs
started before the automatic defaults were introduced: keep their original
configuration and journal paths. Repeat the result check against `my-run-02`.
Keep an in-progress migration on its original machine and source paths.
Manifests include the resolved source root and records include platform-specific
relative paths. Cross-platform journal relocation is not implemented or validated.

An explicitly recognized file-download service error allows one read-only
retry after its error dialogs are acknowledged. Both attempts recheck the
selected revision and compare the returned bytes with the source. Unknown
errors, a second service failure, or a hash mismatch stop verification.
This retry does not apply to project/folder creation, uploads or workflow
commands.

The previous manual version is preserved at
`manual-pilot-v0.1.0`. See `README.md` for the exact current live-validation
results and remaining limits of this branch.

## Portability validation scope

The November 2026 portability work uses local adapter/CLI/regression tests,
generated screenshots with real OCR, and intercepted Chrome fixture pages.
`.github/workflows/drawings-portability.yml` runs the same synthetic suite with
native Windows Python, Tesseract and Chrome on GitHub-hosted Windows.
Hosted fixtures are separate from the user's Windows VM and live Kahua.

The user's Windows VM has also passed the fixture suite and a standalone
plan-only command. Actual Kahua display/layout calibration and an authorized
live Windows migration remain required. Existing supervised macOS live results
in `README.md` are historical; they do not validate the new Windows backend or
fresh unattended uploads.

Local verification on November 5, 2026: the complete 162-test suite passed with
Tesseract 5.5.4 on macOS. The same suite with the compiled Vision helper passed
161 tests and skipped the one Tesseract-specific binary-pipe test. Both runs
included all eight intercepted Chrome tests, original-file/hash and ordered
batch regressions, project isolation, and durable journal recovery. The cold
plan-only CLI also ran with Python site packages disabled. These are local
and synthetic results, not Windows VM or live Kahua validation.

The [hosted Windows run](https://github.com/jaintj95/kahua-migration/actions/runs/42032712135)
also passed all 162 tests on November 5, 2026, using native Python 3.12 and
Tesseract `v5.5.3.20260724`, including all eight Chrome fixtures and four real
OCR image tests. This verifies installation, native subprocess/UTF-8 paths,
binary PNG input, CSS coordinate scaling, engine checks and synthetic recovery
on a GitHub runner. It does not validate real tenant layout, sign-in/MFA or
migration throughput.

After adding `project_sequence`, all 171 tests passed on macOS with Tesseract
and in the [updated hosted Windows run](https://github.com/jaintj95/kahua-migration/actions/runs/42035787165).
The nine additional tests cover numeric project ordering, invalid/conflicting
sequence values, filtering, legacy CSV compatibility, stable journal operation
identities, sequential execution, and continuing after a recoverable project
failure.

Native Windows VM verification on November 5, 2026 used an isolated copy under
`C:\Users\MigrationEngineer\Documents\KahuaDrawings-20261105\code`, based on
commit `05f8c8c` plus the privilege-aware symlink test correction. The full suite
reported `Ran 171 tests` and `OK (skipped=1)`: 170 passed, with only the symlink
fixture skipped because this account lacks Windows symbolic-link creation
privilege (error 1314). That fixture passed on macOS and hosted Windows; the
production source-path checks are unchanged. All OCR, Chrome, queue-ordering,
revision and journal fixtures ran on the VM. Logs remain beside `code` in
`tests-v2-stdout.log` and `tests-v2-stderr.log`.

The VM's standalone plan-only command used Python `-S` (site packages disabled)
and a nonexistent OCR executable. It exited 0 with both synthetic projects
ready, project sequences `3, 20`, and version-set sequences `1, 2, 3` in each
project. No browser profile or journal was created. A second plan containing
duplicate `upload_sequence` values exited 2 and marked the affected project
not ready with `DUPLICATE_VERSION_KEY`, `SEQUENCE_GAP` and
`DATE_ORDER_CONFLICT`. Plans and issues remain under `plan-only` and
`plan-duplicate` beside `code`. Neither check connected to Kahua or uploaded
drawings.

To reproduce the local checks, first install `.[browser,ocr]` even on macOS
(Pillow renders synthetic test pixels). Compile Vision as above, then:

```sh
KAHUA_OCR_TESTS=1 KAHUA_BROWSER_TESTS=1 KAHUA_TEST_OCR_BACKEND=vision \
  .venv/bin/python -m unittest discover -s tests -v
```

On Windows, after the native Tesseract install, run the same fixture suite:

```powershell
$env:KAHUA_OCR_TESTS = '1'
$env:KAHUA_BROWSER_TESTS = '1'
$env:KAHUA_TEST_OCR_BACKEND = 'tesseract'
$env:KAHUA_TEST_OCR_EXECUTABLE = $ocr
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests use generated images, intercepted Chrome pages and localhost HTTP fixtures.
The browser tests create temporary profiles, never reuse the migration profile,
and never call Kahua. Running without the two opt-in variables skips real
OCR/Chrome integration tests. On macOS, Tesseract can be checked separately with
`brew install tesseract` and `KAHUA_TEST_OCR_BACKEND=tesseract` in the shell command.
