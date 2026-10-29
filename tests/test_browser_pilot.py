import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from kahua_migration.browser import BrowserError
from kahua_migration.browser_pilot import (CONTRACT_ID, PilotRunner, compare_metadata,
                                           pilot_scope, require_pending_outcome)
from kahua_migration.journal import Journal, STAGES
from kahua_migration.preflight import InputError, digest, file_digest


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = dict(transport="browser", environment="https://devproductionlaunch.kahua.com/",
                           domain_name="Test", project_number="0001", project_name="Test", folder_path="Sheets/Pilot")
        self.records, self.batches = [], []
        for sequence, sheets in ((1, [("A", "1")]), (2, [("A", "2")]), (3, [("A", "3"), ("B", "1")])):
            batch = dict(version_set_uid=f"v{sequence}", batch_name=f"Set {sequence}",
                         upload_sequence=sequence, expected_files=len(sheets))
            self.batches.append(batch)
            for sheet, revision in sheets:
                path = self.root / str(sequence) / (sheet + ".pdf")
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(f"%PDF synthetic {sheet} {revision}".encode())
                record = dict(project_uid="p", version_set_uid=batch["version_set_uid"],
                              sheet_number=sheet, revision=revision, title=f"TITLE {sheet}",
                              batch_name=batch["batch_name"], batch_date=f"2025-0{sequence}-01",
                              revision_date=f"2025-0{sequence}-01", upload_sequence=sequence,
                              source_file=str(path.relative_to(self.root)), size_bytes=path.stat().st_size,
                              sha256=file_digest(path), operation_key=digest([sheet, revision]))
                record["fingerprint"] = digest(record)
                self.records.append(record)
        self.project = dict(project_uid="p", target=self.target, batches=self.batches, ready_for_upload=True,
                            revision_policy="per_sheet_occurrence", revision_start=1)
        self.manifest = dict(schema_version=1, source_root=str(self.root), files=self.records,
                             projects=[self.project], issues=[])
        self.seal(self.manifest)
        self.journal = Journal(self.root / "journal.sqlite")
        self.addCleanup(self.journal.close)
        for r in self.records:
            self.journal.register(r["operation_key"], r["fingerprint"], {**self.target, "contract_id": CONTRACT_ID})

    def seal(self, manifest):
        manifest.pop("manifest_id", None)
        manifest["manifest_id"] = digest(manifest)

    def final_report(self):
        rows = []
        for r in self.records:
            current = r["sheet_number"] == "B" or r["revision"] == "3"
            rows.append({**r, "original_filename": Path(r["source_file"]).name,
                         "target_record_id": "observed-" + r["operation_key"], "file_version": 1,
                         "is_current": current, "status": "Released" if current else "Superseded",
                         "qa_status": "Accepted", "workflow_step": "Complete"})
        return dict(complete_visible_folder=True, records=rows)

    def test_scope_requires_audited_complete_small_batches(self):
        self.assertEqual(len(pilot_scope(self.manifest)[1]), 4)
        oversized = copy.deepcopy(self.manifest)
        oversized["files"].append(copy.deepcopy(oversized["files"][-1]))
        self.seal(oversized)
        with self.assertRaisesRegex(InputError, "at most 4"):
            pilot_scope(oversized)
        duplicate_names = copy.deepcopy(self.manifest)
        duplicate_names["projects"][0]["batches"][1]["batch_name"] = "Set 1"
        self.seal(duplicate_names)
        with self.assertRaisesRegex(InputError, "must be distinct"):
            pilot_scope(duplicate_names)
        oversized_batch = copy.deepcopy(self.manifest)
        for row in oversized_batch["files"]:
            row["version_set_uid"] = "v1"
        oversized_batch["projects"][0]["batches"] = [{**self.batches[0], "expected_files": 4}]
        self.seal(oversized_batch)
        with self.assertRaisesRegex(InputError, "at most 2"):
            pilot_scope(oversized_batch)

    def test_metadata_proof_rejects_missing_fields_and_duplicate_versions(self):
        report = self.final_report()
        self.assertEqual(len(compare_metadata(report, self.records)), 4)
        for key, value in (("batch_date", None), ("file_version", 2), ("original_filename", "other.pdf")):
            bad = copy.deepcopy(report)
            bad["records"][0][key] = value
            with self.assertRaises(BrowserError):
                compare_metadata(bad, self.records)
        bad = copy.deepcopy(report)
        bad["records"][-1] = bad["records"][0]
        with self.assertRaisesRegex(BrowserError, "membership"):
            compare_metadata(bad, self.records)

    def test_pending_workflow_needs_independent_matching_state(self):
        require_pending_outcome("submitted", dict(status="Submitted to QA", qa_status="Pending Review", is_current=False, workflow_step="QA Check"))
        require_pending_outcome("accepted", dict(status="QA Accepted", qa_status="Accepted", is_current=False, workflow_step="QA Check"))
        require_pending_outcome("released", dict(status="Released", qa_status="Accepted", is_current=True, workflow_step="Complete"))
        for stage, row in (("submitted", dict(status="Uploaded", qa_status="", is_current=False)),
                           ("accepted", dict(qa_status="Pending Review", is_current=False)),
                           ("released", dict(status="Released", qa_status="Accepted", is_current=False, workflow_step="Complete"))):
            with self.subTest(stage=stage), self.assertRaisesRegex(BrowserError, "will not be repeated"):
                require_pending_outcome(stage, row)

    def runner(self):
        with patch("kahua_migration.browser_pilot.ReadOnlyInspector"):
            page = MagicMock()
            page.get_by_role.return_value.count.return_value = 0
            return PilotRunner(page, self.manifest, self.journal, self.root / "out", "unused-ocr")

    def test_completed_journal_reverifies_without_any_upload_or_workflow(self):
        runner = self.runner()
        for r in self.records:
            for stage in STAGES:
                self.journal.begin(r["operation_key"], stage)
                self.journal.confirm(r["operation_key"], stage, dict(target_record_id="observed", observed_at="now", evidence_path="proof.json"))
        for batch in self.batches:
            members = [r for r in self.records if r["version_set_uid"] == batch["version_set_uid"]]
            self.journal.confirm_batch(runner._batch_key(batch, members), "batch-proof.json")
        runner._capture = MagicMock(return_value=(self.final_report(), self.final_report()["records"], self.root / "metadata.json"))
        runner._hash = MagicMock(return_value=self.root / "integrity.json")
        with patch("kahua_migration.browser_pilot.BatchAttachment") as upload, patch("kahua_migration.browser_pilot.BatchWorkflow") as flow:
            result = runner.run()
        self.assertTrue(result["passed"], result)
        upload.assert_not_called()
        flow.assert_not_called()
        runner._hash.assert_called_once()

    def test_absent_pending_upload_stops_without_retry_or_next_batch(self):
        self.journal.begin(self.records[0]["operation_key"], "uploaded")
        runner = self.runner()
        runner._capture = MagicMock(return_value=(dict(complete_visible_folder=True, records=[]), [], self.root / "empty.json"))
        with patch("kahua_migration.browser_pilot.BatchAttachment") as upload:
            result = runner.run()
        self.assertFalse(result["passed"])
        self.assertEqual(result["completed_batches"], [])
        upload.assert_not_called()
        self.assertEqual(self.journal.status(self.records[0]["operation_key"])["pending_stage"], "uploaded")
        self.assertIsNone(self.journal.status(self.records[1]["operation_key"])["pending_stage"])


if __name__ == "__main__":
    unittest.main()
