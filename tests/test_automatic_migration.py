import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from kahua_migration.browser import BrowserError
from kahua_migration.browser_grid import GridWalker
from kahua_migration.browser_migration import CONTRACT_ID, MigrationRunner, ProjectRunner, project_scope
from kahua_migration.browser_navigation import DestinationNavigator, match_project, match_folder
from kahua_migration.csv_config import merge_csv_config, validate_destination
from kahua_migration.journal import Journal, JournalConflict, STAGES
from kahua_migration.preflight import InputError, digest
from kahua_migration.migration_cli import main as migrate_main
import test_preflight as source_fixtures


TARGET = dict(transport="browser", environment="https://devproductionlaunch.kahua.com/", domain_name="Example",
              project_number="0001", project_name="Project one", folder_path="Sheets/Imported",
              project_policy="find_or_create", folder_policy="find_or_create", record_type="Project")


def row(index, number, name):
    return dict(grid_row=index, name=name,
                cells=[dict(column=2, value=number), dict(column=3, value=name), dict(column=6, value=name)])


class CsvMappingTests(unittest.TestCase):
    def test_csv_can_supply_all_project_mappings_and_keep_leading_zeros(self):
        config = {"project_defaults": {"drawing_log": "drawing.csv", "target": TARGET}}
        records = [dict(project_uid="p", version_set_uid="v1", source_directory="src", target_project_number="0009", version_folder="Drawings/First"),
                   dict(project_uid="p", version_set_uid="v2", source_directory="src", target_project_number="0009", version_folder="Drawings/Later")]
        merged = merge_csv_config(config, records)["projects"][0]
        self.assertEqual(merged["target"]["project_number"], "0009")
        self.assertEqual(merged["source_directory"], "src")
        self.assertEqual(merged["version_folders"], {"v1": "Drawings/First", "v2": "Drawings/Later"})
        self.assertNotIn("projects", config)

    def test_partial_repeated_mapping_and_json_conflict_are_rejected(self):
        rows = [dict(project_uid="p", version_set_uid="v1", target_project_number="0001"),
                dict(project_uid="p", version_set_uid="v2", target_project_number="")]
        with self.assertRaisesRegex(InputError, "Conflicting"):
            merge_csv_config({}, rows)
        with self.assertRaisesRegex(InputError, "disagree"):
            merge_csv_config({"projects": [{"project_uid": "p", "target": {"project_number": "2"}}]}, rows[:1])

    def test_creation_requires_explicit_type_and_supported_parent(self):
        for field, value in (("record_type", None), ("parent_path", "Hidden/Parent"), ("folder_path", "Sheets/../Wrong"),
                             ("environment", "https://example.org/"), ("project_policy", "always_create")):
            with self.subTest(field=field), self.assertRaises(InputError):
                validate_destination({**TARGET, field: value})


class NavigationTests(unittest.TestCase):
    def test_project_lookup_requires_number_and_name_and_rejects_duplicates(self):
        rows = [row(1, "0001", "Other"), row(2, "0001", "Project one"), row(3, "1", "Project one")]
        self.assertEqual(match_project(rows, "0001", "Project one")["grid_row"], 2)
        with self.assertRaisesRegex(BrowserError, "More than one"):
            match_project(rows + [row(4, "0001", "Project one")], "0001", "Project one")

    def test_file_with_folder_name_cannot_be_reused_as_a_folder(self):
        file = row(1, "", "Sheets")
        file["name"] = "Sheets, Modified, 1"
        file["cells"].append(dict(column=9, value="1"))
        self.assertIsNone(match_folder([file], "Sheets"))
        self.assertEqual(match_folder([file, row(2, "", "Sheets")], "Sheets")["grid_row"], 2)

    def test_destination_intent_survives_reopen_and_blocks_duplicate_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "journal.sqlite"
            j = Journal(path)
            spec = {k: TARGET[k] for k in ("environment", "domain_name", "project_number", "project_name", "record_type")}
            key = digest(["project", TARGET["environment"], TARGET["domain_name"], TARGET["project_number"], TARGET["project_name"]])
            j.begin_destination(key, spec, {"evidence_path": "review.json"})
            j.close()
            j = Journal(path)
            self.addCleanup(j.close)
            nav = DestinationNavigator(MagicMock(), j, Path(temp) / "out", "ocr")
            nav.target = TARGET
            nav.find_project = MagicMock(return_value=None)
            nav.create_project = MagicMock()
            with self.assertRaisesRegex(BrowserError, "refusing to repeat"):
                nav.ensure_project()
            nav.create_project.assert_not_called()
            nav.find_project.return_value = row(1, "0001", "Project one")
            nav.guard, nav.evidence = MagicMock(), MagicMock(return_value="observed.json")
            nav.ensure_project()
            self.assertEqual(j.destination(key, spec)["status"], "confirmed")
            with self.assertRaises(JournalConflict):
                j.destination(key, {**spec, "record_type": "Node"})

    def test_page_merging_requires_stable_contiguous_identities(self):
        class FixtureWalker(GridWalker):
            def __init__(self, pages):
                self.pages, self.index, self.guard = pages, 0, lambda: None
            def wheel(self, amount, **kwargs):
                self.index = 0 if amount < 0 else min(self.index + 1, len(self.pages) - 1)
            def rect(self):
                return {"height": 640}
            def read(self):
                return self.pages[self.index]
        pages = [[row(i + 1, str(n), f"P{n}") for i, n in enumerate(range(start, min(start + 16, 51)))]
                 for start in (1, 9, 17, 25, 33, 41)]
        walker = FixtureWalker(pages)
        observed = walker.collect(walker.read, lambda r: r["name"], 50)
        self.assertEqual([r["absolute_row"] for r in observed], list(range(1, 51)))
        self.assertEqual([r["name"] for r in observed], [f"P{i}" for i in range(1, 51)])
        walker = FixtureWalker(pages[:2])
        with self.assertRaisesRegex(BrowserError, "stopped moving"):
            walker.collect(walker.read, lambda r: r["name"], 50)
        walker = FixtureWalker([pages[0], pages[-1]])
        with self.assertRaisesRegex(BrowserError, "overlap"):
            walker.collect(walker.read, lambda r: r["name"], 50)

    def test_creation_recovery_requires_no_action_and_fresh_absence(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = Journal(Path(temp) / "journal.sqlite")
            self.addCleanup(journal.close)
            spec = {"folder_path": "Sheets/Imported"}
            journal.begin_destination("folder", spec, {"evidence_path": "before.json"})
            with self.assertRaises(JournalConflict):
                journal.cancel_uninvoked_destination("folder", spec, {
                    "observed_absent": True, "evidence_path": "absence.json", "reason": "Save timed out"})
            self.assertEqual(journal.destination("folder", spec)["status"], "pending")
            journal.cancel_uninvoked_destination("folder", spec, {
                "action_not_invoked": True, "observed_absent": True,
                "evidence_path": "absence.json", "reason": "Button resolution failed before activation"})
            journal.begin_destination("folder", spec, {"evidence_path": "new-review.json"})
            receipt = json.loads(journal.destination("folder", spec)["evidence"])
            self.assertEqual(receipt["previous_cancelled_intent"]["original_intent"]["evidence_path"], "before.json")
            with self.assertRaises(JournalConflict):
                journal.begin_destination("folder", spec, {"evidence_path": "duplicate.json"})

    def test_directory_waits_through_transient_empty_loading_state(self):
        with tempfile.TemporaryDirectory() as temp:
            nav = DestinationNavigator(MagicMock(), None, temp, "ocr")
            nav.target, nav.guard = TARGET, MagicMock()
            table, add_files = MagicMock(), MagicMock()
            nav.control = lambda role, name: table if role == "region" else add_files
            populated = [row(1, "", "Sheets")]
            add_files.is_enabled.side_effect = [True, False, False] + [True] * 10
            with patch("kahua_migration.browser_navigation.read_table_rows", side_effect=[[], [], []] + [populated] * 8) as read, \
                    patch("kahua_migration.browser_navigation.GridWalker") as walker:
                walker.return_value.scan_to_end.return_value = populated
                observed, _, _ = nav.folder_rows()
            self.assertEqual(observed, populated)
            self.assertGreaterEqual(read.call_count, 8)
            walker.return_value.scan_to_end.assert_called_once()


class AutomaticRunnerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = source_fixtures.MigrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        f.config["projects"][0]["target"] = TARGET.copy()
        for v in f.versions:
            v["project_kahua_entity_id"] = "0"
        f.write_inputs()
        self.manifest = f.audit()
        self.root = f.root
        self.records = self.manifest["files"]
        self.journal = Journal(self.root / "journal.sqlite")
        self.addCleanup(self.journal.close)
        for r in self.records:
            self.journal.register(r["operation_key"], r["fingerprint"], {**TARGET, "contract_id": CONTRACT_ID})

    def runner(self):
        page = MagicMock()
        page.get_by_role.return_value.count.return_value = 0
        with patch("kahua_migration.browser_migration.RecordFilterInspector"):
            return ProjectRunner(page, self.manifest, self.journal, self.root / "run", "ocr", "p1")

    def report(self, records):
        highest = {}
        for r in records:
            highest[r["sheet_number"]] = max(int(r["revision"]), highest.get(r["sheet_number"], 0))
        return dict(complete_visible_folder=True, records=[{**r, "original_filename": Path(r["source_file"]).name,
                    "target_record_id": "id-" + r["operation_key"], "file_version": 1,
                    "is_current": int(r["revision"]) == highest[r["sheet_number"]],
                    "status": "Released" if int(r["revision"]) == highest[r["sheet_number"]] else "Superseded",
                    "qa_status": "Accepted", "workflow_step": "Complete"} for r in records])

    def test_more_than_four_records_are_valid_for_the_new_runner(self):
        self.assertEqual(len(project_scope(self.manifest, "p1")[1]), 5)

    def test_entire_batch_upload_precedes_workflow_and_next_batch(self):
        runner = self.runner()
        events = []
        def upload(record, existing):
            self.assertEqual([x["operation_key"] for x in existing], [x["operation_key"] for x in self.records[:self.records.index(record)]])
            events.append(("upload", record["version_set_uid"], record["sheet_number"]))
            for stage in ("uploaded", "metadata_verified"):
                self.journal.begin(record["operation_key"], stage)
                self.journal.confirm(record["operation_key"], stage, dict(target_record_id="id", observed_at="now", evidence_path="p"))
        def workflow(stage, records, prefix):
            self.assertTrue(all(self.journal.status(r["operation_key"])["stage_index"] >= STAGES.index("metadata_verified") for r in records))
            events.append((stage, records[0]["version_set_uid"]))
            for r in records:
                self.journal.begin(r["operation_key"], stage)
                self.journal.confirm(r["operation_key"], stage, dict(target_record_id="id", observed_at="now", evidence_path="p"))
        runner._upload_one, runner._workflow = upload, workflow
        for batch in runner.batches:
            members = [r for r in self.records if r["version_set_uid"] == batch["version_set_uid"]]
            prefix = [r for r in self.records if r["upload_sequence"] <= batch["upload_sequence"]]
            runner._capture = MagicMock(return_value=(self.report(prefix), [], self.root / "observed.json"))
            runner._batch(batch, members, prefix)
        self.assertEqual([e[0] for e in events], ["upload", "upload", "submitted", "accepted", "released", "upload", "submitted", "accepted", "released", "upload", "upload", "submitted", "accepted", "released"])
        self.assertTrue(all(self.journal.status(r["operation_key"])["verified_stage"] == "verified" for r in self.records))

    def test_pending_absent_upload_is_not_replayed(self):
        runner = self.runner()
        record = self.records[0]
        self.journal.begin(record["operation_key"], "uploaded")
        runner._capture = MagicMock(return_value=({"complete_visible_folder": True, "records": []}, [], self.root / "empty.json"))
        with patch("kahua_migration.browser_migration.BatchAttachment") as attachment:
            with self.assertRaisesRegex(BrowserError, "membership"):
                runner._upload_one(record, [])
        attachment.assert_not_called()
        self.assertEqual(self.journal.status(record["operation_key"])["pending_stage"], "uploaded")

    def test_completed_project_resume_reaudits_without_repeating_mutations(self):
        runner = self.runner()
        for record in self.records:
            for stage in STAGES:
                self.journal.begin(record["operation_key"], stage)
                self.journal.confirm(record["operation_key"], stage,
                                     dict(target_record_id="id", observed_at="now", evidence_path="p"))
        for batch in runner.batches:
            records = [r for r in self.records if r["version_set_uid"] == batch["version_set_uid"]]
            self.journal.confirm_batch(runner._batch_key(batch, records), "prior-audit.json")
        events_before = self.journal.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        runner.inspector = MagicMock()
        runner._upload_one, runner._workflow = MagicMock(), MagicMock()
        runner._capture = MagicMock(return_value=(self.report(self.records), self.records, self.root / "fresh.json"))
        runner._hash = MagicMock(return_value=str(self.root / "fresh-hashes.json"))
        result = runner.run()
        self.assertTrue(result["passed"])
        runner._upload_one.assert_not_called()
        runner._workflow.assert_not_called()
        runner._capture.assert_called_once_with("project-final")
        runner._hash.assert_called_once_with(self.records, self.records, "final-originals")
        self.assertEqual(self.journal.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], events_before)

    def test_final_project_failure_does_not_reload_or_sign_out_the_browser(self):
        page, nav, project = MagicMock(), MagicMock(), MagicMock()
        nav.return_value.ensure.side_effect = BrowserError("creation control not ready")
        result = MigrationRunner(page, self.manifest, self.journal, self.root / "queue-last", "ocr",
                                 navigator_factory=nav, project_factory=project).run()
        self.assertFalse(result["passed"])
        self.assertIn("creation control not ready", result["projects"][0]["error"])
        page.goto.assert_not_called()
        project.assert_not_called()

    def test_failed_project_does_not_prevent_next_project(self):
        second = copy.deepcopy(self.manifest["projects"][0])
        second["project_uid"] = "p2"
        manifest = copy.deepcopy(self.manifest)
        manifest["projects"].append(second)
        manifest.pop("manifest_id")
        manifest["manifest_id"] = digest({k: v for k, v in manifest.items() if k != "generated_at"})
        nav, project = MagicMock(), MagicMock()
        nav.return_value.ensure.side_effect = [BrowserError("missing project"), {}]
        project.return_value.run.return_value = {"project_uid": "p2", "passed": True}
        page = MagicMock()
        page.get_by_role.return_value.count.return_value = 0
        with patch("kahua_migration.browser_migration.project_scope", return_value=(second, [], [])):
            report = MigrationRunner(page, manifest, self.journal, self.root / "queue", "ocr", navigator_factory=nav, project_factory=project).run()
        self.assertEqual([r["passed"] for r in report["projects"]], [False, True])
        project.assert_called_once()
        nav.return_value.recover_to_finder.assert_called_once()
        page.goto.assert_not_called()

    def test_plan_only_cli_never_opens_a_browser(self):
        with patch("playwright.sync_api.sync_playwright") as browser:
            result = migrate_main(["--version-sets", str(self.root / "versions.csv"), "--config", str(self.root / "config.json"),
                                  "--journal", str(self.root / "never.sqlite"), "--ocr-executable", "unused", "--out", str(self.root / "plan")])
        self.assertEqual(result, 0)
        browser.assert_not_called()
        self.assertFalse((self.root / "never.sqlite").exists())
        self.assertEqual(len(json.loads((self.root / "plan/manifest.json").read_text())["files"]), 5)


if __name__ == "__main__":
    unittest.main()
