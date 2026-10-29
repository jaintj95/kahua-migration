# Current worker: manual setup and operation

This is the baseline before automatic project creation or navigation. The
entry point is `python -m kahua_migration browser pilot`, implemented in
`kahua_migration/browser_pilot.py`. It runs the batches after you sign in and
open the destination. No API key or paid model service is used.

**Current limits:** one project per invocation, at most four revision records
in total, and at most two PDFs per version-set batch. The complete 166-file
export cannot run through this command. Creating projects, creating folders,
choosing destinations and handling session expiry are manual steps.

The live four-file validation finished all three batches with correct metadata
and matching original PDF hashes, but required code corrections and supervised
recovery. A second run against its completed journal passed without changing
the journal. A fresh uninterrupted upload run and the complete CLI launch have
not been validated. This is a working calibration baseline, not a production
scale release.

## 1. Install locally

Use macOS, Python 3.10 or newer, Google Chrome, and the Xcode Command Line Tools
(`swiftc` is needed for the local macOS Vision OCR helper). Start in the
extracted package or repository directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[browser]'
mkdir -p output/tools/swift-cache state
swiftc -module-cache-path output/tools/swift-cache \
  scripts/ocr_screenshot.swift -o output/tools/ocr-screenshot
```

The commands below use the installed Chrome browser. The worker sets its
viewport to 1600 × 1000; leave that size unchanged. The OCR helper in this
baseline is macOS-specific. No Windows OCR implementation is included.

## 2. Prepare the local CSVs, PDFs and destination mapping

For a new small run, copy `examples/manual-pilot` to `manual-run`. The example
contains **synthetic CSV metadata only**, with no PDF files or existing journal.

```sh
cp -R examples/manual-pilot manual-run
```

Supply the actual PDFs and correct the example CSVs to match them. The example
layout is:

```text
manual-run/
  migration.json
  version_sets.csv
  source/
    drawing_log.csv
    current_set_log.csv
    Drawings/
      01/A.pdf
      02/A.pdf
      03/A.pdf
      03/B.pdf
      Current/A.pdf
      Current/B.pdf
```

`version_sets.csv` lists each version set once. `upload_sequence` must cover
1, 2, 3, ... in order. `published_at` is its batch date. The drawing log lists
each sheet occurrence, its exact version name, title and revision date.
Dates use `YYYY-MM-DD`. Each historical PDF basename must equal its `Sheet Name`
plus `.pdf`. The `Current` files and log are used for source reconciliation;
they are never uploaded.

Edit `manual-run/migration.json` to set the exact Kahua `domain_name`,
`project_number`, `project_name` and `folder_path`. Keep leading zeros in project
numbers. Map every version-set UID to its actual local folder. Paths are
relative to the configuration directory and source directory as shown in the
example. The supplied environment is the DEVPRODUCTION tenant used for
calibration; another tenant or layout needs validation.

Keep `revision_policy` as `per_sheet_occurrence` and `revision_start` as `1`.
The worker calculates revision 1 for a sheet's first appearance and increments
only when that same sheet appears again. A later batch does not make a newly
introduced sheet revision 2 or 3.

Run the source audit, then write a plan without opening a browser:

```sh
.venv/bin/python -m kahua_migration audit \
  --version-sets manual-run/version_sets.csv \
  --config manual-run/migration.json \
  --out output/manual-preflight

.venv/bin/python -m kahua_migration browser pilot \
  --manifest output/manual-preflight/manifest.json \
  --journal state/manual-run.sqlite \
  --ocr-executable output/tools/ocr-screenshot \
  --channel chrome --out output/manual-plan
```

Review `output/manual-preflight/manifest.json` and
`output/manual-plan/run-plan.json`. Resolve audit errors and review warnings.
The plan-only command also checks the four-record/two-file limits. Do not edit
the generated manifest by hand; update the input CSV/configuration and re-audit.

## 3. Manually create and open the destination

Start the command below. It opens a separate Chrome window and waits at a
terminal prompt. Close any earlier migration-controlled Chrome window using
the same `state/browser-profile` directory first; only one process can own it.

```sh
.venv/bin/python -m kahua_migration browser pilot \
  --manifest output/manual-preflight/manifest.json \
  --journal state/manual-run.sqlite \
  --ocr-executable output/tools/ocr-screenshot \
  --channel chrome --out output/manual-run-01 --execute
```

While the terminal is waiting:

1. Sign in in the new Chrome window and complete any authentication or
   environment-notice prompts.
2. Select the mapped domain and project. If the project does not exist, create
   it manually using your organization's required settings first.
3. Open **File Manager**. Keep the left navigation pane expanded.
4. Create the mapped destination folder if needed. The drawing destination
   must be a **Controlled** folder. Use a new empty folder for a new journal.
5. Open the exact folder path in the configuration, select the **Default**
   view, and clear search/filter selections. Verify the project number, project
   name and full folder path.
6. Return to the terminal and press Enter only when that folder is open.

The worker enables Kahua accessibility and checks the destination before
continuing. The existing example browser profile is sufficient for the bounded
`pilot` command; the separate general `browser run` command remains
uncalibrated and is not the command to use for this baseline.

## 4. What the worker does after Enter

For each complete version-set batch, it attaches the PDFs from disk, sets and
reads back the metadata, saves once, exports and reads the saved records,
downloads the originals and checks their hashes, then submits, accepts and
releases the batch. It verifies the history before starting the next batch.
At the end, it rechecks all records and downloads every original again.

Leave the browser and terminal running and do not operate the browser during
the run. Manual file-picker interaction is not part of this command. If it
stops, keep the browser state and evidence for diagnosis; a reported timeout
does not establish whether a Save completed.

## 5. Check the result and preserve the journal

Read `output/manual-run-01/run-report.json`. Success requires `passed: true`,
the expected `completed_batches`, a passing `metadata_audit`, and the linked
integrity report with `all_match: true`. A failure contains an `error` and
recovery guidance. The output directory retains screenshots, metadata
observations, exports and downloaded originals.

Keep `state/manual-run.sqlite` and any associated SQLite files. For a resume,
use the **same manifest and journal**, a new evidence output directory, and
manually reopen the same destination. Never delete or replace the journal to
force a retry into a populated folder. Pending outcomes require reconciliation;
the worker deliberately refuses to replay an uncertain upload.

For another project, prepare its inputs and destination separately, finish or
resolve the current run, then repeat these steps with its own journal.

## Existing completed validation in this workspace

This command applies only in the original workspace, where the manifest, source
PDFs and completed journal already exist. They are intentionally absent from
the source ZIP. It performs a completed-run verification after you manually
open `Onindus → 0001 - 220 - Seattle Heights → Sheets/Migration Worker Validation`:

```sh
.venv/bin/python -m kahua_migration browser pilot \
  --manifest output/worker-validation-proposal/manifest.json \
  --journal state/worker-validation.sqlite \
  --ocr-executable output/tools/ocr-screenshot \
  --channel chrome --out output/worker-validation-manual-resume --execute
```

Do not use a new journal with any of the already populated validation folders.
The source ZIP contains code, tests, documentation and examples, without browser
sessions, credentials, customer PDFs, local journals or run evidence.

## Baseline and checks

The Git tag for this handoff is `manual-pilot-v0.1.0`. All 21 Python modules
matched the SHA-256 values recorded for the 93-test passing suite on
October 29, 2026. No project-creation or automatic-navigation code was added
before this baseline. The existing tests can be run with:

```sh
.venv/bin/python -m unittest discover -s tests -q
```

The synthetic HTTP tests bind localhost; they do not call the live tenant.
The complete validation history and current limitations are in `README.md`.
