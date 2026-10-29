"""Observe original-file downloads and recognize the calibrated service errors."""

import re
import time
from pathlib import Path

from .browser import BrowserError
from .browser_visual import click_text, recognize
from .preflight import atomic_json
from .journal import now


class DownloadServiceError(BrowserError):
    pass


def service_error(document, *, confident=True):
    """Read only known error bodies inside the observed centered popup."""
    lines = [line for line in document["lines"]
             if 610 <= line["x"] and line["x"] + line["width"] <= 995
             and 430 <= line["y"] < 505
             and (not confident or line["confidence"] >= .95)]
    text = " ".join(line["text"] for line in sorted(lines, key=lambda line: (line["y"], line["x"])))
    service = re.findall(r"Something went wrong\. Please try again\. \(Ref:\s*(SVC-[0-9]+)\)", text)
    if len(service) == 1:
        return service[0]
    reference = re.findall(r"Reference:\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-\s*[0-9a-f]{12})", text, re.I)
    if '"errorCode"' in text and "An unexpected error occurred." in text and len(reference) == 1:
        return "HTTP service error " + reference[0].replace(" ", "")
    return None


def inspect_service_error(page, ocr, output):
    output = Path(output)
    document = recognize(page, ocr, output.with_suffix(".png"), bounds=[600, 400, 1000, 625])
    observed = service_error(document)
    if observed or not service_error(document, confident=False):
        return observed
    # An uncertain body can only choose a fresh crop. It cannot authorize an
    # acknowledgement or retry until the fresh recognition is confident.
    document = recognize(page, ocr, output.with_name(output.name + "-body").with_suffix(".png"),
                         bounds=[610, 430, 995, 505])
    observed = service_error(document)
    if observed is None:
        raise BrowserError("A download error is visible but cannot be read confidently")
    return observed


def clear_service_errors(page, ocr, output):
    """Acknowledge at most the two observed error layers, never other dialogs."""
    output = Path(output)
    for index in range(3):
        observed = inspect_service_error(page, ocr, output / f"error-layer-{index}")
        if observed is None:
            if (page.get_by_role("button", name="OK", exact=True).count()
                    or page.get_by_role("button", name="Ok", exact=True).count()):
                raise BrowserError("An unrecognized popup remains after the download error")
            return
        if index == 2:
            raise BrowserError("More than two download error layers remain")
        click_text(page, ocr, output, "Ok", [845, 550, 990, 615], refine=True)
        page.wait_for_timeout(500)


def wait_for_original_download(inspector, output, ocr, activate, *, timeout_seconds=600):
    """Keep an event listener active while checking for an explicit service failure."""
    page, output = inspector.page, Path(output)
    downloads = []
    def receive(download):
        downloads.append(download)
    page.on("download", receive)
    try:
        activate()
        deadline, next_check = time.monotonic() + timeout_seconds, time.monotonic() + 3
        check = 0
        while time.monotonic() < deadline:
            if downloads:
                if len(downloads) != 1:
                    raise BrowserError("More than one download arrived for the requested original")
                return downloads[0]
            page.wait_for_timeout(500)
            if not downloads and time.monotonic() >= next_check:
                inspector._guard()
                check += 1
                failure = inspect_service_error(page, ocr, output / f"download-wait-{check:03}")
                if failure:
                    atomic_json(output / "download-service-error.json", {
                        "observed_at": now(), "error": failure, "operation": "read_original_file"})
                    raise DownloadServiceError(failure)
                next_check = time.monotonic() + 5
        raise BrowserError("The original-file download did not arrive before its timeout")
    finally:
        page.remove_listener("download", receive)
