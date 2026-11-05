import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from kahua_migration.browser_setup import ensure_config
from kahua_migration.journal import Journal
from kahua_migration.migration_cli import main
from kahua_migration.preflight import InputError
import test_preflight as fixtures


class BrowserSetupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "migration.local.json"

    def setup_config(self, answers=None, *, interactive=True, no_input=False):
        with patch("sys.stdin.isatty", return_value=interactive), \
                patch("builtins.input", side_effect=answers) as prompt, redirect_stdout(io.StringIO()):
            result = ensure_config(self.path, no_input=no_input)
        self.assertEqual(result, self.path)
        return json.loads(self.path.read_text(encoding="utf-8")), prompt

    def test_enter_accepts_production_defaults_and_saves_no_project_guesses(self):
        config, prompt = self.setup_config([""] * 7)
        target = config["project_defaults"]["target"]
        self.assertEqual(target["environment"], "https://launch.kahua.com/")
        self.assertEqual(target["domain_name"], "TDOT.ECPCS")
        self.assertEqual(target["folder_path"], "Sheets")
        self.assertEqual(target["project_policy"], "find_or_create")
        self.assertEqual(config["revision_policy"], "per_sheet_occurrence")
        self.assertEqual(config["revision_start"], 1)
        self.assertNotIn("project_number", target)
        self.assertNotIn("projects", config)
        self.assertEqual(prompt.call_count, 7)

    def test_custom_answers_are_saved_for_later_runs(self):
        config, _ = self.setup_config(["https://devproductionlaunch.kahua.com/", "Onindus",
                                       "Sheets/Import", "no", "n", "export_drawings.csv", "export_current.csv"])
        defaults = config["project_defaults"]
        self.assertEqual(defaults["target"]["domain_name"], "Onindus")
        self.assertEqual(defaults["target"]["environment"], "https://devproductionlaunch.kahua.com/")
        self.assertEqual(defaults["target"]["folder_path"], "Sheets/Import")
        self.assertEqual(defaults["target"]["project_policy"], "existing_only")
        self.assertEqual(defaults["target"]["folder_policy"], "existing_only")
        self.assertEqual(defaults["drawing_log"], "export_drawings.csv")
        self.assertEqual(defaults["current_log"], "export_current.csv")
        saved = self.path.read_bytes()
        _, prompt = self.setup_config(AssertionError("Existing settings must not prompt"))
        prompt.assert_not_called()
        self.assertEqual(self.path.read_bytes(), saved)

    def test_invalid_answers_reprompt_before_saving(self):
        config, prompt = self.setup_config(["http://example.com/", "", "", "Sheets/../Wrong", "",
                                            "maybe", "no", "", "../outside.csv", "", ""])
        target = config["project_defaults"]["target"]
        self.assertEqual(target["environment"], "https://launch.kahua.com/")
        self.assertEqual(target["folder_path"], "Sheets")
        self.assertEqual(target["project_policy"], "existing_only")
        self.assertEqual(prompt.call_count, 11)

    def test_no_input_option_uses_defaults_without_reading_stdin(self):
        config, prompt = self.setup_config(AssertionError("Unexpected prompt"), no_input=True)
        prompt.assert_not_called()
        self.assertEqual(config["project_defaults"]["target"]["domain_name"], "TDOT.ECPCS")

    def test_noninteractive_first_run_does_not_hang(self):
        config, prompt = self.setup_config(AssertionError("Unexpected prompt"), interactive=False)
        prompt.assert_not_called()
        self.assertEqual(config["project_defaults"]["target"]["environment"], "https://launch.kahua.com/")

    def test_end_of_input_uses_defaults_for_remaining_settings(self):
        config, prompt = self.setup_config(EOFError())
        self.assertEqual(prompt.call_count, 1)
        self.assertEqual(config["project_defaults"]["target"]["domain_name"], "TDOT.ECPCS")

    def test_cancelled_setup_does_not_leave_a_partial_config(self):
        with self.assertRaisesRegex(InputError, "Setup cancelled"):
            self.setup_config(KeyboardInterrupt())
        self.assertFalse(self.path.exists())

    def test_another_invocation_cannot_have_its_settings_overwritten(self):
        saved = '{"already_saved": "keep these settings"}\n'
        def answer(_):
            self.path.write_text(saved, encoding="utf-8")
            return ""
        self.setup_config(answer)
        self.assertEqual(self.path.read_text(encoding="utf-8"), saved)


class FirstRunCliTests(unittest.TestCase):
    def setUp(self):
        self.fixture = f = fixtures.MigrationTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.root = f.root
        drawings = self.root / "drawings"
        drawings.mkdir()
        (f.project / "drawing.csv").rename(f.project / "drawing_log.csv")
        (f.project / "current.csv").rename(f.project / "current_set_log.csv")
        f.project.rename(drawings / "source")
        self.rows = [dict(row, source_directory="source", version_folder=f"Drawings/Set {row['upload_sequence']}",
                          target_project_number="0001", target_project_name="Project one") for row in f.versions]
        f.write_csv(drawings / "version_sets.csv", self.rows)
        previous_cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous_cwd)

    def plan(self, *args):
        with patch("builtins.input", side_effect=AssertionError("Unexpected prompt")), \
                patch("kahua_migration.migration_cli.resolve_ocr") as ocr, redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--no-input", *args]), 0)
        ocr.assert_not_called()

    def test_first_plan_creates_settings_and_unique_reports_without_a_journal(self):
        self.plan()
        saved = (self.root / "migration.local.json").read_bytes()
        self.plan()
        self.assertEqual((self.root / "migration.local.json").read_bytes(), saved)
        reports = list((self.root / "output/drawings").glob("*/manifest.json"))
        self.assertEqual(len(reports), 2)
        self.assertFalse((self.root / "state/drawings.sqlite").exists())
        for report in reports:
            manifest = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(manifest["summary"]["errors"], 0, manifest["issues"])
            self.assertEqual(len(manifest["files"]), 5)
            self.assertEqual(manifest["projects"][0]["target"]["environment"], "https://launch.kahua.com/")
            self.assertEqual(manifest["projects"][0]["target"]["project_number"], "0001")

    def test_custom_config_location_is_created_automatically(self):
        self.plan("--config", "settings/customer.json", "--out", "reports/plan")
        self.assertTrue((self.root / "settings/customer.json").is_file())
        self.assertTrue((self.root / "reports/plan/run-plan.json").is_file())
        self.assertFalse((self.root / "migration.local.json").exists())

    def test_first_run_parent_override_reads_existing_exports_in_place(self):
        source_root = self.root / "customer drawings"
        (self.root / "drawings").rename(source_root)
        self.plan("--source-root", str(source_root), "--out", "plan")
        manifest = json.loads((self.root / "plan/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["source_root"], str(source_root.resolve()))
        self.assertEqual(len(manifest["files"]), 5)
        self.assertFalse((self.root / "drawings").exists())

    def test_csv_destination_overrides_do_not_replace_saved_defaults(self):
        for row in self.rows:
            row.update(target_environment="https://devproductionlaunch.kahua.com/", target_domain_name="Onindus")
        self.fixture.write_csv(self.root / "drawings/version_sets.csv", self.rows)
        self.plan("--out", "plan")
        manifest = json.loads((self.root / "plan/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["projects"][0]["target"]["domain_name"], "Onindus")
        self.assertEqual(manifest["projects"][0]["target"]["environment"], "https://devproductionlaunch.kahua.com/")
        saved = json.loads((self.root / "migration.local.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["project_defaults"]["target"]["domain_name"], "TDOT.ECPCS")
        self.assertEqual(saved["project_defaults"]["target"]["environment"], "https://launch.kahua.com/")

    def test_malformed_existing_config_is_preserved_and_stops_the_run(self):
        path = self.root / "migration.local.json"
        path.write_text("broken existing settings", encoding="utf-8")
        with patch("builtins.input", side_effect=AssertionError("Unexpected prompt")), \
                patch("kahua_migration.migration_cli.resolve_ocr") as ocr, \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--execute"]), 1)
        ocr.assert_not_called()
        self.assertEqual(path.read_text(encoding="utf-8"), "broken existing settings")
        self.assertFalse((self.root / "state/drawings.sqlite").exists())

    def test_cold_first_run_needs_no_browser_ocr_or_manual_settings_file(self):
        repository = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, "-S", "-m", "kahua_migration", "browser", "migrate"],
                                cwd=self.root, env=dict(os.environ, PYTHONPATH=str(repository)),
                                stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No browser opened", result.stdout)
        self.assertTrue((self.root / "migration.local.json").is_file())
        self.assertFalse((self.root / "state/drawings.sqlite").exists())

    def test_execution_reuses_default_journal_and_saved_settings(self):
        with patch("kahua_migration.migration_cli.resolve_ocr"), \
                patch("playwright.sync_api.sync_playwright"), \
                patch("kahua_migration.migration_cli.wait_for_sign_in"), \
                patch("kahua_migration.migration_cli.MigrationRunner") as runner, \
                patch("kahua_migration.migration_cli.Journal", wraps=Journal) as journal, \
                redirect_stdout(io.StringIO()):
            runner.return_value.run.return_value = {"passed": True}
            self.assertEqual(main(["--no-input", "--execute"]), 0)
            saved = (self.root / "migration.local.json").read_bytes()
            self.assertEqual(main(["--no-input", "--execute"]), 0)
        self.assertEqual(journal.call_count, 2)
        self.assertEqual([call.args[0] for call in journal.call_args_list], [Path("state/drawings.sqlite")] * 2)
        self.assertTrue((self.root / "state/drawings.sqlite").is_file())
        self.assertEqual((self.root / "migration.local.json").read_bytes(), saved)


if __name__ == "__main__":
    unittest.main()
