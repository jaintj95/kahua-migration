# CSV-driven automatic migration

This branch adds `browser migrate`. It reads the CSV, opens each mapped project,
creates missing projects and folders when enabled, and processes every version
set in order. You do not select destinations in the browser.

Kahua sign-in and MFA remain manual when Kahua requires them.
The command waits for sign-in automatically; no terminal Enter or destination
selection is required. The worker uses a local Chrome profile and no LLM or
Kahua API credentials.

## Prepare the inputs

Use Python 3.10+, Chrome, and the macOS OCR helper described in
[MANUAL_RUN.md](MANUAL_RUN.md). The worker requires a 1600 × 1000 browser viewport.

Copy `examples/automatic-queue` to a working directory. It contains synthetic
CSV metadata and configuration only; supply the actual PDFs.

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

Close any earlier migration process that owns the same browser profile. First
write a local audit and plan:

```sh
.venv/bin/python -m kahua_migration browser migrate \
  --version-sets my-migration/version_sets.csv \
  --config my-migration/migration.json \
  --journal state/my-migration.sqlite \
  --ocr-executable output/tools/ocr-screenshot \
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
  --ocr-executable output/tools/ocr-screenshot \
  --out output/my-run-01 --execute
```

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

An explicitly recognized file-download service error allows one read-only
retry after its error dialogs are acknowledged. Both attempts recheck the
selected revision and compare the returned bytes with the source. Unknown
errors, a second service failure, or a hash mismatch stop verification.
This retry does not apply to project/folder creation, uploads or workflow
commands.

The previous manual command is preserved on `main` and at
`manual-pilot-v0.1.0`. See `README.md` for the exact current live-validation
results and remaining limits of this branch.
