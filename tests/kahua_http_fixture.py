"""Local HTTP stand-in: published endpoint shapes, deliberately synthetic tenant fields.

This proves our client/orchestrator behavior, not the actual Kahua tenant's
attribute names, activity names, permissions or workflow side effects.
"""

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit


class KahuaMock:
    def __init__(self):
        self.entities, self.files, self.events = {}, {}, []
        self.marker = 1
        self.drop_next_upload = False
        self.reject_next_upload = False
        self.wrong_metadata = False
        self.skip_superseding = False
        self.repeat_pages = False
        self.corrupt_download = False
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.dispatch()

            def do_POST(self):
                self.dispatch()

            def dispatch(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                path = unquote(urlsplit(self.path).path)
                fixture.events.append({"method": self.command, "path": path})
                status, payload = fixture.handle(self.command, path, body)
                if status is None:
                    self.close_connection = True
                    return
                raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/octet-stream" if isinstance(payload, bytes) else "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def contract(self):
        return {"schema_version": 1, "environment": "test", "service_origin": self.origin,
                "domain_key": "example", "verified_at": "synthetic-test-fixture",
                "verification_evidence": "tests/kahua_http_fixture.py", "app_name": "kahua_FileManager",
                "entity_def": "kahua_FileManager.DrawingLogRevision", "page_size": 2,
                "query_explicits": ["PrimaryFile"],
                "fields": {"sheet_number": "Sheet", "revision": "Rev", "title": "Title",
                           "revision_date": "RevDate", "batch_name": "Batch", "batch_date": "BatchDate",
                           "status": "Status", "is_current": "Current", "file_id": "PrimaryFile.id",
                           "file_parent_id": "PrimaryFile.Parent", "file_version": "PrimaryFile.Version"},
                "workflow": {"draft_status": "draft", "superseded_status": "superseded",
                    "submitted": {"activity_name": "submit", "set_name": "Selected", "status": "qa"},
                    "accepted": {"activity_name": "accept", "set_name": "Selected", "status": "accepted"},
                    "released": {"activity_name": "release", "set_name": "Selected", "status": "released"}}}

    @property
    def writes(self):
        return [e for e in self.events if e["method"] == "POST" and not e["path"].endswith(("/token", "/query"))]

    def handle(self, method, path, body):
        if path == "/oauth2/token":
            return 200, {"access_token": "local-test-token", "token_type": "Bearer", "expires_in": 3600}
        if method == "GET" and path.endswith("/projects/101/info"):
            return 200, {"id": 101, "number": "001", "name": "Project one", "isArchived": False}
        if method == "GET" and path.endswith("/folders/202/info"):
            return 200, {"entityId": 202, "path": "Sheets", "partitionEntityDef": "kahua_FileManager.DrawingLogRevision"}
        if method == "POST" and path.endswith("/projects/202/query"):
            query = json.loads(body)
            if query["Partition"] != {"Scope": "Partition"}:
                return 400, {"error": "Query must have exact folder scope"}
            rows = sorted(self.entities.values(), key=lambda row: row["id"])
            for condition in query.get("Condition", []):
                rows = [r for r in rows if str(r[condition["Path"]]) == str(condition["Value"])]
            start = 0 if self.repeat_pages else query["Skip"]
            return 200, {"count": len(rows), "entities": rows[start:start + query["Take"]],
                         "skipped": query["Skip"], "taken": query["Take"], "marker": self.marker}
        if method == "POST" and "/addcontrolled/" in path:
            if self.reject_next_upload:
                self.reject_next_upload = False
                return 503, {"error": "Synthetic upstream failure"}
            name, rev, sheet = path.split("/addcontrolled/")[1].split("/")
            self.events[-1].update(sheet=sheet, revision=rev, sha256=hashlib.sha256(body).hexdigest())
            existing = next((r for r in self.entities.values() if r["Sheet"] == sheet and r["Rev"] == rev), None)
            if existing:
                existing["PrimaryFile"]["Version"] += 1
                row, fid = existing, existing["PrimaryFile"]["id"]
            else:
                rid, fid = 1000 + len(self.entities), 2000 + len(self.entities)
                row = {"id": rid, "entityDef": "kahua_FileManager.DrawingLogRevision",
                       "PartitionId": 202, "DomainPartitionId": 101, "Sheet": sheet, "Rev": rev,
                       "Title": None, "RevDate": None, "Batch": None, "BatchDate": None,
                       "Status": "draft", "Current": False,
                       "PrimaryFile": {"id": fid, "Parent": 202, "Version": 1}}
                self.entities[rid] = row
            self.files[fid] = body
            self.marker += 1
            if self.drop_next_upload:
                self.drop_next_upload = False
                return None, None
            return 200, {"entityId": fid, "partitionEntityId": 202, "name": name,
                         "size": len(body), "checksum": hashlib.md5(body).hexdigest()}
        if method == "POST" and "/activities/" in path:
            payload = json.loads(body)
            entities = payload["sets"][0]["entities"]
            self.events[-1]["ids"] = [r["id"] for r in entities]
            if path.endswith("/activities/run"):
                for edit in entities:
                    self.entities[edit["id"]].update(edit)
                    if self.wrong_metadata:
                        self.entities[edit["id"]]["BatchDate"] = "2000-01-01"
            else:
                action = path.split("/activities/")[1].split("/")[0]
                before, after = {"submit": ("draft", "qa"), "accept": ("qa", "accepted"),
                                 "release": ("accepted", "released")}[action]
                for edit in entities:
                    row = self.entities[edit["id"]]
                    if row["Status"] != before:
                        return 409, {"error": "Wrong transition order"}
                    row["Status"] = after
                    if action == "release":
                        if not self.skip_superseding:
                            for other in self.entities.values():
                                if other["Sheet"] == row["Sheet"] and other["id"] != row["id"] and other["Current"]:
                                    other.update(Status="superseded", Current=False)
                        row["Current"] = True
            self.marker += 1
            return 200, {"sets": payload["sets"]}
        if method == "GET" and "/filemanager/files/" in path and path.endswith("/info"):
            fid = int(path.split("/files/")[1].split("/")[0])
            data = self.files[fid]
            return 200, {"entityId": fid, "partitionEntityId": 202, "size": len(data),
                         "checksum": hashlib.md5(data).hexdigest()}
        if method == "GET" and path.endswith("/stream"):
            fid = int(path.split("/files/")[1].split("/")[0])
            data = self.files[fid]
            return 200, b"X" + data[1:] if self.corrupt_download else data
        return 501, {"error": "Endpoint not modeled by this fixture"}
