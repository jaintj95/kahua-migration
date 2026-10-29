"""Exact record filters avoid Kahua's recycled cells in virtualized drawing logs.

The full XLSX export and independent visible count establish folder membership.
Each record is then filtered by exact batch and sheet, read and hashed through
the ordinary UI. No target field is copied from the expected source values.
"""

from pathlib import Path
import time

from .browser import BrowserError, canvas_relative_rect
from .browser_evidence import identity
from .browser_export import read_revision_export
from .browser_grid import GridWalker
from .browser_readback import ReadOnlyInspector, read_ocr_count
from .browser_visual import recognize
from .browser_workflow import BatchWorkflow, open_toolbar_action
from .journal import now
from .preflight import atomic_json, file_digest


class RecordFilterInspector(ReadOnlyInspector):
    def _grid(self):
        try:
            return super()._grid()
        except BrowserError:
            table = self._control("region", "Record List")
            header = table.get_by_role("columnheader", name="Sheet Number", exact=True)
            data = header.evaluate("""e=>({rect:e.getBoundingClientRect().toJSON(),
                application:e.closest('[role=application]')?.getBoundingClientRect().toJSON(),
                canvas:document.querySelector('canvas#uno-canvas')?.getBoundingClientRect().toJSON()})""")
            rect = canvas_relative_rect(data["rect"], data["application"], data["canvas"], self.page.viewport_size)
            walker = GridWalker(self.page, table, self._guard)
            walker.wheel(rect["x"] - walker.rect()["x"] - 60, horizontal=True)
            return super()._grid()

    def count(self):
        self._guard()
        table = self._control("region", "Record List")
        if not table.get_by_role("scrollbar", name="Vertical", exact=True).count():
            layout = table.evaluate("""e=>{const t=e.getBoundingClientRect();return {
                height:t.height,rows:[...e.querySelectorAll('[role=row]')].map(r=>{
                    const b=r.getBoundingClientRect();return {id:r.id,top:b.top-t.top,bottom:b.bottom-t.top,height:b.height};
                })};}""")
            if all(r["top"] >= 35 and r["bottom"] <= layout["height"] and r["height"] == 40 for r in layout["rows"]):
                # A small grid with no vertical scrollbar and every complete
                # row inside its viewport gives an independent exact UI count.
                count = len(layout["rows"])
                atomic_json(self.output / "count-proof.json", {"count": count, "source": "complete_unscrolled_UI_grid", "layout": layout})
                return count
        return read_ocr_count(recognize(self.page, self.ocr_executable, self.output / "visible-count.png",
                                       bounds=[630, 930, 1590, 965]))

    def unfilter(self):
        self._guard()
        self._close_detail()
        self._control("button", "View Selector").press("Enter")
        self._control("button", "Default").press("Enter")
        self._clear_filter_chips()
        self._wait_folder_layout()
        if self._control("textbox", "Search").input_value():
            raise BrowserError("Clear the drawing search before complete export")

    def filter_record(self, record):
        self.unfilter()
        self._control("button", "Filter").press("Enter")
        for section_name, value in (("Batch Name", record["batch_name"]), ("Sheet Number", record["sheet_number"])):
            # Keep only this section expanded so equal text in another facet
            # cannot select a different checkbox.
            sections = self.page.locator('[aria-expanded="true"][aria-label$=" Section"]').all()
            for section in sections:
                label = section.get_attribute("aria-label") or ""
                if label.endswith(" Section") and section.get_attribute("aria-expanded") == "true":
                    section.press("Enter")
            section = self.page.get_by_role("button", name=section_name + " Section", exact=True)
            section.wait_for(state="attached", timeout=30000)
            if section.get_attribute("aria-expanded") != "false":
                raise BrowserError("Filter section does not expose its collapsed state")
            section.press("Enter")
            from playwright.sync_api import expect
            expect(section).to_have_attribute("aria-expanded", "true")
            box = self.page.get_by_role("checkbox", name=value, exact=True)
            box.wait_for(state="attached", timeout=30000)
            if box.count() != 1:
                raise BrowserError("The exact filter value is ambiguous")
            if not box.is_checked():
                box.press("Space")
            expect(box).to_be_checked()
        self.page.keyboard.press("Escape")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                self._wait_folder_layout()
                rows = self._grid()
            except BrowserError:
                rows = []
            if len(rows) == 1 and identity(rows[0]) == identity(record):
                if self.count() != 1:
                    raise BrowserError("Exact record filter has more than one UI result")
                atomic_json(self.output / "record-filter.json", {"observed_at": now(), "target": self.target,
                            "batch_filter": record["batch_name"], "sheet_filter": record["sheet_number"], "observed_grid": rows})
                return rows
            self.page.wait_for_timeout(500)
        raise BrowserError("Exact batch/sheet filters did not produce the one requested revision")


def export_complete_folder(inspector, output, ocr_executable):
    """Count the unfiltered log, export all revisions, and recheck its count."""
    page, output = inspector.page, Path(output)
    output.mkdir(parents=True, exist_ok=True)
    inspector.unfilter()
    before_count = inspector.count()
    open_toolbar_action(inspector, output, ocr_executable, "Export")
    lookups = page.get_by_role("checkbox", name="Include Lookups", exact=True)
    lookups.wait_for(state="attached", timeout=30000)
    if lookups.is_checked():
        lookups.press("Space")
    if lookups.is_checked():
        raise BrowserError("Cannot disable unrelated lookup exports")
    with page.expect_download(timeout=120000) as event:
        inspector._control("button", "Ok").press("Enter")
    path = output / "membership.xlsx"
    event.value.save_as(str(path))
    lookups.wait_for(state="detached", timeout=30000)
    inspector._wait_folder_layout()
    exported = read_revision_export(path)
    after_count = inspector.count()
    if before_count != len(exported) or after_count != before_count:
        raise BrowserError("Unfiltered folder count and complete revision export differ")
    snapshot = {"observed_at": now(), "target": inspector.target, "records": exported,
                "count": before_count, "workbook": str(path.resolve()), "workbook_sha256": file_digest(path),
                "membership_source": "full_Kahua_export_and_independent_unfiltered_count"}
    atomic_json(output / "membership.json", snapshot)
    inspector.membership_snapshot = snapshot
    return exported


class RecordWorkflow(BatchWorkflow):
    def filter_batch(self):
        if len(self.records) != 1:
            raise BrowserError("The exact-filter workflow processes one revision at a time")
        self.inspector.filter_record(self.records[0])
        return self.select_visible()
