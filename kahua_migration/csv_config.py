"""Export-layout defaults and optional explicit bindings for project queues.

Columns are data, never templates or executable expressions. Repeated project
fields must agree on every version-set row. Existing JSON mappings remain valid.
"""

from copy import deepcopy
from collections import defaultdict

from .preflight import InputError, exact_text, inside, positive_int


SOURCE_FIELDS = ("source_directory", "drawing_log", "current_log", "current_folder", "drawings_root")
TARGET_FIELDS = ("environment", "domain_name", "project_number", "project_name", "folder_path",
                 "project_policy", "folder_policy", "record_type")


def _export_component(value, label):
    """Names from the export identify direct children, never arbitrary paths."""
    exact_text(value, label)
    if value in (".", "..") or any(c in value for c in '/\\:\x00'):
        raise InputError(f"{label} must identify one exported folder, not a path: {value!r}")
    return value


def _apply_export_defaults(project, rows, declared, source_root, csv_value):
    """Accept the supplied nine-column export without adding mapping columns."""
    clean_name = csv_value(rows, "project_clean_name")
    _export_component(clean_name, "project_clean_name")
    project["project_clean_name"] = clean_name
    project["source_layout"] = "plangrid_export"
    project["_source_directory_inferred"] = not project.get("source_directory")
    project.setdefault("source_directory", clean_name)
    target = project["target"]
    target.setdefault("project_name", clean_name)
    if not target.get("project_number"):
        # The number is read from the unique exact Project Finder result during
        # execution. project_sequence is an ordering field, never a number.
        target["project_lookup"] = "unique_name"
        target["source_project_uid"] = project["project_uid"]
    entity_id = csv_value(rows, "project_kahua_entity_id")
    if entity_id not in (None, "0"):
        positive_int(entity_id)
        if target.get("project_lookup") == "unique_name":
            target["export_project_entity_id"] = entity_id

    issues = project.setdefault("_export_issues", [])
    for field, suffix in (("drawing_log", "drawing_log.csv"), ("current_log", "current_set_log.csv")):
        # Explicit per-project overrides remain authoritative. Shared filename
        # defaults also accept the exporter's <project_clean_name>_ prefix.
        if declared.get(field) or csv_value(rows, field):
            continue
        filename = project.get(field, suffix)
        if "/" in filename or "\\" in filename:
            continue
        candidates = [f"{clean_name}_{filename}", filename]
        existing = []
        if source_root is not None:
            local_root = inside(source_root, project["source_directory"])
            # The ZIP's log prefix can sanitize the original project name
            # differently from the export directory (e.g. '-' versus '_').
            # Resolve only a unique direct-child log, never a recursive guess.
            if local_root.is_dir():
                existing = sorted(p.name for p in local_root.iterdir()
                                  if p.is_file() and (p.name == filename or p.name.endswith("_" + filename)))
        if len(existing) > 1:
            issues.append({"code": "AMBIGUOUS_EXPORT_LOG", "message":
                           f"More than one {field} file exists in {project['source_directory']}: {existing}. "
                           "Keep the intended export log unambiguous before retrying."})
        project[field] = existing[0] if existing else candidates[0]

    for row in rows:
        bid = row["version_set_uid"]
        if bid not in project["version_folders"]:
            name = _export_component(row["version_name"], "version_name")
            project["version_folders"][bid] = f"Drawings/{name}"


def merge_csv_config(config, versions, *, source_root=None):
    result = deepcopy(config)
    defaults = result.get("project_defaults", {})
    if not isinstance(defaults, dict):
        raise InputError("project_defaults must be an object")
    explicit = {}
    for project in result.get("projects", []):
        uid = exact_text(project.get("project_uid"), "project_uid")
        if uid in explicit:
            raise InputError(f"Duplicate project binding: {uid}")
        explicit[uid] = project
    groups = defaultdict(list)
    for row in versions:
        groups[exact_text(row["project_uid"], "project_uid")].append(row)

    def csv_value(rows, column):
        if not any(column in row for row in rows):
            return None
        values = {row.get(column, "") for row in rows}
        if len(values) != 1:
            raise InputError(f"Conflicting {column} values for project {rows[0]['project_uid']}")
        value = next(iter(values))
        return exact_text(value, column) if value else None

    projects = []
    for uid, rows in groups.items():
        declared = explicit.get(uid, {})
        project = {**deepcopy(defaults), **deepcopy(declared), "project_uid": uid}
        project["target"] = {**defaults.get("target", {}), **declared.get("target", {})}
        for field in SOURCE_FIELDS:
            value = csv_value(rows, field)
            if value is not None:
                if declared.get(field) not in (None, value):
                    raise InputError(f"CSV and JSON disagree on {field} for {uid}")
                project[field] = value
        for field in TARGET_FIELDS:
            value = csv_value(rows, "target_" + field)
            if value is not None:
                if declared.get("target", {}).get(field) not in (None, value):
                    raise InputError(f"CSV and JSON disagree on target.{field} for {uid}")
                project["target"][field] = value
        folders = dict(project.get("version_folders", {}))
        for row in rows:
            if row.get("version_folder"):
                value = exact_text(row["version_folder"], "version_folder")
                bid = row["version_set_uid"]
                if folders.get(bid) not in (None, value):
                    raise InputError(f"CSV and JSON disagree on version_folder for {bid}")
                folders[bid] = value
        project["version_folders"] = folders
        if "project_clean_name" in rows[0] and project["target"].get("transport") == "browser":
            try:
                _apply_export_defaults(project, rows, declared, source_root, csv_value)
            except InputError as exc:
                project.setdefault("_export_issues", []).append({"code": "INVALID_EXPORT_MAPPING", "message": str(exc)})
        projects.append(project)
    projects.extend(p for uid, p in explicit.items() if uid not in groups)
    result["projects"] = projects
    return result


def validate_destination(target):
    """Validate provisioning scope before opening any browser or making writes."""
    from urllib.parse import urlsplit
    if target.get("transport") != "browser":
        raise InputError("The automatic destination worker requires browser transport")
    for field in ("environment", "domain_name", "project_name", "folder_path"):
        exact_text(target.get(field), "target." + field)
    if target.get("project_lookup") == "unique_name":
        exact_text(target.get("source_project_uid"), "target.source_project_uid")
        if target.get("project_number") is not None:
            exact_text(target["project_number"], "target.project_number")
    else:
        exact_text(target.get("project_number"), "target.project_number")
    url = urlsplit(target["environment"])
    if (url.scheme != "https" or not url.hostname or not url.hostname.endswith(".kahua.com")
            or url.username or url.password or url.query or url.fragment):
        raise InputError("Use a Kahua HTTPS environment without credentials, query or fragment")
    for key in ("project_policy", "folder_policy"):
        if target.get(key, "existing_only") not in ("existing_only", "find_or_create"):
            raise InputError(f"target.{key} must be existing_only or find_or_create")
    if target.get("project_policy") == "find_or_create":
        exact_text(target.get("record_type"), "target.record_type")
    parts = target["folder_path"].split("/")
    if any(not p or p in (".", "..") or p != p.strip() or "\\" in p for p in parts):
        raise InputError("folder_path must contain exact slash-separated folder names")
    if len({p.casefold() for p in parts}) != len(parts):
        raise InputError("Repeated folder names cannot be distinguished by this tenant's breadcrumb controls")
    # Blank projects are created at the selected domain root. Existing nested
    # projects can be found by the domain-wide project search.
    if target.get("parent_path") or target.get("project_template"):
        raise InputError("This browser contract creates blank projects at the domain root; templates/parent paths need a separate contract")
    return target
