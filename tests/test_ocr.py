import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from kahua_migration.browser import BrowserError
from kahua_migration.browser_readback import ReadOnlyInspector
from kahua_migration.browser_visual import text_center
from kahua_migration.ocr import OcrEngine, parse_tesseract_tsv, resolve_ocr, validate_document


DOCUMENT = dict(image_width=400, image_height=200, lines=[
    dict(text="Download", confidence=.963, x=10, y=20, width=90, height=18)])
HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
PAGE = "1\t1\t0\t0\t0\t0\t0\t0\t800\t400\t-1\t\n"


def word(text, confidence=96.3, x=20, block=1, number=1, width=100):
    return f"5\t1\t{block}\t1\t1\t{number}\t{x}\t40\t{width}\t36\t{confidence}\t{text}\n"


class OcrContractTests(unittest.TestCase):
    def test_json_helper_contract_and_original_confidence_are_preserved(self):
        with patch("kahua_migration.ocr.subprocess.run", return_value=SimpleNamespace(
                returncode=0, stdout=json.dumps(DOCUMENT).encode("utf-8"))) as run:
            result = OcrEngine("external", r"C:\Program Files\OCR\helper.exe").recognize("a path/é image.png")
        self.assertEqual(result, DOCUMENT)
        self.assertEqual(run.call_args.args[0], [r"C:\Program Files\OCR\helper.exe", str(Path("a path/é image.png").resolve())])
        self.assertNotIn("encoding", run.call_args.kwargs)
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(run.call_args.kwargs["timeout"], 30)

    def test_real_subprocess_handles_utf8_and_space_in_image_path_without_shell(self):
        with tempfile.TemporaryDirectory(prefix="OCR space ") as directory:
            script = Path(directory) / "é $literal image.py"
            doc = copy.deepcopy(DOCUMENT)
            doc["lines"][0]["text"] = "Révision 日本語"
            script.write_text("import sys\nsys.stdout.buffer.write(" + repr(json.dumps(doc, ensure_ascii=False).encode("utf-8")) + ")", encoding="utf-8")
            result = OcrEngine("external", sys.executable).recognize(script)
        self.assertEqual(result, doc)

    def test_missing_bad_and_failed_helpers_raise_browser_error_without_raw_output(self):
        for error in (FileNotFoundError(), PermissionError(), subprocess.TimeoutExpired("ocr", 30)):
            with self.subTest(error=type(error)), patch("kahua_migration.ocr.subprocess.run", side_effect=error):
                with self.assertRaises(BrowserError):
                    OcrEngine("external", "ocr").recognize("image.png")
        for code, output in ((1, "private screenshot text"), (0, "not json"), (0, "[]")):
            with self.subTest(code=code, output=output), patch("kahua_migration.ocr.subprocess.run",
                    return_value=SimpleNamespace(returncode=code, stdout=output.encode("utf-8"), stderr=b"private screenshot text")):
                with self.assertRaises(BrowserError) as caught:
                    OcrEngine("external", "ocr").recognize("image.png")
                self.assertNotIn("private screenshot text", str(caught.exception))
        with patch("kahua_migration.ocr.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=b"\xff")):
            with self.assertRaisesRegex(BrowserError, "UTF-8"):
                OcrEngine("external", "ocr").recognize("image.png")

    def test_invalid_confidence_dimensions_and_geometry_fail_closed(self):
        mutations = [("confidence", value) for value in (-.1, 1.01, float("nan"), float("inf"), "1", True)]
        mutations += [("x", float("nan")), ("x", -1), ("width", 0), ("height", -1), ("width", 500), ("text", None)]
        for field, value in mutations:
            doc = copy.deepcopy(DOCUMENT)
            doc["lines"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(BrowserError):
                validate_document(doc)
        for value in (0, -1, True, "400", 400.5, float("inf")):
            with self.subTest(width=value), self.assertRaises(BrowserError):
                validate_document({**DOCUMENT, "image_width": value})

    def test_tesseract_groups_words_scales_geometry_and_uses_minimum_confidence(self):
        doc = parse_tesseract_tsv(HEADER + PAGE + word("Add", 98) + word("Files", 91, x=130, number=2), 400, 200)
        self.assertEqual(doc["lines"], [dict(text="Add Files", confidence=.91, x=10, y=20, width=105, height=18)])
        with self.assertRaises(BrowserError):
            text_center(doc, "Add Files")

    def test_tesseract_preserves_duplicate_low_confidence_and_column_boundaries(self):
        doc = parse_tesseract_tsv(HEADER + PAGE + word("Download") + word("Download", 20, x=300, block=2), 400, 200)
        with self.assertRaises(BrowserError):
            text_center(doc, "Download")
        doc = parse_tesseract_tsv(HEADER + PAGE + word("Title") + word("Roadway", x=300, number=2), 400, 200)
        self.assertEqual([line["text"] for line in doc["lines"]], ["Title", "Roadway"])

    def test_tesseract_rejects_invalid_tsv_and_mismatched_scaled_image(self):
        for content in ("", "not tsv", HEADER, HEADER + PAGE.replace("800", "1600"),
                        HEADER + PAGE + word("Bad", -1), HEADER + PAGE + word("Bad", 101),
                        HEADER + PAGE + word("Bad", "nan"), HEADER + PAGE + word("Bad", x=-20),
                        HEADER + PAGE + word("Bad").replace("5\t1", "5\t2"),
                        HEADER + PAGE + word("Bad").replace("\t96.3", "\tnot-a-number")):
            with self.subTest(content=content), self.assertRaises(BrowserError):
                parse_tesseract_tsv(content, 400, 200)
        self.assertEqual(parse_tesseract_tsv(HEADER + PAGE, 400, 200)["lines"], [])

    def test_readback_uses_shared_recognition_with_dimension_and_sign_in_guards(self):
        page = Mock(viewport_size={"width": 1600, "height": 1000})
        page.get_by_role.return_value.count.return_value = 0
        engine = OcrEngine("external", "ocr")
        with tempfile.TemporaryDirectory() as directory:
            inspector = ReadOnlyInspector(page, {}, directory, engine)
            with patch("kahua_migration.browser_readback.recognize", return_value=DOCUMENT) as recognize, \
                    patch("kahua_migration.browser_readback.file_digest", return_value="hash"):
                self.assertEqual(inspector._ocr("folder"), DOCUMENT)
                recognize.assert_called_once_with(page, engine, Path(directory).resolve() / "folder.png")
            with patch("kahua_migration.ocr.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=json.dumps(DOCUMENT).encode("utf-8"))):
                with self.assertRaisesRegex(BrowserError, "viewport"):
                    inspector._ocr("folder")
            page.get_by_role.return_value.count.return_value = 1
            page.screenshot.reset_mock()
            with self.assertRaisesRegex(BrowserError, "sign-in"):
                inspector._ocr("folder")
            page.screenshot.assert_not_called()


class OcrResolutionTests(unittest.TestCase):
    def test_path_name_and_space_in_executable_are_resolved_without_splitting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "OCR space.exe"
            path.touch()
            path.chmod(0o700)
            self.assertEqual(resolve_ocr(executable=path).executable, str(path.resolve()))
            with patch("kahua_migration.ocr.shutil.which", return_value=str(path)) as which:
                self.assertEqual(resolve_ocr(executable="ocr-on-path").executable, str(path.resolve()))
                which.assert_called_once_with("ocr-on-path")

    def test_auto_defaults_and_legacy_override(self):
        with patch("kahua_migration.ocr._executable", side_effect=os.fspath):
            with patch("kahua_migration.ocr.sys.platform", "darwin"):
                self.assertEqual(resolve_ocr(), OcrEngine("vision", "output/tools/ocr-screenshot"))
            with patch("kahua_migration.ocr.sys.platform", "win32"), \
                    patch("kahua_migration.ocr.shutil.which", return_value="C:/Program Files/Tesseract-OCR/tesseract.exe"):
                self.assertEqual(resolve_ocr().backend, "tesseract")
                self.assertEqual(resolve_ocr(executable="custom-json.exe").backend, "external")
                self.assertEqual(resolve_ocr("tesseract", "custom-tesseract.exe").backend, "tesseract")
                with self.assertRaisesRegex(BrowserError, "macOS"):
                    resolve_ocr("vision")

    def test_missing_executable_and_shell_scripts_are_rejected(self):
        with patch("kahua_migration.ocr.shutil.which", return_value=None):
            with self.assertRaisesRegex(BrowserError, "not found"):
                resolve_ocr(executable="missing-ocr-helper")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "helper.cmd"
            path.touch()
            with self.assertRaisesRegex(BrowserError, "native executable"):
                resolve_ocr(executable=path)

    def test_tesseract_checks_version_and_english_data_before_execution(self):
        engine = OcrEngine("tesseract", "tesseract")
        for outputs in (["tesseract 4.0", "eng\n"], ["tesseract 5.5.0", "osd\n"]):
            with patch("kahua_migration.ocr._pillow"), patch("kahua_migration.ocr._run", side_effect=outputs):
                with self.assertRaises(BrowserError):
                    engine.check()
        for version in ("tesseract 5.5.0", "tesseract v5.4.0.20240606\r\n leptonica-1.84.1\r\n"):
            with self.subTest(version=version), patch("kahua_migration.ocr._pillow"), \
                    patch("kahua_migration.ocr._run", side_effect=[version, "List:\r\neng\r\nosd\r\n"]):
                engine.check()


if __name__ == "__main__":
    unittest.main()
