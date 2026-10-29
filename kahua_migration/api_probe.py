"""Read-only Kahua API discovery; no file upload or workflow changes.

Usage: python3 -m kahua_migration.api_probe --project-id ID --folder-id ID --out DIR
Supply KAHUA_SERVICE_URL, KAHUA_DOMAIN_KEY, KAHUA_CLIENT_ID, and
KAHUA_CLIENT_SECRET through the local environment, never command-line arguments.
"""

import argparse
import json
import os
from pathlib import Path
from urllib.parse import quote

from .preflight import InputError, atomic_json, positive_int
from .http_client import ApiError, KahuaClient


ProbeError = ApiError


def segment(value):
    return quote(str(value), safe="")


ReadOnlyClient = KahuaClient


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True, type=positive_int)
    parser.add_argument("--folder-id", type=positive_int)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    names = ("KAHUA_SERVICE_URL", "KAHUA_DOMAIN_KEY", "KAHUA_CLIENT_ID", "KAHUA_CLIENT_SECRET")
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        parser.error("Configure these local environment variables: " + ", ".join(missing))
    client = ReadOnlyClient(os.environ[names[0]], os.environ[names[2]], os.environ[names[3]])
    domain = segment(os.environ[names[1]])
    base = f"/v1/domains/{domain}/projects/{args.project_id}"
    # Sequential discovery stays below the documented two-process limit.
    for label, path in [("project-info", base + "/info"),
                        *(([("folder-info", base + f"/filemanager/folders/{args.folder_id}/info")]) if args.folder_id else [])]:
        data, _ = client.get(path)
        payload = json.loads(data)
        atomic_json(args.out / (label + ".json"), payload)
    if args.folder_id:
        # This POST is a documented read-only query, not an activity/write.
        path = f"/v2/domains/{domain}/projects/{args.folder_id}/query?returnDefaultAttributes=true"
        sample = client.json("POST", path, {"PropertyName": "Query",
            "EntityDef": "kahua_FileManager.DrawingLogRevision", "Partition": {"Scope": "Partition"},
            "Skip": 0, "Take": 5}, read_only=True)
        atomic_json(args.out / "revision-sample.json", sample)
    schema_path = f"/v1/domains/{domain}/projects/0/entityschema/get?appNames=kahua_FileManager&includeReferences=true"
    data, content_type = client.get(schema_path)
    if not data.startswith(b"PK"):
        raise ProbeError("The schema endpoint did not return an XLSX ZIP payload")
    args.out.mkdir(parents=True, exist_ok=True)
    temporary = args.out / ".filemanager-schema.xlsx.tmp"
    temporary.write_bytes(data)
    temporary.replace(args.out / "filemanager-schema.xlsx")
    print("Saved project/folder information, a revision sample, and the tenant's File Manager data dictionary. No Kahua data was changed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (InputError, ProbeError, OSError, KeyError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
