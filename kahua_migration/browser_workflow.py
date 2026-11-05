"""Batch workflow controls calibrated in the DEVPRODUCTION pilot.

The caller supplies an already filtered batch with independently verified
metadata and content hashes. Review rows are compared before Execute. Journal
intents precede every mutation; ambiguous outcomes are left pending for readback.
This module does not navigate projects, attach files, or enter credentials.
"""

from pathlib import Path
import time

from .browser import BrowserError
from .browser_visual import click_text, recognize, text_center
from .journal import now
from .preflight import atomic_json

RELEASE_PROMPT = "Select 'Continue' to complete the process of releasing the selected Document Revision(s) as 'Current'."

def verify_review_rows(rows, records, stage):
    """Bind every selected revision and file version to the reviewed batch."""
    if stage not in ("submitted", "accepted"):
        raise BrowserError("Unsupported review stage")
    expected = [[r["sheet_number"], r["revision"], "1", r["title"]]
                + ([r["batch_name"]] if stage == "submitted" else []) for r in records]
    if (not expected or len({(r[0], r[1]) for r in expected}) != len(expected)
            or len(rows) != len(expected) or sorted(rows) != sorted(expected)):
        raise BrowserError("Workflow review contains missing, extra, duplicate, or mismatched revisions")
    return rows


def open_toolbar_action(inspector, output, ocr_executable, action):
    """Activate one observed toolbar/menu command after guarding the folder."""
    if action not in ("Submit", "Accept", "Release", "Export"):
        raise BrowserError("Unsupported calibrated toolbar action")
    page = inspector.page
    inspector._guard()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if page.viewport_size != {"width": 1600, "height": 1000}:
        raise BrowserError("Workflow layout requires a 1600 x 1000 viewport")
    if page.get_by_role("button", name="Close Record Detail", exact=True).count():
        raise BrowserError("Close the record detail before a batch action")
    anchor = recognize(page, ocr_executable, output / (action.lower() + "-toolbar.png"),
                       bounds=[300, 135, 950, 180])
    try:
        text_center(anchor, "Add Files", [520, 135, 625, 180])
    except BrowserError:
        # Vision can reduce this blue button's confidence in the full toolbar
        # crop. Isolate the observed button and keep the same exact-text and
        # confidence requirement before allowing any command.
        isolated = recognize(page, ocr_executable, output / (action.lower() + "-anchor.png"),
                             bounds=[520, 135, 625, 180])
        text_center(isolated, "Add Files", [520, 135, 625, 180])
    if action == "Export":
        try:
            text_center(anchor, "Export", [630, 135, 710, 180])
        except BrowserError:
            pass  # Export moves into More when records are selected.
        else:
            inspector._control("button", "Export").press("Enter")
            return
    hit = page.evaluate("""() => {
        const e=document.elementFromPoint(1567,159);
        return e ? {tag:e.tagName,id:e.id} : null;
    }""")
    if hit != {"tag": "CANVAS", "id": "uno-canvas"}:
        raise BrowserError("Unexpected overlay above the toolbar")
    page.mouse.click(1567, 159)
    page.get_by_role("menuitem", name=action, exact=True).wait_for(state="attached", timeout=30000)
    click_text(page, ocr_executable, output, action, [1440, 180, 1585, 625])


class BatchWorkflow:
    def __init__(self, inspector, journal, records, observed, output, ocr_executable):
        self.inspector, self.page, self.journal = inspector, inspector.page, journal
        self.records = records
        self.observed = {(r["sheet_number"], r["revision"]): r for r in observed}
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.ocr = ocr_executable
        keys = {(r["sheet_number"], r["revision"]) for r in records}
        if (not records or len(keys) != len(records)
                or len({r["batch_name"] for r in records}) != 1
                or not keys <= self.observed.keys()
                or not all(self.observed[k].get("target_record_id") for k in keys)):
            raise BrowserError("Workflow requires one exact batch with independently observed record IDs")

    def filter_batch(self):
        self.inspector._close_detail()
        self.inspector._clear_filter_chips()
        self.inspector._wait_folder_layout()
        self.inspector._control("button", "Filter").press("Enter")
        section = self.page.get_by_role("button", name="Batch Name Section", exact=True)
        section.wait_for(state="attached", timeout=30000)
        expanded = section.get_attribute("aria-expanded")
        if expanded not in ("true", "false"):
            raise BrowserError("The batch filter has no explicit expansion state")
        if expanded == "false":
            # Exclude the expansion chevrons, which Vision can merge into the
            # labels (for example, "• Batch Name"). Keep exact confident text.
            click_text(self.page, self.ocr, self.output, "Batch Name", [1190, 270, 1448, 625])
        box = self.page.get_by_role("checkbox", name=self.records[0]["batch_name"], exact=True)
        box.wait_for(state="attached", timeout=30000)
        if not box.is_checked():
            box.press("Space")
        if not box.is_checked():
            raise BrowserError("The intended batch filter was not selected")
        self.page.keyboard.press("Escape")
        self.wait_for_membership()
        self.inspector._wait_folder_layout()
        return self.select_visible()

    def wait_for_membership(self, *, timeout=60):
        expected = {(r["sheet_number"], r["revision"]) for r in self.records}
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                grid = self.inspector._grid()
                if len(grid) == len(expected) and {(r["sheet_number"], r["revision"]) for r in grid} == expected:
                    return grid
            except BrowserError:
                pass  # A partially repainted grid is not evidence of membership.
            self.page.wait_for_timeout(500)
        raise BrowserError("The visible batch did not reach the intended membership")

    def select_visible(self):
        grid = self.wait_for_membership()
        for row in grid:
            cell, box = self._selection_control(row)
            if not box.is_checked():
                # Kahua gives every child checkbox the first row's Y position.
                # The parent gridcell preserves each actual row rectangle.
                self.inspector.ui._canvas_click(cell)
                from playwright.sync_api import expect
                expect(box).to_be_checked(timeout=30000)
        return self._selected()

    def _selection_control(self, row):
        table = self.inspector._control("region", "Record List")
        cell = table.locator(f'[role="gridcell"][aria-colindex="1"][aria-rowindex="{row["grid_row"]}"]')
        if cell.count() != 1:
            raise BrowserError("Cannot bind a selection cell to its visible row")
        boxes = self.page.get_by_role("checkbox", name="Select row", exact=True)
        # Uno attaches these inputs beside the grid, not inside its cells.
        # Require the complete filtered view and bind their observed DOM order.
        if boxes.count() != len(self.records) or not 1 <= row["grid_row"] <= len(self.records):
            raise BrowserError("Cannot bind the complete checkbox list to the filtered rows")
        rect = cell.bounding_box()
        if not rect or rect["height"] != 40 or not 20 <= rect["width"] <= 60:
            raise BrowserError("The calibrated row selection layout changed")
        return cell, boxes.nth(row["grid_row"] - 1)

    def _selected(self):
        self.inspector._guard()
        grid = self.inspector._grid()
        expected = {(r["sheet_number"], r["revision"]) for r in self.records}
        if len(grid) != len(expected) or {(r["sheet_number"], r["revision"]) for r in grid} != expected:
            raise BrowserError("Filtered workflow membership differs from the intended batch")
        if not all(self._selection_control(row)[1].is_checked() for row in grid):
            raise BrowserError("Select exactly the visible batch before invoking workflow")
        return grid

    def _menu(self, action):
        open_toolbar_action(self.inspector, self.output, self.ocr, action)

    def _review_rows(self):
        table = self.page.get_by_role("grid", name="DataGrid", exact=True)
        from playwright.sync_api import expect
        expect(table.get_by_role("row")).to_have_count(len(self.records), timeout=60000)
        return table.get_by_role("row").evaluate_all("""es=>es.map(e=>
            [...e.children].map(c=>c.getAttribute('aria-label')).filter(Boolean))""")

    def review(self, stage):
        """Open Submit/Accept review after persisting intents; do not execute yet."""
        prior = {"submitted": "metadata_verified", "accepted": "submitted"}.get(stage)
        if prior is None:
            raise BrowserError("Unsupported workflow review")
        self._selected()
        for record in self.records:
            status = self.journal.status(record["operation_key"])
            if status["pending_stage"] or status["verified_stage"] != prior:
                raise BrowserError("Reconcile existing workflow intents before advancing")
        for record in self.records:
            self.journal.begin(record["operation_key"], stage)
        self._menu({"submitted": "Submit", "accepted": "Accept"}[stage])
        table = self.page.get_by_role("grid", name="DataGrid", exact=True)
        table.wait_for(state="attached", timeout=60000)
        self.page.get_by_role("checkbox", name="Ignore Warnings", exact=True).wait_for(state="attached", timeout=30000)
        from playwright.sync_api import expect
        expect(self.inspector._control("button", "Execute")).to_be_enabled(timeout=30000)
        rows = self._review_rows()
        verify_review_rows(rows, self.records, stage)
        if self.page.get_by_role("checkbox", name="Ignore Warnings", exact=True).is_checked():
            raise BrowserError("Workflow review is configured to ignore warnings")
        self.page.screenshot(path=str(self.output / (stage + "-review.png")))
        atomic_json(self.output / (stage + "-review.json"), {"observed_at": now(), "rows": rows,
                    "target": self.inspector.target, "stage": stage, "ignore_warnings": False})
        return rows

    def execute_review(self, stage):
        """Recheck the live review, execute once, and save the actual confirmation."""
        if stage not in ("submitted", "accepted"):
            raise BrowserError("Unsupported workflow review")
        for record in self.records:
            if self.journal.status(record["operation_key"])["pending_stage"] != stage:
                raise BrowserError("No matching workflow intent")
        rows = self._review_rows()
        verify_review_rows(rows, self.records, stage)
        if self.page.get_by_role("checkbox", name="Ignore Warnings", exact=True).is_checked():
            raise BrowserError("Refusing a workflow with ignored warnings")
        self.inspector._guard()
        from playwright.sync_api import expect
        expect(self.inspector._control("button", "Execute")).to_be_enabled(timeout=30000)
        self.inspector._control("button", "Execute").press("Enter")
        verb = {"submitted": "Submitted", "accepted": "Accepted"}[stage]
        message = f"{len(self.records)} Documents have been {verb}"
        self.page.get_by_text(message, exact=True).wait_for(state="attached", timeout=60000)
        path = self.output / (stage + "-confirmed.json")
        self.page.screenshot(path=str(path.with_suffix(".png")))
        atomic_json(path, {"observed_at": now(), "target": self.inspector.target,
                          "review_rows": rows, "confirmation": message})
        for record in self.records:
            observed = self.observed[(record["sheet_number"], record["revision"])]
            self.journal.confirm(record["operation_key"], stage, {
                "target_record_id": observed["target_record_id"], "observed_at": now(), "evidence_path": str(path)})
        return message

    def acknowledge(self):
        self.inspector._control("button", "OK").press("Enter")
        self.inspector._wait_folder_layout()

    def begin_release(self):
        selected = self._selected()
        for record in self.records:
            status = self.journal.status(record["operation_key"])
            if status["pending_stage"] or status["verified_stage"] != "accepted":
                raise BrowserError("Every revision must be accepted before release")
        atomic_json(self.output / "released-selection.json", {
            "observed_at": now(), "target": self.inspector.target, "grid": selected})
        for record in self.records:
            self.journal.begin(record["operation_key"], "released")
        self._menu("Release")
        self.page.get_by_text(RELEASE_PROMPT, exact=True).wait_for(state="attached", timeout=60000)
        from playwright.sync_api import expect
        expect(self.inspector._control("button", "Continue")).to_be_enabled(timeout=30000)
        self.page.screenshot(path=str(self.output / "released-review.png"))

    def execute_release(self):
        for record in self.records:
            if self.journal.status(record["operation_key"])["pending_stage"] != "released":
                raise BrowserError("No matching release intent")
        self._selected()
        self.page.get_by_text(RELEASE_PROMPT, exact=True).wait_for(state="attached", timeout=30000)
        self.inspector._control("button", "Continue").press("Enter")
        return self.observe_release()

    def observe_release(self):
        """Observe a pending release without repeating it after a timeout.

        Kahua omits Results-cell text and the completion footer from its semantic
        tree. Bind the actual result filenames, then wait for the selected
        revisions to be current/complete. Full detail readback must still confirm
        Released and Accepted before the caller confirms the journal stage.
        """
        for record in self.records:
            if self.journal.status(record["operation_key"])["pending_stage"] != "released":
                raise BrowserError("No matching pending release to observe")
        table = self.page.get_by_role("grid", name="DataGrid", exact=True)
        table.get_by_role("columnheader", name="Results", exact=True).wait_for(state="attached", timeout=60000)
        from playwright.sync_api import expect
        expect(table.get_by_role("row")).to_have_count(len(self.records), timeout=60000)
        names = table.get_by_role("row").evaluate_all(
            "es=>es.map(e=>[...e.children].map(c=>c.getAttribute('aria-label')).filter(Boolean))")
        if sorted(names) != sorted([[Path(r["source_file"]).name] for r in self.records]):
            raise BrowserError("Release result filenames differ from the intended batch")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            grid = self._selected()
            if all(r["is_current"] and r["workflow_step"] == "Complete" for r in grid):
                self.page.screenshot(path=str(self.output / "released-result.png"))
                result = {"observed_at": now(), "target": self.inspector.target,
                          "result_filenames": names, "grid": grid, "full_detail_audit_required": True}
                atomic_json(self.output / "released-result.json", result)
                return result
            self.page.wait_for_timeout(500)
        raise BrowserError("The released batch did not become current/complete")

    def close_release_result(self):
        self.observe_release()
        click_text(self.page, self.ocr, self.output, "Close", [1420, 860, 1540, 910])
        self.inspector._wait_folder_layout()

