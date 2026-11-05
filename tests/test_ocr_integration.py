"""Opt-in real local OCR on generated pixels; no Kahua or browser session.

KAHUA_OCR_TESTS=1 selects this suite. KAHUA_TEST_OCR_BACKEND and
KAHUA_TEST_OCR_EXECUTABLE select the engine (same defaults as the CLI).
"""

import io
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import Mock, patch

from kahua_migration.browser import BrowserError
from kahua_migration.browser_readback import read_ocr_field
from kahua_migration.browser_visual import recognize, text_center
from kahua_migration.ocr import resolve_ocr


@unittest.skipUnless(os.environ.get("KAHUA_OCR_TESTS") == "1", "Set KAHUA_OCR_TESTS=1 for real local OCR")
class RealOcrTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PIL import ImageFont
        cls.engine = resolve_ocr(os.environ.get("KAHUA_TEST_OCR_BACKEND", "auto"),
                                 os.environ.get("KAHUA_TEST_OCR_EXECUTABLE"))
        cls.engine.check()
        fonts = [Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
                 Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/arial.ttf",
                 Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
        font = next((p for p in fonts if p.is_file()), None)
        if font is None:
            raise AssertionError("Install Arial or DejaVuSans to render the synthetic fixture")
        cls.font = ImageFont.truetype(str(font), 22)

    def setUp(self):
        from PIL import Image, ImageDraw
        self.temp = tempfile.TemporaryDirectory(prefix="Kahua OCR é space ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = Image.new("RGB", (1600, 1000), "white")
        self.draw = ImageDraw.Draw(self.image)

    def page(self):
        page = Mock(viewport_size={"width": 1600, "height": 1000})
        page.get_by_role.return_value.count.return_value = 0
        def screenshot(path, scale, clip=None):
            self.assertEqual(scale, "css")
            image = self.image
            if clip:
                image = image.crop((clip["x"], clip["y"], clip["x"] + clip["width"], clip["y"] + clip["height"]))
            image.save(path)
        page.screenshot.side_effect = screenshot
        return page

    def test_original_pixel_coordinates_and_focused_crop(self):
        self.draw.text((1100, 517), "Download", font=self.font, fill="black")
        for bounds in (None, [1080, 500, 1300, 570]):
            with self.subTest(bounds=bounds):
                doc = recognize(self.page(), self.engine, self.root / "menu é.png", bounds=bounds)
                x, y = text_center(doc, "Download", bounds)
                self.assertTrue(1100 < x < 1220 and 517 < y < 550, (x, y))
                self.assertEqual((doc["image_width"], doc["image_height"]), (1600, 1000))

    def test_tesseract_uses_binary_stdin_for_unicode_paths(self):
        if self.engine.backend != "tesseract":
            self.skipTest("Tesseract-specific native pipe contract")
        from PIL import Image
        self.draw.text((1100, 517), "Download", font=self.font, fill="black")
        with patch("kahua_migration.ocr.subprocess.run", wraps=subprocess.run) as run:
            doc = recognize(self.page(), self.engine, self.root / "drawing 日本語.png")
        self.assertEqual(run.call_args.args[0][1:3], ["stdin", "stdout"])
        self.assertIsInstance(run.call_args.kwargs["input"], bytes)
        with Image.open(io.BytesIO(run.call_args.kwargs["input"])) as scaled:
            self.assertEqual(scaled.size, (3200, 2000))
        text_center(doc, "Download")

    def test_actual_duplicate_labels_remain_ambiguous(self):
        for y in (400, 600):
            self.draw.text((1100, y), "Download", font=self.font, fill="black")
        doc = recognize(self.page(), self.engine, self.root / "duplicate.png")
        self.assertEqual(sum(line["text"] == "Download" for line in doc["lines"]), 2)
        with self.assertRaises(BrowserError):
            text_center(doc, "Download")

    def test_independent_field_label_and_value_survive_tsv_segmentation(self):
        self.draw.text((1000, 420), "Batch Name", font=self.font, fill="black")
        self.draw.text((1270, 420), "Sample Revision", font=self.font, fill="black")
        doc = recognize(self.page(), self.engine, self.root / "readback.png", bounds=[950, 405, 1550, 465])
        self.assertEqual(read_ocr_field(doc, "Batch Name"), "Sample Revision")


if __name__ == "__main__":
    unittest.main()
