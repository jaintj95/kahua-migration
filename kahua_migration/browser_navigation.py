"""Find or provision exact CSV destinations using the visible Kahua interface."""

from pathlib import Path
from urllib.parse import urlsplit
import time

from .browser import BrowserAdapter, BrowserError, canvas_relative_rect
from .browser_grid import GridWalker, read_table_rows
from .browser_visual import click_text, recognize, text_center
from .csv_config import validate_destination
from .preflight import atomic_json, digest
from .journal import now


class AuthenticationRequired(BrowserError):
    pass


def column(row, number):
    values = [c["value"] for c in row["cells"] if c["column"] == number]
    if len(values) != 1:
        raise BrowserError("A directory row is missing its identity column")
    return values[0]


def match_project(rows, number, name):
    matches = [r for r in rows if column(r, 2) == number and column(r, 3) == name]
    if len(matches) > 1:
        raise BrowserError("More than one project has the exact requested number and name")
    return matches[0] if matches else None


def match_folder(rows, name):
    # Cell children are recycled independently from rows in large folders.
    # Bind the parent row label, then verify visible text before clicking and
    # the actual breadcrumb after opening. Never trust a stale child name.
    matches = [r for r in rows if r["name"] == name]
    if len(matches) > 1:
        raise BrowserError("More than one child folder has the requested name")
    return matches[0] if matches else None


def folder_row_key(row):
    return row["name"]


class DestinationNavigator:
    def __init__(self, page, journal, output, ocr_executable):
        self.page, self.journal = page, journal
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.ocr = str(Path(ocr_executable).resolve())
        self.ui = object.__new__(BrowserAdapter)
        self.ui.page, self.ui.timeout, self.ui.context = page, 30000, {}
        self.target, self.sequence = None, 0

    def control(self, role, name):
        return self.ui._locate({"role": role, "name": name, "min_height": 20})

    def guard(self, *, project=False):
        if (self.page.get_by_role("group", name="Login", exact=True).count()
                or self.page.get_by_role("textbox", name="Password", exact=True).count()):
            raise AuthenticationRequired("Sign in to the migration browser; destination navigation is automatic")
        expected, actual = urlsplit(self.target["environment"]), urlsplit(self.page.url)
        if (expected.scheme, expected.netloc) != (actual.scheme, actual.netloc):
            raise BrowserError("Navigation left the configured Kahua origin")
        if self.page.viewport_size != {"width": 1600, "height": 1000}:
            raise BrowserError("Automatic navigation requires the calibrated 1600 x 1000 viewport")
        self.control("button", "Open project descendants for " + self.target["domain_name"])
        if project:
            self.control("button", f"Current Project {self.target['project_number']} - {self.target['project_name']}")

    def evidence(self, label, facts):
        self.guard()
        self.sequence += 1
        path = self.output / f"{self.sequence:03}-{label}.json"
        self.page.screenshot(path=str(path.with_suffix(".png")))
        atomic_json(path, {"observed_at": now(), **facts})
        return str(path)

    def wait(self, predicate, message, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.guard()
            if predicate():
                return
            self.page.wait_for_timeout(300)
        raise BrowserError(message)

    def enter(self, locator, value, *, name=None, point=None):
        if point is None:
            self.ui._canvas_click(locator)
        else:
            self.page.mouse.click(*point)
        focused = self.page.locator(":focus").element_handle()
        if not focused or focused.evaluate("e=>e.tagName") not in ("INPUT", "TEXTAREA"):
            raise BrowserError("Navigation field did not focus a native text input")
        if name is not None and focused.get_attribute("aria-label") != name:
            raise BrowserError("Navigation focused a different input")
        self.page.keyboard.press("ControlOrMeta+A")
        self.page.keyboard.insert_text(value)
        self.page.keyboard.press("Tab")
        if focused.input_value() != value:
            raise BrowserError("Navigation field readback differs from the CSV")
        return focused

    def find_project(self):
        self.guard()
        self.control("option", "Open Project Finder").press("Enter")
        self.page.get_by_role("grid", name="Partition Items", exact=True).wait_for(state="attached", timeout=60000)
        # Re-entering Finder can retain its previous search.
        back = self.page.get_by_role("button", name="Back", exact=True)
        if back.count():
            self.control("button", "Back").press("Enter")
        self.control("button", "Search").press("Enter")
        field = self.control("textbox", "")
        focused = self.enter(field, self.target["project_name"])
        field.press("Enter")
        table = self.control("grid", "Partition Items")
        # Search results replace the entire grid. Require a stable result and
        # the unchanged input, then traverse every explicit result row.
        previous, stable = None, 0
        for _ in range(30):
            self.guard()
            rows = read_table_rows(table)
            signature = [(r["grid_row"], r["name"], r["cells"]) for r in rows]
            stable = stable + 1 if signature == previous else 0
            if stable >= 4:
                break
            previous = signature
            self.page.wait_for_timeout(500)
        else:
            raise BrowserError("Project search did not stabilize")
        if focused.input_value() != self.target["project_name"]:
            raise BrowserError("Project search changed unexpectedly")
        walker = GridWalker(self.page, table, self.guard)
        rows = walker.scan_to_end(lambda: read_table_rows(table), lambda r: (column(r, 2), column(r, 3)))
        self.evidence("project-search", {"query": self.target["project_name"], "rows": rows})
        row = match_project(rows, self.target["project_number"], self.target["project_name"])
        if row:
            fresh = walker.seek(lambda: read_table_rows(table), lambda r: (column(r, 2), column(r, 3)),
                                (self.target["project_number"], self.target["project_name"]),
                                row.get("absolute_row", row["grid_row"]))
            cell = table.locator(f'[role="gridcell"][aria-rowindex="{fresh["grid_row"]}"][aria-colindex="3"]')
            self.ui._canvas_click(cell, double=True)
            self.wait(lambda: self.page.get_by_role("button", name=f"Current Project {self.target['project_number']} - {self.target['project_name']}", exact=True).count() == 1,
                      "The requested project did not open")
        return row

    def create_project(self, key, spec):
        from playwright.sync_api import expect
        self.guard()
        self.control("button", "NEW").press("Enter")
        next_button = self.control("button", "Next")
        expect(next_button).to_be_enabled(timeout=60000)
        # The first page displays the actual parent. Only the configured domain
        # root is supported; never inherit a previously selected project parent.
        click_text(self.page, self.ocr, self.output, self.target["domain_name"], [480, 140, 1145, 180])
        next_button.press("Enter")
        blank = self.page.get_by_role("checkbox", name="Create Blank Project", exact=True)
        blank.wait_for(state="attached", timeout=60000)
        if not blank.is_checked():
            blank.press("Space")
        expect(blank).to_be_checked()
        expect(self.control("button", "Next")).to_be_enabled(timeout=60000)
        self.control("button", "Next").press("Enter")
        self.page.get_by_role("textbox", name="Name", exact=True).wait_for(state="attached", timeout=60000)
        self.control("textbox", "Name")
        doc = recognize(self.page, self.ocr, self.output / "project-fields.png", bounds=[470, 470, 1110, 710])
        for label in ("Record Type", "Name", "Number"):
            text_center(doc, label)
        self.page.mouse.click(1075, 512)
        self.page.get_by_role("option", name=self.target["record_type"], exact=True).wait_for(state="attached", timeout=10000)
        self.control("option", self.target["record_type"]).press("Enter")
        name = self.enter(self.control("textbox", "Name"), self.target["project_name"], name="Name", point=(730, 590))
        number = self.enter(self.control("textbox", "Number"), self.target["project_number"], name="Number", point=(920, 667))
        # Uno exposes an empty input behind this selected-value control.
        # Independently read the displayed value in its own field before Save.
        selected_type = recognize(self.page, self.ocr, self.output / "project-record-type.png",
                                  bounds=[455, 498, 1095, 530])
        text_center(selected_type, self.target["record_type"])
        path = self.evidence("project-precommit", {"specification": spec, "name": name.input_value(), "number": number.input_value(), "record_type": self.target["record_type"]})
        save = self.control("button", "Save")
        self.journal.begin_destination(key, spec, {"evidence_path": path})
        save.press("Enter")
        self.wait(lambda: self.page.get_by_role("checkbox", name="Create Blank Project", exact=True).count() == 0
                  and self.page.get_by_role("textbox", name="Name", exact=True).count() == 0, "Project creation did not finish", 120)

    def ensure_project(self):
        spec = {k: self.target.get(k) for k in ("environment", "domain_name", "project_number", "project_name", "record_type")}
        key = digest(["project", self.target["environment"], self.target["domain_name"], self.target["project_number"], self.target["project_name"]])
        prior = self.journal.destination(key, spec)
        found = self.find_project()
        if not found:
            if prior and prior["status"] != "cancelled_before_action":
                raise BrowserError("A previously attempted project creation is not found; refusing to repeat Save")
            if self.target.get("project_policy", "existing_only") != "find_or_create":
                raise BrowserError("The mapped project is missing and creation is disabled")
            self.create_project(key, spec)
            found = self.find_project()
            if not found:
                raise BrowserError("Created project cannot be independently found; creation remains pending")
        self.guard(project=True)
        path = self.evidence("project-confirmed", {"specification": spec, "row": found})
        self.journal.confirm_destination(key, spec, {"evidence_path": path, "observed_identity": spec})

    def folder_rows(self):
        self.guard(project=True)
        # A project switch or root click first renders the new breadcrumb and
        # an empty grid, then disables the toolbar while its rows arrive. One
        # early enabled check can precede that loading transition. Require a
        # stable directory together with an enabled toolbar before concluding
        # that a folder is absent and potentially creating it.
        previous, stable = None, 0
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            self.guard(project=True)
            table = self.control("region", "Record List")
            rows = read_table_rows(table)
            signature = [(r["name"], r["grid_row"], r["cells"]) for r in rows]
            ready = self.control("button", "Add Files").is_enabled()
            stable = stable + 1 if ready and signature == previous else 0
            previous = signature if ready else None
            if stable >= 4:
                break
            self.page.wait_for_timeout(500)
        else:
            raise BrowserError("The directory did not finish loading")
        walker = GridWalker(self.page, table, lambda: self.guard(project=True))
        rows = walker.scan_to_end(lambda: read_table_rows(table), folder_row_key)
        if not self.control("button", "Add Files").is_enabled():
            raise BrowserError("Directory loading restarted during its membership scan")
        return rows, walker, table

    def open_folder_row(self, row, walker, table, name):
        current = walker.seek(lambda: read_table_rows(table), folder_row_key, name,
                              row.get("absolute_row", row["grid_row"]))
        if not current.get("row_id"):
            raise BrowserError("The folder row has no observed DOM identity")
        parent = table.locator('[id="' + current["row_id"] + '"]')
        if parent.count() != 1 or parent.get_attribute("aria-label") != name:
            raise BrowserError("The folder row changed before opening")
        geometry = parent.evaluate("""e=>({rect:e.getBoundingClientRect().toJSON(),
            application:e.closest('[role=application]')?.getBoundingClientRect().toJSON(),
            canvas:document.querySelector('canvas#uno-canvas')?.getBoundingClientRect().toJSON()})""")
        rect = canvas_relative_rect(geometry["rect"], geometry["application"], geometry["canvas"], self.page.viewport_size)
        top, bottom = max(316, rect["y"]), min(935, rect["y"] + rect["height"])
        if bottom - top < 20:
            raise BrowserError("The folder row is clipped; cannot bind visible text")
        bounds = [820, top, 1580, bottom]
        doc = recognize(self.page, self.ocr, self.output / "folder-row.png", bounds=bounds)
        x, y = text_center(doc, name, bounds)
        self.guard(project=True)
        self.page.mouse.click(x, y, click_count=2)

    def create_folder(self, name, key, spec):
        self.guard(project=True)
        self.control("button", "Create Folder").press("Enter")
        self.page.get_by_role("textbox", name="Folder Name", exact=True).wait_for(state="attached", timeout=30000)
        field = self.control("textbox", "Folder Name")
        self.control("combobox", "Folder Type")
        # The semantic combobox geometry is offset from the canvas. Bind the
        # visible dialog before opening the calibrated dropdown and require the
        # exact Controlled option, rather than accepting its default Files type.
        doc = recognize(self.page, self.ocr, self.output / "folder-form.png", bounds=[725, 412, 1175, 628])
        text_center(doc, "New Folder", [740, 420, 930, 465])
        try:
            text_center(doc, "Controlled", [740, 470, 1160, 520])
        except BrowserError:
            # A child of a Controlled folder already defaults to Controlled;
            # the project root defaults to Files. Some Uno controls consume
            # their first click only to focus, so retry this read-only opening
            # once if no option appeared. No save is retried here.
            option = self.page.get_by_role("option", name="Controlled", exact=True)
            for attempt in range(2):
                self.guard(project=True)
                self.page.mouse.click(1140, 494)
                try:
                    option.wait_for(state="attached", timeout=3000)
                    if option.count() != 1:
                        raise BrowserError("The Controlled folder option is ambiguous")
                    self.page.wait_for_timeout(250)
                    # This visible Uno option can have a zero-size semantic
                    # rectangle. Bind its exact text inside the open dropdown.
                    click_text(self.page, self.ocr, self.output, "Controlled", [745, 515, 1155, 615])
                    break
                except Exception:
                    if attempt:
                        raise BrowserError("The Controlled folder option did not open")
        name_field = self.enter(field, name, name="Folder Name", point=(917, 538))
        doc = recognize(self.page, self.ocr, self.output / "folder-precommit.png", bounds=[740, 470, 1160, 570])
        text_center(doc, "Controlled")
        if name_field.input_value() != name:
            raise BrowserError("Folder name changed before Create")
        path = self.evidence("folder-precommit", {"specification": spec, "name": name_field.input_value(), "type": "Controlled"})
        create = self.control("button", "OK")
        self.journal.begin_destination(key, spec, {"evidence_path": path})
        create.press("Enter")
        self.wait(lambda: self.page.get_by_role("textbox", name="Folder Name", exact=True).count() == 0,
                  "Folder creation did not finish")

    def open_file_manager(self):
        self.guard(project=True)
        shortcut = self.page.get_by_role("option", name="Open File Manager", exact=True)
        if shortcut.count():
            self.control("option", "Open File Manager").press("Enter")
        else:
            # A fresh sign-in has no File Manager shortcut in the sidebar.
            # Find the app through All Apps instead of requiring the operator
            # to open it once and seed the recent-app list.
            self.control("option", "Open Apps").press("Enter")
            self.page.get_by_role("option", name="All Apps", exact=True).wait_for(state="attached", timeout=30000)
            self.control("option", "All Apps").press("Enter")
            self.page.get_by_role("textbox", name="Search", exact=True).wait_for(state="attached", timeout=30000)
            self.enter(self.control("textbox", "Search"), "File Manager", name="Search", point=(680, 103))
            self.page.get_by_role("button", name="Open File Manager", exact=True).wait_for(state="attached", timeout=30000)
            self.control("button", "Open File Manager").press("Enter")
        self.page.get_by_role("region", name="Record List", exact=True).wait_for(state="attached", timeout=60000)

    def ensure_folders(self):
        from playwright.sync_api import expect
        self.open_file_manager()
        self.control("region", "Record List")
        expand = self.page.get_by_role("button", name="Expand Navigation Pane", exact=True)
        if expand.count():
            self.control("button", "Expand Navigation Pane").press("Enter")
        # Clicking the exact project breadcrumb restores the folder root.
        self.page.get_by_role("button", name=self.target["project_name"], exact=True).first.wait_for(state="attached", timeout=30000)
        # The same name is also used by the folder-tree root. Bind the exact
        # visible breadcrumb, where the two controls cannot be confused.
        click_text(self.page, self.ocr, self.output, self.target["project_name"], [365, 185, 900, 222], refine=True)
        self.page.wait_for_timeout(700)
        search = self.control("textbox", "Search")
        if search.input_value():
            self.enter(search, "", name="Search", point=(925, 255))
            search.press("Enter")

            self.page.wait_for_timeout(700)
        traversed = []
        for name in self.target["folder_path"].split("/"):
            expect(self.control("button", "Add Files")).to_be_enabled(timeout=60000)
            rows, walker, table = self.folder_rows()
            self.evidence("folder-search", {"parent": "/".join(traversed), "name": name, "rows": rows})
            found = match_folder(rows, name)
            spec = {k: self.target[k] for k in ("environment", "domain_name", "project_number", "project_name")}
            spec.update(folder_path="/".join(traversed + [name]), folder_type="Controlled")
            key = digest(["folder", spec])
            prior = self.journal.destination(key, spec)
            if not found:
                if prior and prior["status"] != "cancelled_before_action":
                    raise BrowserError("A previously attempted folder creation is absent; refusing to repeat Create")
                if self.target.get("folder_policy", "existing_only") != "find_or_create":
                    raise BrowserError("The mapped folder is missing and creation is disabled")
                self.create_folder(name, key, spec)
                # Kahua may open the newly created folder immediately.
                if not self.page.get_by_role("button", name=name, exact=True).count():
                    rows, walker, table = self.folder_rows()
                    found = match_folder(rows, name)
                    if not found:
                        raise BrowserError("Created folder is not visible; creation remains pending")
            if found:
                self.open_folder_row(found, walker, table, name)
            self.wait(lambda: self.page.get_by_role("button", name=name, exact=True).count() == 1,
                      "The requested folder breadcrumb did not appear")
            expect(self.control("button", "Add Files")).to_be_enabled(timeout=60000)
            self.page.wait_for_timeout(700)
            traversed.append(name)
            for part in traversed:
                self.control("button", part)
            if name == self.target["folder_path"].split("/")[-1]:
                for header in ("Sheet Number", "Rev Number", "Current Rev"):
                    self.control("region", "Record List").get_by_role("columnheader", name=header, exact=True).wait_for(state="attached", timeout=30000)
            path = self.evidence("folder-confirmed", {"specification": spec, "breadcrumbs": traversed})
            self.journal.confirm_destination(key, spec, {"evidence_path": path, "observed_identity": spec})
        self.control("button", "View Selector").press("Enter")
        self.control("button", "Default").press("Enter")
        search = self.control("textbox", "Search")
        if search.input_value():
            self.enter(search, "", name="Search", point=(925, 255))
            search.press("Enter")

    def recover_to_finder(self):
        """Continue only when in-app navigation can be re-established.

        Reloading this tenant can discard its sign-in session. A known folder
        form may be cancelled, but an upload/project dialog with uncertain
        state stops the queue for journal-based reconciliation.
        """
        self.guard()
        folder_name = self.page.get_by_role("textbox", name="Folder Name", exact=True)
        if folder_name.count():
            self.control("button", "Cancel").press("Enter")
            folder_name.wait_for(state="detached", timeout=10000)
        if (self.page.get_by_role("group", name="Add Files", exact=True).count()
                or self.page.get_by_role("textbox", name="Name", exact=True).count()):
            raise BrowserError("An unfinished dialog needs reconciliation before the queue can continue")
        self.control("option", "Open Project Finder").press("Enter")
        self.page.get_by_role("grid", name="Partition Items", exact=True).wait_for(state="attached", timeout=30000)

    def ensure(self, target):
        self.target = validate_destination(target)
        self.guard()
        self.ensure_project()
        self.ensure_folders()
        self.guard(project=True)
        return {"target": target, "evidence_directory": str(self.output)}
