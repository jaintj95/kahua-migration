import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path

from kahua_migration.preflight import InputError, audit, reconcile


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "source"
        self.project.mkdir()
        self.versions = [dict(project_uid="p1", project_name="Project one", project_kahua_entity_id="101",
                              version_set_uid=f"v{i}", version_name=f"Set {i}",
                              published_at=f"2025-0{i}-01", upload_sequence=str(i)) for i in range(1, 4)]
        self.rows = []
        self.latest = {}
        for seq, sheets in [(1, ["A", "C"]), (2, ["A"]), (3, ["A", "B"])]:
            folder = self.project / "Drawings" / f"Set {seq}"
            folder.mkdir(parents=True)
            for sheet in sheets:
                data = f"%PDF-1.7\nfixture {sheet} {seq}\n".encode()
                (folder / f"{sheet}.pdf").write_bytes(data)
                row = {"Sheet Name": sheet, "Version": f"Set {seq}", "Description": f"Title {sheet}",
                       "Date Issued": f"2025-0{seq}-01", "Revision": str(seq)}
                self.rows.append(row)
                self.latest[sheet] = (row, data)
        (self.project / "Drawings" / "Current").mkdir()
        for sheet, (_, data) in self.latest.items():
            (self.project / "Drawings" / "Current" / f"{sheet}.pdf").write_bytes(data)
        self.config = {"schema_version": 1, "source_root": ".", "revision_policy": "per_sheet_occurrence",
                       "revision_start": 1, "projects": [{"project_uid": "p1", "source_directory": "source",
                        "drawing_log": "drawing.csv", "current_log": "current.csv",
                        "version_folders": {f"v{i}": f"Drawings/Set {i}" for i in range(1, 4)},
                        "target": {"environment": "test", "domain_key": "example", "project_id": 101,
                                   "project_number": "001", "folder_id": 202, "folder_path": "Sheets"}}]}
        self.write_inputs()

    def write_csv(self, path, rows):
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def write_inputs(self):
        self.write_csv(self.root / "versions.csv", self.versions)
        self.write_csv(self.project / "drawing.csv", self.rows)
        self.write_csv(self.project / "current.csv", [r for r, _ in self.latest.values()])
        (self.root / "config.json").write_text(json.dumps(self.config))

    def audit(self):
        return audit(self.root / "versions.csv", self.root / "config.json")

    def codes(self, manifest):
        return {i["code"] for i in manifest["issues"] if i["severity"] == "error"}

    def target_snapshot(self, manifest):
        project = manifest["projects"][0]
        files = []
        for index, record in enumerate(manifest["files"]):
            current = project["expected_current"][record["sheet_number"]] == record["operation_key"]
            files.append({**record, **project["target"], "target_record_id": index + 1000,
                          "is_current": current, "status": "Released" if current else "Superseded"})
        return {"complete": True, "manifest_id": manifest["manifest_id"], "files": files}

    def test_revision_is_per_sheet_and_current_is_not_uploaded(self):
        m = self.audit()
        self.assertEqual(m["summary"]["errors"], 0, m["issues"])
        self.assertEqual([(r["sheet_number"], r["revision"]) for r in m["files"]],
                         [("A", "1"), ("C", "1"), ("A", "2"), ("A", "3"), ("B", "1")])
        self.assertEqual(len(m["files"]), 5)
        self.assertTrue(all("Current" not in r["source_file"] for r in m["files"]))
        self.assertEqual(m["projects"][0]["current_comparison"]["identical_bytes"], 3)

    def test_order_comes_from_sequence_not_csv_or_folder_order(self):
        self.versions.reverse()
        self.rows.reverse()
        self.write_inputs()
        m = self.audit()
        self.assertEqual([r["revision"] for r in m["files"] if r["sheet_number"] == "A"], ["1", "2", "3"])

    def test_sheet_skipping_batch_two_uses_revision_two_in_batch_three(self):
        row = {"Sheet Name": "C", "Version": "Set 3", "Description": "Title C",
               "Date Issued": "2025-03-01", "Revision": "3"}
        data = b"%PDF-1.7\nC third batch"
        self.rows.append(row)
        self.latest["C"] = (row, data)
        (self.project / "Drawings/Set 3/C.pdf").write_bytes(data)
        (self.project / "Drawings/Current/C.pdf").write_bytes(data)
        self.write_inputs()
        m = self.audit()
        self.assertEqual(m["summary"]["errors"], 0)
        self.assertEqual([r["revision"] for r in m["files"] if r["sheet_number"] == "C"], ["1", "2"])

    def test_browser_mapping_does_not_require_api_ids(self):
        self.config["projects"][0]["target"] = {
            "transport": "browser", "environment": "https://devproductionlaunch.kahua.com/",
            "domain_name": "Test domain", "project_number": "001", "project_name": "Project one", "folder_path": "Sheets"}
        for version in self.versions:
            version["project_kahua_entity_id"] = "0"
        self.write_inputs()
        self.assertTrue(self.audit()["projects"][0]["ready_for_upload"])

    def test_conflicting_destination_blocks_both_projects(self):
        duplicate = copy.deepcopy(self.config["projects"][0])
        duplicate["project_uid"] = "p2"
        self.config["projects"].append(duplicate)
        self.versions += [{**v, "project_uid": "p2"} for v in list(self.versions)]
        self.write_inputs()
        m = self.audit()
        self.assertTrue(all(not p["ready_for_upload"] for p in m["projects"]))
        self.assertEqual({i["project_uid"] for i in m["issues"] if i["code"] == "TARGET_COLLISION"}, {"p1", "p2"})

    def test_missing_file_blocks_project(self):
        (self.project / "Drawings/Set 2/A.pdf").unlink()
        m = self.audit()
        self.assertIn("INVALID_DRAWING", self.codes(m))
        self.assertFalse(m["projects"][0]["ready_for_upload"])

    def test_extra_file_is_not_silently_uploaded_or_ignored(self):
        (self.project / "Drawings/Set 1/Extra.pdf").write_bytes(b"%PDF-extra")
        m = self.audit()
        self.assertIn("UNLISTED_BATCH_FILE", self.codes(m))
        self.assertEqual(len(m["files"]), 5)

    def test_duplicate_sheet_batch_blocks_project(self):
        self.rows.append(dict(self.rows[0]))
        self.write_inputs()
        self.assertIn("INVALID_DRAWING", self.codes(self.audit()))

    def test_zero_target_id_is_not_assumed_to_be_a_project(self):
        self.config["projects"][0]["target"]["project_id"] = 0
        self.write_inputs()
        self.assertIn("TARGET_IDS_UNCONFIRMED", self.codes(self.audit()))

    def test_invalid_date_is_not_silently_coerced(self):
        self.versions[0]["published_at"] = "2025-02-30"
        self.write_inputs()
        self.assertIn("INVALID_VERSION_METADATA", self.codes(self.audit()))

    def test_current_must_match_last_occurrence_for_each_sheet(self):
        current = [r for r, _ in self.latest.values()]
        current[0] = {**current[0], "Version": "Set 1"}
        self.write_csv(self.project / "current.csv", current)
        self.assertIn("CURRENT_VERSION_MISMATCH", self.codes(self.audit()))

    def test_exact_revision_labels_survive_explicit_mode(self):
        self.config["revision_policy"] = "explicit"
        for i, row in enumerate(self.rows):
            row["Revision"] = ["00", "P01", "A", "B", "000"][i]
        self.write_inputs()
        self.assertEqual([r["revision"] for r in self.audit()["files"]], ["00", "P01", "A", "B", "000"])

    def test_symlink_cannot_escape_source_folder(self):
        outside = self.root / "outside.pdf"
        outside.write_bytes(b"%PDF-confidential")
        original = self.project / "Drawings/Set 1/A.pdf"
        original.unlink()
        try:
            original.symlink_to(outside)
        except OSError as exc:
            if getattr(exc, "winerror", None) != 1314:
                raise
            self.skipTest("Windows account lacks symbolic-link creation privilege")
        self.assertIn("INVALID_DRAWING", self.codes(self.audit()))

    def test_repeated_audit_has_stable_manifest_id(self):
        self.assertEqual(self.audit()["manifest_id"], self.audit()["manifest_id"])

    def test_reconciliation_checks_metadata_not_just_counts(self):
        m = self.audit()
        snapshot = self.target_snapshot(m)
        self.assertTrue(reconcile(m, snapshot)["passed"])
        snapshot["files"][0]["batch_date"] = "2020-01-01"
        self.assertFalse(reconcile(m, snapshot)["passed"])

    def test_duplicate_and_missing_target_records_cannot_cancel_out(self):
        m = self.audit()
        snapshot = self.target_snapshot(m)
        snapshot["files"][-1] = copy.deepcopy(snapshot["files"][0])
        result = reconcile(m, snapshot)
        self.assertEqual(result["compared_files"], result["expected_files"] - 1)
        self.assertEqual({r["problem"] for r in result["mismatches"]}, {"duplicate_target_record", "missing_target_record"})

    def test_incomplete_target_export_cannot_pass(self):
        m = self.audit()
        s = self.target_snapshot(m)
        s["complete"] = False
        with self.assertRaises(InputError):
            reconcile(m, s)

    def test_hash_and_target_and_current_state_are_checked(self):
        m = self.audit()
        s = self.target_snapshot(m)
        s["files"][0].update(sha256="bad", folder_id=999, is_current=True)
        result = reconcile(m, s)
        self.assertFalse(result["passed"])
        self.assertTrue({"field_mismatch", "target_mismatch", "current_flag_mismatch"}.issubset({r["problem"] for r in result["mismatches"]}))


if __name__ == "__main__":
    unittest.main()
