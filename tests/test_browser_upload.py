import tempfile
import unittest
from pathlib import Path

from kahua_migration.browser import BrowserError
from kahua_migration.browser_upload import candidate_header_bounds, bind_upload_row_names, bulk_revision_date_points, focused_bulk_date_points, upload_date_points, bind_upload_cells, isolated_header_bounds, validate_attachment_sources
from kahua_migration.preflight import digest, file_digest


class UploadHeaderTests(unittest.TestCase):
    def test_hidden_filename_uses_only_the_same_previously_observed_row(self):
        expected = ["1.pdf", "ROADWAY-SIGN4.pdf"]
        visible = [{"row_id": "row-a", "index": 0, "File Name": expected[0]},
                   {"row_id": "row-b", "index": 1, "File Name": expected[1]}]
        _, bindings = bind_upload_row_names(visible, {}, expected)
        hidden = [visible[0], dict(visible[1], **{"File Name": None})]
        observed, _ = bind_upload_row_names(hidden, bindings, expected)
        self.assertEqual(observed[1]["File Name"], "ROADWAY-SIGN4.pdf")
        self.assertEqual(observed[1]["filename_evidence"], "same_observed_row_element")
        for previous, changed in (({}, hidden), (bindings, [hidden[0], dict(hidden[1], row_id="new-row")]),
                                  (bindings, [dict(hidden[0], **{"File Name": "other.pdf"}), hidden[1]])):
            with self.subTest(previous=previous, changed=changed), self.assertRaises(BrowserError):
                bind_upload_row_names(changed, previous, expected)

    def cells(self, offset=25):
        headers = [{"x": 100 + i * 70, "width": 70} for i in range(4)]
        cells = [{"x": h["x"] + offset, "width": h["width"], "column": i + 1 if i in (0, 3) else 0,
                  "value": ("", "Add", "1.pdf", "1")[i]} for i, h in enumerate(headers)]
        return headers, cells

    def test_explicit_columns_reconcile_different_row_and_header_scroll_offsets(self):
        for offset in (0, 1, 25, -24):
            with self.subTest(offset=offset):
                headers, cells = self.cells(offset)
                self.assertEqual(bind_upload_cells(headers, cells), [
                    {"column": i + 1, "value": c["value"]} for i, c in enumerate(cells)])
        headers, cells = self.cells()
        for cell in cells[2:]:
            cell["x"] -= 25
        self.assertEqual([c["column"] for c in bind_upload_cells(headers, list(reversed(cells)))], [1, 2, 3, 4])

    def test_reordered_duplicate_or_unanchored_upload_cells_stop(self):
        for mode in ("reordered", "duplicate", "unanchored", "wrong_width", "partial"):
            with self.subTest(mode=mode):
                headers, cells = self.cells()
                if mode == "reordered":
                    cells[-1]["x"] = cells[0]["x"] - 10
                elif mode == "duplicate":
                    cells.append(dict(cells[1]))
                elif mode == "unanchored":
                    cells[-1]["column"] = 0
                elif mode == "partial":
                    cells.pop()
                else:
                    cells[1]["width"] += 10
                with self.assertRaises(BrowserError):
                    bind_upload_cells(headers, cells)

    def test_absent_edge_cells_remain_unknown_with_consistent_column_anchors(self):
        headers, cells = self.cells()
        cells[2]["column"] = 3
        observed = bind_upload_cells(headers, cells[:3])
        self.assertEqual(observed, [{"column": i + 1, "value": c["value"]}
                                    for i, c in enumerate(cells[:3])])
        self.assertNotIn(4, [c["column"] for c in observed])
        self.assertEqual(bind_upload_cells(headers, cells[1:]), [
            {"column": i + 2, "value": c["value"]} for i, c in enumerate(cells[1:])])
        # A missing interior cell is also unknown when every remaining cell
        # has its own explicit index; no neighboring value shifts into it.
        self.assertEqual(bind_upload_cells(headers, [cells[0], cells[2], cells[3]]), [
            {"column": i + 1, "value": cells[i]["value"]} for i in (0, 2, 3)])

    def test_stale_cell_positions_need_unique_independent_widths(self):
        headers = [{"x": x, "width": width} for x, width in ((100, 48), (148, 95), (243, 162), (405, 130))]
        cells = [{"x": x, "width": width, "column": column, "value": value}
                 for x, width, column, value in ((379, 48, 1, ""), (427, 95, 0, "Add"),
                                                  (522, 162, 0, "ROADWAY-SIGN4.pdf"), (405, 130, 4, "ROADWAY-SIGN4"))]
        self.assertEqual(bind_upload_cells(headers, cells), [
            {"column": i + 1, "value": value} for i, value in enumerate(("", "Add", "ROADWAY-SIGN4.pdf", "ROADWAY-SIGN4"))])
        headers[1]["width"] = 162
        cells[1]["width"] = 162
        with self.assertRaisesRegex(BrowserError, "unique column width"):
            bind_upload_cells(headers, cells)

    def document(self):
        return {"image_width": 1600, "image_height": 1000, "lines": [
            {"text": "File Name", "x": 495, "y": 373, "width": 66, "height": 16, "confidence": 1},
            {"text": "Numbe", "x": 575, "y": 373, "width": 45, "height": 16, "confidence": .3},
            {"text": "Title", "x": 638, "y": 373, "width": 31, "height": 16, "confidence": 1}]}

    def test_uncertain_exact_header_is_only_a_crop_candidate(self):
        doc = self.document()
        doc["lines"][1]["text"] = "Number"
        self.assertEqual(candidate_header_bounds(doc, "Number", [350, 362, 1550, 404]), [563, 362, 632, 404])
        doc["lines"].append(dict(doc["lines"][1]))
        with self.assertRaises(BrowserError):
            candidate_header_bounds(doc, "Number", [350, 362, 1550, 404])

    def test_failed_header_can_be_isolated_only_between_observed_neighbors(self):
        self.assertEqual(isolated_header_bounds(self.document(), "Number", ("File Name", "Number", "Title"),
                                               [350, 362, 1550, 404]), [562, 362, 637, 404])

    def test_uncertain_duplicate_missing_or_reordered_neighbors_stop(self):
        for mode in ("uncertain", "duplicate", "missing", "reordered"):
            with self.subTest(mode=mode):
                doc = self.document()
                if mode == "uncertain":
                    doc["lines"][0]["confidence"] = .5
                elif mode == "duplicate":
                    doc["lines"].append(dict(doc["lines"][0], x=400))
                elif mode == "missing":
                    doc["lines"].pop()
                else:
                    doc["lines"][-1]["x"] = 400
                with self.assertRaises(BrowserError):
                    isolated_header_bounds(doc, "Number", ("File Name", "Number", "Title"),
                                           [350, 362, 1550, 404])


class UploadDateLayoutTests(unittest.TestCase):
    def test_bulk_calendar_uses_focused_control_when_editor_widths_lag(self):
        viewport = {"width": 1600, "height": 1000}
        geometry = {"tag": "BUTTON", "label": "Date Picker",
                    "rect": {"x": 555, "y": 505, "width": 24, "height": 24},
                    "application": {"x": -78, "y": 0, **viewport},
                    "canvas": {"x": 0, "y": 0, **viewport}}
        self.assertEqual(focused_bulk_date_points(geometry, viewport, 520), ((645, 517), (613, 517)))
        with self.assertRaises(BrowserError):
            focused_bulk_date_points(geometry, viewport, 560)
        with self.assertRaises(BrowserError):
            focused_bulk_date_points({**geometry, "label": "Other field"}, viewport, 520)

    def test_bulk_calendar_follows_both_observed_editor_widths(self):
        self.assertEqual(bulk_revision_date_points(540, [143, 143]), ((646, 540), (614, 540)))
        self.assertEqual(bulk_revision_date_points(540, [158, 158]), ((661, 540), (629, 540)))
        for widths in ([], [158], [158, 143], [300, 300], [158, 158, 158]):
            with self.subTest(widths=widths), self.assertRaises(BrowserError):
                bulk_revision_date_points(540, widths)

    def test_date_points_follow_the_observed_header_after_horizontal_movement(self):
        for left in (1405, 1431):
            self.assertEqual(upload_date_points({"header_left": left, "y": 420}, "Batch Date", 150),
                             ((left + 88, 420), (left + 116, 420)))
        self.assertEqual(upload_date_points({"header_left": 1051, "y": 460}, "- Rev Date:", 166),
                         ((1139, 460), (1167, 460)))
        self.assertEqual(upload_date_points({"header_left": 1414, "y": 420}, "Batch Date", 145),
                         ((1497, 420), (1525, 420)))

    def test_changed_width_or_offscreen_calendar_stops(self):
        for column, width, left in (("Batch Date", 200, 1405), ("- Rev Date:", 100, 1051),
                                    ("Batch Date", 150, 1500), ("Batch Date", 150, 200)):
            with self.subTest(column=column, width=width, left=left):
                with self.assertRaises(BrowserError):
                    upload_date_points({"header_left": left, "y": 420}, column, width)


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.records = []
        for name in ("1.pdf", "ROADWAY-SIGN4.pdf"):
            path = self.root / name
            path.write_bytes(b"%PDF audited sample " + name.encode())
            self.records.append({"operation_key": name, "project_uid": "project", "version_set_uid": "batch",
                                 "source_file": name, "size_bytes": path.stat().st_size, "sha256": file_digest(path)})
        self.manifest = {"schema_version": 1, "source_root": str(self.root), "files": self.records}
        self.manifest["manifest_id"] = digest(self.manifest)

    def test_complete_batch_returns_only_audited_paths(self):
        self.assertEqual(validate_attachment_sources(self.manifest, self.records),
                         [(self.root / r["source_file"]).resolve() for r in self.records])

    def test_partial_batch_is_not_attached(self):
        with self.assertRaisesRegex(BrowserError, "complete audited"):
            validate_attachment_sources(self.manifest, self.records[:1])

    def test_changed_source_is_not_attached(self):
        (self.root / "1.pdf").write_bytes(b"changed")
        with self.assertRaisesRegex(BrowserError, "changed after source audit"):
            validate_attachment_sources(self.manifest, self.records)

    def test_modified_record_is_not_attached(self):
        altered = [dict(r) for r in self.records]
        altered[0]["source_file"] = "ROADWAY-SIGN4.pdf"
        with self.assertRaisesRegex(BrowserError, "differs from the audited"):
            validate_attachment_sources(self.manifest, altered)


if __name__ == "__main__":
    unittest.main()
