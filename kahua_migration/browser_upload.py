"""Direct browser attachment for one audited version set.

No Finder/Explorer control or middleware upload is involved. File-chooser
handoff is not a successful upload receipt: intents stay pending until saved
Kahua records, original bytes and metadata have been independently read back.
"""

from pathlib import Path
from datetime import date
from math import ceil, floor, isfinite

from .browser import BrowserError, canvas_relative_rect
from .browser_visual import click_text, recognize, text_center
from .browser_dates import read_calendar_at
from .journal import now
from .preflight import atomic_json, file_digest, inside, verify_manifest


def isolated_header_bounds(document, column, columns, bounds):
    """Separate a small header from neighboring labels without lowering OCR confidence."""
    index = columns.index(column)
    if index == 0 or index == len(columns) - 1 or not columns[index - 1]:
        raise BrowserError("The upload header needs two observed neighboring labels")
    adjacent = []
    for label in (columns[index - 1], columns[index + 1]):
        center = text_center(document, label, bounds)
        adjacent.append(next(line for line in document["lines"]
                             if line["text"] == label
                             and (line["x"] + line["width"] / 2,
                                  line["y"] + line["height"] / 2) == center))
    left = ceil(adjacent[0]["x"] + adjacent[0]["width"] + 1)
    right = floor(adjacent[1]["x"] - 1)
    if not bounds[0] <= left < right <= bounds[2] or right - left < 8:
        raise BrowserError("The neighboring upload headers are out of order or overlap")
    return [left, bounds[1], right, bounds[3]]


def candidate_header_bounds(document, column, bounds):
    """Crop a single exact OCR candidate for a new high-confidence reading."""
    lines = [line for line in document["lines"] if line["text"] == column]
    if len(lines) != 1:
        raise BrowserError("The upload header has no unique exact OCR candidate")
    line = lines[0]
    x, y, width, height = (line[k] for k in ("x", "y", "width", "height"))
    if (not all(isfinite(v) for v in (x, y, width, height)) or width <= 0 or height <= 0
            or not bounds[0] <= x < x + width <= bounds[2]
            or not bounds[1] <= y < y + height <= bounds[3]):
        raise BrowserError("The upload header candidate falls outside its header strip")
    return [max(bounds[0], floor(x) - 12), bounds[1], min(bounds[2], ceil(x + width) + 12), bounds[3]]


def bind_upload_cells(headers, cells):
    """Bind observed row cells by visual order, widths and explicit anchors.

    Uno updates different cell positions at different times after scrolling or
    resizing. Prefer a contiguous slice whose explicit indices all agree. When
    cached positions reorder cells, require a unique width match for every
    unindexed cell after binding the explicit columns. Missing cells remain
    unknown; neither fallback uses expected source values.
    """
    if not cells or len(cells) > len(headers) or not headers:
        raise BrowserError("The upload row has missing or extra structural cells")
    anchors = [c for c in cells if c["column"]]
    if len(anchors) < 2 or len({c["column"] for c in anchors}) != len(anchors):
        raise BrowserError("Upload rows need distinct explicit column anchors")
    if any(h["width"] <= 0 for h in headers) or any(a["x"] >= b["x"] for a, b in zip(headers, headers[1:])):
        raise BrowserError("Upload header positions or widths are ambiguous")
    ordered = sorted(cells, key=lambda cell: cell["x"])
    starts = {cell["column"] - index for index, cell in enumerate(ordered) if cell["column"]}
    if len(starts) == 1:
        start = next(iter(starts))
        if (1 <= start and start + len(cells) - 1 <= len(headers)
                and all(a["x"] < b["x"] for a, b in zip(ordered, ordered[1:]))
                and all(abs(cell["width"] - header["width"]) <= 1
                        for header, cell in zip(headers[start - 1:], ordered))):
            return [{"column": start + i, "value": cell["value"]} for i, cell in enumerate(ordered)]
    # Live two-row edits can leave the first three cells at an old X offset
    # while the remaining cells have moved. Explicit indices still identify
    # editable cells. Bind the others only if each remaining width is unique.
    bound = {}
    for cell in anchors:
        column = cell["column"]
        if not 1 <= column <= len(headers) or abs(cell["width"] - headers[column - 1]["width"]) > 1:
            raise BrowserError("An explicit upload column has an unexpected width")
        bound[column] = cell["value"]
    for cell in (c for c in cells if not c["column"]):
        candidates = [i for i, h in enumerate(headers, start=1)
                      if i not in bound and abs(cell["width"] - h["width"]) <= 1]
        if len(candidates) != 1:
            raise BrowserError("An unindexed upload cell cannot be bound to one unique column width")
        bound[candidates[0]] = cell["value"]
    return [{"column": column, "value": value} for column, value in sorted(bound.items())]


def upload_date_points(layout, column, width):
    """Calibrated date-editor points anchored to the actual visible header."""
    inset = {"- Rev Date:": 50, "Batch Date": 34}.get(column)
    if inset is None or not 134 <= width <= 180:
        raise BrowserError("The upload date-column cannot fit the calibrated editor")
    # The existing-sheet form redistributed five pixels from Batch Date to
    # other populated columns. Bind to its observed right edge, not one width.
    calendar_x = layout["header_left"] + width - inset
    year_x = calendar_x - 28
    left, right = max(350, layout["header_left"] - 8), min(1551, layout["header_left"] - 8 + width)
    if not left < year_x < calendar_x < right:
        raise BrowserError("The date field is not fully accessible in the upload table")
    return (year_x, layout["y"]), (calendar_x, layout["y"])


def bulk_revision_date_points(y, editor_widths):
    """Use the two observed bulk date-editor widths; their positions can lag."""
    if (len(editor_widths) != 2 or len(set(editor_widths)) != 1
            or not 130 <= editor_widths[0] <= 165 or not 450 <= y <= 700):
        raise BrowserError("The bulk date editor layout changed")
    calendar_x = 523 + editor_widths[0] - 20
    return (calendar_x, y), (calendar_x - 32, y)


def focused_bulk_date_points(geometry, viewport, label_y):
    """Bind the date button focused by Tab after editing this bulk field."""
    if geometry.get("tag") != "BUTTON" or geometry.get("label") != "Date Picker":
        raise BrowserError("Bulk date entry did not focus its calendar button")
    rect = canvas_relative_rect(geometry["rect"], geometry["application"], geometry["canvas"], viewport)
    x, y = rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2
    if (rect["width"] != 24 or rect["height"] != 24 or not 625 <= x <= 670
            or not 450 <= y <= 700 or abs(y - label_y) > 8):
        raise BrowserError("The focused calendar is not bound to the visible bulk revision date")
    return (x, y), (x - 32, y)


def bind_upload_row_names(rows, previous, expected_names):
    """Keep observed filename identity on the same row while it is offscreen."""
    keys = [(r["row_id"], r["index"]) for r in rows]
    if any(not key[0] for key in keys) or len(set(key[0] for key in keys)) != len(keys):
        raise BrowserError("Upload rows do not have distinct live element identities")
    missing = any(r["File Name"] is None for r in rows)
    if missing and set(keys) != set(previous):
        raise BrowserError("Offscreen filenames cannot be bound after upload rows changed")
    result = []
    for row, key in zip(rows, keys):
        name = row["File Name"]
        if missing:
            if name is not None and name != previous[key]:
                raise BrowserError("An upload row changed identity while another filename was offscreen")
            name = previous[key]
        result.append({**row, "File Name": name, "filename_evidence":
                       "same_observed_row_element" if row["File Name"] is None else "live_filename_cell"})
    if any(r["File Name"] is None for r in result) or sorted(r["File Name"] for r in result) != sorted(expected_names):
        raise BrowserError("The upload grid filenames differ from the audited batch")
    return result, {key: row["File Name"] for key, row in zip(keys, result)}


def validate_attachment_sources(manifest, records, *, allow_partial=False):
    verify_manifest(manifest)
    if (not records or len({r["project_uid"] for r in records}) != 1
            or len({r["version_set_uid"] for r in records}) != 1
            or len({r["operation_key"] for r in records}) != len(records)):
        raise BrowserError("Attach exactly one nonempty, distinct version set")
    declared = {r["operation_key"]: r for r in manifest["files"]}
    project_uid, batch_uid = records[0]["project_uid"], records[0]["version_set_uid"]
    complete_batch = {r["operation_key"] for r in manifest["files"]
                      if r["project_uid"] == project_uid and r["version_set_uid"] == batch_uid}
    if not allow_partial and {r["operation_key"] for r in records} != complete_batch:
        raise BrowserError("Attach the complete audited version set, not a subset")
    paths = []
    for record in records:
        if declared.get(record["operation_key"]) != record:
            raise BrowserError("An attachment differs from the audited manifest")
        path = inside(Path(manifest["source_root"]), record["source_file"])
        if path.stat().st_size != record["size_bytes"] or file_digest(path) != record["sha256"]:
            raise BrowserError("An attachment changed after source audit")
        paths.append(path)
    return paths


class BatchAttachment:
    def __init__(self, inspector, manifest, records, journal, output, ocr_executable, *, allow_partial=False, membership_snapshot=None):
        self.inspector, self.page, self.manifest = inspector, inspector.page, manifest
        self.records, self.journal = records, journal
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.ocr = str(Path(ocr_executable).resolve())
        self.allow_partial = allow_partial
        self.membership_snapshot = membership_snapshot

    def attach(self, expected_existing):
        sources = validate_attachment_sources(self.manifest, self.records, allow_partial=self.allow_partial)
        project = next(p for p in self.manifest["projects"] if p["project_uid"] == self.records[0]["project_uid"])
        if project["target"] != self.inspector.target:
            raise BrowserError("Attachment destination differs from the manifest")
        self.inspector._guard()
        self.inspector._wait_folder_layout()
        if self.page.evaluate("typeof window.showOpenFilePicker") != "undefined":
            raise BrowserError("Relaunch with the HTML upload fallback before attaching files")
        if self.membership_snapshot is None:
            existing = self.inspector.all_grid()
        else:
            from .browser_export import read_revision_export
            snapshot = self.membership_snapshot
            workbook = Path(snapshot["workbook"])
            if (not self.allow_partial or snapshot["target"] != self.inspector.target
                    or file_digest(workbook) != snapshot["workbook_sha256"]):
                raise BrowserError("Attachment membership evidence changed or belongs to another destination")
            existing = read_revision_export(workbook)
            if existing != snapshot["records"] or len(existing) != snapshot["count"]:
                raise BrowserError("Attachment membership does not match its independent workbook")
            self.inspector.unfilter()
            if self.inspector.count() != len(existing):
                raise BrowserError("Unfiltered membership count changed before attachment")
        key = lambda r: (r["sheet_number"], r["revision"])
        if len(existing) != len(expected_existing) or {key(r) for r in existing} != {key(r) for r in expected_existing}:
            raise BrowserError("Target membership changed before attachment")
        if {key(r) for r in existing} & {key(r) for r in self.records}:
            raise BrowserError("A planned revision already exists; reconcile instead of uploading again")
        for record in self.records:
            status = self.journal.status(record["operation_key"])
            if status["verified_stage"] is not None or status["pending_stage"] is not None:
                raise BrowserError("The upload journal has an existing outcome or unresolved intent")
        for previous in expected_existing:
            state = self.journal.status(previous["operation_key"])
            same_batch = self.allow_partial and previous["version_set_uid"] == self.records[0]["version_set_uid"]
            required = "metadata_verified" if same_batch else "verified"
            if state["verified_stage"] != required or state["pending_stage"]:
                raise BrowserError("Finish and verify the previous batch before uploading another")
        atomic_json(self.output / "attachment-intent.json", {
            "observed_at": now(), "target": project["target"],
            "records": self.records, "existing_membership": existing})
        for record in self.records:
            self.journal.begin(record["operation_key"], "uploaded")
        with self.page.expect_file_chooser(timeout=30000) as event:
            click_text(self.page, self.ocr, self.output, "Add Files", [520, 135, 625, 185])
        event.value.set_files([str(path) for path in sources])
        atomic_json(self.output / "attachment-handed-to-browser.json", {
            "observed_at": now(), "file_count": len(sources),
            "operation_keys": [r["operation_key"] for r in self.records],
            "saved_upload_verified": False})
        return len(sources)

    def continue_attachment(self):
        """Advance the selected-file page once its real Continue action is ready."""
        from playwright.sync_api import expect
        self.inspector._guard()
        execute = self.inspector._control("button", "Execute")
        expect(execute).to_be_enabled(timeout=120000)
        execute.press("Enter")
        self.page.wait_for_timeout(500)
        self.set_batch_metadata()

    def set_batch_metadata(self):
        """Fill the observed first metadata page and verify its visible date.

        The canvas input is offset from its semantic rectangle in this tenant.
        Visible label anchors bind the calibrated points before typing; the
        actual focused input and screenshot date are read back before Continue.
        """
        self.inspector._guard()
        self.inspector._control("group", "Add Files")
        batches = {(r["batch_name"], r["batch_date"]) for r in self.records}
        if len(batches) != 1:
            raise BrowserError("Files in a version set must share batch metadata")
        name, iso = next(iter(batches))
        value = date.fromisoformat(iso)
        document = recognize(self.page, self.ocr, self.output / "initial-metadata-form.png",
                             bounds=[610, 510, 840, 605])
        text_center(document, "Batch Name", [600, 515, 850, 555])
        text_center(document, "Batch Date", [600, 560, 850, 600])
        self.page.mouse.click(1015, 537)
        focused = self.page.locator(":focus").element_handle()
        if (not focused or focused.evaluate("e=>e.tagName") != "INPUT"
                or focused.get_attribute("aria-label") == "Working Folder Name"):
            raise BrowserError("Batch Name did not focus the intended input")
        self.page.keyboard.press("ControlOrMeta+A")
        self.page.keyboard.insert_text(name)
        self.page.keyboard.press("Tab")
        if focused.input_value() != name:
            raise BrowserError("Batch Name readback differs from the audited value")
        for x, part in ((875, f"{value.month:02}"), (905, f"{value.day:02}"), (944, str(value.year))):
            self.page.mouse.click(x, 578)
            self.page.keyboard.type(part)
        self.page.keyboard.press("Tab")
        actual = read_calendar_at(self.page, self.output / "initial-metadata-date", (976, 578), (944, 578))
        if actual != iso:
            raise BrowserError("Selected Batch Date differs before Continue")
        if focused.input_value() != name:
            raise BrowserError("Batch Name changed while editing the date")
        atomic_json(self.output / "initial-metadata-verified.json", {
            "observed_at": now(), "batch_name": focused.input_value(), "batch_date": actual})
        self.inspector._control("button", "Execute").press("Enter")
        self.page.get_by_role("grid", name="fileGrid", exact=True).wait_for(state="attached", timeout=60000)


class UploadGrid:
    """Bind visible upload rows to source filenames before editing their inputs."""

    COLUMNS = ("", "Add / Update", "File Name", "Number", "Title", "Last Rev Number",
               "Last Revision Date", "Rev Number", "- Rev Date:", "Batch Name", "Batch Date")

    def __init__(self, inspector, records, output, ocr_executable):
        self.inspector, self.page, self.records = inspector, inspector.page, records
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.ocr = str(Path(ocr_executable).resolve())
        names = [Path(r["source_file"]).name for r in records]
        if len(set(names)) != len(names):
            raise BrowserError("Upload rows require unique source filenames within this batch")
        self._row_bindings = {}
        self._table_identity = None

    def rows(self):
        self.inspector._guard()
        table = self.page.get_by_role("grid", name="fileGrid", exact=True)
        data = table.evaluate("""e=>{
            const headers=[...e.querySelectorAll('[role=columnheader]')];
            return {table_id:e.id,headers:headers.map(h=>({name:h.getAttribute('aria-label')||'',
                    x:h.getBoundingClientRect().x,width:h.getBoundingClientRect().width})),
                rows:[...e.querySelectorAll('[role=row]')].map((r,i)=>({row_id:r.id,index:i,height:r.getBoundingClientRect().height,
                    cells:[...r.children].map(c=>({
                        column:Number(c.getAttribute('aria-colindex')) || 0,
                        x:c.getBoundingClientRect().x,width:c.getBoundingClientRect().width,
                        value:c.getAttribute('aria-label')||c.innerText||''}))}))};
        }""")
        if tuple(h["name"] for h in data["headers"]) != self.COLUMNS:
            raise BrowserError("The upload grid's columns changed")
        if len(data["rows"]) != len(self.records):
            raise BrowserError("The full upload batch is not represented by the visible grid")
        if (not data["table_id"] or self._table_identity is not None
                and data["table_id"] != self._table_identity):
            raise BrowserError("The upload table changed during metadata preparation")
        self._table_identity = data["table_id"]
        rows = []
        for raw in data["rows"]:
            row = {name: None for name in self.COLUMNS}  # Offscreen cells remain unknown.
            seen = set()
            for cell in bind_upload_cells(data["headers"], raw["cells"]):
                column = cell["column"]
                if not 1 <= column <= len(self.COLUMNS) or column in seen:
                    raise BrowserError("Upload cells cannot be bound to unique columns")
                seen.add(column)
                row[self.COLUMNS[column - 1]] = cell["value"]
            rows.append({**row, "row_id": raw["row_id"], "index": raw["index"], "height": raw["height"]})
        rows, self._row_bindings = bind_upload_row_names(
            rows, self._row_bindings, [Path(r["source_file"]).name for r in self.records])
        return rows

    def validate_initial(self):
        by_name = {Path(r["source_file"]).name: r for r in self.records}
        for row in self.rows():
            record = by_name[row["File Name"]]
            revision = int(record["revision"])
            if row["Batch Name"] != record["batch_name"]:
                raise BrowserError("A file has the wrong batch name before editing")
            if revision > 1:
                if (row["Add / Update"] != "Revise" or row["Number"] != record["sheet_number"]
                        or row["Title"] != record["title"] or row["Last Rev Number"] != str(revision - 1)):
                    raise BrowserError("Kahua's existing-sheet match differs from the audited revision history")
            elif row["Add / Update"] != "Add" or row["Last Rev Number"]:
                raise BrowserError("A planned new sheet unexpectedly matches existing history")
        return self.rows()

    def _point(self, filename, column):
        layout = self._cell_layout(filename, column)
        return layout["x"], layout["y"]

    def _cell_layout(self, filename, column):
        if column not in self.COLUMNS:
            raise BrowserError("Unknown upload field")
        row = next(r for r in self.rows() if r["File Name"] == filename)
        document = recognize(self.page, self.ocr, self.output / "grid-layout.png", bounds=[350, 250, 1550, 700])
        _, title_y = text_center(document, "Add Files", [350, 250, 700, 650])
        strip = [350, int(title_y + 24), 1550, int(title_y + 66)]
        header_source = document
        try:
            x, header_y = text_center(document, column, strip)
        except BrowserError:
            header = recognize(self.page, self.ocr, self.output / "grid-headers.png", bounds=strip)
            header_source = header
            try:
                x, header_y = text_center(header, column, strip)
            except BrowserError:
                try:
                    # A small exact label can be low-confidence in a wide crop.
                    # Its candidate box is only a region to re-read, never an
                    # actionable target until the same .95 threshold passes.
                    candidate = candidate_header_bounds(header, column, strip)
                    detail = recognize(self.page, self.ocr, self.output / "candidate-header.png", bounds=candidate)
                    x, header_y = text_center(detail, column, candidate)
                except BrowserError:
                    # Merged labels need a crop between independently observed
                    # neighbors, followed by the same exact confident reading.
                    isolated = isolated_header_bounds(header, column, self.COLUMNS, strip)
                    detail = recognize(self.page, self.ocr, self.output / "isolated-header.png", bounds=isolated)
                    x, header_y = text_center(detail, column, isolated)
                header_source = detail
        if row["height"] != 40:
            raise BrowserError("The calibrated upload row height changed")
        y = header_y + (row["index"] + 1) * row["height"]
        _, bulk_y = text_center(document, "Set Revision Number", [350, 250, 900, 700])
        if y >= bulk_y - 25:
            raise BrowserError("The target upload row is outside the visible table")
        line = next(line for line in header_source["lines"]
                    if line["text"] == column
                    and (line["x"] + line["width"] / 2,
                         line["y"] + line["height"] / 2) == (x, header_y))
        return {"x": x, "y": y, "header_left": line["x"]}

    def _scroll(self, end=False):
        table = self.page.get_by_role("grid", name="fileGrid", exact=True)
        scroll = table.get_by_role("scrollbar", name="Horizontal", exact=True)
        if scroll.count():
            heights = table.get_by_role("row").evaluate_all("es=>es.map(e=>e.getBoundingClientRect().height)")
            if len(heights) != len(self.records) or any(height != 40 for height in heights):
                raise BrowserError("The calibrated upload row height changed")
            document = recognize(self.page, self.ocr, self.output / "scroll-layout.png",
                                 bounds=[350, 250, 1550, 700])
            _, title_y = text_center(document, "Add Files", [350, 250, 700, 650])
            _, bulk_y = text_center(document, "Set Revision Number", [350, 250, 900, 700])
            row_y = title_y + 83
            if not 300 < row_y < bulk_y - 25:
                raise BrowserError("Cannot bind the upload table's horizontal scroll area")
            # Home/End on Uno's semantic scrollbar can move DOM focus without
            # scrolling the canvas. A horizontal wheel over the observed row
            # scrolls the actual table and does not steal keyboard focus.
            self.page.mouse.move(1000, row_y)
            self.page.mouse.wheel(5000 if end else -5000, 0)
            self.page.wait_for_timeout(500)

    def read_text(self, filename, column):
        # Some fields expose an actual input inside the explicitly indexed
        # cell. Read that directly instead of changing focus just to read it.
        row = next(r for r in self.rows() if r["File Name"] == filename)
        table = self.page.get_by_role("grid", name="fileGrid", exact=True)
        cell = table.get_by_role("row").nth(row["index"]).locator(
            f'[role="gridcell"][aria-colindex="{self.COLUMNS.index(column) + 1}"]')
        native = cell.get_by_role("textbox")
        if native.count() > 1:
            raise BrowserError("The upload cell contains ambiguous text inputs")
        if native.count() == 1:
            return native.input_value()
        control = self.input(filename, column)
        result = control.input_value()
        self.page.keyboard.press("Tab")
        return result

    def read_date(self, filename, column):
        """Read the calendar selected day using the visually bound date cell."""
        if column not in ("- Rev Date:", "Batch Date"):
            raise BrowserError("Unsupported upload date column")
        self._scroll(end=True)
        layout = self._cell_layout(filename, column)
        width = self.page.get_by_role("grid", name="fileGrid", exact=True).get_by_role(
            "columnheader", name=column, exact=True).bounding_box()["width"]
        year, _ = upload_date_points(layout, column, width)
        # The first click can select the row; the second activates its editor.
        # Neither click types a value or changes the selected date.
        for _ in range(2):
            self.page.mouse.click(*year)
            self.page.wait_for_timeout(250)
        # Activation can move the table horizontally. Re-read the visible
        # header before opening its icon. The detached semantic calendar button
        # retains stale coordinates and keyboard focus in this tenant.
        layout = self._cell_layout(filename, column)
        year, calendar = upload_date_points(layout, column, width)
        actual = read_calendar_at(
            self.page, self.output / (filename + "." + column.replace(" ", "-") + ".calendar"), calendar, year)
        self.rows()
        return actual

    def input(self, filename, column):
        for attempt in range(2):
            x, y = self._point(filename, column)
            self.page.mouse.click(x, y, click_count=2 if attempt else 1)
            self.page.wait_for_timeout(350)
            control = self.page.locator(":focus").element_handle()
            if control and control.evaluate("e=>e.tagName") == "INPUT":
                return control
            # The first click can focus the grid after horizontal scrolling.
            # Resolve the exact filename/column again before a focus-only retry.
            if not attempt:
                self.page.wait_for_timeout(500)
        raise BrowserError(f"The {filename} / {column} cell did not focus an input")

    def set_text(self, filename, column, value, *, expected_before=""):
        control = self.input(filename, column)
        if control.input_value() != expected_before:
            raise BrowserError("Upload cell does not contain the expected pre-edit value")
        self.page.keyboard.press("ControlOrMeta+A")
        self.page.keyboard.insert_text(value)
        self.page.keyboard.press("Tab")
        if control.input_value() != value:
            raise BrowserError("Upload input readback differs after editing")
        atomic_json(self.output / (filename + "." + column.replace(" ", "-") + ".json"), {
            "observed_at": now(), "filename": filename, "field": column, "input_readback": control.input_value()})
        return value

    def apply_auto_increment(self):
        self.validate_initial()
        document = recognize(self.page, self.ocr, self.output / "auto-increment-before.png", bounds=[350, 450, 1050, 700])
        _, y = text_center(document, "Auto-Increment Revision Number", [350, 450, 690, 700])
        click_text(self.page, self.ocr, self.output, "Apply", [690, int(y - 20), 790, int(y + 20)])
        self.page.wait_for_timeout(500)
        values = {}
        for record in self.records:
            name = Path(record["source_file"]).name
            value = self.input(name, "Rev Number").input_value()
            expected = record["revision"] if int(record["revision"]) > 1 else ""
            if value != expected:
                raise BrowserError("Kahua Auto-Increment differs from the planned sheet revision")
            values[name] = value
            self.page.keyboard.press("Tab")
        atomic_json(self.output / "auto-increment-verified.json", {"observed_at": now(), "values": values})
        return values

    def set_common_revision_date(self):
        dates = {r["revision_date"] for r in self.records}
        if len(dates) != 1:
            raise BrowserError("This bulk date action requires one revision date for the entire batch")
        iso = next(iter(dates))
        value = date.fromisoformat(iso)
        document = recognize(self.page, self.ocr, self.output / "bulk-date-layout.png", bounds=[350, 450, 1050, 700])
        _, y = text_center(document, "Set Revision Date", [350, 450, 690, 700])
        for x, part in ((539, f"{value.month:02}"), (569, f"{value.day:02}"), (608, str(value.year))):
            self.page.mouse.click(x, y)
            self.page.keyboard.type(part)
        self.page.keyboard.press("Tab")
        geometry = self.page.locator(":focus").evaluate("""e=>({tag:e.tagName,label:e.getAttribute('aria-label'),
            rect:e.getBoundingClientRect().toJSON(),
            application:e.closest('[role=application]')?.getBoundingClientRect().toJSON(),
            canvas:document.querySelector('canvas#uno-canvas')?.getBoundingClientRect().toJSON()})""")
        calendar, closing = focused_bulk_date_points(geometry, self.page.viewport_size, y)
        actual = read_calendar_at(self.page, self.output / "bulk-revision-date-calendar", calendar, closing)
        if actual != iso:
            raise BrowserError("Bulk revision date differs from the plan")
        self.rows()  # The upload form must remain open after the calendar closes.
        click_text(self.page, self.ocr, self.output, "Apply", [690, int(y - 20), 790, int(y + 20)])
        self.page.wait_for_timeout(500)
        return actual

    def prepare(self):
        """Fill an already bound batch, then read every final value before Save.

        This only supports the fully visible upload table. A larger or changed
        layout raises before the final Save and leaves its journal intent open.
        """
        self._scroll()
        self.validate_initial()
        if any(int(r["revision"]) > 1 for r in self.records):
            self.apply_auto_increment()
        for record in self.records:
            if int(record["revision"]) == 1:
                name = Path(record["source_file"]).name
                self._scroll()
                for column, field in (("Number", "sheet_number"), ("Title", "title"), ("Rev Number", "revision")):
                    if self.read_text(name, column) != record[field]:
                        # An exact existing value needs no edit. Any unexpected
                        # nonblank value still fails the set_text blank guard.
                        self.set_text(name, column, record[field])
        self.set_common_revision_date()
        return self.verify()

    def verify(self):
        """Read a populated unsaved form without repeating its data-entry steps."""
        self._scroll()
        self.validate_initial()
        observed = []
        for record in self.records:
            name = Path(record["source_file"]).name
            self._scroll()
            row = next(r for r in self.rows() if r["File Name"] == name)
            title = row["Title"] if int(record["revision"]) > 1 else self.read_text(name, "Title")
            value = {"sheet_number": self.read_text(name, "Number"), "title": title,
                     "revision": self.read_text(name, "Rev Number")}
            self._scroll(end=True)
            value["batch_name"] = self.read_text(name, "Batch Name")
            value["revision_date"] = self.read_date(name, "- Rev Date:")
            value["batch_date"] = self.read_date(name, "Batch Date")
            if any(value[field] != record[field] for field in value):
                raise BrowserError("Final upload metadata differs from the audited source; Save is blocked")
            observed.append({**value, "original_filename": name})
        self._scroll()
        self.rows()
        path = self.output / "precommit.json"
        atomic_json(path, {"observed_at": now(), "target": self.inspector.target,
                          "records": observed, "commit_attempted": False})
        self.page.screenshot(path=str(path.with_suffix(".png")))
        return path

    def commit(self, manifest, journal, precommit_path, *, allow_partial=False):
        import json
        validate_attachment_sources(manifest, self.records, allow_partial=allow_partial)
        proof = json.loads(Path(precommit_path).read_text(encoding="utf-8"))
        expected = [{**{field: r[field] for field in ("sheet_number", "title", "revision", "batch_name", "revision_date", "batch_date")},
                     "original_filename": Path(r["source_file"]).name} for r in self.records]
        if proof.get("target") != self.inspector.target or proof.get("records") != expected:
            raise BrowserError("Final Save requires this exact batch's precommit evidence")
        self.rows()
        self.inspector._guard()
        for record in self.records:
            journal.mark_upload_commit(record["operation_key"], {
                "observed_at": now(), "evidence_path": str(Path(precommit_path).resolve())})
        self.inspector._control("button", "Execute").press("Enter")
        self.page.get_by_role("grid", name="fileGrid", exact=True).wait_for(state="detached", timeout=120000)
