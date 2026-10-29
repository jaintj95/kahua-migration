"""Explicit CSV-to-destination bindings for unattended project queues.

Columns are data, never templates or executable expressions. Repeated project
fields must agree on every version-set row. Existing JSON mappings remain valid.
"""

from copy import deepcopy
from collections import defaultdict

from .preflight import InputError, exact_text


SOURCE_FIELDS = ("source_directory", "drawing_log", "current_log", "current_folder", "drawings_root")
TARGET_FIELDS = ("environment", "domain_name", "project_number", "project_name", "folder_path",
                 "project_policy", "folder_policy", "record_type")


def merge_csv_config(config, versions):
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
        projects.append(project)
    projects.extend(p for uid, p in explicit.items() if uid not in groups)
    result["projects"] = projects
    return result


def validate_destination(target):
    """Validate provisioning scope before opening any browser or making writes."""
    from urllib.parse import urlsplit
    if target.get("transport") != "browser":
        raise InputError("The automatic destination worker requires browser transport")
    for field in ("environment", "domain_name", "project_number", "project_name", "folder_path"):
        exact_text(target.get(field), "target." + field)
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
