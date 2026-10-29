import copy
import json
import os
import unittest
from pathlib import Path

from kahua_migration.contract import validate_contract
from kahua_migration.http_client import KahuaClient
from kahua_migration.journal import Journal
from kahua_migration.kahua import KahuaAdapter
from kahua_migration.preflight import InputError, atomic_json, digest
from kahua_migration.runner import Runner, exclusive_run
from kahua_http_fixture import KahuaMock
import test_preflight


class RunnerHttpTests(unittest.TestCase):
    def setUp(self):
        # Reuse the source-data fixture; do not rerun its tests.
        self.source = test_preflight.MigrationTests("test_revision_is_per_sheet_and_current_is_not_uploaded")
        self.source.setUp()
        self.addCleanup(self.source.doCleanups)
        self.manifest = self.source.audit()
        self.mock = KahuaMock()
        self.addCleanup(self.mock.close)
        self.addCleanup(self.save_http_evidence)
        self.journal = Journal(self.source.root / "journal.sqlite")
        self.addCleanup(self.journal.close)
        client = KahuaClient(self.mock.origin, "fixture-client", "fixture-secret", allow_local_mock=True, sleep=lambda _: None)
        self.adapter = KahuaAdapter(client, self.mock.contract())

    def save_http_evidence(self):
        if directory := os.environ.get("KAHUA_TEST_EVIDENCE_DIR"):
            atomic_json(Path(directory) / (self._testMethodName + ".json"), {
                "evidence_class": "local_http_simulation_synthetic_tenant_mapping",
                "endpoint_source": "Kahua official OAuth Postman collection",
                "events": self.mock.events, "mutation_requests": len(self.mock.writes)})

    def run_migration(self):
        return Runner(self.manifest, self.adapter, self.journal, self.source.root / "run").run()

    def test_three_batches_history_metadata_and_resume(self):
        report = self.run_migration()
        self.assertTrue(report["passed"], report)
        rows = self.mock.entities.values()
        self.assertEqual({(r["Sheet"], r["Rev"]) for r in rows if r["Current"]}, {("A", "3"), ("B", "1"), ("C", "1")})
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(r["PrimaryFile"]["Version"] == 1 for r in rows))
        events = self.mock.writes
        uploads = [(i, e["sheet"], e["revision"]) for i, e in enumerate(events) if "/addcontrolled/" in e["path"]]
        self.assertEqual([(s, r) for _, s, r in uploads], [("A", "1"), ("C", "1"), ("A", "2"), ("A", "3"), ("B", "1")])
        # Entire first batch uploads before metadata; both releases precede batch 2.
        self.assertEqual([e["path"].split("/")[-1] for e in events[:2]], ["A", "C"])
        batch2_start = uploads[2][0]
        self.assertEqual(sum(e["path"].endswith("/release/run") for e in events[:batch2_start]), 2)
        writes_before = len(self.mock.writes)
        self.assertTrue(self.run_migration()["passed"])
        self.assertEqual(len(self.mock.writes), writes_before, "A resume must not repeat mutations")

    def test_lost_upload_response_is_read_back_not_uploaded_twice(self):
        self.mock.drop_next_upload = True
        first = self.run_migration()
        self.assertFalse(first["passed"])
        self.assertEqual(len(self.mock.entities), 1)
        self.assertTrue(self.run_migration()["passed"])
        uploads = [e for e in self.mock.writes if "/addcontrolled/" in e["path"]]
        self.assertEqual(len(uploads), 5)
        self.assertTrue(all(r["PrimaryFile"]["Version"] == 1 for r in self.mock.entities.values()))

    def test_completed_resume_detects_extra_version_even_with_identical_bytes(self):
        self.assertTrue(self.run_migration()["passed"])
        project, record = self.manifest["projects"][0], self.manifest["files"][0]
        # A separate actor accidentally uploads the same PDF to an old revision.
        self.adapter.upload(project, record, self.manifest["source_root"])
        before = len(self.mock.writes)
        result = self.run_migration()
        self.assertFalse(result["passed"])
        self.assertIn("extra version", result["projects"][0]["reason"])
        self.assertEqual(len(self.mock.writes), before)

    def test_unknown_upload_outcome_never_blindly_retries(self):
        self.mock.reject_next_upload = True
        self.assertFalse(self.run_migration()["passed"])
        writes = len(self.mock.writes)
        result = self.run_migration()
        self.assertFalse(result["passed"])
        self.assertIn("Uncertain uploaded outcome", result["projects"][0]["reason"])
        self.assertEqual(len(self.mock.writes), writes)
        self.assertEqual(len(self.mock.entities), 0)

    def test_wrong_batch_metadata_blocks_release_and_next_batch(self):
        self.mock.wrong_metadata = True
        report = self.run_migration()
        self.assertFalse(report["passed"])
        self.assertIn("batch_date", report["projects"][0]["reason"])
        self.assertEqual(len(self.mock.entities), 2)
        self.assertFalse(any(e["path"].endswith("/submit/run") for e in self.mock.writes))

    def test_superseding_failure_stops_before_third_batch(self):
        self.mock.skip_superseding = True
        report = self.run_migration()
        self.assertFalse(report["passed"])
        self.assertIn("Current flag", report["projects"][0]["reason"])
        self.assertEqual(len(self.mock.entities), 3)
        # Restart must re-check the failed batch barrier, even though each file
        # individually reached its verified stage before the batch check failed.
        before = len(self.mock.writes)
        self.assertFalse(self.run_migration()["passed"])
        self.assertEqual(len(self.mock.writes), before)

    def test_hash_mismatch_blocks_further_uploads(self):
        self.mock.corrupt_download = True
        report = self.run_migration()
        self.assertFalse(report["passed"])
        self.assertIn("SHA-256", report["projects"][0]["reason"])
        self.assertEqual(len(self.mock.entities), 1)

    def test_changed_source_stops_before_network(self):
        source = self.source.root / self.manifest["files"][0]["source_file"]
        source.write_bytes(b"%PDF-different")
        self.assertFalse(self.run_migration()["passed"])
        self.assertEqual(self.mock.events, [])

    def test_duplicate_query_page_cannot_prove_completion(self):
        self.mock.repeat_pages = True
        result = self.run_migration()
        self.assertFalse(result["passed"])
        self.assertIn("repeated a revision ID", result["projects"][0]["reason"])
        self.assertEqual(len(self.mock.entities), 1)

    def test_existing_unowned_revision_is_not_overwritten(self):
        project, record = self.manifest["projects"][0], self.manifest["files"][0]
        self.adapter.upload(project, record, self.manifest["source_root"])
        before = len(self.mock.writes)
        result = self.run_migration()
        self.assertFalse(result["passed"])
        self.assertIn("without a migration intent", result["projects"][0]["reason"])
        self.assertEqual(len(self.mock.writes), before)

    def test_failed_project_does_not_stop_other_projects(self):
        bad = copy.deepcopy(self.manifest["projects"][0])
        bad["project_uid"] = "bad-project"
        bad["target"]["folder_id"] = 999
        self.manifest["projects"].insert(0, bad)
        for source in list(self.manifest["files"]):
            row = {**source, "project_uid": "bad-project", "operation_key": digest(["bad-project", source["operation_key"]])}
            row["fingerprint"] = digest({k: v for k, v in row.items() if k != "fingerprint"})
            self.manifest["files"].append(row)
        self.manifest["manifest_id"] = digest({k: v for k, v in self.manifest.items() if k not in ("manifest_id", "generated_at")})
        result = self.run_migration()
        self.assertFalse(result["passed"])
        self.assertEqual([p["status"] for p in result["projects"]], ["blocked", "verified"])

    def test_unverified_contract_and_second_runner_are_rejected(self):
        contract = self.mock.contract()
        contract["verification_evidence"] = None
        with self.assertRaises(InputError):
            validate_contract(contract)
        path = self.source.root / "lock-test.sqlite"
        with exclusive_run(path):
            with self.assertRaises(InputError):
                with exclusive_run(path):
                    self.fail("Two runners acquired the same journal")


if __name__ == "__main__":
    unittest.main()
