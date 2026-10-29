"""Bounded pilot runner using the browser controls calibrated in DEVPRODUCTION.

The operator opens the exact project/folder after signing in. This runner owns
whole batches and all verification between them. The live calibration covered
four revisions and at most two attachments per batch; larger runs are refused
before attachment until pagination and upload-table navigation are validated.
"""

from pathlib import Path

from .browser import BrowserError
from .browser_evidence import export_folder, download_original, identity
from .browser_readback import ReadOnlyInspector, compare_readback
from .browser_upload import BatchAttachment, UploadGrid, validate_attachment_sources
from .browser_workflow import BatchWorkflow
from .contract import METADATA
from .journal import STAGES, now
from .preflight import InputError, atomic_json, digest
from .runner import select_projects


CONTRACT_ID = "standalone-calibration-v1"
MAX_RECORDS = 4
MAX_BATCH_FILES = 2


def pilot_scope(manifest, project_uid=None):
    projects = select_projects(manifest, [project_uid] if project_uid else None)
    if len(projects) != 1:
        raise InputError("Select exactly one project for the calibrated pilot runner")
    project = projects[0]
    records = [r for r in manifest["files"] if r["project_uid"] == project["project_uid"]]
    batches = sorted(project["batches"], key=lambda b: b["upload_sequence"])
    if not project["ready_for_upload"] or project["target"].get("transport") != "browser":
        raise InputError("The pilot project needs a complete, audited browser destination")
    if project.get("revision_policy") != "per_sheet_occurrence" or project.get("revision_start") != 1:
        raise InputError("The calibrated pilot requires per-sheet revisions starting at 1")
    if not records or len(records) > MAX_RECORDS:
        raise InputError(f"The calibrated pilot supports at most {MAX_RECORDS} revision records; larger logs need validation")
    if len({b["batch_name"] for b in batches}) != len(batches):
        raise InputError("Batch names must be distinct because the browser workflow filters by name")
    for batch in batches:
        members = [r for r in records if r["version_set_uid"] == batch["version_set_uid"]]
        if not members or len(members) != batch["expected_files"] or len(members) > MAX_BATCH_FILES:
            raise InputError(f"The calibrated pilot supports complete batches of at most {MAX_BATCH_FILES} files")
        validate_attachment_sources(manifest, members)
    return project, records, batches


def compare_metadata(report, records):
    expected = {identity(r): r for r in records}
    actual = {identity(r): r for r in report.get("records", [])}
    if (not report.get("complete_visible_folder") or len(expected) != len(records)
            or len(actual) != len(report.get("records", [])) or set(actual) != set(expected)):
        raise BrowserError("The complete folder membership differs from the batch prefix")
    for key, source in expected.items():
        observed = actual[key]
        if any(observed.get(field) != source[field] for field in METADATA):
            raise BrowserError(f"Saved metadata differs for {key[0]} revision {key[1]}")
        if observed.get("original_filename") != Path(source["source_file"]).name or observed.get("file_version") != 1:
            raise BrowserError("A saved original filename or file-version count differs")
    return actual


def require_pending_outcome(stage, observed):
    """Recover an uncertain workflow only from the actual requested state."""
    if stage == "submitted":
        valid = (observed.get("status") == "Submitted to QA"
                 and observed.get("qa_status") == "Pending Review" and observed.get("is_current") is False
                 and observed.get("workflow_step") == "QA Check")
    elif stage == "accepted":
        valid = (observed.get("status") == "QA Accepted" and observed.get("qa_status") == "Accepted"
                 and observed.get("is_current") is False and observed.get("workflow_step") == "QA Check")
    elif stage == "released":
        valid = (observed.get("status") == "Released" and observed.get("qa_status") == "Accepted"
                 and observed.get("is_current") is True and observed.get("workflow_step") == "Complete")
    else:
        raise BrowserError("Unsupported pending browser workflow")
    if not valid:
        raise BrowserError(f"The pending {stage} result is not established; the command will not be repeated")


class PilotRunner:
    def __init__(self, page, manifest, journal, output, ocr_executable, project_uid=None):
        self.project, self.records, self.batches = pilot_scope(manifest, project_uid)
        self.page, self.manifest, self.journal = page, manifest, journal
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.ocr, self.sequence = ocr_executable, 0
        self.inspector = ReadOnlyInspector(page, self.project["target"], self.output / "initial", self.ocr)

    def _capture(self, label, *, metadata_only=False):
        self.sequence += 1
        output = self.output / f"{self.sequence:03}-{label}"
        self.inspector = ReadOnlyInspector(self.page, self.project["target"], output, self.ocr)
        exported = export_folder(self.inspector, output / "export", self.ocr)
        if exported:
            report = self.inspector.read(exported_records=exported, metadata_only=metadata_only)
        else:
            # Fresh independent export and visible grid are both empty; no
            # detail navigation caption exists for an empty controlled folder.
            report = {"observed_at": now(), "target": self.project["target"],
                      "complete_visible_folder": True, "records": [],
                      "empty_membership_source": "fresh_Kahua_export_and_unfiltered_grid"}
            atomic_json(output / "readback-report.json", report)
        return report, exported, output / "readback-report.json"

    def _confirm(self, record, stage, observed, path, *, begin=False):
        state = self.journal.status(record["operation_key"])
        if state["stage_index"] >= STAGES.index(stage):
            return
        if begin and state["pending_stage"] is None:
            self.journal.begin(record["operation_key"], stage)
        self.journal.confirm(record["operation_key"], stage, {
            "target_record_id": observed["target_record_id"], "observed_at": now(), "evidence_path": str(path)})

    def _hash(self, records, membership, label):
        checks = []
        for record in records:
            checks.append(download_original(self.inspector, record, membership,
                          self.output / label / record["operation_key"], self.ocr))
        path = self.output / label / "integrity-report.json"
        atomic_json(path, {"observed_at": now(), "target": self.project["target"],
                          "all_match": all(c["source_match"] for c in checks), "records": checks})
        return path

    def _batch_key(self, batch, records):
        return digest([self.project["project_uid"], batch["version_set_uid"], self.project["target"],
                       CONTRACT_ID, [r["fingerprint"] for r in records]])

    def _workflow(self, stage, records, prefix):
        states = [self.journal.status(r["operation_key"]) for r in records]
        if all(s["stage_index"] >= STAGES.index(stage) for s in states):
            return
        if any(s["pending_stage"] for s in states):
            report, _, path = self._capture("reconcile-" + stage)
            observed = compare_metadata(report, prefix)
            for record, state in zip(records, states):
                if state["stage_index"] >= STAGES.index(stage):
                    continue
                if state["pending_stage"] != stage:
                    raise BrowserError("The batch contains incompatible pending stages")
                require_pending_outcome(stage, observed[identity(record)])
                self._confirm(record, stage, observed[identity(record)], path)
            return
        report, _, _ = self._capture("before-" + stage, metadata_only=True)
        observed = compare_metadata(report, prefix)
        flow = BatchWorkflow(self.inspector, self.journal, records, list(observed.values()),
                             self.output / (records[0]["version_set_uid"] + "-workflow"), self.ocr)
        flow.filter_batch()
        if stage == "released":
            flow.begin_release()
            flow.execute_release()
            flow.close_release_result()
            # The journal remains pending until the full final-prefix audit.
        else:
            flow.review(stage)
            flow.execute_review(stage)
            flow.acknowledge()

    def _batch(self, batch, records, prefix):
        key = self._batch_key(batch, records)
        if self.journal.batch_verified(key):
            return
        states = [self.journal.status(r["operation_key"]) for r in records]
        if not all(s["stage_index"] >= STAGES.index("metadata_verified") for s in states):
            before = [r for r in prefix if r not in records]
            if all(s["stage_index"] == -1 and s["pending_stage"] is None for s in states):
                old, _, _ = self._capture("before-upload")
                if before:
                    if not compare_readback(old, before)["passed"]:
                        raise BrowserError("The previous batch is not fully verified before attachment")
                elif old["records"]:
                    raise BrowserError("A new pilot journal requires an empty target folder")
                attachment = BatchAttachment(self.inspector, self.manifest, records, self.journal,
                                             self.output / (batch["version_set_uid"] + "-attachment"), self.ocr)
                attachment.attach(before)
                attachment.continue_attachment()
                grid = UploadGrid(self.inspector, records, self.output / (batch["version_set_uid"] + "-upload"), self.ocr)
                proof = grid.prepare()
                grid.commit(self.manifest, self.journal, proof)
            # A pending upload is never replayed. It must already be present in
            # the independently observed folder with exact metadata and bytes.
            saved, membership, path = self._capture("saved-draft", metadata_only=True)
            observed = compare_metadata(saved, prefix)
            for record in records:
                row = observed[identity(record)]
                if row.get("workflow_step") != "Draft" or row.get("is_current") is not False:
                    raise BrowserError("The unverified uploaded revision is not in its expected Draft state")
            integrity_path = self._hash(records, membership, batch["version_set_uid"] + "-before-submit")
            verified_path = self.output / (batch["version_set_uid"] + "-draft-verification.json")
            atomic_json(verified_path, {"observed_at": now(), "target": self.project["target"],
                                       "metadata_report": str(path), "integrity_report": str(integrity_path),
                                       "records": [observed[identity(r)] for r in records]})
            for record in records:
                row = observed[identity(record)]
                state = self.journal.status(record["operation_key"])
                if state["stage_index"] < 0 and state["pending_stage"] != "uploaded":
                    raise BrowserError("A target revision exists without this journal's upload intent")
                self._confirm(record, "uploaded", row, verified_path)
                self._confirm(record, "metadata_verified", row, verified_path, begin=True)
        for stage in ("submitted", "accepted", "released"):
            self._workflow(stage, records, prefix)
        report, _, path = self._capture("batch-final")
        checked = compare_readback(report, prefix)
        if not checked["passed"]:
            raise BrowserError("Final batch metadata/history check failed: " + "; ".join(checked["issues"]))
        observed = compare_metadata(report, prefix)
        for record in records:
            self._confirm(record, "released", observed[identity(record)], path)
            self._confirm(record, "verified", observed[identity(record)], path, begin=True)
        self.journal.confirm_batch(key, str(path))

    def run(self):
        result = {"started_at": now(), "manifest_id": self.manifest["manifest_id"],
                  "target": self.project["target"], "contract_id": CONTRACT_ID,
                  "mode": "bounded_current_folder_pilot", "passed": False,
                  "completed_batches": [], "limits": {"records": MAX_RECORDS, "files_per_batch": MAX_BATCH_FILES}}
        try:
            self.inspector._guard()
            for record in self.records:
                self.journal.register(record["operation_key"], record["fingerprint"],
                                      {**self.project["target"], "contract_id": CONTRACT_ID})
            for batch in self.batches:
                members = [r for r in self.records if r["version_set_uid"] == batch["version_set_uid"]]
                prefix = [r for r in self.records if r["upload_sequence"] <= batch["upload_sequence"]]
                self._batch(batch, members, prefix)
                result["completed_batches"].append(batch["version_set_uid"])
                atomic_json(self.output / "run-report.json", result)
            report, membership, metadata_path = self._capture("project-final")
            check = compare_readback(report, self.records)
            if not check["passed"]:
                raise BrowserError("Final project metadata/history check failed: " + "; ".join(check["issues"]))
            integrity_path = self._hash(self.records, membership, "final-originals")
            result.update(passed=True, metadata_audit=check, metadata_report=str(metadata_path),
                          integrity_report=str(integrity_path), verified_records=len(self.records))
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
            result["recovery"] = "Reconcile saved evidence and the same journal. Do not replace the journal or replay uploads."
            if not self.page.get_by_role("textbox", name="Password", exact=True).count():
                try:
                    self.page.screenshot(path=str(self.output / "stopped.png"))
                except Exception:
                    pass
        result["finished_at"] = now()
        atomic_json(self.output / "run-report.json", result)
        return result
