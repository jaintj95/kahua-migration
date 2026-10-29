import copy
from collections import Counter
from contextlib import ExitStack
import json
import unittest
from unittest.mock import MagicMock, patch

from kahua_migration.browser import BrowserError
from kahua_migration.browser_migration import MigrationRunner
from kahua_migration.journal import Journal, JournalConflict
from kahua_migration.preflight import digest
import project_failure_fixture as fixture
import test_automatic_migration as automatic


class ProjectFailureTests(unittest.TestCase):
    def setUp(self):
        self.source = automatic.AutomaticRunnerTests()
        self.source.setUp()
        self.addCleanup(self.source.doCleanups)
        self.root, self.journal = self.source.root, self.source.journal
        self.manifest = copy.deepcopy(self.source.manifest)
        second = copy.deepcopy(self.manifest["projects"][0])
        second.update(project_uid="p2", project_name="Project two")
        second["target"].update(project_number="0002", project_name="Project two")
        self.manifest["projects"].append(second)
        for old in list(self.manifest["files"]):
            record = {**old, "project_uid": "p2", "operation_key": digest(["p2", old["operation_key"]])}
            record["fingerprint"] = digest({k: v for k, v in record.items() if k != "fingerprint"})
            self.manifest["files"].append(record)
        self.manifest["manifest_id"] = digest({k: v for k, v in self.manifest.items() if k not in ("manifest_id", "generated_at")})
        self.attempt = 0

    def queue(self, tenant, *, recovery=None, page=None, navigator_factory=fixture.Navigator):
        page = page or fixture.Page(tenant)
        recovery = recovery or fixture.Recovery(page)
        self.attempt += 1
        with ExitStack() as stack:
            for name, implementation in (("BatchAttachment", fixture.Attachment), ("UploadGrid", fixture.Grid), ("RecordWorkflow", fixture.Workflow)):
                stack.enter_context(patch("kahua_migration.browser_migration." + name, implementation))
            runner = MigrationRunner(page, self.manifest, self.journal, self.root / f"attempt-{self.attempt}", "ocr",
                                     navigator_factory=navigator_factory, project_factory=fixture.Runner, session_recovery=recovery)
            report = runner.run()
        saved = json.loads((self.root / f"attempt-{self.attempt}" / "run-report.json").read_text())
        self.assertEqual(saved, report)
        return report, recovery

    def assert_paused_then_completed(self, report):
        self.assertEqual([p["status"] for p in report["projects"]], ["paused", "completed"], report)
        self.assertEqual(report["queue_status"], "completed_with_pauses")
        self.assertEqual(report["not_started_projects"], [])
        self.assertFalse(report["passed"])

    def test_each_precommit_failure_isolated_and_no_retry_without_reconciliation(self):
        for fault in ("attach", "initial_metadata", "prepare"):
            with self.subTest(fault=fault):
                # Independent journals make each scenario a real first attempt.
                original = self.journal
                self.journal = Journal(self.root / (fault + ".sqlite"))
                try:
                    tenant = fixture.Tenant(self.manifest, fault)
                    first, recovery = self.queue(tenant)
                    self.assert_paused_then_completed(first)
                    self.assertEqual(recovery.replacements, 1)
                    self.assertEqual(first["projects"][0]["browser_recovery"]["mode"], "fresh_tab")
                    self.assertEqual(len(tenant.saves), 5)
                    self.assertTrue(all(k[0] == "p2" for k in tenant.saves))
                    before = (list(tenant.saves), list(tenant.attachments), list(tenant.actions))
                    self.journal.close()
                    self.journal = Journal(self.root / (fault + ".sqlite"))
                    again, _ = self.queue(tenant)
                    self.assert_paused_then_completed(again)
                    self.assertEqual(before, (tenant.saves, tenant.attachments, tenant.actions))
                    pending = first["projects"][0]["pending_operations"]
                    self.assertEqual(len(pending), 1)
                    self.assertFalse(pending[0]["upload_save_attempted"])
                finally:
                    self.journal.close()
                    self.journal = original

    def test_unknown_save_that_never_reached_target_stays_paused_without_replay(self):
        tenant = fixture.Tenant(self.manifest, "save_before_transport")
        report, _ = self.queue(tenant)
        self.assert_paused_then_completed(report)
        operation = tenant.first["operation_key"]
        self.assertTrue(self.journal.upload_commit_attempted(operation))
        before = (list(tenant.saves), list(tenant.attachments))
        for _ in range(2):
            self.assert_paused_then_completed(self.queue(tenant)[0])
        self.assertEqual(before, (tenant.saves, tenant.attachments))
        with self.assertRaises(JournalConflict):
            self.journal.mark_upload_commit(operation, {"observed_at": "now", "evidence_path": "replay"})

    def test_saved_uploads_reconcile_after_failure_without_duplicate_save(self):
        for fault in ("save_after_commit", "metadata_readback", "hash", "closed_tab"):
            with self.subTest(fault=fault):
                original = self.journal
                self.journal = Journal(self.root / (fault + ".sqlite"))
                try:
                    tenant = fixture.Tenant(self.manifest, fault)
                    first, recovery = self.queue(tenant)
                    self.assert_paused_then_completed(first)
                    self.assertEqual(recovery.replacements, 1)
                    self.assertEqual([k for k in tenant.saves if k[0] == "p1"], [fixture.key(tenant.first)])
                    self.assertEqual(len(tenant.saves), 6)
                    self.journal.close()
                    self.journal = Journal(self.root / (fault + ".sqlite"))
                    completed, _ = self.queue(tenant)
                    self.assertTrue(completed["passed"], completed)
                    self.assertEqual(len(tenant.saves), 10)
                    self.assertEqual(set(Counter(tenant.saves).values()), {1})
                    self.assertTrue(all(row["file_version"] == 1 for row in tenant.rows.values()))
                    before = (list(tenant.saves), list(tenant.attachments), list(tenant.actions))
                    self.assertTrue(self.queue(tenant)[0]["passed"])
                    self.assertEqual(before, (tenant.saves, tenant.attachments, tenant.actions))
                finally:
                    self.journal.close()
                    self.journal = original

    def test_uncertain_workflow_resumes_from_observed_result_without_repeating_action(self):
        for fault in ("submitted", "accepted", "released"):
            with self.subTest(fault=fault):
                original = self.journal
                self.journal = Journal(self.root / (fault + ".sqlite"))
                try:
                    tenant = fixture.Tenant(self.manifest, fault)
                    report, _ = self.queue(tenant)
                    self.assert_paused_then_completed(report)
                    self.assertEqual(len([k for k in tenant.saves if k[0] == "p1"]), 2)
                    self.assertTrue(self.queue(tenant)[0]["passed"])
                    self.assertEqual(set(Counter(tenant.saves).values()), {1})
                    self.assertEqual(set(Counter(tenant.actions).values()), {1})
                finally:
                    self.journal.close()
                    self.journal = original

    def test_extra_target_file_version_is_never_overwritten_on_resume(self):
        tenant = fixture.Tenant(self.manifest, "save_after_commit")
        self.assert_paused_then_completed(self.queue(tenant)[0])
        tenant.save(tenant.first)  # Another actor adds a duplicate version.
        before = list(tenant.saves)
        self.assert_paused_then_completed(self.queue(tenant)[0])
        self.assertEqual(tenant.saves, before)
        self.assertEqual(tenant.rows[fixture.key(tenant.first)]["file_version"], 2)

    def test_authentication_loss_is_reported_as_a_shared_session_failure(self):
        tenant = fixture.Tenant(self.manifest, "expired_auth")
        report, recovery = self.queue(tenant)
        self.assertEqual(report["queue_status"], "blocked")
        self.assertEqual(report["queue_stop_reason"], "authentication_required")
        self.assertEqual(report["not_started_projects"], ["p2"])
        self.assertEqual(len(tenant.saves), 1)
        self.assertEqual(recovery.replacements, 0)

    def test_browser_process_failure_never_loses_pending_journal_entries(self):
        tenant = fixture.Tenant(self.manifest, "save_after_commit")
        recovery = fixture.Recovery(fixture.Page(tenant))
        recovery.replace_failed_tab = MagicMock(side_effect=BrowserError("browser disconnected"))
        report, _ = self.queue(tenant, recovery=recovery)
        self.assertEqual(report["queue_status"], "blocked")
        self.assertEqual(report["queue_stop_reason"], "browser_unavailable")
        self.assertEqual(report["not_started_projects"], ["p2"])
        pending = self.journal.status(tenant.first["operation_key"])
        self.assertEqual(pending["pending_stage"], "uploaded")
        self.assertTrue(self.journal.upload_commit_attempted(tenant.first["operation_key"]))


if __name__ == "__main__":
    unittest.main()
