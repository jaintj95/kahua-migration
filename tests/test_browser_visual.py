import unittest
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from kahua_migration.browser import BrowserError
from kahua_migration.browser_visual import text_center, recognize, exact_candidate_bounds, click_text
from kahua_migration.ocr import OcrEngine


class VisualTargetTests(unittest.TestCase):
    def test_uncertain_breadcrumb_can_only_propose_a_new_recognition_crop(self):
        document = {"image_width": 1600, "image_height": 1000, "lines": [
            {"text": "220 - Seattle Heights", "x": 373, "y": 198, "width": 150, "height": 14, "confidence": .5}]}
        bounds = [365, 185, 900, 222]
        self.assertEqual(exact_candidate_bounds(document, "220 - Seattle Heights", bounds), [365, 185, 535, 222])
        with self.assertRaises(BrowserError):
            text_center(document, "220 - Seattle Heights", bounds)
        document["lines"].append(dict(document["lines"][0]))
        with self.assertRaises(BrowserError):
            exact_candidate_bounds(document, "220 - Seattle Heights", bounds)

    def document(self, lines):
        return {"image_width": 1600, "image_height": 1000, "lines": lines}

    def line(self, text="Download", x=1100, y=517, confidence=1):
        return {"text": text, "x": x, "y": y, "width": 70, "height": 16, "confidence": confidence}

    def test_exact_download_cannot_select_send_or_lock_and_download(self):
        doc = self.document([self.line("Send File", y=193), self.line(),
                             self.line("Lock and Download", y=553)])
        self.assertEqual(text_center(doc, "Download"), (1135, 525))

    def test_duplicate_low_confidence_and_out_of_region_targets_stop(self):
        for lines, bounds in [([self.line(), self.line(y=700, confidence=.5)], None),
                              ([self.line(confidence=.5)], None),
                              ([self.line()], [0, 0, 800, 1000])]:
            with self.subTest(lines=lines, bounds=bounds):
                with self.assertRaises(BrowserError):
                    text_center(self.document(lines), "Download", bounds)

    def test_bounds_must_not_escape_viewport(self):
        for bounds in ([0, 0, 1700, 1000], [0, 0, float("nan"), 1000], [0, 100, 100, 50]):
            with self.subTest(bounds=bounds):
                with self.assertRaises(BrowserError):
                    text_center(self.document([self.line()]), "Download", bounds)

    def test_subpixel_edge_rounding_is_allowed_only_at_the_actual_crop_boundary(self):
        doc = self.document([self.line(x=1099.975)])
        doc["recognition_clip"] = {"x": 1100, "y": 500, "width": 150, "height": 60}
        self.assertEqual(text_center(doc, "Download", [1100, 500, 1250, 560]), (1134.975, 525))
        with self.assertRaises(BrowserError):
            text_center(doc, "Download", [1100, 500, 1249, 560])
        doc["lines"][0]["x"] = 1099
        with self.assertRaises(BrowserError):
            text_center(doc, "Download", [1100, 500, 1250, 560])

    def test_crop_tolerance_preserves_confidence_and_duplicate_checks(self):
        for lines in ([self.line(x=1099.975, confidence=.5)],
                      [self.line(x=1099.975), self.line(x=1101)]):
            doc = self.document(lines)
            doc["recognition_clip"] = {"x": 1100, "y": 500, "width": 150, "height": 60}
            with self.assertRaises(BrowserError):
                text_center(doc, "Download", [1100, 500, 1250, 560])

    def test_cropped_menu_coordinates_are_translated_back_to_viewport(self):
        page = Mock(viewport_size={"width": 1600, "height": 1000})
        page.get_by_role.return_value.count.return_value = 0
        result = {"image_width": 170, "image_height": 45,
                  "lines": [self.line(x=20, y=10)]}
        with tempfile.TemporaryDirectory() as directory, patch(
                "kahua_migration.ocr.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout=json.dumps(result).encode("utf-8"))):
            path = Path(directory) / "menu.png"
            document = recognize(page, OcrEngine("external", "ocr"), path, bounds=[1080, 720, 1250, 765])
        self.assertEqual(text_center(document, "Download", [1080, 720, 1250, 765]), (1135, 738))
        self.assertEqual(page.screenshot.call_args.kwargs["clip"],
                         {"x": 1080, "y": 720, "width": 170, "height": 45})
        self.assertEqual(page.screenshot.call_args.kwargs["scale"], "css")

    def test_cropped_recognition_rejects_wrong_pixel_dimensions(self):
        page = Mock(viewport_size={"width": 1600, "height": 1000})
        page.get_by_role.return_value.count.return_value = 0
        with tempfile.TemporaryDirectory() as directory, patch(
                "kahua_migration.ocr.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout=json.dumps(self.document([])).encode("utf-8"))):
            with self.assertRaises(BrowserError):
                recognize(page, OcrEngine("external", "ocr"), Path(directory) / "menu.png", bounds=[1080, 720, 1250, 765])

    def test_unexpected_overlay_never_receives_an_ocr_click(self):
        page = Mock()
        page.evaluate.return_value = {"tag": "DIV", "id": "unexpected"}
        with tempfile.TemporaryDirectory() as directory, patch(
                "kahua_migration.browser_visual.recognize", return_value=self.document([self.line()])):
            with self.assertRaisesRegex(BrowserError, "overlay"):
                click_text(page, "ocr", directory, "Download")
        page.mouse.click.assert_not_called()

    def test_sign_in_is_never_captured_or_sent_to_ocr(self):
        page = Mock(viewport_size={"width": 1600, "height": 1000})
        page.get_by_role.return_value.count.return_value = 1
        with patch("kahua_migration.browser_visual.recognize_image") as engine:
            with self.assertRaisesRegex(BrowserError, "sign-in"):
                recognize(page, "ocr", "unused.png")
        page.screenshot.assert_not_called()
        engine.assert_not_called()


if __name__ == "__main__":
    unittest.main()
