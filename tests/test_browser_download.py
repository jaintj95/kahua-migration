import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from kahua_migration.browser import BrowserError
from kahua_migration.browser_download import DownloadServiceError, service_error, wait_for_original_download
from kahua_migration.browser_evidence import download_original


def line(text, y, confidence=1):
    return {"text": text, "x": 625, "y": y, "width": 350, "height": 16, "confidence": confidence}


class DownloadTests(unittest.TestCase):
    def test_only_confident_known_service_error_body_is_actionable(self):
        document = {"lines": [line("Something went wrong. Please try again. (Ref:", 450), line("SVC-895992)", 470)]}
        self.assertEqual(service_error(document), "SVC-895992")
        document["lines"][1]["confidence"] = .5
        self.assertIsNone(service_error(document))
        self.assertEqual(service_error(document, confident=False), "SVC-895992")
        document["lines"][0]["y"] = 100
        self.assertIsNone(service_error(document, confident=False))
        payload = {"lines": [line('{"errorCode":"500","message":"An unexpected error', 443),
            line('occurred. Reference: 400a65bb-a6c9-47fc-aa54-', 463), line('ac4f3bf75571"}', 482)]}
        self.assertEqual(service_error(payload), "HTTP service error 400a65bb-a6c9-47fc-aa54-ac4f3bf75571")
        self.assertIsNone(service_error({"lines": [line("Download finished", 450)]}))

    def test_event_listener_is_live_during_wait_and_always_removed(self):
        inspector = MagicMock()
        received, download = {}, object()
        def bind(event, callback):
            # Playwright associates its wrapper with the callable; a built-in
            # list.append method cannot carry that state.
            callback._fixture_handler = True
            received["callback"] = callback
        inspector.page.on.side_effect = bind
        inspector.page.wait_for_timeout.side_effect = lambda delay: received["callback"](download)
        with patch("kahua_migration.browser_download.inspect_service_error") as error:
            result = wait_for_original_download(inspector, Path("unused"), "ocr", lambda: None)
        self.assertIs(result, download)
        error.assert_not_called()
        inspector.page.remove_listener.assert_called_once_with("download", received["callback"])

    def test_service_error_is_detected_before_long_download_timeout(self):
        inspector = MagicMock()
        with tempfile.TemporaryDirectory() as temp, \
                patch("kahua_migration.browser_download.time.monotonic", side_effect=[0, 0, 4, 4]), \
                patch("kahua_migration.browser_download.inspect_service_error", return_value="SVC-895992"):
            with self.assertRaises(DownloadServiceError):
                wait_for_original_download(inspector, Path(temp), "ocr", lambda: None)
            self.assertTrue((Path(temp) / "download-service-error.json").exists())
        inspector.page.remove_listener.assert_called_once()

    def test_only_service_failure_gets_one_read_only_retry(self):
        for failures in (1, 2):
            with self.subTest(failures=failures), tempfile.TemporaryDirectory() as temp, \
                    patch("kahua_migration.browser_evidence.clear_service_errors") as clear, \
                    patch("kahua_migration.browser_evidence._download_original_once") as read:
                success = {"source_match": True}
                read.side_effect = [DownloadServiceError("SVC-1")] * failures + [success]
                if failures == 1:
                    self.assertEqual(download_original(MagicMock(), {}, [], temp, "ocr"), success)
                else:
                    with self.assertRaisesRegex(BrowserError, "failed twice"):
                        download_original(MagicMock(), {}, [], temp, "ocr")
                self.assertEqual(read.call_count, 2)
                self.assertEqual(clear.call_count, failures)
        with patch("kahua_migration.browser_evidence._download_original_once", side_effect=BrowserError("Hash mismatch")) as read:
            with self.assertRaisesRegex(BrowserError, "Hash mismatch"):
                download_original(MagicMock(), {}, [], "unused", "ocr")
            read.assert_called_once()


if __name__ == "__main__":
    unittest.main()
