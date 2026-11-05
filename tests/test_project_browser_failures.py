"""Opt-in Chrome tests of real dialog controls around the project queue.

Set KAHUA_BROWSER_TESTS=1. Chrome and a local OCR backend are required.
KAHUA_TEST_OCR_BACKEND / KAHUA_TEST_OCR_EXECUTABLE select OCR as in the CLI.
Every URL is intercepted with fixture HTML; these tests never contact Kahua.
File writes and workflow transitions use the synthetic tenant from the logic
tests. DOM dismissal, screenshots, OCR, tabs, queue and SQLite journal are real.
"""

import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from kahua_migration.browser import BrowserError
from kahua_migration.browser_navigation import DestinationNavigator
from kahua_migration.browser_session import BrowserSessionRecovery
from kahua_migration.browser_visual import click_text
from kahua_migration.journal import Journal
from kahua_migration.ocr import resolve_ocr
import project_failure_fixture as fixture
import test_project_failures as logic_cases


HTML = """<!doctype html><html><body style="font:18px Arial">
<main role="application">
<button style="height:32px" aria-label="Open project descendants for Example">Example</button>
<button style="height:32px" role="option" aria-label="Open Project Finder"
 onclick="document.querySelector('#finder').hidden=false">Project Finder</button>
<div id="finder" role="grid" aria-label="Partition Items" hidden>Project directory</div>
<p id="current"></p></main>
<canvas id="uno-canvas" style="position:fixed;inset:0;width:100vw;height:100vh;z-index:20"></canvas>
<script>
// Model Uno's pointer surface over its accessibility controls. Production OCR
// deliberately refuses clicks that do not land on this exact canvas.
document.querySelector('canvas').onclick=e=>{
  const button=document.elementsFromPoint(e.clientX,e.clientY).find(node=>node.tagName==='BUTTON');
  if(button) button.click();
};
</script></body></html>"""


class NativePage:
    def __init__(self, page, tenant, mode="cancel"):
        self.native, self.tenant, self.mode = page, tenant, mode
        self.prepared = None

    def __getattr__(self, name):
        return getattr(self.native, name)

    @property
    def closed(self):
        return self.native.is_closed()

    @property
    def dialog_open(self):
        return bool(self.native.get_by_role("group", name="Add Files", exact=True).count())

    @dialog_open.setter
    def dialog_open(self, value):
        if not value:
            self.native.evaluate("document.querySelector('#upload')?.remove()")
        elif not self.dialog_open:
            self.native.evaluate("""mode => {
                const modal = document.createElement('section');
                modal.id='upload'; modal.role='group'; modal.ariaLabel='Add Files';
                modal.style='position:fixed;left:600px;top:420px;width:650px;background:white;padding:24px;border:2px solid black';
                modal.innerHTML='<h2>Add Files</h2><div role="grid" aria-label="fileGrid">1.pdf</div>' +
                    '<button style="height:36px">Cancel</button> <button style="height:36px">Close</button>' +
                    '<button style="height:36px">Execute</button>';
                const [cancel, close, execute] = modal.querySelectorAll('button');
                execute.onclick=()=>{document.body.dataset.unexpectedSave='true';};
                // Kahua paints canvas buttons. Avoid OS-native focus rings and
                // bevels in this synthetic canvas fixture; they differ by OS
                // and may be recognized as punctuation around the label.
                for (const button of [cancel, close, execute]) button.style='display:block;height:36px;font:18px Arial;margin-top:10px;border:0;border-radius:0;outline:none;background:#eee';
                cancel.disabled = mode === 'close' || mode === 'disabled';
                close.disabled = mode === 'disabled';
                for (const button of [cancel, close]) button.onclick=()=>{
                    modal.remove();
                    if (mode === 'late') setTimeout(()=>{
                        const error=document.createElement('section');
                        error.id='upload-error';
                        error.style='position:fixed;left:628px;top:414px;width:342px;height:172px;background:white;border:1px solid black';
                        error.innerHTML='<p style="margin:16px 24px;font:17px Arial">Upload Failed</p>' +
                            '<p style="margin:30px 24px;font:14px Arial;white-space:nowrap">File could not be uploaded. Please try again.</p>' +
                            '<button style="height:32px;float:right;margin-right:24px">OK</button>';
                        error.querySelector('button').onclick=()=>error.remove();
                        document.body.append(error);
                    }, 200);
                };
                if (mode === 'pointer') cancel.onkeydown=e=>{if(e.key==='Enter') e.preventDefault();};
                document.body.append(modal);
            }""", self.mode)


class NativeNavigator(DestinationNavigator):
    def __init__(self, page, journal, output, _ocr):
        engine = resolve_ocr(os.environ.get("KAHUA_TEST_OCR_BACKEND", "auto"),
                             os.environ.get("KAHUA_TEST_OCR_EXECUTABLE"))
        super().__init__(page, journal, output, engine)

    def ensure(self, target):
        self.target = target
        self.guard()
        if self._upload_open():
            raise BrowserError("The prior upload still blocks project navigation")
        self.page.evaluate("name=>document.querySelector('#current').textContent=name", target["project_name"])
        if self.page.mode == "late" and target["project_number"] == "0002":
            self.page.wait_for_timeout(300)
            self.guard()


@unittest.skipUnless(os.environ.get("KAHUA_BROWSER_TESTS") == "1", "Set KAHUA_BROWSER_TESTS=1 for Chrome/OCR integration")
class BrowserProjectFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel="chrome", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.logic = logic_cases.ProjectFailureTests()
        self.logic.setUp()
        self.addCleanup(self.logic.doCleanups)
        self.context = self.browser.new_context(viewport={"width": 1600, "height": 1000})
        self.addCleanup(self.context.close)
        self.context.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=HTML))
        self.environment = self.logic.manifest["projects"][0]["target"]["environment"]

    def page(self, tenant, mode="cancel"):
        native = self.context.new_page()
        native.goto(self.environment)
        return NativePage(native, tenant, mode)

    def test_css_pixels_keep_ocr_clicks_aligned_at_double_display_scale(self):
        context = self.browser.new_context(viewport={"width": 1600, "height": 1000}, device_scale_factor=2)
        self.addCleanup(context.close)
        page = context.new_page()
        page.set_content(HTML)
        page.evaluate("""() => {
            const button = document.createElement('button');
            button.textContent = 'Download';
            button.style = 'position:fixed;left:1100px;top:517px;width:180px;height:40px;font:22px Arial';
            button.onclick = () => document.body.dataset.downloadClicked = 'true';
            document.body.append(button);
        }""")
        engine = resolve_ocr(os.environ.get("KAHUA_TEST_OCR_BACKEND", "auto"),
                             os.environ.get("KAHUA_TEST_OCR_EXECUTABLE"))
        click_text(page, engine, self.logic.root / "scaled", "Download", [1080, 500, 1320, 580])
        self.assertEqual(page.locator("body").get_attribute("data-download-clicked"), "true")
        page.evaluate("""() => {
            const overlay = document.createElement('div');
            overlay.style = 'position:fixed;inset:0;z-index:30';
            document.body.append(overlay);
        }""")
        with self.assertRaisesRegex(BrowserError, "overlay"):
            click_text(page, engine, self.logic.root / "overlay", "Download", [1080, 500, 1320, 580])

    def check_dialog(self, mode, fault, expected_action):
        tenant = fixture.Tenant(self.logic.manifest, fault)
        page = self.page(tenant, mode)
        recovery = MagicMock()
        recovery.replace_failed_tab.side_effect = AssertionError("In-app recovery must preserve this tab")
        pointer_errors = []
        def observed_click(*args, **kwargs):
            try:
                return click_text(*args, **kwargs)
            except Exception as exc:
                pointer_errors.append(str(exc))
                raise
        with patch("kahua_migration.browser_navigation.click_text", side_effect=observed_click):
            first, _ = self.logic.queue(tenant, page=page, recovery=recovery, navigator_factory=NativeNavigator)
        self.logic.assert_paused_then_completed(first)
        self.assertEqual(first["projects"][0]["browser_recovery"]["dismissal_action"], expected_action, pointer_errors)
        self.assertEqual(page.locator("#current").inner_text(), "Project two")
        self.assertFalse(page.dialog_open)
        self.assertIsNone(page.locator('body').get_attribute('data-unexpected-save'))
        self.assertFalse(page.closed)
        recovery.replace_failed_tab.assert_not_called()
        if mode == "late":
            notices = first["projects"][0]["browser_recovery"].get("dismissed_upload_errors", [])
            notices += first["projects"][1].get("dismissed_previous_upload_errors", [])
            self.assertEqual(len(notices), 1)
            self.assertEqual(page.get_by_text("Upload Failed", exact=True).count(), 0)
        pending = self.logic.journal.status(tenant.first["operation_key"])
        self.assertEqual(pending["pending_stage"], "uploaded")
        self.assertTrue(pending["uncertain"])
        self.logic.journal.close()
        self.logic.journal = Journal(self.logic.root / "journal.sqlite")
        self.addCleanup(self.logic.journal.close)
        before = list(tenant.saves)
        again, _ = self.logic.queue(tenant, page=page, recovery=recovery, navigator_factory=NativeNavigator)
        if fault == "attach":
            self.logic.assert_paused_then_completed(again)
            self.assertEqual(before, tenant.saves)
        else:
            self.assertTrue(again["passed"], again)
            self.assertEqual(len(tenant.saves), 10)
            self.assertTrue(all(row["file_version"] == 1 for row in tenant.rows.values()))
        before = (list(tenant.saves), list(tenant.attachments), list(tenant.actions))
        self.logic.queue(tenant, page=page, recovery=recovery, navigator_factory=NativeNavigator)
        self.assertEqual(before, (tenant.saves, tenant.attachments, tenant.actions))

    def test_cancel_closes_failed_attachment_and_next_project_finishes(self):
        self.check_dialog("cancel", "attach", "cancel_key")

    def test_disabled_cancel_uses_close_and_saved_upload_is_not_repeated(self):
        self.check_dialog("close", "save_after_commit", "close_key")

    def test_lost_keyboard_action_uses_visible_cancel_and_preserves_unknown_save(self):
        self.check_dialog("pointer", "save_after_commit", "cancel_pointer")

    def test_late_upload_error_does_not_pause_the_next_project(self):
        self.check_dialog("late", "save_after_commit", "cancel_key")

    def test_unknown_upload_notice_is_not_acknowledged(self):
        page = self.page(fixture.Tenant(self.logic.manifest))
        page.evaluate("""() => {
            document.body.insertAdjacentHTML('beforeend', '<h2>Upload Failed</h2><p>Overwrite existing data?</p><button id="unknown-ok">OK</button>');
            document.querySelector('#unknown-ok').onclick=()=>document.body.dataset.unexpectedSave='true';
        }""")
        nav = NativeNavigator(page, self.logic.journal, self.logic.root / "unknown", "ocr")
        nav.target = self.logic.manifest["projects"][0]["target"]
        nav.allow_upload_error_dismissal = True
        with self.assertRaisesRegex(BrowserError, "unrecognized"):
            nav.dismiss_upload_error()
        self.assertIsNone(page.locator('body').get_attribute('data-unexpected-save'))
        page.goto("https://other.example/")
        page.evaluate("document.body.insertAdjacentHTML('beforeend', '<h2>Upload Failed</h2>')")
        with self.assertRaisesRegex(BrowserError, "origin"):
            nav.dismiss_upload_error()

    def test_disabled_dismissals_never_invoke_execute_or_clear_save_intent(self):
        tenant = fixture.Tenant(self.logic.manifest, "save_after_commit")
        page = self.page(tenant, "disabled")
        recovery = MagicMock(page=page)
        recovery.replace_failed_tab.side_effect = BrowserError("Browser unavailable")
        report, _ = self.logic.queue(tenant, page=page, recovery=recovery, navigator_factory=NativeNavigator)
        self.assertEqual(report["queue_status"], "blocked")
        self.assertEqual(report["not_started_projects"], ["p2"])
        self.assertEqual(len(tenant.saves), 1)
        self.assertTrue(page.dialog_open)
        self.assertIsNone(page.locator('body').get_attribute('data-unexpected-save'))
        self.assertTrue(self.logic.journal.upload_commit_attempted(tenant.first["operation_key"]))

    def test_replacement_tab_copies_storage_and_leaves_old_tab_closed(self):
        page = self.context.new_page()
        page.goto(self.environment)
        page.evaluate("sessionStorage.setItem('fixture', 'synthetic-value')")
        recovery = BrowserSessionRecovery(page, timeout_ms=5000)
        recovery.checkpoint(page, self.environment)
        fresh = recovery.replace_failed_tab(page, self.environment)
        self.assertTrue(page.is_closed())
        self.assertFalse(fresh.is_closed())
        self.assertEqual(fresh.evaluate("sessionStorage.getItem('fixture')"), "synthetic-value")
