"""Ordered, resumable migration. One process, one in-flight request, exact row scope."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

from .contract import METADATA
from .http_client import ApiError
from .journal import Journal, JournalConflict, STAGES, now
from .preflight import InputError, atomic_json, digest, file_digest, inside, verify_manifest


class VerificationError(RuntimeError):
    pass


@contextmanager
def exclusive_run(journal_path):
    """OS lock releases on process exit/crash; never hold a SQLite transaction over HTTP."""
    lock_path = Path(str(journal_path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if __import__("os").name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise InputError("Another migration process is using this journal") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if __import__("os").name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def select_projects(manifest, project_uids=None):
    verify_manifest(manifest)
    selected = set(project_uids or [p["project_uid"] for p in manifest["projects"]])
    unknown = selected - {p["project_uid"] for p in manifest["projects"]}
    if unknown:
        raise InputError(f"Unknown project UIDs: {sorted(unknown)}")
    if any(i["severity"] == "error" and not i.get("project_uid") for i in manifest["issues"]):
        raise InputError("Resolve export-wide errors before selecting projects")
    return [p for p in manifest["projects"] if p["project_uid"] in selected]


def build_plan(manifest, project_uids=None):
    projects = []
    for project in select_projects(manifest, project_uids):
        uid = project["project_uid"]
        batches = []
        for batch in project["batches"]:
            files = [r for r in manifest["files"] if r["project_uid"] == uid and r["version_set_uid"] == batch["version_set_uid"]]
            batches.append({**batch, "files": [{n: r[n] for n in
                ("sheet_number", "revision", "title", "revision_date", "source_file", "sha256", "size_bytes", "operation_key")}
                for r in files]})
        projects.append({"project_uid": uid, "project_name": project["project_name"], "target": project["target"],
                         "revision_policy": project["revision_policy"], "revision_start": project["revision_start"],
                         "ready_for_upload": project["ready_for_upload"], "batches": batches,
                         "issues": [i for i in manifest["issues"] if i["project_uid"] == uid]})
    return {"manifest_id": manifest["manifest_id"], "mode": "plan_only", "network_requests": 0,
            "revision_rule": "First appearance 1; increment for each later appearance of that sheet",
            "batch_rule": "Upload every file, verify metadata, submit, accept, release, verify batch; then next upload_sequence",
            "projects": projects}


class Runner:
    def __init__(self, manifest, adapter, journal, output):
        verify_manifest(manifest)
        self.manifest, self.adapter, self.journal = manifest, adapter, journal
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)

    def _save(self, key, label, payload):
        # Content addressed operation keys come from the verified manifest.
        path = self.output / "evidence" / key / (label + "-" + now().replace(":", "-") + ".json")
        atomic_json(path, payload)
        return str(path)

    def _confirm(self, record, stage, observed):
        observed_at = now()
        path = self._save(record["operation_key"], stage, {"observed_at": observed_at, "record": observed})
        self.journal.confirm(record["operation_key"], stage, {
            "target_record_id": observed["target_record_id"], "observed_at": observed_at, "evidence_path": path})

    def _require(self, project, record, row, stage, *, fresh=False):
        if row is None:
            raise VerificationError("Expected target revision is absent")
        if (row["sheet_number"], row["revision"]) != (record["sheet_number"], record["revision"]):
            raise VerificationError("Target sheet/revision differs from the plan")
        if row.get("file_version") != 1:
            raise VerificationError("Target contains an unexpected extra version of this revision")
        if stage != "uploaded":
            for field in METADATA:
                if row.get(field) != record[field]:
                    raise VerificationError(f"Readback mismatch for {field} on sheet {record['sheet_number']}")
        expected_status = {"uploaded": "Draft", "metadata_verified": "Draft", "submitted": "Submitted",
                           "accepted": "Accepted", "released": "Released", "verified": "Released"}[stage]
        if row.get("status") != expected_status:
            raise VerificationError(f"Expected {expected_status}, observed {row.get('status')}")
        if stage == "verified" and row.get("is_current") is not True:
            raise VerificationError("Released revision is not marked current")
        if stage in ("uploaded", "verified"):
            row = self.adapter.verify_content(project, record, row, fresh=fresh)
        return row

    def _step(self, project, record, stage):
        key = record["operation_key"]
        state = self.journal.status(key)
        if state["stage_index"] >= STAGES.index(stage):
            return
        row = self.adapter.find(project, record)
        if state["pending_stage"]:
            if state["pending_stage"] != stage:
                raise JournalConflict("A different earlier stage remains pending")
            # A crash/timeout is recovered ONLY by observing the requested result.
            # Absence is not proof that an in-flight server operation failed.
            try:
                observed = self._require(project, record, row, stage)
            except (VerificationError, ApiError, InputError) as exc:
                raise JournalConflict(f"Uncertain {stage} outcome; no automatic retry: {exc}") from exc
            self._confirm(record, stage, observed)
            return
        if stage == "uploaded" and row is not None:
            raise VerificationError("Sheet/revision already exists without a journaled upload; refusing to add another version")
        if stage != "uploaded" and row is None:
            raise VerificationError("Previously uploaded revision disappeared")
        if stage in ("submitted", "accepted", "released"):
            preceding = {"submitted": "metadata_verified", "accepted": "submitted", "released": "accepted"}[stage]
            self._require(project, record, row, preceding)
        elif stage == "metadata_verified":
            self._require(project, record, row, "uploaded")
        self.journal.begin(key, stage)
        try:
            if stage == "uploaded":
                response = self.adapter.upload(project, record, self.manifest["source_root"])
            elif stage == "metadata_verified":
                response = self.adapter.set_metadata(project, record, row)
            elif stage in ("submitted", "accepted", "released"):
                response = self.adapter.transition(project, record, row, stage)
            else:
                response = None
            if response is not None:
                self._save(key, stage + "-response", response)
                row = self.adapter.find(project, record)
            observed = self._require(project, record, row, stage)
            self._confirm(record, stage, observed)
        except Exception as exc:
            self.journal.uncertain(key, f"{type(exc).__name__}: {exc}")
            raise

    def _verify_batch(self, project, records, *, final=False):
        rows = self.adapter.snapshot(project)
        expected = {(r["sheet_number"], r["revision"]): r for r in records}
        actual = {(r["sheet_number"], r["revision"]): r for r in rows}
        if len(actual) != len(rows) or set(expected) != set(actual):
            raise VerificationError("Target membership differs from the completed batch prefix")
        latest = {}
        for record in sorted(records, key=lambda r: r["upload_sequence"]):
            latest[record["sheet_number"]] = record["operation_key"]
        normalized = []
        for identity, record in expected.items():
            row = actual[identity]
            if row.get("file_version") != 1:
                raise VerificationError(f"Unexpected extra version of revision {identity}")
            for field in METADATA:
                if row.get(field) != record[field]:
                    raise VerificationError(f"Batch verification failed: {field} for {identity}")
            current = latest[record["sheet_number"]] == record["operation_key"]
            if type(row.get("is_current")) is not bool or row["is_current"] != current:
                raise VerificationError(f"Current flag differs for {identity}")
            if row["status"] != ("Released" if current else "Superseded"):
                raise VerificationError(f"Release/supersede state differs for {identity}")
            checked = self.adapter.verify_content(project, record, row, fresh=final)
            normalized.append({**{k: record[k] for k in ("operation_key", "project_uid", "version_set_uid")},
                               "target": project["target"],
                               **{k: project["target"].get(k) for k in ("environment", "domain_key", "project_id", "folder_id")},
                               **checked})
        evidence_path = self._save(digest(project["project_uid"]), "final" if final else "batch", {
            "manifest_id": self.manifest["manifest_id"], "complete": True,
            "project_uid": project["project_uid"], "observed_at": now(), "files": normalized})
        return normalized, evidence_path

    def run(self, project_uids=None):
        report = {"manifest_id": self.manifest["manifest_id"], "contract_id": self.adapter.contract_id,
                  "started_at": now(), "projects": [], "passed": False}
        for project in select_projects(self.manifest, project_uids):
            uid = project["project_uid"]
            result = {"project_uid": uid, "project_name": project["project_name"], "status": "blocked",
                      "completed_batches": [], "files": 0}
            report["projects"].append(result)
            if not project["ready_for_upload"]:
                result["reason"] = "Resolve this project's preflight errors before uploading"
                atomic_json(self.output / "run-report.json", report)
                continue
            records = [r for r in self.manifest["files"] if r["project_uid"] == uid]
            try:
                for record in records:
                    source = inside(Path(self.manifest["source_root"]), record["source_file"])
                    if source.stat().st_size != record["size_bytes"] or file_digest(source) != record["sha256"]:
                        raise InputError("Source changed since preflight; no uploads started for this project")
                    self.journal.register(record["operation_key"], record["fingerprint"],
                                          {**project["target"], "contract_id": self.adapter.contract_id})
                target_evidence = self.adapter.check_target(project)
                self._save(digest(uid), "target-binding", target_evidence)
                existing = self.adapter.snapshot(project)
                expected = {(r["sheet_number"], r["revision"]): r for r in records}
                for row in existing:
                    record = expected.get((row["sheet_number"], row["revision"]))
                    if record is None:
                        raise VerificationError("Folder contains records outside this migration plan")
                    if row.get("file_version") != 1:
                        raise VerificationError("Target contains an unexpected extra version of this revision")
                    state = self.journal.status(record["operation_key"])
                    if state["stage_index"] < 0 and state["pending_stage"] is None:
                        raise VerificationError("Target revision exists without a migration intent; manual reconciliation required")
                for batch in sorted(project["batches"], key=lambda b: b["upload_sequence"]):
                    batch_records = [r for r in records if r["version_set_uid"] == batch["version_set_uid"]]
                    if not batch_records or len(batch_records) != batch["expected_files"]:
                        raise VerificationError("Batch membership differs from its declared file count")
                    batch_key = digest([uid, batch["version_set_uid"], project["target"],
                                        self.adapter.contract_id, [r["fingerprint"] for r in batch_records]])
                    if not self.journal.batch_verified(batch_key):
                        # All files complete each stage before any file starts the next stage.
                        for stage in STAGES:
                            for record in batch_records:
                                self._step(project, record, stage)
                        prefix = [r for r in records if r["upload_sequence"] <= batch["upload_sequence"]]
                        _, evidence = self._verify_batch(project, prefix)
                        self.journal.confirm_batch(batch_key, evidence)
                    result["completed_batches"].append(batch["version_set_uid"])
                    atomic_json(self.output / "run-report.json", report)
                normalized, _ = self._verify_batch(project, records, final=True)
                result.update(status="verified", files=len(normalized))
            except (InputError, ApiError, JournalConflict, VerificationError, OSError, ValueError, KeyError, TypeError) as exc:
                result.update(status="blocked", reason=f"{type(exc).__name__}: {exc}")
            atomic_json(self.output / "run-report.json", report)
        report["finished_at"] = now()
        report["passed"] = bool(report["projects"]) and all(p["status"] == "verified" for p in report["projects"])
        atomic_json(self.output / "run-report.json", report)
        return report
