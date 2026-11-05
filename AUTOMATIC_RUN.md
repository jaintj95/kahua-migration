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

### Source inputs

For a new migration, copy `examples/automatic-queue` to a working directory
(`cp -R examples/automatic-queue my-migration` on macOS, or
`Copy-Item -Recurse .\examples\automatic-queue .\my-migration` in PowerShell).
It contains synthetic
CSV metadata and configuration only; supply the actual PDFs.
Replace example project mappings, logs and folders with your actual values.
For an existing migration, keep its unchanged inputs and existing journal.
JSON path values can use forward slashes on Windows; literal backslashes in
JSON must be doubled. Quote command-line paths that contain spaces.

`version_sets.csv` retains the existing version-set columns and adds explicit
project/destination columns. Repeat project fields consistently on every row
for that project. Preserve leading zeros in project numbers.

| Column | Meaning |
|---|---|
| `project_uid` | Stable source project identity |
| `project_name` | Source project name |
| `project_kahua_entity_id` | Retained export field; `0` does not identify a browser destination |
| `version_set_uid` | Stable version-set identity |
| `version_name` | Exact batch name |
| `published_at` | Batch date, `YYYY-MM-DD` |
| `upload_sequence` | Whole-batch order, starting at 1 |
| `source_directory` | Project source directory, relative to `source_root` |
| `version_folder` | This batch's PDF directory, relative to its source directory |
| `target_project_number` | Exact destination number |
| `target_project_name` | Exact destination name |
| `target_folder_path` | Exact slash-separated folder path |

Shared settings go in `project_defaults` in `migration.json`. CSV fields such as
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
.venv/bin/python -m kahua_migration browser migrate \
  --version-sets my-migration/version_sets.csv \
  --config my-migration/migration.json \
  --journal state/my-migration.sqlite \
  --out output/my-plan
```

Review `manifest.json`, `issues.json`, and `run-plan.json`. The plan does not
open a browser or change Kahua. Add `--execute` to run, using a new output
directory and the same journal:

```sh
.venv/bin/python -m kahua_migration browser migrate \
  --version-sets my-migration/version_sets.csv \
  --config my-migration/migration.json \
  --journal state/my-migration.sqlite \
  --ocr-backend vision \
  --out output/my-run-01 --execute
```

Check the report (success requires the literal boolean `true`):

```sh
.venv/bin/python -c 'import json; from pathlib import Path; r=json.loads(Path("output/my-run-01/run-report.json").read_text(encoding="utf-8")); print(r); assert r["passed"] is True'
```

### Windows plan, execution and result

```powershell
& .\.venv\Scripts\python.exe -m kahua_migration browser migrate `
  --version-sets .\my-migration\version_sets.csv `
  --config .\my-migration\migration.json `
  --journal .\state\my-migration.sqlite `
  --out .\output\my-plan
```

Review `manifest.json`, `issues.json` and `run-plan.json`. Resolve audit errors
and review warnings before executing. In the same PowerShell session, with
`$ocr` set as above:

```powershell
& .\.venv\Scripts\python.exe -m kahua_migration browser migrate `
  --version-sets .\my-migration\version_sets.csv `
  --config .\my-migration\migration.json `
  --journal .\state\my-migration.sqlite `
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
  --version-sets my-migration/version_sets.csv \
  --config my-migration/migration.json \
  --journal state/my-migration.sqlite --ocr-backend vision \
  --out output/my-run-02 --execute
```

In Windows PowerShell:

```powershell
& .\.venv\Scripts\python.exe -m kahua_migration browser migrate `
  --version-sets .\my-migration\version_sets.csv `
  --config .\my-migration\migration.json `
  --journal .\state\my-migration.sqlite `
  --ocr-backend tesseract --ocr-executable "$ocr" `
  --out .\output\my-run-02 --execute
```

Repeat the result check against `my-run-02`. Keep an in-progress migration on
its original machine and source paths. Manifests include the resolved source
root and records include platform-specific relative paths. Cross-platform
journal relocation is not implemented or validated.

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

Native Windows VM/display/layout calibration and an authorized live migration
remain required. No Windows VM/devbox access or real migration is part of these
tests. Existing supervised macOS live results in `README.md` are historical;
they do not validate the new Windows backend or fresh unattended uploads.

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
on a GitHub runner. It does not use or validate the user's Windows VM, real
tenant layout, sign-in/MFA or migration throughput.

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
