"""Readable source-audit diagnostics without changing the manifest or its decisions."""

from pathlib import Path
from collections import Counter


FIXES = {
    "SOURCE_MAPPING_MISSING": "For an export, keep its project_clean_name column. Older custom layouts can use source_directory in the project configuration.",
    "SOURCE_UNAVAILABLE": "Check --source-root, the extracted project directory and the drawing-log path shown above.",
    "TARGET_MAPPING_MISSING": "Check the named destination setting in the local configuration. Standard exports resolve project names and numbers automatically.",
    "VERSION_FOLDER_UNAVAILABLE": "Check the indicated version folder under this project's Drawings directory; fully extract the export before retrying.",
    "INVALID_DRAWING": "Check the indicated drawing-log row and original PDF, including the sheet filename, date and duplicate sheet numbers.",
    "TITLE_MISSING": "Restore the missing Description from the original drawing log.",
    "EMPTY_DRAWING_LOG": "Supply the complete drawing log from the export.",
    "EMPTY_BATCH": "Check that the drawing log includes this version_name and that the complete version set was exported.",
    "UNMAPPED_VERSION": "Check that version_sets.csv includes every version named in the drawing log.",
    "UNMAPPED_DRAWING_FILE": "Check that version_sets.csv includes this historical version folder. Current is comparison-only.",
    "UNLISTED_BATCH_FILE": "Check the original drawing log and version folder; every historical drawing file must be accounted for.",
    "DUPLICATE_VERSION_KEY": "Use one row per version set, with distinct version_set_uid, version_name and upload_sequence within the project.",
    "SEQUENCE_GAP": "Check that upload_sequence is 1 through N for the complete project export, with no duplicates or gaps.",
    "DATE_ORDER_CONFLICT": "Check the source published_at dates against upload_sequence; the script does not guess a different order.",
    "CURRENT_CHECK_UNAVAILABLE": "Check the indicated current-set log and Drawings/Current folder in the extracted export.",
    "CURRENT_MEMBERSHIP_MISMATCH": "Check that the current-set log contains exactly the latest historical sheets.",
    "CURRENT_VERSION_MISMATCH": "Check the current-set Version against the last occurrence of this sheet in upload_sequence.",
    "CURRENT_METADATA_MISMATCH": "Check the current-set title and date against the latest historical drawing-log row.",
    "CURRENT_FILE_MISSING": "Restore the indicated sheet PDF in Drawings/Current from the original export.",
    "CURRENT_BYTES_DIFFER": "Inspect the current and historical originals before proceeding; their SHA-256 hashes differ.",
    "TARGET_COLLISION": "Check why two source projects resolve to the same Kahua destination; they must not upload into one another's folders.",
    "SOURCE_DIRECTORY_COLLISION": "Check the project_clean_name values and export directories; distinct projects must not share one source folder.",
    "INVALID_EXPORT_MAPPING": "Check the indicated export value. project_clean_name and version_name must identify direct folder names, with consistent project fields on every row.",
    "AMBIGUOUS_EXPORT_LOG": "Keep one authoritative log for the project or select its exact filename in the optional per-project configuration.",
}


def render_audit_summary(manifest, project_uids=None, *, limit=None):
    selected = set(project_uids) if project_uids else None
    projects = [p for p in manifest["projects"] if selected is None or p["project_uid"] in selected]
    ready = sum(bool(p["ready_for_upload"]) for p in projects)
    lines = [f"Source audit: {ready} of {len(projects)} selected projects ready; {len(projects) - ready} blocked."]

    def append_issues(issues, batches=None):
        ordered = sorted(issues, key=lambda item: item["severity"] != "error")
        shown = ordered if limit is None else ordered[:limit]
        counts = Counter((item["severity"], item["code"]) for item in ordered)
        for (severity, code), count in counts.items():
            lines.append(f"  {severity.upper()} [{code}]: {count} issue(s)")
            if hint := FIXES.get(code):
                lines.append(f"    Fix: {hint}")
        for item in shown:
            context = []
            if bid := item.get("version_set_uid"):
                context.append(f"version {((batches or {}).get(bid) or bid)!r} ({bid})")
            if sheet := item.get("sheet_number"):
                context.append(f"sheet {sheet!r}")
            suffix = " | " + ", ".join(context) if context else ""
            lines.append(f"  {item['severity'].upper()} [{item['code']}]{suffix}: {item['message']}")
        if len(shown) < len(ordered):
            lines.append(f"  ... {len(ordered) - len(shown)} more issues; see the full audit-summary.txt and issues.json below.")

    global_issues = [i for i in manifest["issues"] if not i.get("project_uid")]
    if global_issues:
        lines.extend(("", "Export-wide issues:"))
        append_issues(global_issues)
    for project in projects:
        uid = project["project_uid"]
        issues = [i for i in manifest["issues"] if i.get("project_uid") == uid]
        errors = sum(i["severity"] == "error" for i in issues)
        warnings = sum(i["severity"] == "warning" for i in issues)
        status = "READY" if project["ready_for_upload"] else "BLOCKED"
        lines.extend(("", f"{status}: {project['project_name']} (project_uid={uid}) - {errors} errors, {warnings} warnings"))
        if project.get("source_directory"):
            lines.append(f"  Source directory: {Path(manifest['source_root']) / project['source_directory']}")
        if project["target"].get("project_lookup") == "unique_name":
            lines.append(f"  Kahua project: exact name {project['target']['project_name']!r}; number resolved in Project Finder during execution.")
        append_issues(issues, {b["version_set_uid"]: b["batch_name"] for b in project["batches"]})
        if any(i["code"] in ("SOURCE_MAPPING_MISSING", "SOURCE_UNAVAILABLE", "INVALID_EXPORT_MAPPING") for i in issues):
            lines.append("  File checks could not finish for this project. Rerun the plan after correcting these errors.")
    return "\n".join(lines) + "\n"


def write_audit_summary(manifest, output, project_uids=None):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    path = output / "audit-summary.txt"
    footer = f"\nFull audit log: {path}\nStructured issues: {output / 'issues.json'}\n"
    path.write_text(render_audit_summary(manifest, project_uids) + footer, encoding="utf-8")
    print(render_audit_summary(manifest, project_uids, limit=20) + footer, end="", flush=True)
    return path
