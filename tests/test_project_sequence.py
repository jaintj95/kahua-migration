"""CSV queue ordering, including validation before browser startup."""

import copy
import json
import unittest
from unittest.mock import MagicMock, patch

from kahua_migration.browser import BrowserError
from kahua_migration.browser_migration import MigrationRunner
from kahua_migration.journal import Journal
from kahua_migration.migration_cli import main as migrate_main
from kahua_migration.preflight import InputError
from kahua_migration.runner import build_plan, select_projects
import test_preflight as source_fixtures


class ProjectSequenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = f = source_fixtures.MigrationTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        f.config["projects"][0]["target"] = dict(
            transport="browser", environment="https://devproductionlaunch.kahua.com/",
            domain_name="Example", project_number="001", project_name="First",
            folder_path="Sheets/Imported")
        second = copy.deepcopy(f.config["projects"][0])
        second["project_uid"] = "p2"
        second["target"].update(project_number="002", project_name="Second")
        f.config["projects"].append(second)
        # Rows are interleaved; lexical UID and lexical sequence order would
        # both be wrong. Nonconsecutive ranks are intentional and supported.
        f.versions = [dict(row, project_uid=uid, project_sequence=sequence)
                      for row in reversed(f.versions)
                      for uid, sequence in (("p1", "20"), ("p2", "3"))]
        f.write_inputs()

    def test_audit_plan_and_selection_use_numeric_project_then_batch_order(self):
        manifest = self.fixture.audit()
        self.assertEqual(manifest["summary"]["errors"], 0, manifest["issues"])
        plan = build_plan(manifest)
        self.assertEqual([(p["project_uid"], p["project_sequence"]) for p in plan["projects"]],
                         [("p2", 3), ("p1", 20)])
        for project in plan["projects"]:
            self.assertEqual([b["upload_sequence"] for b in project["batches"]], [1, 2, 3])
        self.assertEqual([p["project_uid"] for p in select_projects(manifest, ["p1", "p2"])], ["p2", "p1"])
        self.assertEqual([p["project_sequence"] for p in select_projects(manifest, ["p1"])], [20])
        self.assertEqual([r["project_uid"] for r in manifest["files"]], ["p2"] * 5 + ["p1"] * 5)

    def test_missing_column_preserves_legacy_order_and_journal_identities(self):
        ordered = self.fixture.audit()
        for row in self.fixture.versions:
            del row["project_sequence"]
        self.fixture.write_inputs()
        legacy = self.fixture.audit()
        self.assertEqual([p["project_uid"] for p in legacy["projects"]], ["p1", "p2"])
        identities = lambda m: {r["operation_key"]: r["fingerprint"] for r in m["files"]}
        self.assertEqual(identities(ordered), identities(legacy))

    def test_invalid_or_blank_sequence_is_rejected(self):
        for invalid in ("", "0", "-1", "1.5", "03", " 3", "three"):
            with self.subTest(value=invalid):
                self.fixture.versions[0]["project_sequence"] = invalid
                self.fixture.write_inputs()
                with self.assertRaisesRegex(InputError, "Invalid project_sequence"):
                    self.fixture.audit()

    def test_partial_project_sequence_is_rejected(self):
        for row in self.fixture.versions:
            if row["project_uid"] == "p2":
                row["project_sequence"] = ""
        self.fixture.write_inputs()
        with self.assertRaisesRegex(InputError, "Invalid project_sequence for p2"):
            self.fixture.audit()

    def test_conflicting_sequence_within_a_project_is_rejected(self):
        self.fixture.versions[0]["project_sequence"] = "21"
        self.fixture.write_inputs()
        with self.assertRaisesRegex(InputError, "Conflicting project_sequence"):
            self.fixture.audit()

    def test_duplicate_sequence_across_projects_is_rejected_before_browser(self):
        for row in self.fixture.versions:
            row["project_sequence"] = "1"
        self.fixture.write_inputs()
        with self.assertRaisesRegex(InputError, "Duplicate project_sequence"):
            self.fixture.audit()
        with patch("playwright.sync_api.sync_playwright") as browser:
            self.assertEqual(self.cli("invalid", execute=True), 1)
        browser.assert_not_called()
        self.assertFalse((self.fixture.root / "never.sqlite").exists())

    def cli(self, output, execute=False):
        root = self.fixture.root
        args = ["--version-sets", str(root / "versions.csv"), "--config", str(root / "config.json"),
                "--journal", str(root / "never.sqlite"), "--out", str(root / output)]
        return migrate_main(args + (["--execute"] if execute else []))

    def test_plan_only_cli_reports_the_full_ordered_queue(self):
        with patch("playwright.sync_api.sync_playwright") as browser:
            self.assertEqual(self.cli("plan"), 0)
        browser.assert_not_called()
        self.assertFalse((self.fixture.root / "never.sqlite").exists())
        plan = json.loads((self.fixture.root / "plan/run-plan.json").read_text())
        self.assertEqual([p["project_uid"] for p in plan["projects"]], ["p2", "p1"])

    def test_queue_completes_each_project_before_starting_the_next(self):
        self.run_queue(fail_first=False)

    def test_failed_first_project_recovers_then_advances_in_sequence(self):
        self.run_queue(fail_first=True)

    def run_queue(self, fail_first):
        manifest = self.fixture.audit()
        events = []
        def factory(page, manifest, journal, output, ocr, uid):
            events.append(("start", uid))
            project = MagicMock()
            def run():
                events.append(("finish", uid))
                if fail_first and uid == "p2":
                    raise BrowserError("synthetic project failure")
                return {"project_uid": uid, "passed": True}
            project.run.side_effect = run
            return project
        journal = Journal(self.fixture.root / "queue.sqlite")
        self.addCleanup(journal.close)
        nav = MagicMock()
        page = MagicMock()
        page.get_by_role.return_value.count.return_value = 0
        report = MigrationRunner(page, manifest, journal, self.fixture.root / "queue", "unused",
                                 navigator_factory=nav, project_factory=factory,
                                 session_recovery=MagicMock()).run()
        self.assertEqual(events, [("start", "p2"), ("finish", "p2"), ("start", "p1"), ("finish", "p1")])
        self.assertEqual([p["project_uid"] for p in report["projects"]], ["p2", "p1"])
        self.assertEqual([p["passed"] for p in report["projects"]], [not fail_first, True])
        self.assertEqual(report["not_started_projects"], [])
        self.assertEqual(nav.return_value.recover_to_finder.call_count, int(fail_first))


if __name__ == "__main__":
    unittest.main()
