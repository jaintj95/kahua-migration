"""Read-only calibration audit of the visible Kahua drawing log.

This is deliberately separate from the uploader. It never opens Edit, Save,
Add Files or a workflow command. It reads the semantic grid and recognizes
read-only detail screenshots locally. Large/virtualized logs stop until their
pagination has been calibrated; a visible subset is never called complete.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime, timezone
from math import ceil, floor, isfinite
from pathlib import Path

from .browser import BrowserAdapter, BrowserError
from .browser_visual import recognize
from .contract import METADATA
from .preflight import atomic_json, file_digest


def read_ocr_field(document, label, *, min_confidence=0.95):
    """Associate a visible label with exactly one value on the same line.

    No expected source value is accepted by this function. Missing text, wrapped
    values, low confidence and ambiguous associations all stop the audit.
    """
    width, height = document["image_width"], document["image_height"]
    lines = [line for line in document["lines"]
             if width * .59 <= line["x"] < width * .97
             and height * .34 <= line["y"] < height * .91]
    labels = [line for line in lines if line["text"] == label]
    if len(labels) != 1 or labels[0]["confidence"] < min_confidence:
        raise BrowserError(f"Cannot read an unambiguous screenshot label: {label}")
    key = labels[0]
    center = key["y"] + key["height"] / 2
    values = [line for line in lines
              if line["x"] > key["x"] + key["width"] + 15
              and abs(line["y"] + line["height"] / 2 - center) <= 9]
    if len(values) != 1 or values[0]["confidence"] < min_confidence:
        raise BrowserError(f"Cannot read an unambiguous screenshot value: {label}")
    return values[0]["text"]


def read_ocr_count(document):
    lines = [line for line in document["lines"]
             if line["y"] > document["image_height"] * .9
             and re.fullmatch(r"[0-9,]+ Items?", line["text"])]
    if len(lines) != 1 or lines[0]["confidence"] < .95:
        raise BrowserError("Cannot read the visible folder item count")
    return int(lines[0]["text"].split()[0].replace(",", ""))


def field_line_bounds(document, label):
    """Find a crop for re-reading an exact label and its independently shown value."""
    width, height = document["image_width"], document["image_height"]
    candidates = [line for line in document["lines"] if line["text"] == label
                  and width * .59 <= line["x"] < width * .97
                  and height * .34 <= line["y"] < height * .91]
    if len(candidates) != 1:
        raise BrowserError("The field has no unique label candidate for focused recognition")
    line = candidates[0]
    if not all(isfinite(line[k]) for k in ("x", "y", "width", "height")) or line["height"] <= 0:
        raise BrowserError("The field label has invalid screenshot geometry")
    center = line["y"] + line["height"] / 2
    return [floor(width * .59), max(0, floor(center - 16)), ceil(width * .97), min(height, ceil(center + 16))]


def normalize_ui_date(value):
    try:
        return datetime.strptime(value, "%m/%d/%Y").date().isoformat()
    except ValueError as exc:
        raise BrowserError("Unrecognized date in target readback") from exc


def refresh_record_binding(captured, current_rows):
    """Re-resolve a record after Kahua rebuilds its accessible row label."""
    key = captured["sheet_number"], captured["revision"]
    matches = [r for r in current_rows if (r["sheet_number"], r["revision"]) == key]
    if len(matches) != 1:
        raise BrowserError("The intended sheet/revision is no longer unique in the current grid")
    current = matches[0]
    if any(current.get(field) != captured.get(field)
           for field in ("revision_date", "is_current", "grid_row", "workflow_step")):
        raise BrowserError("The intended record changed while inspecting the folder")
    return current


def compare_readback(report, records):
    """Compare independently observed final metadata with an audited plan."""
    expected = {(r["sheet_number"], r["revision"]): r for r in records}
    actual = {(r["sheet_number"], r["revision"]): r for r in report.get("records", [])}
    problems = []
    if not report.get("complete_visible_folder"):
        problems.append("The target folder readback is incomplete")
    if len(expected) != len(records) or len(actual) != len(report.get("records", [])):
        problems.append("Duplicate sheet/revision identities")
    if set(actual) != set(expected):
        problems.append("Target membership differs from the plan")
    latest = {}
    for row in sorted(records, key=lambda r: r["upload_sequence"]):
        latest[row["sheet_number"]] = row["revision"]
    for key in actual.keys() & expected.keys():
        source, observed = expected[key], actual[key]
        current = latest[key[0]] == key[1]
        checks = {**{field: source[field] for field in METADATA},
                  "original_filename": Path(source["source_file"]).name,
                  "is_current": current, "file_version": 1,
                  "status": "Released" if current else "Superseded", "qa_status": "Accepted"}
        for field, value in checks.items():
            if observed.get(field) != value or (field == "is_current" and type(observed.get(field)) is not bool):
                problems.append(f"{key[0]} revision {key[1]}: {field} differs")
    return {"passed": not problems, "checked_records": len(actual), "issues": problems,
            "scope": "Final metadata, membership, current flags, workflow state and file-version counters",
            "content_hashes_verified_by_this_audit": False}


class ReadOnlyInspector:
    def __init__(self, page, target, output, ocr_executable):
        self.page, self.target = page, target
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.ocr_executable = str(Path(ocr_executable).resolve())
        self.ui = object.__new__(BrowserAdapter)
        self.ui.page, self.ui.timeout, self.ui.context = page, 30000, {}
        if page.viewport_size != {"width": 1600, "height": 1000}:
            raise BrowserError("The calibrated readback requires a 1600 x 1000 browser viewport")
        self.evidence = []

    def _control(self, role, name, *, min_height=20):
        return self.ui._locate({"role": role, "name": name, "min_height": min_height})

    def _guard(self):
        from urllib.parse import urlsplit
        if (self.page.get_by_role("group", name="Login", exact=True).count()
                or self.page.get_by_role("textbox", name="Password", exact=True).count()):
            raise BrowserError("Kahua sign-in is required; unlock this browser session before continuing")
        expected, actual = urlsplit(self.target["environment"]), urlsplit(self.page.url)
        if (expected.scheme, expected.netloc) != (actual.scheme, actual.netloc):
            raise BrowserError("Readback is outside the configured Kahua origin")
        self._control("button", f"Current Project {self.target['project_number']} - {self.target['project_name']}")
        self._control("button", f"Open project descendants for {self.target['domain_name']}")
        for part in self.target["folder_path"].replace("\\", "/").split("/"):
            self._control("button", part)

    def _ocr(self, name):
        if name.startswith("record-"):
            self._detail_top()
        path = self.output / (name + ".png")
        self.page.screenshot(path=str(path))
        run = subprocess.run([self.ocr_executable, str(path)], capture_output=True,
                             text=True, timeout=30, check=False)
        if run.returncode:
            raise BrowserError("Local screenshot text recognition failed")
        document = json.loads(run.stdout)
        atomic_json(self.output / (name + ".ocr.json"), document)
        self.evidence.append({"screenshot": str(path), "sha256": file_digest(path)})
        return document

    def _field(self, document, label, name):
        try:
            return read_ocr_field(document, label)
        except BrowserError:
            bounds = field_line_bounds(document, label)
            self._guard()
            path = self.output / (name + '-' + label.lower().replace(' ', '-') + '-line.png')
            focused = recognize(self.page, self.ocr_executable, path, bounds=bounds)
            # Both label and value must independently meet the original .95
            # confidence and same-line association rules in the fresh crop.
            value = read_ocr_field(focused, label)
            self.evidence.append({"screenshot": str(path), "sha256": file_digest(path)})
            return value

    def _detail_top(self):
        self.page.get_by_role("form", name="Entity Form", exact=True).get_by_role(
            "scrollbar", name="Vertical", exact=True).press("Home")
        self.page.wait_for_timeout(350)

    def _section(self, name, expanded):
        self.ui._steps([{"op": "set_expanded", "locator": {
            "role": "button", "name": name + " Section"}, "value": expanded, "settle_ms": 350}])

    def _close_detail(self):
        controls = self.page.get_by_role("button", name="Close Record Detail", exact=True)
        if controls.count():
            self._control("button", "Close Record Detail").press("Enter")
            controls.wait_for(state="detached", timeout=60000)
            self._wait_folder_layout()

    def _wait_folder_layout(self):
        """A detached detail control can precede the canvas returning to its log."""
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            table = self._control("region", "Record List")
            rect = table.bounding_box()
            if (rect and rect["width"] >= 900
                    and not self.page.get_by_role("button", name="Close Record Detail", exact=True).count()
                    and self._control("button", "Add Files").is_enabled()):
                self.page.wait_for_timeout(500)
                return
            self.page.wait_for_timeout(500)
        raise BrowserError("The full folder layout did not finish restoring")

    def _clear_filter_chips(self):
        # Choosing Default can preserve existing filters when Default is already
        # active. Remove the actual visible chips before claiming full membership.
        chips = self.page.locator('button:has(> button[aria-label="Remove"])')
        for chip in chips.all():
            rect = chip.bounding_box()
            if not rect or not (230 <= rect["y"] <= 270 and rect["height"] >= 20):
                continue
            if not chip.get_attribute("aria-label"):
                raise BrowserError("An active filter chip has no accessible identity")
            from playwright.sync_api import TimeoutError as PlaywrightTimeout
            # Removing this identified chip is idempotent. The first click can
            # land while the canvas is still restoring its old layout; retry
            # only if the same chip still exists, using newly resolved geometry.
            for attempt in range(3):
                self.ui._canvas_click(chip.get_by_role("button", name="Remove", exact=True))
                try:
                    chip.wait_for(state="detached", timeout=5000)
                    break
                except PlaywrightTimeout:
                    if attempt == 2:
                        raise BrowserError("The visible filter chip could not be removed") from None
                    self.page.screenshot(path=str(self.output / f"filter-clear-retry-{attempt + 1}.png"))
            self._wait_folder_layout()

    def _open_record(self, grid):
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        for attempt in range(2):
            self._guard()
            current = refresh_record_binding(grid, self._grid())
            cell = self._control("region", "Record List").locator(
                f'[role="gridcell"][aria-rowindex="{current["grid_row"]}"]'
                f'[aria-colindex="{current["sheet_column"]}"]')
            if cell.count() != 1 or cell.get_attribute("aria-label") != grid["sheet_number"]:
                raise BrowserError("The selected sheet cell differs from the captured grid")
            self.ui._canvas_click(cell, double=True)
            try:
                self.page.get_by_role("tab", name="Details", exact=True).wait_for(
                    state="attached", timeout=5000 if attempt == 0 else 30000)
                return
            except PlaywrightTimeout:
                if attempt or self.page.get_by_role("button", name="Close Record Detail", exact=True).count():
                    raise BrowserError("The selected record detail did not finish opening") from None
                self._wait_folder_layout()

    def _grid(self):
        self._guard()
        table = self._control("region", "Record List")
        data = table.evaluate("""e => ({
            headers:[...e.querySelectorAll('[role=columnheader]')].map(h=>h.getAttribute('aria-label')||''),
            rows:[...e.querySelectorAll('[role=row]')].map(r=>({
                name:r.getAttribute('aria-label'),
                cells:[...r.querySelectorAll('[role=gridcell]')].map(c=>({
                    column:Number(c.getAttribute('aria-colindex')),
                    row:Number(c.getAttribute('aria-rowindex')),
                    value:c.getAttribute('aria-label')||''
                }))
            }))
        })""")
        result, seen = [], set()
        for required in ("Sheet Number", "Rev Number", "Rev Date", "Current Rev"):
            if data["headers"].count(required) != 1:
                raise BrowserError(f"Missing or duplicate grid header: {required}")
        for raw in data["rows"]:
            fields = {}
            for cell in raw["cells"]:
                if 1 <= cell["column"] <= len(data["headers"]):
                    fields[data["headers"][cell["column"] - 1]] = cell["value"]
            for required in ("Sheet Number", "Rev Number", "Rev Date", "Current Rev"):
                if not fields.get(required):
                    raise BrowserError(f"Visible grid is missing {required}; do not infer it from a row label")
            key = (fields["Sheet Number"], fields["Rev Number"])
            if key in seen:
                raise BrowserError("Duplicate sheet/revision in visible grid")
            seen.add(key)
            current = fields["Current Rev"]
            if current not in ("Yes", "No"):
                raise BrowserError("Unknown grid Current Rev value")
            result.append({"sheet_number": key[0], "revision": key[1],
                           "revision_date": normalize_ui_date(fields["Rev Date"]),
                           "is_current": current == "Yes", "grid_name": raw["name"],
                           "grid_row": raw["cells"][0]["row"],
                           "sheet_column": data["headers"].index("Sheet Number") + 1,
                           "workflow_step": fields.get("Workflow Step"),
                           "revision_status": fields.get("Rev Status"),
                           "qa_cell_present": "QA Status" in fields,
                           "qa_cell_value": fields.get("QA Status")})
        return result

    def read(self, *, exported_records=None, metadata_only=False):
        """Audit only already-visible records; writes are not part of this class."""
        if self.page.get_by_role("button", name="Save / Close", exact=True).count():
            raise BrowserError("An editor is open; cancel it before read-only inspection")
        self._close_detail()
        self._control("button", "View Selector").press("Enter")
        self._control("button", "Default").press("Enter")
        self.page.wait_for_timeout(350)
        self._clear_filter_chips()
        self._wait_folder_layout()
        initial = self._grid()
        self._ocr("folder-before")
        exported = None
        if exported_records is not None:
            exported = {(r["sheet_number"], r["revision"]): r for r in exported_records}
            if (len(exported) != len(exported_records)
                    or set(exported) != {(r["sheet_number"], r["revision"]) for r in initial}):
                raise BrowserError("Visible folder membership differs from the independent Kahua export")
        if not initial:
            raise BrowserError("Empty-folder readback needs a separately calibrated count control")
        count = None
        results = []
        for index, grid in enumerate(initial, 1):
            self._open_record(grid)
            self._control("tab", "Details").press("Enter")
            self.page.wait_for_timeout(350)
            # Detail item numbering independently binds the opened form to the
            # clicked row before reading any metadata.
            caption = self.page.get_by_text(re.compile(r"^Item " + str(grid["grid_row"]) + r" of [0-9,]+$"))
            if len(initial) == 1 and exported is not None:
                # Kahua omits the navigation caption for a one-record folder.
                # The fresh independent export supplies membership in that case.
                observed_count = len(exported)
            else:
                caption.wait_for(state="visible", timeout=30000)
                match = re.fullmatch(r"Item ([0-9,]+) of ([0-9,]+)", caption.inner_text())
                if not match:
                    raise BrowserError("Cannot read the detail navigation count")
                observed_count = int(match[2].replace(",", ""))
            if observed_count != len(initial) or count not in (None, observed_count):
                raise BrowserError("The grid is virtualized or changed; pagination needs live calibration")
            count = observed_count
            self._section("Document", True)
            self._section("Revision Details", False)
            self._section("Revision Status", False)
            self._section("Versions", False)
            document = self._ocr(f"record-{index}-document")
            title = self._field(document, "Title", f"record-{index}-document")
            self._section("Document", False)
            self._section("Revision Details", True)
            details = self._ocr(f"record-{index}-revision")
            batch_name = self._field(details, "Batch Name", f"record-{index}-revision")
            batch_date = normalize_ui_date(self._field(details, "Batch Date", f"record-{index}-revision"))
            revision_date = normalize_ui_date(self._field(details, "Rev Date", f"record-{index}-revision"))
            # The detail header exposes the filename as a region name even
            # where OCR assigns low confidence to a short name such as 1.pdf.
            filename_regions = self.page.get_by_role("main", name="File Manager Main Content", exact=True).get_by_role("region")
            if filename_regions.count() != 1:
                raise BrowserError("Cannot identify a unique record filename header")
            filename = filename_regions.get_attribute("aria-label")
            if not filename or not filename.lower().endswith(".pdf"):
                raise BrowserError("The target record has no PDF filename header")
            if revision_date != grid["revision_date"]:
                raise BrowserError("Grid and record detail disagree on revision date")
            self._section("Revision Details", False)
            self._section("Revision Status", True)
            status_doc = self._ocr(f"record-{index}-status")
            # Draft forms omit the Rev Status detail field. The grid exposes
            # Uploaded explicitly, alongside its Draft workflow step.
            status = grid["revision_status"]
            if not metadata_only and not status:
                status = self._field(status_doc, "Rev Status", f"record-{index}-status")
            if grid["qa_cell_present"]:
                # Use the actual rendered grid value when that cell is present.
                # A blank remains blank; an absent/offscreen cell needs OCR.
                # This also avoids recognizing tiny QA labels unnecessarily.
                qa_status = grid["qa_cell_value"]
            elif not metadata_only:
                qa_status = self._field(status_doc, "QA Status", f"record-{index}-status")
            else:
                qa_status = None  # Draft metadata inspection does not infer missing workflow fields.
            self._section("Revision Status", False)
            self._section("Versions", True)
            self._detail_top()
            versions = self.page.get_by_role("grid", name="FileVersionItemsGrid", exact=True)
            names = versions.get_by_role("row").evaluate_all("es=>es.map(e=>e.getAttribute('aria-label')||e.innerText)")
            if len(names) != 1 or versions.get_by_role("scrollbar", name="Vertical", exact=True).count():
                raise BrowserError("Expected exactly one complete file-version row")
            match = re.match(re.escape(filename) + r"\s+([1-9][0-9]*)\s+", names[0] or "")
            if not match:
                raise BrowserError("Cannot bind the file-version row to this record's filename")
            self.page.screenshot(path=str(self.output / f"record-{index}-versions.png"))
            results.append({k: grid[k] for k in ("sheet_number", "revision", "revision_date", "is_current")} | {
                "title": title, "batch_name": batch_name, "batch_date": batch_date,
                "original_filename": filename, "status": status, "qa_status": qa_status,
                "workflow_step": grid["workflow_step"],
                "file_version": int(match[1]), "file_version_row": names[0]})
            if exported is not None:
                export_row = exported[(grid["sheet_number"], grid["revision"])]
                if export_row["title"] != title or export_row["original_filename"] != filename:
                    raise BrowserError("Record detail differs from the independent export")
                results[-1]["target_record_id"] = export_row["target_record_id"]
            self._close_detail()
            atomic_json(self.output / "observed-records.json", results)
        final = self._grid()
        self._ocr("folder-after")
        facts = lambda rows: sorted((r["sheet_number"], r["revision"], r["revision_date"], r["is_current"]) for r in rows)
        if facts(final) != facts(initial):
            raise BrowserError("The folder changed during inspection")
        report = {"evidence_class": "standalone_browser_readback", "target": self.target,
                  "observed_at": datetime.now(timezone.utc).isoformat(), "complete_visible_folder": True,
                  "workflow_status_required": not metadata_only,
                  "records": results, "evidence": self.evidence,
                  "field_sources": {"sheet_number": "semantic_grid", "revision": "semantic_grid",
                                    "revision_date": "semantic_grid_and_local_ocr", "is_current": "semantic_grid",
                                    "original_filename": "semantic_detail_header", "file_version": "semantic_versions_grid",
                                    "title": "local_ocr", "batch_name": "local_ocr", "batch_date": "local_ocr",
                                    "status": "semantic_grid_or_local_ocr", "qa_status": "semantic_grid_or_local_ocr",
                                    "target_record_id": "Kahua_export_when_supplied"},
                  "limitations": ["Read-only calibration, not an upload run", "Large-log pagination unvalidated",
                                  "Content hashes require a separate original-file download"]}
        atomic_json(self.output / "readback-report.json", report)
        return report
