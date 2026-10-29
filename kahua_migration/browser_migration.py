"""CSV-driven destination provisioning and ordered project/batch execution."""

from pathlib import Path

from .browser import BrowserError
from .browser_evidence import download_original, identity
from .browser_navigation import AuthenticationRequired, DestinationNavigator
from .browser_session import BrowserSessionRecovery, needs_sign_in
from .browser_record_filter import RecordFilterInspector, RecordWorkflow, export_complete_folder
from .browser_pilot import PilotRunner, compare_metadata, require_pending_outcome
from .browser_readback import compare_readback
from .browser_upload import BatchAttachment, UploadGrid, validate_attachment_sources
from .csv_config import validate_destination
from .journal import STAGES, now
from .preflight import InputError, atomic_json, digest
from .runner import select_projects


CONTRACT_ID = "csv-destination-exact-filter-v1"


def project_scope(manifest, project_uid):
    project = select_projects(manifest, [project_uid])[0]
    validate_destination(project["target"])
    if not project["ready_for_upload"]:
        raise InputError("The source audit blocks this project")
    if project.get("revision_policy") != "per_sheet_occurrence" or project.get("revision_start") != 1:
        raise InputError("Automatic browser migration requires per-sheet revisions starting at 1")
    records = [r for r in manifest["files"] if r["project_uid"] == project_uid]
    batches = sorted(project["batches"], key=lambda b: b["upload_sequence"])
    if not records or len({b["batch_name"] for b in batches}) != len(batches):
        raise InputError("A project needs nonempty batches with distinct batch names")
    for batch in batches:
        members = [r for r in records if r["version_set_uid"] == batch["version_set_uid"]]
        if not members or len(members) != batch["expected_files"]:
            raise InputError("A complete audited batch is required")
        validate_attachment_sources(manifest, members)
    return project, records, batches


def verify_export(exported, records):
    observed = {identity(r): r for r in exported}
    if len(observed) != len(exported) or len(observed) != len(records) or set(observed) != {identity(r) for r in records}:
        raise BrowserError("The independent export does not match the expected complete membership")
    for record in records:
        actual = observed[identity(record)]
        if actual["title"] != record["title"] or actual["original_filename"] != Path(record["source_file"]).name:
            raise BrowserError("Exported title or original filename differs from the source")
    return observed


class ProjectRunner(PilotRunner):
    def __init__(self, page, manifest, journal, output, ocr_executable, project_uid):
        self.project, self.records, self.batches = project_scope(manifest, project_uid)
        self.page, self.manifest, self.journal = page, manifest, journal
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.ocr, self.sequence = ocr_executable, 0
        self.inspector = RecordFilterInspector(page, self.project["target"], self.output / "initial", self.ocr)

    def _batch_key(self, batch, records):
        return digest([self.project["project_uid"], batch["version_set_uid"], self.project["target"], CONTRACT_ID,
                       [r["fingerprint"] for r in records]])

    def _capture(self, label, *, metadata_only=False, subset=None):
        self.sequence += 1
        output = self.output / f"{self.sequence:03}-{label}"
        self.inspector = RecordFilterInspector(self.page, self.project["target"], output, self.ocr)
        exported = export_complete_folder(self.inspector, output / "export", self.ocr)
        source = {identity(r): r for r in self.records}
        actual = {identity(r): r for r in exported}
        if not set(actual) <= set(source):
            raise BrowserError("The folder contains unexpected revisions outside this migration")
        selected = [source[identity(r)] for r in exported] if subset is None else subset
        observed, reports = [], []
        for index, record in enumerate(selected, 1):
            if identity(record) not in actual:
                raise BrowserError("A requested revision is absent from the complete export")
            self.inspector = RecordFilterInspector(self.page, self.project["target"], output / f"record-{index:05}", self.ocr)
            self.inspector.filter_record(record)
            one = self.inspector.read(exported_records=[actual[identity(record)]], metadata_only=metadata_only, preserve_filter=True)
            observed.extend(one["records"])
            reports.append(str(self.inspector.output / "readback-report.json"))
        report = {"observed_at": now(), "target": self.project["target"], "complete_visible_folder": subset is None,
                  "complete_membership": True, "membership_count": len(exported), "records": observed,
                  "record_reports": reports, "membership_source": "complete_export_and_unfiltered_count",
                  "record_scope": "exact_batch_and_sheet_filters"}
        atomic_json(output / "readback-report.json", report)
        return report, exported, output / "readback-report.json"

    def _hash(self, records, membership, label):
        exported = {identity(r): r for r in membership}
        checks = []
        for record in records:
            output = self.output / label / record["operation_key"]
            self.inspector = RecordFilterInspector(self.page, self.project["target"], output, self.ocr)
            self.inspector.filter_record(record)
            checks.append(download_original(self.inspector, record, [exported[identity(record)]], output, self.ocr, preserve_filter=True))
        path = self.output / label / "integrity-report.json"
        atomic_json(path, {"observed_at": now(), "target": self.project["target"],
                          "all_match": all(r["source_match"] for r in checks), "records": checks})
        return path

    def _workflow(self, stage, records, prefix):
        states = [self.journal.status(r["operation_key"]) for r in records]
        if all(s["stage_index"] >= STAGES.index(stage) for s in states):
            return
        report, _, _ = self._capture("before-" + stage, metadata_only=True)
        observed = compare_metadata(report, prefix)
        for record in records:
            state = self.journal.status(record["operation_key"])
            if state["stage_index"] >= STAGES.index(stage):
                continue
            if state["pending_stage"]:
                if state["pending_stage"] != stage:
                    raise BrowserError("Batch workflow has incompatible pending stages")
                one, exported, path = self._capture("reconcile-" + stage, subset=[record])
                verify_export(exported, prefix)
                row = compare_metadata({**one, "complete_visible_folder": True}, [record])[identity(record)]
                require_pending_outcome(stage, row)
                self._confirm(record, stage, row, path)
                continue
            flow = RecordWorkflow(self.inspector, self.journal, [record], list(observed.values()),
                                  self.output / record["operation_key"] / "workflow", self.ocr)
            flow.filter_batch()
            if stage == "released":
                flow.begin_release()
                flow.execute_release()
                flow.close_release_result()
            else:
                flow.review(stage)
                flow.execute_review(stage)
                flow.acknowledge()

    def _upload_one(self, record, existing):
        status = self.journal.status(record["operation_key"])
        if status["stage_index"] >= STAGES.index("metadata_verified"):
            return
        if status["stage_index"] == -1 and status["pending_stage"] is None:
            _, exported, _ = self._capture("before-upload", metadata_only=True, subset=[])
            verify_export(exported, existing)
            attachment = BatchAttachment(self.inspector, self.manifest, [record], self.journal,
                                         self.output / record["operation_key"] / "attachment", self.ocr, allow_partial=True,
                                         membership_snapshot=self.inspector.membership_snapshot)
            attachment.attach(existing)
            attachment.continue_attachment()
            grid = UploadGrid(self.inspector, [record], self.output / record["operation_key"] / "upload", self.ocr)
            proof = grid.prepare()
            grid.commit(self.manifest, self.journal, proof, allow_partial=True)
        # Existing or uncertain uploads are observed and hashed; never attached again.
        report, exported, path = self._capture("saved-draft", metadata_only=True, subset=[record])
        verify_export(exported, existing + [record])
        observed = compare_metadata({**report, "complete_visible_folder": True}, [record])
        row = observed[identity(record)]
        if row.get("workflow_step") != "Draft" or row.get("is_current") is not False:
            raise BrowserError("The unverified upload is not in its expected Draft state")
        self._hash([record], exported, record["operation_key"] + "-before-submit")
        if self.journal.status(record["operation_key"])["stage_index"] < 0 and self.journal.status(record["operation_key"])["pending_stage"] != "uploaded":
            raise BrowserError("A target revision exists without this journal's upload intent")
        self._confirm(record, "uploaded", row, path)
        self._confirm(record, "metadata_verified", row, path, begin=True)

    def _batch(self, batch, records, prefix):
        key = self._batch_key(batch, records)
        if self.journal.batch_verified(key):
            return
        existing = [r for r in prefix if r not in records]
        # One PDF per upload avoids variable-height/large upload tables while
        # retaining the original whole-batch workflow and revision semantics.
        for record in records:
            self._upload_one(record, existing)
            existing.append(record)
        for stage in ("submitted", "accepted", "released"):
            self._workflow(stage, records, prefix)
        report, _, path = self._capture("batch-final")
        checked = compare_readback(report, prefix)
        if not checked["passed"]:
            raise BrowserError("Final batch history check failed: " + "; ".join(checked["issues"]))
        observed = compare_metadata(report, prefix)
        for record in records:
            self._confirm(record, "released", observed[identity(record)], path)
            self._confirm(record, "verified", observed[identity(record)], path, begin=True)
        self.journal.confirm_batch(key, str(path))

    def run(self):
        result = {"started_at": now(), "manifest_id": self.manifest["manifest_id"], "project_uid": self.project["project_uid"],
                  "target": self.project["target"], "contract_id": CONTRACT_ID, "mode": "automatic_destination",
                  "passed": False, "completed_batches": [], "files_per_upload": 1}
        try:
            self.inspector._guard()
            for record in self.records:
                self.journal.register(record["operation_key"], record["fingerprint"], {**self.project["target"], "contract_id": CONTRACT_ID})
            for batch in self.batches:
                records = [r for r in self.records if r["version_set_uid"] == batch["version_set_uid"]]
                prefix = [r for r in self.records if r["upload_sequence"] <= batch["upload_sequence"]]
                self._batch(batch, records, prefix)
                result["completed_batches"].append(batch["version_set_uid"])
                atomic_json(self.output / "run-report.json", result)
            report, membership, path = self._capture("project-final")
            comparison = compare_readback(report, self.records)
            if not comparison["passed"]:
                raise BrowserError("Final project history check failed: " + "; ".join(comparison["issues"]))
            integrity = self._hash(self.records, membership, "final-originals")
            result.update(passed=True, metadata_audit=comparison, metadata_report=str(path),
                          integrity_report=str(integrity), verified_records=len(self.records))
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
            result["recovery"] = "Resume the same CSV, configuration and journal after reconciliation; do not replay an uncertain Save."
            result["pending_operations"] = []
            for record in self.records:
                state = self.journal.status(record["operation_key"])
                if state["pending_stage"]:
                    if not state["uncertain"]:
                        self.journal.uncertain(record["operation_key"], result["error"])
                    result["pending_operations"].append({
                        "operation_key": record["operation_key"], "stage": state["pending_stage"],
                        "upload_save_attempted": self.journal.upload_commit_attempted(record["operation_key"])})
            if needs_sign_in(self.page):
                result["requires_sign_in"] = True
            else:
                try:
                    self.page.screenshot(path=str(self.output / "stopped.png"), timeout=3000)
                except Exception:
                    pass
        result["status"] = "completed" if result["passed"] else "paused"
        result["finished_at"] = now()
        atomic_json(self.output / "run-report.json", result)
        return result


class MigrationRunner:
    def __init__(self, page, manifest, journal, output, ocr_executable, *, navigator_factory=DestinationNavigator, project_factory=ProjectRunner, session_recovery=None):
        self.page, self.manifest, self.journal = page, manifest, journal
        self.output, self.ocr = Path(output).resolve(), ocr_executable
        self.navigator_factory, self.project_factory = navigator_factory, project_factory
        self.session_recovery = session_recovery if session_recovery is not None else BrowserSessionRecovery(page)

    def _recover_browser(self, navigator, target, entry):
        if entry.get("requires_sign_in") or needs_sign_in(self.page):
            raise AuthenticationRequired("Sign-in is required before the queue can continue")
        try:
            details = navigator.recover_to_finder()
            entry["browser_recovery"] = {**(details if isinstance(details, dict) else {"mode": "in_app"}),
                                         "journal_intents_preserved": True}
        except AuthenticationRequired:
            raise
        except Exception as exc:
            entry["in_app_recovery_error"] = f"{type(exc).__name__}: {exc}"
            try:
                self.page = self.session_recovery.replace_failed_tab(self.page, target["environment"])
            finally:
                # Keep the new page available even if it needs authentication.
                self.page = self.session_recovery.page
            entry["browser_recovery"] = {"mode": "fresh_tab", "failed_tab_closed": True,
                                         "journal_intents_preserved": True}

    def run(self, project_uids=None):
        projects = select_projects(self.manifest, project_uids)
        result = {"started_at": now(), "manifest_id": self.manifest["manifest_id"], "passed": False, "projects": []}
        recover_previous_upload_errors = False
        for project in projects:
            output = self.output / digest(project["project_uid"])[:16]
            entry = {"project_uid": project["project_uid"], "target": project["target"], "passed": False}
            navigation_started = False
            try:
                _, records, _ = project_scope(self.manifest, project["project_uid"])
                # Reject changed source/target bindings before creating even an
                # empty project or folder when reusing an existing journal.
                for record in records:
                    self.journal.register(record["operation_key"], record["fingerprint"],
                                          {**project["target"], "contract_id": CONTRACT_ID})
                navigator = self.navigator_factory(self.page, self.journal, output / "navigation", self.ocr)
                navigator.allow_upload_error_dismissal = recover_previous_upload_errors
                navigation_started = True
                self.session_recovery.checkpoint(self.page, project["target"]["environment"])
                navigator.ensure(project["target"])
                entry = self.project_factory(self.page, self.manifest, self.journal, output / "batches", self.ocr,
                                             project["project_uid"]).run()
            except AuthenticationRequired as exc:
                entry.update(error=str(exc), requires_sign_in=True, status="paused")
                result["projects"].append(entry)
                result["queue_stop_reason"] = "authentication_required"
                break
            except Exception as exc:
                entry["error"] = f"{type(exc).__name__}: {exc}"
            notices = getattr(navigator, "dismissed_upload_errors", []) if navigation_started else []
            if isinstance(notices, list) and notices:
                entry["dismissed_previous_upload_errors"] = notices
            entry["status"] = "completed" if entry["passed"] else "paused"
            result["projects"].append(entry)
            atomic_json(self.output / "run-report.json", result)
            if len(result["projects"]) == len(projects):
                break
            if not entry["passed"] and navigation_started:
                try:
                    self._recover_browser(navigator, project["target"], entry)
                    recover_previous_upload_errors = True
                except AuthenticationRequired as exc:
                    entry.update(requires_sign_in=True, navigation_recovery_error=str(exc))
                    result["queue_stop_reason"] = "authentication_required"
                    break
                except Exception as exc:
                    entry["navigation_recovery_error"] = f"{type(exc).__name__}: {exc}"
                    result["queue_stop_reason"] = "browser_unavailable"
                    break
                atomic_json(self.output / "run-report.json", result)
        result.update(finished_at=now(), passed=len(result["projects"]) == len(projects) and all(r["passed"] for r in result["projects"]))
        result["queue_status"] = ("completed" if result["passed"] else
                                  "blocked" if len(result["projects"]) < len(projects) else "completed_with_pauses")
        result["not_started_projects"] = [p["project_uid"] for p in projects[len(result["projects"]):]]
        atomic_json(self.output / "run-report.json", result)
        return result
