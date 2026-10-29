"""Kahua's documented transport plus a separately verified tenant field/action map."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import quote

from .contract import METADATA, WRITABLE, validate_contract, value_at
from .http_client import ApiError
from .preflight import InputError, inside, positive_int


def segment(value):
    return quote(str(value), safe="")


def date_value(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}(?:T00:00:00(?:\.0+)?(?:Z|\+00:00)?)?", value):
        raise ApiError("Date readback is not an unambiguous date or midnight ISO timestamp")
    return value[:10]


class KahuaAdapter:
    def __init__(self, client, contract):
        self.contract_id = validate_contract(contract)
        if client.origin != contract["service_origin"].rstrip("/"):
            raise InputError("The client service origin differs from the verified contract")
        self.client, self.contract = client, contract
        self.fields = contract["fields"]
        self.domain = segment(contract["domain_key"])
        workflow = contract["workflow"]
        self.status_map = {workflow["draft_status"]: "Draft", workflow["superseded_status"]: "Superseded"}
        self.status_map.update({workflow[s]["status"]: label for s, label in (
            ("submitted", "Submitted"), ("accepted", "Accepted"), ("released", "Released"))})
        self.hash_cache = {}

    def base(self, project):
        return f"/v1/domains/{self.domain}/projects/{positive_int(project['target']['project_id'])}"

    def check_target(self, project):
        target = project["target"]
        if target["environment"] != self.contract["environment"] or target["domain_key"] != self.contract["domain_key"]:
            raise InputError("Target environment/domain does not match the verified tenant contract")
        project_info = self.client.json("GET", self.base(project) + "/info", read_only=True)
        folder_info = self.client.json("GET", self.base(project) +
            f"/filemanager/folders/{positive_int(target['folder_id'])}/info", read_only=True)
        if str(project_info.get("id")) != str(target["project_id"]) or project_info.get("number") != target["project_number"]:
            raise ApiError("Live project ID/number differs from the explicit project mapping")
        if project_info.get("isArchived") is not False:
            raise ApiError("Target project is archived or its archive state is unknown")
        if str(folder_info.get("entityId")) != str(target["folder_id"]):
            raise ApiError("Folder lookup returned a different folder ID")
        normalized_path = lambda p: str(p).replace("\\", "/").strip("/")
        if normalized_path(folder_info.get("path")) != normalized_path(target["folder_path"]):
            raise ApiError("Live folder path differs from the explicit mapping")
        if folder_info.get("partitionEntityDef") != self.contract["entity_def"]:
            raise ApiError("Target folder is not confirmed to contain controlled drawing revisions")
        return {"project": project_info, "folder": folder_info}

    def snapshot(self, project, identity=None):
        """Read every revision, explicitly scoped to this folder, with stable pagination."""
        folder_id = positive_int(project["target"]["folder_id"])
        project_id = positive_int(project["target"]["project_id"])
        path = f"/v2/domains/{self.domain}/projects/{folder_id}/query?returnDefaultAttributes=true"
        seen, result, skip, count, marker = set(), [], 0, None, None
        while True:
            query = {"PropertyName": "Query", "EntityDef": self.contract["entity_def"],
                     "Partition": {"Scope": "Partition"}, "Skip": skip,
                     "Take": self.contract.get("page_size", 100),
                     "Sorts": [{"PropertyName": "Data", "Path": "Id", "Direction": "Ascending"}],
                     "Explicits": [{"PropertyName": "Data", "Path": p}
                                   for p in self.contract.get("query_explicits", [])]}
            if identity is not None:
                query["Condition"] = [{"PropertyName": "Data", "Path": self.fields[n],
                                        "Type": "EqualTo", "Value": identity[n]}
                                       for n in ("sheet_number", "revision")]
            page = self.client.json("POST", path, query, read_only=True)
            if (not isinstance(page.get("entities"), list) or type(page.get("count")) is not int
                    or page["count"] < 0 or "marker" not in page or page.get("skipped") != skip):
                raise ApiError("Query returned an incomplete pagination envelope")
            if count is None:
                count, marker = page["count"], page["marker"]
            elif page["count"] != count or page["marker"] != marker:
                raise ApiError("Folder changed while paging; restart verification")
            for entity in page["entities"]:
                eid = positive_int(entity.get("id"))
                if eid in seen:
                    raise ApiError("Query pagination repeated a revision ID")
                seen.add(eid)
                if (entity.get("entityDef") != self.contract["entity_def"]
                        or str(entity.get("PartitionId")) != str(folder_id)
                        or str(entity.get("DomainPartitionId")) != str(project_id)):
                    raise ApiError("A revision does not belong to the configured folder/project")
                result.append(self._normalize(entity, folder_id))
            if not page["entities"]:
                if len(result) != count:
                    raise ApiError("The target count disagrees with the collected revisions")
                break
            skip += len(page["entities"])
            if skip > count or skip > 1000000:
                raise ApiError("Query exceeded its declared total or folder safety limit")
        keys = [(r["sheet_number"].casefold(), r["revision"]) for r in result]
        if len(keys) != len(set(keys)):
            raise ApiError("Duplicate sheet/revision identities found in target folder")
        return result

    def find(self, project, record):
        rows = self.snapshot(project, identity=record)
        if len(rows) > 1:
            raise ApiError("More than one target revision matches the requested sheet/revision")
        if rows and (rows[0]["sheet_number"], rows[0]["revision"]) != (record["sheet_number"], record["revision"]):
            raise ApiError("Target query returned a different sheet/revision")
        return rows[0] if rows else None

    def _normalize(self, entity, folder_id):
        row = {name: value_at(entity, self.fields[name]) for name in self.fields if self.fields[name] != "$folder_id"}
        if self.fields["file_parent_id"] == "$folder_id":
            row["file_parent_id"] = folder_id
        row["target_record_id"] = positive_int(entity["id"])
        for name in ("sheet_number", "revision"):
            if not isinstance(row[name], (str, int)) or isinstance(row[name], bool) or str(row[name]) == "":
                raise ApiError(f"Target {name} is missing")
            row[name] = str(row[name])
        row["batch_date"] = date_value(row["batch_date"])
        row["revision_date"] = date_value(row["revision_date"])
        if row["status"] not in self.status_map:
            raise ApiError(f"Unmapped workflow status on revision {entity['id']}")
        row["status"] = self.status_map[row["status"]]
        for name in ("file_id", "file_parent_id", "file_version"):
            if row[name] is not None:
                row[name] = positive_int(row[name])
        return row

    def verify_content(self, project, record, observed, *, fresh=False):
        file_id = positive_int(observed.get("file_id"))
        parent_id = positive_int(observed.get("file_parent_id"))
        info = self.client.json("GET", self.base(project) + f"/filemanager/files/{file_id}/info", read_only=True)
        if (str(info.get("entityId")) != str(file_id) or str(info.get("partitionEntityId")) != str(parent_id)
                or info.get("size") != record["size_bytes"]):
            raise ApiError("Uploaded file identity, container or byte length differs from source")
        # The API's checksum algorithm is not assumed. Download and compute SHA-256.
        key = (project["project_uid"], file_id, parent_id, observed.get("file_version"),
               info.get("checksum"), info["size"])
        if fresh or key not in self.hash_cache:
            path = self.base(project) + f"/filemanager/folders/{parent_id}/files/{file_id}/stream"
            self.hash_cache[key] = self.client.hash_download(path, record["size_bytes"])
        hashes = self.hash_cache[key]
        if hashes != {"size_bytes": record["size_bytes"], "sha256": record["sha256"]}:
            raise ApiError("Downloaded target file does not match the source SHA-256 and size")
        if observed.get("file_version") != 1:
            raise ApiError("Revision has an unexpected additional file version; investigate duplicate upload")
        return {**observed, **hashes}

    def upload(self, project, record, source_root):
        source = inside(Path(source_root), record["source_file"])
        path = self.base(project) + f"/filemanager/folders/{positive_int(project['target']['folder_id'])}/addcontrolled/" + "/".join(
            segment(x) for x in (source.name, record["revision"], record["sheet_number"]))
        with source.open("rb") as handle:
            sha, size = hashlib.sha256(), 0
            while chunk := handle.read(1024 * 1024):
                sha.update(chunk)
                size += len(chunk)
            if sha.hexdigest() != record["sha256"] or size != record["size_bytes"]:
                raise InputError("Source file changed since preflight")
            handle.seek(0)
            return self.client.upload(path, handle, size)

    def set_metadata(self, project, record, observed):
        entity = {"id": positive_int(observed["target_record_id"]), "entityDef": self.contract["entity_def"]}
        entity.update({self.fields[name]: record[name] for name in WRITABLE})
        payload = {"activity": {"PropertyName": "Activity", "Name": "MigrationMetadata",
                    "Set": "MigrationRows", "Flow": [{"PropertyName": "Iterate", "Set": "MigrationRows", "Existing": {}}]},
                   "sets": [{"Name": "MigrationRows", "entities": [entity]}]}
        path = f"/v2/domains/{self.domain}/projects/{positive_int(project['target']['folder_id'])}/apps/{segment(self.contract['app_name'])}/activities/run"
        return self.client.json("POST", path, payload)

    def transition(self, project, record, observed, stage):
        action = self.contract["workflow"][stage]
        path = (f"/v2/domains/{self.domain}/projects/{positive_int(project['target']['folder_id'])}"
                f"/apps/{segment(self.contract['app_name'])}/activities/{segment(action['activity_name'])}/run")
        payload = {"listIdentifier": 1, "listId": positive_int(project["target"]["folder_id"]),
                   "sets": [{"Name": action["set_name"], "entities": [
                       {"id": positive_int(observed["target_record_id"]), "entityDef": self.contract["entity_def"]}]}]}
        return self.client.json("POST", path, payload)
