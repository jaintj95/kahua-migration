"""Synthetic UI boundaries around the real project queue, uploader and journal.

The target deliberately permits duplicate saves by increasing file_version.
Only the client journal and verification can prevent a duplicate in these tests.
This fixture never connects to Kahua.
"""

import copy
from pathlib import Path
from types import SimpleNamespace

from kahua_migration.browser import BrowserError
from kahua_migration.browser_migration import ProjectRunner
from kahua_migration.browser_upload import UploadGrid, validate_attachment_sources
from kahua_migration.journal import now
from kahua_migration.preflight import atomic_json, file_digest


def key(record):
    return record["project_uid"], record["sheet_number"], record["revision"]


class Tenant:
    def __init__(self, manifest, failure=None):
        self.rows, self.bytes, self.saves, self.actions, self.attachments = {}, {}, [], [], []
        self.manifest, self.failure, self.failed = manifest, failure, False
        self.first = next(r for r in manifest["files"] if r["project_uid"] == "p1")

    def fail(self, stage, page, record):
        if stage == self.failure and not self.failed and key(record) == key(self.first):
            self.failed = True
            page.dialog_open = True
            if stage == "closed_tab":
                page.closed = True
            if stage == "expired_auth":
                page.signed_in = False
            raise BrowserError("Injected " + stage + " failure")

    def save(self, record):
        self.saves.append(key(record))
        prior = self.rows.get(key(record))
        self.rows[key(record)] = {**copy.deepcopy(record), "target_record_id": "target-" + record["operation_key"],
                                 "original_filename": Path(record["source_file"]).name,
                                 "file_version": prior["file_version"] + 1 if prior else 1,
                                 "workflow_step": "Draft", "status": "Draft", "qa_status": None, "is_current": False}
        self.bytes[key(record)] = (Path(self.manifest["source_root"]) / record["source_file"]).read_bytes()

    def transition(self, record, stage):
        self.actions.append((key(record), stage))
        row = self.rows[key(record)]
        if stage == "submitted":
            row.update(status="Submitted to QA", qa_status="Pending Review", workflow_step="QA Check")
        elif stage == "accepted":
            row.update(status="QA Accepted", qa_status="Accepted", workflow_step="QA Check")
        elif stage == "released":
            for prior in self.rows.values():
                if prior["project_uid"] == record["project_uid"] and prior["sheet_number"] == record["sheet_number"] and prior["is_current"]:
                    prior.update(is_current=False, status="Superseded")
            row.update(status="Released", qa_status="Accepted", workflow_step="Complete", is_current=True)


class Page:
    viewport_size = {"width": 1600, "height": 1000}

    def __init__(self, tenant):
        self.tenant, self.closed, self.dialog_open, self.signed_in = tenant, False, False, True
        self.context, self.prepared = SimpleNamespace(), None

    def get_by_role(self, role, name, **kwargs):
        if self.closed:
            raise BrowserError("Target closed")
        count = int(not self.signed_in and (role, name) in (("group", "Login"), ("textbox", "Password")))
        def wait_for(**kwargs):
            if self.dialog_open:
                raise BrowserError("Injected stuck upload grid")
        locator = SimpleNamespace(count=lambda: count, wait_for=wait_for, is_visible=lambda: bool(count))
        locator.nth = lambda _: locator
        return locator

    def screenshot(self, **kwargs):
        if self.closed:
            raise BrowserError("Target closed")


class Recovery:
    def __init__(self, page):
        self.page, self.replacements = page, 0

    def checkpoint(self, page, environment):
        self.page = page

    def replace_failed_tab(self, page, environment):
        page.closed = True
        self.page = Page(page.tenant)
        self.replacements += 1
        return self.page


class Navigator:
    def __init__(self, page, journal, output, ocr):
        self.page = page

    def ensure(self, target):
        if self.page.closed or self.page.dialog_open:
            raise BrowserError("Project navigation is blocked by the previous upload")

    def recover_to_finder(self):
        if self.page.closed or self.page.dialog_open:
            raise BrowserError("The upload dialog is stuck")


class Inspector:
    def __init__(self, page, target):
        self.page, self.target, self.membership_snapshot = page, target, None

    def _guard(self):
        if self.page.closed:
            raise BrowserError("Target closed")

    def _control(self, role, name):
        assert (role, name) == ("button", "Execute")
        page, tenant = self.page, self.page.tenant
        def press(_):
            tenant.fail("save_before_transport", page, page.prepared)
            tenant.save(page.prepared)
            tenant.fail("save_after_commit", page, page.prepared)
            tenant.fail("closed_tab", page, page.prepared)
            tenant.fail("expired_auth", page, page.prepared)
            page.dialog_open = False
        return SimpleNamespace(press=press)


class Attachment:
    def __init__(self, inspector, manifest, records, journal, output, ocr, **kwargs):
        self.page, self.manifest, self.records, self.journal = inspector.page, manifest, records, journal

    def attach(self, existing):
        validate_attachment_sources(self.manifest, self.records, allow_partial=True)
        record = self.records[0]
        self.journal.begin(record["operation_key"], "uploaded")
        self.page.dialog_open = True
        self.page.tenant.attachments.append(key(record))
        self.page.tenant.fail("attach", self.page, record)

    def continue_attachment(self):
        self.page.tenant.fail("initial_metadata", self.page, self.records[0])


class Grid:
    # Use the real upload commit: source revalidation, exact precommit proof,
    # durable Save intent, one Execute activation, then grid-detachment check.
    commit = UploadGrid.commit

    def __init__(self, inspector, records, output, ocr):
        self.inspector, self.page, self.records, self.output = inspector, inspector.page, records, Path(output)

    def rows(self):
        return self.records

    def prepare(self):
        record = self.records[0]
        self.page.prepared = record
        self.page.tenant.fail("prepare", self.page, record)
        path = self.output / "precommit.json"
        fields = ("sheet_number", "title", "revision", "batch_name", "revision_date", "batch_date")
        atomic_json(path, {"observed_at": now(), "target": self.inspector.target, "records": [
            {**{field: r[field] for field in fields}, "original_filename": Path(r["source_file"]).name} for r in self.records]})
        return path


class Workflow:
    def __init__(self, inspector, journal, records, observed, output, ocr):
        self.page, self.journal, self.record, self.output = inspector.page, journal, records[0], Path(output)

    def filter_batch(self):
        pass

    def review(self, stage):
        self.journal.begin(self.record["operation_key"], stage)

    def execute_review(self, stage):
        self.page.tenant.transition(self.record, stage)
        self.page.tenant.fail(stage, self.page, self.record)
        path = self.output / (stage + ".json")
        atomic_json(path, {"observed_at": now(), "stage": stage})
        self.journal.confirm(self.record["operation_key"], stage,
                             {"target_record_id": self.page.tenant.rows[key(self.record)]["target_record_id"],
                              "observed_at": now(), "evidence_path": str(path)})

    def acknowledge(self):
        pass

    def begin_release(self):
        self.review("released")

    def execute_release(self):
        self.page.tenant.transition(self.record, "released")
        self.page.tenant.fail("released", self.page, self.record)

    def close_release_result(self):
        pass


class Runner(ProjectRunner):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.inspector = Inspector(self.page, self.project["target"])

    def _capture(self, label, *, metadata_only=False, subset=None):
        if self.page.closed or self.page.dialog_open:
            raise BrowserError("The failed UI must be isolated before readback")
        if label == "saved-draft":
            self.page.tenant.fail("metadata_readback", self.page, subset[0])
        rows = [copy.deepcopy(r) for r in self.page.tenant.rows.values() if r["project_uid"] == self.project["project_uid"]]
        selected = rows if subset is None else [r for r in rows if key(r) in {key(x) for x in subset}]
        report = {"observed_at": now(), "target": self.project["target"], "complete_visible_folder": subset is None, "records": selected}
        self.sequence += 1
        path = self.output / f"{self.sequence:03}-{label}.json"
        atomic_json(path, report)
        return report, rows, path

    def _hash(self, records, membership, label):
        for record in records:
            self.page.tenant.fail("hash", self.page, record)
            if self.page.tenant.bytes[key(record)] != (Path(self.manifest["source_root"]) / record["source_file"]).read_bytes():
                raise BrowserError("Original PDF hash mismatch")
        path = self.output / label / "integrity.json"
        atomic_json(path, {"all_match": True, "record_count": len(records)})
        return path
