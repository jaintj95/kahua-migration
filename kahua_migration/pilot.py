"""Prepare the explicitly approved four-file pilot, using copies of audited files."""

import argparse
import csv
import json
import shutil
from pathlib import Path

from .preflight import InputError, atomic_json, audit, inside, verify_manifest
from .runner import build_plan


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text())
    verify_manifest(manifest)
    uid = "b87288f1-1b8a-42e4-a0de-0d1467fdbf8c"
    records = [r for r in manifest["files"] if r["project_uid"] == uid and
               (r["sheet_number"] == "1" or (r["sheet_number"] == "ROADWAY-SIGN4" and r["upload_sequence"] == 3))]
    if [(r["sheet_number"], r["revision"]) for r in records] != [("1", "1"), ("1", "2"), ("1", "3"), ("ROADWAY-SIGN4", "1")]:
        raise InputError("The source no longer matches the approved four-file pilot")
    if args.out.exists():
        raise InputError("Pilot output already exists; refusing to overwrite source/plan evidence")
    args.out.mkdir(parents=True)
    source = args.out / "source"
    source.mkdir()
    versions, drawings, latest, folders = {}, [], {}, {}
    for record in records:
        seq, bid = record["upload_sequence"], record["version_set_uid"]
        folders[bid] = f"Drawings/{seq:02d}"
        dest = source / folders[bid] / (record["sheet_number"] + ".pdf")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(inside(Path(manifest["source_root"]), record["source_file"]), dest)
        versions[bid] = dict(project_uid=uid, project_name="Approved four-file pilot", project_kahua_entity_id="0",
                             version_set_uid=bid, version_name=record["batch_name"],
                             published_at=record["batch_date"], upload_sequence=seq)
        row = {"Sheet Name": record["sheet_number"], "Version": record["batch_name"],
               "Description": record["title"], "Date Issued": record["revision_date"]}
        drawings.append(row)
        latest[record["sheet_number"]] = (row, dest)
    (source / "Drawings/Current").mkdir()
    for sheet, (_, original) in latest.items():
        shutil.copy2(original, source / "Drawings/Current" / (sheet + ".pdf"))
    write_csv(args.out / "version_sets.csv", list(versions.values()))
    write_csv(source / "drawing_log.csv", drawings)
    write_csv(source / "current_set_log.csv", [row for row, _ in latest.values()])
    config = {"schema_version": 1, "source_root": ".", "revision_policy": "per_sheet_occurrence", "revision_start": 1,
        "projects": [{"project_uid": uid, "source_directory": "source", "drawing_log": "drawing_log.csv",
            "current_log": "current_set_log.csv", "version_folders": folders,
            "target": {"transport": "browser", "environment": "https://devproductionlaunch.kahua.com/",
                "domain_name": "Onindus", "project_number": "0001", "project_name": "220 - Seattle Heights",
                "folder_path": "Sheets/Migration Pilot"}}]}
    atomic_json(args.out / "migration.json", config)
    atomic_json(args.out / "provenance.json", {"source_manifest_id": manifest["manifest_id"],
        "scope": "Four-file pilot only; Current is derived from the selected history, not uploaded",
        "selected_records": records})
    result = audit(args.out / "version_sets.csv", args.out / "migration.json")
    atomic_json(args.out / "manifest.json", result)
    atomic_json(args.out / "run-plan.json", build_plan(result))
    print(json.dumps(result["summary"], indent=2))
    return 2 if result["summary"]["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
