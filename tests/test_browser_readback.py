import copy
import unittest
from unittest.mock import MagicMock

from kahua_migration.browser import BrowserError
from kahua_migration.browser_readback import ReadOnlyInspector, compare_readback, field_line_bounds, read_ocr_field, refresh_record_binding


class ScreenshotReadbackTests(unittest.TestCase):
    def test_record_identity_survives_changed_accessible_labels_and_column_positions(self):
        captured = {"sheet_number": "1", "revision": "3", "revision_date": "2026-04-13",
                    "is_current": False, "grid_row": 3, "workflow_step": "Draft",
                    "grid_name": "1, Draft, 3, 4/13/2026, No", "sheet_column": 7}
        current = {**captured, "grid_name": "1", "sheet_column": 8}
        self.assertEqual(refresh_record_binding(captured, [dict(current, revision="2"), current]), current)
        for rows in ([], [current, current], [dict(current, grid_row=2)],
                     [dict(current, revision_date="2026-04-14")], [dict(current, is_current=True)],
                     [dict(current, workflow_step="Complete")]):
            with self.subTest(rows=rows), self.assertRaises(BrowserError):
                refresh_record_binding(captured, rows)

    def test_locked_session_stops_before_stale_folder_controls_are_used(self):
        inspector = object.__new__(ReadOnlyInspector)
        inspector.page = MagicMock()
        inspector.page.get_by_role.return_value.count.return_value = 1
        inspector._control = MagicMock()
        with self.assertRaisesRegex(BrowserError, "sign-in is required"):
            inspector._guard()
        inspector._control.assert_not_called()

    def test_partial_detail_waits_without_reopening_and_still_has_a_timeout(self):
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        for never_ready in (False, True):
            with self.subTest(never_ready=never_ready):
                inspector = object.__new__(ReadOnlyInspector)
                inspector.page, inspector.ui = MagicMock(), MagicMock()
                inspector._guard, inspector._wait_folder_layout = MagicMock(), MagicMock()
                grid = {"sheet_number": "1", "grid_row": 1, "sheet_column": 8}
                inspector._position_record = MagicMock(return_value=grid)
                inspector._control = MagicMock()
                cell = inspector._control.return_value.locator.return_value
                cell.count.return_value, cell.get_attribute.return_value = 1, "1"
                details, opened_pane = MagicMock(), MagicMock()
                opened_pane.count.return_value = 1
                details.wait_for.side_effect = [PlaywrightTimeout("Tabs loading"),
                    PlaywrightTimeout("Tabs unavailable") if never_ready else None]
                inspector.page.get_by_role.side_effect = lambda role, **kwargs: details if role == "tab" else opened_pane
                if never_ready:
                    with self.assertRaisesRegex(BrowserError, "did not finish opening"):
                        inspector._open_record(grid)
                else:
                    inspector._open_record(grid)
                inspector.ui._canvas_click.assert_called_once_with(cell, double=True)
                inspector._wait_folder_layout.assert_not_called()
                self.assertEqual(details.wait_for.call_count, 2)

    def setUp(self):
        self.document = {"image_width": 1600, "image_height": 1000, "lines": [
            {"text": "Batch Name", "confidence": 1, "x": 980, "y": 500, "width": 95, "height": 18},
            {"text": "Sample Revision", "confidence": 1, "x": 1195, "y": 501, "width": 160, "height": 16},
        ]}

    def test_reads_independent_value_not_an_expected_plan_value(self):
        self.assertEqual(read_ocr_field(self.document, "Batch Name"), "Sample Revision")

    def test_focused_field_crop_does_not_treat_an_uncertain_label_as_evidence(self):
        document = copy.deepcopy(self.document)
        document["lines"][0]["confidence"] = .5
        self.assertEqual(field_line_bounds(document, "Batch Name"), [944, 493, 1552, 525])
        with self.assertRaises(BrowserError):
            read_ocr_field(document, "Batch Name")
        document["lines"].append(dict(document["lines"][0], y=550))
        with self.assertRaises(BrowserError):
            field_line_bounds(document, "Batch Name")

    def test_missing_uncertain_or_ambiguous_value_stops(self):
        for mode in ("missing", "uncertain", "duplicate"):
            with self.subTest(mode=mode):
                document = copy.deepcopy(self.document)
                if mode == "missing":
                    document["lines"].pop()
                elif mode == "uncertain":
                    document["lines"][1]["confidence"] = .5
                else:
                    document["lines"].append(dict(document["lines"][1], x=1400))
                with self.assertRaises(BrowserError):
                    read_ocr_field(document, "Batch Name")

    def test_adjacent_row_is_not_mistaken_for_a_missing_value(self):
        document = copy.deepcopy(self.document)
        document["lines"][1]["y"] += 33
        with self.assertRaises(BrowserError):
            read_ocr_field(document, "Batch Name")

    def test_mismatched_metadata_and_incomplete_membership_fail_comparison(self):
        source = {"sheet_number": "001", "revision": "1", "revision_date": "2025-07-22",
                  "title": "A TITLE", "batch_name": "Bid Set", "batch_date": "2025-07-22",
                  "source_file": "Drawings/01/001.pdf", "upload_sequence": 1}
        actual = {**{k: source[k] for k in ("sheet_number", "revision", "revision_date", "title", "batch_name", "batch_date")},
                  "original_filename": "001.pdf", "is_current": True, "file_version": 1,
                  "status": "Released", "qa_status": "Accepted"}
        report = {"complete_visible_folder": True, "records": [actual]}
        self.assertTrue(compare_readback(report, [source])["passed"])
        self.assertFalse(compare_readback(report, [source])["content_hashes_verified_by_this_audit"])
        for field, value in (("batch_date", None), ("batch_name", "Wrong"), ("is_current", False),
                             ("file_version", 2), ("qa_status", ""), ("qa_status", "Pending Review")):
            with self.subTest(field=field):
                bad = {"complete_visible_folder": True, "records": [{**actual, field: value}]}
                self.assertFalse(compare_readback(bad, [source])["passed"])
        self.assertFalse(compare_readback({**report, "records": []}, [source])["passed"])
        self.assertFalse(compare_readback({**report, "complete_visible_folder": False}, [source])["passed"])


if __name__ == "__main__":
    unittest.main()
