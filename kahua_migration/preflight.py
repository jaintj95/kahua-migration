"""Build an auditable upload manifest without contacting Kahua.

The export and explicit mappings are authoritative. Folder/name guesses, PDF
OCR, and LLM guesses never determine project IDs or revision numbers.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path


VERSION_COLUMNS = {
    "project_uid", "project_name", "project_kahua_entity_id", "version_set_uid",
    "version_name", "published_at", "upload_sequence",
}
DRAWING_COLUMNS = {"Sheet Name", "Version", "Description", "Date Issued"}
POLICIES = {"unresolved", "explicit", "per_sheet_occurrence", "batch_sequence"}


class InputError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def file_digest(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_csv(path, required):
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            headers = reader.fieldnames or []
            if len(headers) != len(set(headers)):
                raise InputError(f"Duplicate CSV headers: {path}")
            missing = required - set(headers)
            if missing:
                raise InputError(f"Missing columns {sorted(missing)}: {path}")
            result = []
            for row_number, row in enumerate(reader, 2):
                if None in row or any(v is None for v in row.values()):
                    raise InputError(f"Malformed CSV row {row_number}: {path}")
                if not any(row.values()):
                    continue
                row["_row"] = row_number
                result.append(row)
            return result
    except (OSError, UnicodeError, csv.Error) as exc:
        raise InputError(f"Cannot read CSV {path}: {exc}") from exc


def inside(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise InputError(f"A nonempty relative path is required: {relative!r}")
    root = root.resolve()
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise InputError(f"Path leaves the configured source root: {relative}")
    return resolved


def iso_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise InputError(f"Expected an exact YYYY-MM-DD date, got {value!r}")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise InputError(f"Invalid calendar date: {value!r}") from exc


def positive_int(value):
    if isinstance(value, bool) or not re.fullmatch(r"[1-9]\d*", str(value)):
        raise InputError(f"Expected a positive integer, got {value!r}")
    return int(value)


def exact_text(value, label):
    if not isinstance(value, str) or not value or value != value.strip():
        raise InputError(f"{label} must be nonempty with no outer whitespace")
    return value


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def audit(version_path, config_path):
    config_path = config_path.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("schema_version") != 1:
        raise InputError("Unsupported or missing configuration schema_version")
    base = config_path.parent
    source_root = inside(base, config.get("source_root", "."))
    versions = read_csv(version_path, VERSION_COLUMNS)
    if not versions:
        raise InputError("The version-set export is empty")
    from .csv_config import merge_csv_config
    config = merge_csv_config(config, versions)
    issues, projects, records = [], [], []
    files_hashed = {}

    def issue(code, message, project_uid=None, batch_uid=None, sheet=None, severity="error"):
        issues.append(dict(severity=severity, code=code, project_uid=project_uid,
                           version_set_uid=batch_uid, sheet_number=sheet, message=message))

    def hash_once(path):
        if path not in files_hashed:
            files_hashed[path] = file_digest(path)
        return files_hashed[path]

    bindings = {}
    for binding in config.get("projects", []):
        uid = exact_text(binding.get("project_uid"), "project_uid")
        if uid in bindings:
            raise InputError(f"Duplicate project binding: {uid}")
        bindings[uid] = binding
    grouped = defaultdict(list)
    for version in versions:
        uid = exact_text(version["project_uid"], "project_uid")
        grouped[uid].append(version)
    for uid in sorted(bindings.keys() - grouped.keys()):
        issue("UNUSED_PROJECT_BINDING", "Mapping has no rows in the version-set export", uid)

    target_owners = {}
    for uid, project_versions in sorted(grouped.items()):
        binding = bindings.get(uid, {})
        start_issues = len(issues)
        project = dict(project_uid=uid, project_name=project_versions[0]["project_name"],
                       batches=[], source_ready=False, ready_for_upload=False,
                       source_directory=binding.get("source_directory"), excluded_files=[])
        projects.append(project)
        names = {v["project_name"] for v in project_versions}
        if len(names) != 1:
            issue("PROJECT_NAME_CONFLICT", "One project UID has multiple source names", uid)
        target = binding.get("target") or {}
        project["target"] = target
        transport = target.get("transport", "api")
        if transport not in ("api", "browser"):
            raise InputError("target.transport must be browser or api")
        required_target = (("environment", "domain_name", "project_number", "project_name", "folder_path")
                           if transport == "browser" else ("environment", "domain_key", "project_number", "folder_path"))
        for field in required_target:
            if not target.get(field):
                issue("TARGET_MAPPING_MISSING", f"Set target.{field} explicitly", uid)
        try:
            if transport == "api":
                target_id = positive_int(target.get("project_id"))
                positive_int(target.get("folder_id"))
                export_ids = {v["project_kahua_entity_id"] for v in project_versions}
                if len(export_ids) != 1:
                    issue("TARGET_ID_CONFLICT", "Source rows disagree on target project ID", uid)
                elif next(iter(export_ids)) not in ("", "0", str(target_id)):
                    issue("TARGET_ID_CONFLICT", "Configured target differs from export project ID", uid)
                target_key = (transport, target.get("environment"), target.get("domain_key"), target_id,
                              str(target.get("folder_id")))
            else:
                target_key = (transport, target.get("environment"), target.get("domain_name"),
                              target.get("project_number"), target.get("project_name"), target.get("folder_path"))
            if target_key in target_owners:
                other_uid = target_owners[target_key]
                issue("TARGET_COLLISION", f"Target folder also bound to {other_uid}", uid)
                issue("TARGET_COLLISION", f"Target folder also bound to {uid}", other_uid)
                previous_project = next(p for p in projects if p["project_uid"] == other_uid)
                previous_project["ready_for_upload"] = False
            target_owners[target_key] = uid
        except InputError:
            issue("TARGET_IDS_UNCONFIRMED", "Explicit positive target project_id and folder_id are required. "
                  "Export value 0 is not a confirmed project mapping; it can refer to the domain root.", uid)

        policy = binding.get("revision_policy", config.get("revision_policy", "unresolved"))
        revision_start = binding.get("revision_start", config.get("revision_start", 1))
        project["revision_policy"] = policy
        project["revision_start"] = revision_start
        if policy not in POLICIES:
            raise InputError(f"Unknown revision policy: {policy!r}")
        if policy == "unresolved":
            issue("REVISION_POLICY_UNCONFIRMED", "Choose an explicit revision rule before upload", uid)
        if isinstance(revision_start, bool) or not isinstance(revision_start, int) or revision_start < 0:
            raise InputError("revision_start must be an integer >= 0")

        valid_versions = []
        for version in project_versions:
            try:
                exact_text(version["version_set_uid"], "version_set_uid")
                exact_text(version["version_name"], "version_name")
                version["sequence"] = positive_int(version["upload_sequence"])
                version["batch_date"] = iso_date(version["published_at"])
                valid_versions.append(version)
            except InputError as exc:
                issue("INVALID_VERSION_METADATA", f"CSV row {version['_row']}: {exc}", uid)
        for field in ("version_set_uid", "version_name", "upload_sequence"):
            duplicates = [key for key, n in Counter(v[field] for v in project_versions).items() if n > 1]
            if duplicates:
                issue("DUPLICATE_VERSION_KEY", f"Duplicate {field}: {duplicates}", uid)
        valid_versions.sort(key=lambda v: v["sequence"])
        if [v["sequence"] for v in valid_versions] != list(range(1, len(project_versions) + 1)):
            issue("SEQUENCE_GAP", "Upload sequence must cover 1..N exactly once for this complete project export", uid)
        dates = [v["batch_date"] for v in valid_versions]
        if dates != sorted(dates):
            issue("DATE_ORDER_CONFLICT", "Batch dates disagree with upload sequence", uid)
        if len(valid_versions) != len(project_versions):
            continue

        if not binding.get("source_directory"):
            issue("SOURCE_MAPPING_MISSING", "No explicit local source directory is mapped", uid)
            continue
        try:
            local_root = inside(source_root, binding["source_directory"])
            if not local_root.is_dir():
                raise InputError("Mapped source directory does not exist")
            drawing_path = inside(local_root, binding.get("drawing_log"))
            drawing_rows = read_csv(drawing_path, DRAWING_COLUMNS)
            project["drawing_log_sha256"] = hash_once(drawing_path)
        except InputError as exc:
            issue("SOURCE_UNAVAILABLE", str(exc), uid)
            continue
        if not drawing_rows:
            issue("EMPTY_DRAWING_LOG", "No drawing rows are present", uid)
        rows_by_version = defaultdict(list)
        known_version_names = {v["version_name"] for v in valid_versions}
        for row in drawing_rows:
            if row["Version"] not in known_version_names:
                issue("UNMAPPED_VERSION", f"Drawing-log row {row['_row']} has an unknown version {row['Version']!r}", uid)
            rows_by_version[row["Version"]].append(row)
        occurrences, latest, used_paths, seen_revisions = defaultdict(int), {}, {}, set()
        version_folders = binding.get("version_folders", {})
        for unused in sorted(version_folders.keys() - {v["version_set_uid"] for v in valid_versions}):
            issue("UNUSED_VERSION_BINDING", f"Mapped version UID not in CSV: {unused}", uid)
        for version in valid_versions:
            bid = version["version_set_uid"]
            rows = rows_by_version[version["version_name"]]
            batch = dict(version_set_uid=bid, batch_name=version["version_name"],
                         batch_date=version["batch_date"], upload_sequence=version["sequence"],
                         expected_files=len(rows), actual_files=0, bytes=0,
                         source_folder=version_folders.get(bid))
            project["batches"].append(batch)
            if not rows:
                issue("EMPTY_BATCH", "No drawing rows for this version set; confirm whether the export is complete", uid, bid)
            try:
                folder = inside(local_root, version_folders.get(bid))
                if not folder.is_dir():
                    raise InputError("Mapped version directory does not exist")
                if "current" in {part.casefold() for part in Path(version_folders[bid]).parts}:
                    raise InputError("Current is a derived set and must never be an upload source")
            except InputError as exc:
                issue("VERSION_FOLDER_UNAVAILABLE", str(exc), uid, bid)
                continue
            declared_count = (binding.get("expected_file_counts") or {}).get(bid)
            if declared_count is not None and declared_count != len(rows):
                issue("SOURCE_COUNT_MISMATCH", f"Database expects {declared_count} files; drawing log has {len(rows)}", uid, bid)
            seen_sheets, expected_paths = set(), set()
            for row in sorted(rows, key=lambda r: r["Sheet Name"]):
                sheet = row["Sheet Name"]
                try:
                    exact_text(sheet, "Sheet Name")
                    if sheet in (".", "..") or "/" in sheet or "\\" in sheet:
                        raise InputError("Sheet Name cannot be used as a safe PDF basename")
                    # Do not silently merge case-only aliases until Kahua identity is verified.
                    if sheet.casefold() in seen_sheets:
                        raise InputError("Duplicate/case-colliding sheet number in the same version set")
                    seen_sheets.add(sheet.casefold())
                    # Number source occurrences even if an earlier local PDF is missing.
                    # A missing file blocks the project; it must not renumber later rows.
                    occurrences[sheet] += 1
                    if not row["Description"]:
                        issue("TITLE_MISSING", "Sheet title is blank in the source log", uid, bid, sheet)
                    revision_date = iso_date(row["Date Issued"])
                    path = inside(folder, sheet + ".pdf")
                    expected_paths.add(path)
                    if path in used_paths:
                        raise InputError(f"The same physical file path is assigned twice (previous: {used_paths[path]})")
                    used_paths[path] = (bid, sheet)
                    if not path.is_file():
                        raise InputError(f"Missing PDF: {path.relative_to(source_root)}")
                    size = path.stat().st_size
                    with path.open("rb") as handle:
                        if size == 0 or not handle.read(1024).lstrip().startswith(b"%PDF-"):
                            raise InputError("File is empty or does not have a PDF header")
                    if policy == "explicit":
                        revision = exact_text(row.get(binding.get("revision_column", "Revision")), "source revision")
                    elif policy == "per_sheet_occurrence":
                        revision = str(revision_start + occurrences[sheet] - 1)
                    elif policy == "batch_sequence":
                        revision = str(revision_start + version["sequence"] - 1)
                    else:
                        revision = None
                    if revision is not None:
                        revision_key = (sheet.casefold(), revision)
                        if revision_key in seen_revisions:
                            issue("REVISION_REUSED", "Repeated revision label needs an explicit same-revision/version policy", uid, bid, sheet)
                        seen_revisions.add(revision_key)
                    key = digest([uid, bid, sheet])
                    record = dict(operation_key=key, project_uid=uid, version_set_uid=bid,
                                  batch_name=version["version_name"], batch_date=version["batch_date"],
                                  upload_sequence=version["sequence"], sheet_number=sheet,
                                  title=row["Description"], revision=revision, revision_date=revision_date,
                                  source_row=row["_row"], source_file=str(path.relative_to(source_root)),
                                  size_bytes=size, sha256=hash_once(path))
                    record["fingerprint"] = digest(record)
                    records.append(record)
                    latest[sheet] = record
                    batch["actual_files"] += 1
                    batch["bytes"] += size
                except (InputError, OSError) as exc:
                    issue("INVALID_DRAWING", f"Drawing-log row {row['_row']}: {exc}", uid, bid, sheet)
            for path in sorted(folder.rglob("*")):
                if path.is_file() and not path.name.startswith(".") and path.resolve() not in expected_paths:
                    issue("UNLISTED_BATCH_FILE", f"File is not accounted for by the drawing log: {path.relative_to(local_root)}", uid, bid)

        # Every drawing folder must be accounted for, including folders missing from the CSV.
        mapped_dirs = {inside(local_root, p) for p in version_folders.values() if isinstance(p, str) and p}
        current_folder_name = binding.get("current_folder", "Drawings/Current")
        current_folder = inside(local_root, current_folder_name)
        drawings_root = inside(local_root, binding.get("drawings_root", "Drawings"))
        if drawings_root.is_dir():
            for p in drawings_root.rglob("*"):
                if p.is_file() and not p.name.startswith("."):
                    rp = p.resolve()
                    if not rp.is_relative_to(current_folder) and not any(rp.is_relative_to(d) for d in mapped_dirs):
                        issue("UNMAPPED_DRAWING_FILE", f"No version-set mapping for {p.relative_to(local_root)}", uid)
        for path in sorted(local_root.rglob("*")):
            if path.is_file() and not path.name.startswith(".") and path.suffix.lower() != ".csv" and not path.resolve().is_relative_to(drawings_root):
                project["excluded_files"].append(str(path.relative_to(local_root)))
        if project["excluded_files"]:
            issue("OUTSIDE_DRAWINGS_SCOPE", f"{len(project['excluded_files'])} non-drawing file(s) are excluded; see excluded_files", uid, severity="warning")

        project["expected_current"] = {sheet: r["operation_key"] for sheet, r in sorted(latest.items())}
        project["current_comparison"] = dict(rows=0, matched_versions=0, identical_bytes=0, byte_differences=[])
        try:
            current_path = inside(local_root, binding.get("current_log"))
            current_rows = read_csv(current_path, DRAWING_COLUMNS)
            project["current_log_sha256"] = hash_once(current_path)
            stats = project["current_comparison"]
            stats["rows"] = len(current_rows)
            current_keys = [r["Sheet Name"] for r in current_rows]
            if len(current_keys) != len(set(current_keys)) or set(current_keys) != set(latest):
                issue("CURRENT_MEMBERSHIP_MISMATCH", "Current log and latest historical sheet membership disagree", uid)
            current_files = set()
            for row in current_rows:
                sheet = row["Sheet Name"]
                latest_record = latest.get(sheet)
                if latest_record is None:
                    continue
                if row["Version"] != latest_record["batch_name"]:
                    issue("CURRENT_VERSION_MISMATCH", "Current log does not point to the last occurrence in upload_sequence", uid, sheet=sheet)
                    continue
                stats["matched_versions"] += 1
                if row["Date Issued"] != latest_record["revision_date"] or row["Description"] != latest_record["title"]:
                    issue("CURRENT_METADATA_MISMATCH", "Current log title/date differs from the selected history row", uid, sheet=sheet)
                current_pdf = inside(current_folder, sheet + ".pdf")
                current_files.add(current_pdf)
                if not current_pdf.is_file():
                    issue("CURRENT_FILE_MISSING", "Current log refers to a missing PDF", uid, sheet=sheet)
                    continue
                current_hash = hash_once(current_pdf)
                if current_hash != latest_record["sha256"]:
                    stats["byte_differences"].append(dict(sheet_number=sheet, history_sha256=latest_record["sha256"], current_sha256=current_hash))
                    issue("CURRENT_BYTES_DIFFER", "Current PDF differs byte-for-byte from the history PDF. "
                          "This does not prove a visual difference; inspect before deciding source fidelity requirements.", uid, sheet=sheet, severity="warning")
                else:
                    stats["identical_bytes"] += 1
            if current_folder.is_dir():
                for p in current_folder.rglob("*"):
                    if p.is_file() and not p.name.startswith(".") and p.resolve() not in current_files:
                        issue("UNLISTED_CURRENT_FILE", f"Current folder has an unlisted file: {p.name}", uid)
        except (InputError, OSError) as exc:
            issue("CURRENT_CHECK_UNAVAILABLE", str(exc), uid)
        project_issues = issues[start_issues:]
        errors = [i for i in project_issues if i["severity"] == "error"]
        mapping_codes = {"TARGET_MAPPING_MISSING", "TARGET_IDS_UNCONFIRMED", "TARGET_ID_CONFLICT",
                         "TARGET_COLLISION", "REVISION_POLICY_UNCONFIRMED"}
        project["source_ready"] = not any(i["code"] not in mapping_codes for i in errors)
        project["ready_for_upload"] = not errors

    # Cross-batch aliases can otherwise create multiple Kahua identities for one source sheet.
    spellings = defaultdict(set)
    for record in records:
        spellings[(record["project_uid"], record["sheet_number"].casefold())].add(record["sheet_number"])
    for (uid, _), variants in spellings.items():
        if len(variants) > 1:
            issue("SHEET_CASE_COLLISION", f"Sheet identity changes case across batches: {sorted(variants)}", uid)
            project = next(p for p in projects if p["project_uid"] == uid)
            project["ready_for_upload"] = False
            project["source_ready"] = False
    records.sort(key=lambda r: (r["project_uid"], r["upload_sequence"], r["sheet_number"]))
    summary = dict(projects_in_export=len(grouped), version_sets_in_export=len(versions),
                   projects_with_local_history=sum(bool(p["batches"]) for p in projects),
                   source_files=len(records), source_bytes=sum(r["size_bytes"] for r in records),
                   expected_current_sheets=sum(len(p.get("expected_current", {})) for p in projects),
                   projects_ready_for_upload=sum(p["ready_for_upload"] for p in projects),
                   errors=sum(i["severity"] == "error" for i in issues),
                   warnings=sum(i["severity"] == "warning" for i in issues))
    manifest = dict(schema_version=1, mode="preflight_only", source_root=str(source_root),
                    version_sets_sha256=hash_once(version_path.resolve()), config_sha256=hash_once(config_path),
                    projects=projects, files=records, issues=issues, summary=summary)
    manifest["manifest_id"] = digest(manifest)
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
    return manifest


def reconcile(manifest, snapshot):
    """Compare a complete normalized target export. Never infer success from a count alone."""
    verify_manifest(manifest)
    if snapshot.get("complete") is not True:
        raise InputError("Target snapshot must attest complete pagination via complete=true")
    if snapshot.get("manifest_id") != manifest["manifest_id"]:
        raise InputError("Target snapshot does not reference this exact manifest")
    project_map = {p["project_uid"]: p for p in manifest["projects"]}
    all_expected = {r["operation_key"]: r for r in manifest["files"]}
    actual, mismatches = {}, []
    compared_fields = ("project_uid", "version_set_uid", "sheet_number", "title", "revision",
                       "revision_date", "batch_name", "batch_date", "size_bytes", "sha256")
    for row in snapshot.get("files", []):
        key = row.get("operation_key")
        if key in actual:
            mismatches.append(dict(operation_key=key, problem="duplicate_target_record"))
            continue
        actual[key] = row
        expected = all_expected.get(key)
        if expected is None:
            mismatches.append(dict(operation_key=key, problem="unexpected_target_record"))
            continue
        project = project_map[expected["project_uid"]]
        target = project["target"]
        for field in compared_fields:
            if row.get(field) != expected[field] or (field == "revision" and expected[field] is None):
                mismatches.append(dict(operation_key=key, problem="field_mismatch", field=field,
                                       expected=expected[field], actual=row.get(field)))
        target_fields = (("environment", "domain_name", "project_number", "project_name", "folder_path")
                         if target.get("transport") == "browser" else ("environment", "domain_key", "project_id", "folder_id"))
        observed_target = row.get("target", {}) if target.get("transport") == "browser" else row
        for field in target_fields:
            if target.get(field) in (None, "", 0, "0") or observed_target.get(field) != target[field]:
                mismatches.append(dict(operation_key=key, problem="target_mismatch", field=field))
        if not row.get("target_record_id"):
            mismatches.append(dict(operation_key=key, problem="target_record_id_missing"))
        expected_current = project.get("expected_current", {}).get(expected["sheet_number"]) == key
        if type(row.get("is_current")) is not bool or row["is_current"] != expected_current:
            mismatches.append(dict(operation_key=key, problem="current_flag_mismatch", expected=expected_current))
        expected_status = "Released" if expected_current else "Superseded"
        if row.get("status") != expected_status:
            mismatches.append(dict(operation_key=key, problem="status_mismatch", expected=expected_status, actual=row.get("status")))
    for key in sorted(all_expected.keys() - actual.keys()):
        mismatches.append(dict(operation_key=key, problem="missing_target_record"))
    unresolved = [p["project_uid"] for p in manifest["projects"] if not p["ready_for_upload"]]
    return dict(manifest_id=manifest["manifest_id"], compared_files=len(actual),
                expected_files=len(all_expected), unresolved_projects=unresolved,
                passed=not mismatches and not unresolved, mismatches=mismatches)


def verify_manifest(manifest):
    payload = {k: v for k, v in manifest.items() if k not in ("manifest_id", "generated_at")}
    if manifest.get("schema_version") != 1 or digest(payload) != manifest.get("manifest_id"):
        raise InputError("Manifest was edited or has an unsupported schema; rebuild it from the source")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    audit_parser = sub.add_parser("audit", help="Validate the source and write a local manifest; no network calls")
    audit_parser.add_argument("--version-sets", type=Path, required=True)
    audit_parser.add_argument("--config", type=Path, required=True)
    audit_parser.add_argument("--out", type=Path, required=True)
    compare_parser = sub.add_parser("reconcile", help="Compare a complete normalized target snapshot with a manifest")
    compare_parser.add_argument("--manifest", type=Path, required=True)
    compare_parser.add_argument("--snapshot", type=Path, required=True)
    compare_parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "audit":
            result = audit(args.version_sets, args.config)
            atomic_json(args.out / "manifest.json", result)
            atomic_json(args.out / "issues.json", result["issues"])
            print(json.dumps(result["summary"], indent=2))
            print(f"Manifest: {args.out / 'manifest.json'}")
            return 2 if result["summary"]["errors"] else 0
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
        result = reconcile(manifest, snapshot)
        atomic_json(args.out, result)
        print(json.dumps({k: v for k, v in result.items() if k != "mismatches"}, indent=2))
        return 0 if result["passed"] else 2
    except (InputError, OSError, ValueError, TypeError, KeyError) as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
