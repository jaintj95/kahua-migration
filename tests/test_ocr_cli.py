import builtins
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

from kahua_migration.browser import BrowserError
from kahua_migration.migration_cli import main
from kahua_migration.ocr import OcrEngine
from test_automatic_migration import TARGET
import test_preflight as fixtures


class OcrCliTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.MigrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.config["projects"][0]["target"] = TARGET.copy()
        self.fixture.write_inputs()
        self.root = self.fixture.root
        self.arguments = ["--version-sets", str(self.root / "versions.csv"),
                          "--config", str(self.root / "config.json"),
                          "--journal", str(self.root / "migration.sqlite"),
                          "--out", str(self.root / "attempt"),
                          "--user-data-dir", str(self.root / "browser-profile")]

    def test_plan_does_not_import_browser_or_pillow_resolve_ocr_or_create_journal(self):
        original_import = builtins.__import__
        def offline_import(name, *args, **kwargs):
            if name.startswith(("playwright", "PIL")):
                raise AssertionError("Planning imported an execution dependency")
            return original_import(name, *args, **kwargs)
        # The old --ocr-executable syntax remains accepted on plan-only calls,
        # even if the path does not exist. It is no longer required.
        for extra in ([], ["--ocr-executable", "missing helper"], ["--ocr-backend", "tesseract"]):
            with self.subTest(extra=extra), patch("builtins.__import__", side_effect=offline_import), \
                    patch("kahua_migration.migration_cli.resolve_ocr") as resolve, redirect_stdout(io.StringIO()):
                self.assertEqual(main(self.arguments + extra), 0)
                resolve.assert_not_called()
            self.assertFalse((self.root / "migration.sqlite").exists())
            self.assertFalse((self.root / "browser-profile").exists())
            plan = json.loads((self.root / "attempt/run-plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["network_requests"], 0)

    def test_bad_engine_stops_execution_before_browser_or_journal(self):
        for stage in ("resolve", "check"):
            with self.subTest(stage=stage), patch("kahua_migration.migration_cli.resolve_ocr") as resolve, \
                    patch("playwright.sync_api.sync_playwright") as browser, redirect_stderr(io.StringIO()):
                if stage == "resolve":
                    resolve.side_effect = BrowserError("OCR missing")
                else:
                    resolve.return_value.check.side_effect = BrowserError("English data missing")
                self.assertEqual(main(self.arguments + ["--execute"]), 1)
                browser.assert_not_called()
            self.assertFalse((self.root / "migration.sqlite").exists())
            self.assertFalse((self.root / "browser-profile").exists())

    def test_cold_module_plan_runs_with_site_packages_disabled(self):
        result = subprocess.run([sys.executable, "-S", "-m", "kahua_migration", "browser", "migrate",
                                 *self.arguments], cwd=Path(__file__).parents[1],
                                capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No browser opened", result.stdout)
        self.assertFalse((self.root / "migration.sqlite").exists())

    def test_cli_passes_selected_engine_through_to_queue(self):
        engine = OcrEngine("tesseract", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        page = MagicMock()
        with patch("kahua_migration.migration_cli.resolve_ocr", return_value=engine) as resolve, \
                patch.object(OcrEngine, "check") as check, \
                patch("playwright.sync_api.sync_playwright") as browser, \
                patch("kahua_migration.migration_cli.wait_for_sign_in"), \
                patch("kahua_migration.migration_cli.MigrationRunner") as runner, redirect_stdout(io.StringIO()):
            context = browser.return_value.__enter__.return_value.chromium.launch_persistent_context.return_value
            context.pages = [page]
            runner.return_value.run.return_value = {"passed": True}
            self.assertEqual(main(self.arguments + ["--ocr-backend", "tesseract", "--ocr-executable", engine.executable, "--execute"]), 0)
            resolve.assert_called_once_with("tesseract", engine.executable)
            check.assert_called_once_with()
            self.assertIs(runner.call_args.args[4], engine)
            context.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
