import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from kahua_migration.browser import BrowserAdapter, BrowserError, canvas_relative_rect, read_log_csv, validate_browser_profile
from kahua_migration.preflight import InputError


class BrowserExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "log.csv"
        columns = {name: name for name in ("sheet_number", "revision", "title", "revision_date",
                                          "batch_name", "batch_date", "is_current", "file_version")}
        self.profile = {"export": {"columns": columns, "record_id_columns": ["record_key"],
            "status_rules": [{"equals": {"workflow": "Draft", "qa": ""}, "status": "Draft"},
                             {"equals": {"workflow": "Complete", "qa": "Accepted"}, "status": "Released"}],
            "date_format": "%m/%d/%Y", "true_values": ["True"], "false_values": ["False"]}}
        self.row = {"record_key": "opaque-target-key", "sheet_number": "001", "revision": "1", "title": "Title",
            "revision_date": "07/22/2025", "batch_name": "Bid Set", "batch_date": "07/22/2025",
            "is_current": "True", "file_version": "1", "workflow": "Complete", "qa": "Accepted"}

    def write(self, rows):
        with self.path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def test_preserves_sheet_number_and_verifies_exported_dates(self):
        self.write([self.row])
        result = read_log_csv(self.path, self.profile, {"folder_path": "Sheets"})
        self.assertEqual(result[0]["sheet_number"], "001")
        self.assertEqual(result[0]["batch_date"], "2025-07-22")
        self.assertEqual(result[0]["status"], "Released")
        self.assertTrue(result[0]["is_current"])

    def test_missing_metadata_is_never_filled_from_source(self):
        row = dict(self.row)
        row.pop("batch_date")
        self.write([row])
        with self.assertRaisesRegex(BrowserError, "fields are missing"):
            read_log_csv(self.path, self.profile, {})

    def test_ambiguous_workflow_or_current_flags_block(self):
        for key, value in [("qa", "Rejected"), ("is_current", "maybe"), ("revision_date", "22/07/2025")]:
            with self.subTest(key=key):
                self.write([{**self.row, key: value}])
                with self.assertRaises(BrowserError):
                    read_log_csv(self.path, self.profile, {})

    def test_duplicate_revisions_block(self):
        self.write([self.row, dict(self.row)])
        with self.assertRaisesRegex(BrowserError, "duplicate"):
            read_log_csv(self.path, self.profile, {})

    def test_unverified_browser_profile_cannot_write(self):
        with self.assertRaisesRegex(InputError, "live four-file pilot"):
            validate_browser_profile({"schema_version": 1, "transport": "browser"})

    def test_metadata_edit_uses_planned_values_not_incorrect_readback(self):
        adapter = object.__new__(BrowserAdapter)
        adapter.profile = {"input_date_format": "%m/%d/%Y"}
        adapter._context({"target": {"folder_path": "Sheets/Pilot"}},
                         {"sheet_number": "001", "revision": "2", "batch_name": "Revision",
                          "batch_date": "2025-12-12"},
                         {"sheet_number": "001", "revision": "2", "batch_name": "Wrong batch",
                          "batch_date": "2000-01-01", "target_record_id": "saved-record"})
        self.assertEqual(adapter._format("{batch_name}"), "Revision")
        self.assertEqual(adapter._format("{batch_date_ui}"), "12/12/2025")
        self.assertEqual(adapter._format("{target_record_id}"), "saved-record")

    def test_appended_text_blocks_the_following_commit(self):
        # The live canvas appended text despite the semantic input's apparent
        # selection. Reproduce that behavior and require a stop before commit.
        class CanvasField:
            def __init__(self):
                self.value = "OLD"
            def fill(self, value, **kwargs):
                self.value = value + self.value
            def press_sequentially(self, value, **kwargs):
                self.value = value + self.value
            def press(self, value, **kwargs):
                pass
            def input_value(self):
                return self.value

        for operation in ("fill", "type"):
            with self.subTest(operation=operation):
                field = CanvasField()
                adapter = object.__new__(BrowserAdapter)
                adapter.timeout, adapter.context = 1000, {}
                resolved = []
                def locate(spec):
                    resolved.append(spec["name"])
                    return field
                adapter._locate = locate
                with self.assertRaisesRegex(BrowserError, "Input readback differs"):
                    adapter._steps([
                        {"op": operation, "locator": {"name": "batch"}, "value": "NEW"},
                        {"op": "press", "locator": {"name": "commit"}, "value": "Enter"},
                    ])
                self.assertEqual(resolved, ["batch"])

    def test_canvas_pointer_blocks_offscreen_geometry_and_unexpected_overlays(self):
        clicks = []
        adapter = object.__new__(BrowserAdapter)
        adapter.page = SimpleNamespace(
            viewport_size={"width": 1600, "height": 1000},
            mouse=SimpleNamespace(click=lambda *args, **kw: clicks.append((args, kw))),
            evaluate=lambda *args: {"tag": "DIV", "id": "confirmation-dialog"})
        frame = {"x": 0, "y": 0, "width": 1600, "height": 1000}
        target = SimpleNamespace(bounding_box=lambda: {"x": 1000, "y": 500, "width": 100, "height": 40},
                                 evaluate=lambda script: {"application": frame, "canvas": frame})
        with self.assertRaisesRegex(BrowserError, "unexpected overlay"):
            adapter._canvas_click(target)
        self.assertEqual(clicks, [])
        adapter.page.evaluate = lambda *args: {"tag": "CANVAS", "id": "uno-canvas"}
        target.bounding_box = lambda: {"x": 1000, "y": 1421, "width": 100, "height": 40}
        with self.assertRaisesRegex(BrowserError, "outside the viewport"):
            adapter._canvas_click(target)
        self.assertEqual(clicks, [])
        target.bounding_box = lambda: {"x": 1000, "y": 500, "width": 100, "height": 40}
        adapter._canvas_click(target, double=True)
        self.assertEqual(clicks, [((1050, 520), {"click_count": 2})])

    def test_canvas_selection_removes_only_the_application_scroll_offset(self):
        viewport = {"width": 1600, "height": 1000}
        canvas = {"x": 0, "y": 0, **viewport}
        for row_y in (316, 356):
            rect = {"x": -311, "y": row_y, "width": 44, "height": 40}
            actual = canvas_relative_rect(rect, {**canvas, "x": -940}, canvas, viewport)
            self.assertEqual(actual, {**rect, "x": 629})
        for application in (None, {**canvas, "width": 800}, {**canvas, "height": 500}):
            with self.assertRaises(BrowserError):
                canvas_relative_rect(rect, application, canvas, viewport)


if __name__ == "__main__":
    unittest.main()
