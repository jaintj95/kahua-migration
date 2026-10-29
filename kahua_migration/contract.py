"""Validate the tenant-specific mapping; there are deliberately no guessed fields."""

import re

from .preflight import InputError, digest, exact_text


METADATA = ("sheet_number", "revision", "title", "revision_date", "batch_name", "batch_date")
WRITABLE = ("title", "revision_date", "batch_name", "batch_date")
WORKFLOW = ("submitted", "accepted", "released")


def validate_contract(contract, *, require_verified=True):
    if contract.get("schema_version") != 1:
        raise InputError("Unsupported contract schema")
    for name in ("environment", "service_origin", "domain_key", "app_name", "entity_def"):
        exact_text(contract.get(name), "contract." + name)
    if contract["app_name"] != "kahua_FileManager" or contract["entity_def"] != "kahua_FileManager.DrawingLogRevision":
        raise InputError("This adapter supports the File Manager DrawingLogRevision contract")
    if require_verified and (not contract.get("verified_at") or not contract.get("verification_evidence")):
        raise InputError("Tenant field/action mapping is unverified; complete the pilot before running uploads")
    fields = contract.get("fields", {})
    required = (*METADATA, "status", "is_current", "file_id", "file_parent_id", "file_version")
    for name in required:
        path = fields.get(name)
        if name == "file_parent_id" and path == "$folder_id":
            continue
        if not isinstance(path, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*", path):
            raise InputError(f"Set the verified field path for {name}")
    if len(set(fields[n] for n in METADATA)) != len(METADATA):
        raise InputError("Metadata fields must map to distinct Kahua attributes")
    reserved = {"id", "Id", "entityDef", "PartitionId", "DomainPartitionId", fields["status"], fields["is_current"]}
    for name in WRITABLE:
        if "." in fields[name] or fields[name] in reserved:
            raise InputError(f"{name} must map to a scalar attribute on the revision, not a reference or system field")
    workflow = contract.get("workflow", {})
    exact_text(workflow.get("draft_status"), "draft_status")
    statuses = [workflow["draft_status"]]
    for stage in WORKFLOW:
        step = workflow.get(stage, {})
        for name in ("activity_name", "set_name", "status"):
            exact_text(step.get(name), f"workflow.{stage}.{name}")
        statuses.append(step["status"])
    exact_text(workflow.get("superseded_status"), "superseded_status")
    statuses.append(workflow["superseded_status"])
    if len(statuses) != len(set(statuses)):
        raise InputError("The configured workflow statuses must be distinct")
    for path in contract.get("query_explicits", []):
        if not isinstance(path, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", path):
            raise InputError("Invalid query explicit path")
    if not 1 <= contract.get("page_size", 100) <= 500:
        raise InputError("page_size must be in 1..500")
    return digest(contract)


def value_at(entity, path, *, default=None):
    value = entity
    for name in path.split("."):
        if not isinstance(value, dict) or name not in value:
            return default
        value = value[name]
    return value
