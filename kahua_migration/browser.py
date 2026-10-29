"""Browser transport with explicit, calibrated UI recipes; no Kahua API calls.

Kahua's accessibility layer can retain stale controls. Resolve exactly one
visible, active-size candidate and use keyboard activation where calibrated.
An incomplete profile is useful for inspection but cannot authorize a run.
"""

from __future__ import annotations

import csv
import json
import re
import uuid
from functools import wraps
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from .contract import METADATA
from .http_client import ApiError
from .preflight import InputError, atomic_json, digest, file_digest, inside, positive_int


class BrowserError(ApiError):
    pass


def canvas_relative_rect(rect, application, canvas, viewport):
    """Remove scrolling of Uno's transparent accessibility container."""
    if not application or not canvas or not viewport:
        raise BrowserError("Canvas target has no verified application coordinate frame")
    for frame in (application, canvas):
        if frame["width"] != viewport["width"] or frame["height"] != viewport["height"]:
            raise BrowserError("The canvas and accessibility coordinate frames differ")
    return {**rect, "x": rect["x"] - application["x"] + canvas["x"],
            "y": rect["y"] - application["y"] + canvas["y"]}


def browser_operation(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except (BrowserError, InputError):
            raise
        except Exception as exc:
            reason = f"Browser {method.__name__} failed: {type(exc).__name__}"
            self.failure_evidence(reason)
            raise BrowserError(reason + "; inspect the saved screenshot before resuming") from exc
    return guarded


RECIPES = ("open_project", "open_folder", "prepare_snapshot", "export_csv", "prepare_upload",
           "upload_metadata", "commit_upload", "select_record", "edit_metadata", "submitted",
           "accepted", "released", "download_file")


def validate_browser_profile(profile, require_verified=True):
    if profile.get("schema_version") != 1 or profile.get("transport") != "browser":
        raise InputError("Expected a browser profile with schema_version=1")
    if require_verified and (not profile.get("verified_at") or not profile.get("verification_evidence")):
        raise InputError("Browser profile needs the live four-file pilot before automated writes")
    for name in RECIPES:
        if not isinstance(profile.get("recipes", {}).get(name), list):
            raise InputError(f"Calibrate browser recipe: {name}")
    for name in ("project_guards", "folder_guards", "snapshot_guards", "record_guards"):
        if not profile.get(name):
            raise InputError(f"Calibrate mandatory browser guards: {name}")
    if not profile.get("upload_trigger"):
        raise InputError("Calibrate the upload control")
    export = profile.get("export", {})
    for field in (*METADATA, "is_current", "file_version"):
        if not isinstance(export.get("columns", {}).get(field), str) and not profile.get("detail_fields", {}).get(field):
            raise InputError(f"The verification CSV must contain {field}; configure the view/profile")
    if not export.get("record_id_columns") or not export.get("status_rules"):
        raise InputError("Configure exported record identity and workflow status rules")
    if not export.get("date_format") or not export.get("true_values") or not export.get("false_values"):
        raise InputError("Configure the observed CSV date and boolean formats")
    if not export.get("count_locator") or not export.get("count_pattern"):
        raise InputError("A verified visible item-count control is required to detect truncated exports")
    if not 1000 <= profile.get("timeout_ms", 30000) <= 60000:
        raise InputError("Browser action timeout must be between 1 and 60 seconds")
    return digest(profile)


def read_log_csv(path, profile, target):
    """Compare explicit exported values; never fill missing metadata from the source plan."""
    spec = profile["export"]
    with Path(path).open(encoding=spec.get("encoding", "utf-8-sig"), newline="") as handle:
        reader = csv.DictReader(handle, delimiter=spec.get("delimiter", ","))
        csv_columns = {name: column for name, column in spec["columns"].items() if column}
        required = set(csv_columns.values()) | set(spec["record_id_columns"])
        required |= {key for rule in spec["status_rules"] for key in rule["equals"]}
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise BrowserError("CSV export has missing or duplicate headers")
        if missing := required - set(reader.fieldnames):
            raise BrowserError(f"CSV verification fields are missing: {sorted(missing)}")
        result, keys = [], set()
        for raw in reader:
            if None in raw or any(v is None for v in raw.values()):
                raise BrowserError("CSV export contains a malformed row")
            if not any(raw.values()):
                continue
            row = {field: raw[column] if column else None for field, column in spec["columns"].items()}
            for field in ("sheet_number", "revision"):
                if not row[field] or row[field] != row[field].strip():
                    raise BrowserError(f"Exported {field} is blank or has ambiguous whitespace")
            for field in ("batch_date", "revision_date"):
                if row[field]:
                    try:
                        row[field] = datetime.strptime(row[field], spec["date_format"]).date().isoformat()
                    except ValueError as exc:
                        raise BrowserError(f"Exported {field} does not match the configured date format") from exc
                else:
                    row[field] = None
            if spec["columns"].get("is_current"):
                row["is_current"] = parse_browser_value(row["is_current"], "is_current", spec)
            if spec["columns"].get("file_version"):
                row["file_version"] = positive_int(row["file_version"])
            statuses = [rule["status"] for rule in spec["status_rules"]
                        if all(raw[name] == value for name, value in rule["equals"].items())]
            if len(statuses) != 1:
                raise BrowserError("Exported workflow fields do not match exactly one configured state")
            row["status"] = statuses[0]
            identity = [raw[name] for name in spec["record_id_columns"]]
            if not all(identity):
                raise BrowserError("Exported target record identity is incomplete")
            row["target_record_id"] = digest([target, identity])
            row["exported_record_identity"] = dict(zip(spec["record_id_columns"], identity))
            key = (row["sheet_number"].casefold(), row["revision"])
            if key in keys:
                raise BrowserError("CSV contains duplicate sheet/revision identities")
            keys.add(key)
            result.append(row)
    return result


def parse_browser_value(value, field, export_spec, date_format=None):
    if field == "is_current":
        if value in export_spec["true_values"]:
            return True
        if value in export_spec["false_values"]:
            return False
        raise BrowserError("Unrecognized Current Revision value")
    if field == "file_version":
        return positive_int(value)
    if field in ("batch_date", "revision_date"):
        return datetime.strptime(value, date_format or export_spec["date_format"]).date().isoformat() if value else None
    return value


class BrowserAdapter:
    def __init__(self, page, profile, output, *, manual_files=False, prompt=input):
        self.contract_id = validate_browser_profile(profile)
        self.page, self.profile = page, profile
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.manual_files, self.prompt = manual_files, prompt
        self.timeout = profile.get("timeout_ms", 30000)
        page.set_default_timeout(self.timeout)
        self.context, self.cache = {}, {}

    def _context(self, project, record=None, observed=None):
        target = project["target"]
        values = {**target, "folder_name": target["folder_path"].replace("\\", "/").rstrip("/").split("/")[-1],
                  **(observed or {}), **(record or {})}
        for name in ("batch_date", "revision_date"):
            if values.get(name):
                values[name + "_ui"] = datetime.strptime(values[name], "%Y-%m-%d").strftime(self.profile.get("input_date_format", "%m/%d/%Y"))
        self.context = values

    def _format(self, value):
        return value.format_map(self.context) if isinstance(value, str) else value

    def _locate(self, spec):
        scope = self._locate(spec["scope"]) if spec.get("scope") else self.page
        if spec.get("role"):
            loc = scope.get_by_role(spec["role"], name=self._format(spec["name"]), exact=True)
        elif spec.get("text"):
            loc = scope.get_by_text(self._format(spec["text"]), exact=True)
        elif spec.get("css"):
            if "{" in spec["css"] or "}" in spec["css"]:
                raise BrowserError("Use exact role/name locators for dynamic values; CSS recipes must be static")
            loc = scope.locator(spec["css"])
        else:
            raise BrowserError("Locator must have a role/name, exact text, or a calibrated CSS selector")
        # No automatic .first(): duplicate semantic controls have caused wrong UI actions.
        loc.first.wait_for(state="attached", timeout=self.timeout)
        candidates = []
        for candidate in loc.all():
            rect = candidate.bounding_box()
            if candidate.is_visible() and rect and rect["height"] >= spec.get("min_height", 1):
                candidates.append(candidate)
        if len(candidates) != 1:
            raise BrowserError(f"Expected one active control; found {len(candidates)} for {spec}")
        return candidates[0]

    def _steps(self, steps):
        for step in steps:
            settle = step.get("settle_ms", 0)
            if type(settle) is not int or not 0 <= settle <= 1000:
                raise BrowserError("Canvas settling delay must be an integer from 0 to 1000 ms")
            loc = self._locate(step["locator"])
            op, value = step["op"], self._format(step.get("value"))
            if op == "press":
                loc.press(value, timeout=self.timeout)
            elif op == "click":
                loc.click(timeout=self.timeout)
            elif op in ("canvas_click", "canvas_double_click"):
                self._canvas_click(loc, double=op == "canvas_double_click")
            elif op == "ocr_click":
                from .browser_visual import click_text
                if not self.profile.get("ocr_executable"):
                    raise BrowserError("Configure the local OCR executable before visual actions")
                click_text(self.page, self.profile["ocr_executable"], self.output,
                           self._format(step["visible_text"]), step.get("bounds"))
            elif op == "set_expanded":
                expected = "true" if value is True else "false" if value is False else None
                if expected is None or loc.get_attribute("aria-expanded") not in ("true", "false"):
                    raise BrowserError("Expand/collapse needs a boolean and an observed expandable control")
                if loc.get_attribute("aria-expanded") != expected:
                    loc.press("Enter", timeout=self.timeout)
                if loc.get_attribute("aria-expanded") != expected:
                    raise BrowserError("Section did not reach its requested expanded state")
            elif op == "fill":
                loc.fill(value, timeout=self.timeout)
                loc.press("Tab", timeout=self.timeout)
                if loc.input_value() != value:
                    raise BrowserError("Input readback differs after fill; refusing to advance the recipe")
            elif op == "type":
                loc.press("ControlOrMeta+A", timeout=self.timeout)
                loc.press_sequentially(value, timeout=self.timeout)
                loc.press("Tab", timeout=self.timeout)
                if loc.input_value() != value:
                    raise BrowserError("Input readback differs after typing; refusing to advance the recipe")
            elif op == "assert_value":
                if loc.input_value() != value:
                    raise BrowserError("Input readback differs from the expected value")
            elif op == "assert_text":
                actual = loc.get_attribute(step["attribute"]) if step.get("attribute") else loc.inner_text()
                if actual != value:
                    raise BrowserError(f"Visible state differs from the expected value for {step['locator']}")
            elif op == "assert_exists":
                pass
            else:
                raise BrowserError(f"Unsupported browser recipe operation: {op}")
            # Canvas section changes animate after their accessibility state has
            # changed. A calibrated short delay is followed by the next recipe's
            # explicit readback/guard; elapsed time never proves an action worked.
            if settle:
                self.page.wait_for_timeout(settle)

    def _canvas_click(self, loc, *, double=False):
        """Activate an observed semantic control through the canvas above it.

        Kahua's semantic cells have pointer-events:none; ordinary Playwright
        click waits forever for the covering canvas to disappear. This operation
        is explicitly calibrated, never an automatic fallback for a failed click.
        It resolves the named control again and refuses offscreen/stale geometry
        or an unexpected overlay. A subsequent recipe guard checks the result.
        """
        rect = loc.bounding_box()
        viewport = self.page.viewport_size
        if not rect or not viewport or rect["width"] < 1 or rect["height"] < 1:
            raise BrowserError("Canvas target has no usable geometry")
        frames = loc.evaluate("""e=>({
            application:e.closest('[role=application]')?.getBoundingClientRect().toJSON(),
            canvas:document.querySelector('canvas#uno-canvas')?.getBoundingClientRect().toJSON()
        })""")
        rect = canvas_relative_rect(rect, frames.get("application"), frames.get("canvas"), viewport)
        x, y = rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2
        if not (0 <= x < viewport["width"] and 0 <= y < viewport["height"]):
            raise BrowserError("Canvas target is outside the viewport; resolve the visible control")
        hit = self.page.evaluate("""({x,y}) => {
            const e = document.elementFromPoint(x,y);
            return e ? {tag:e.tagName, id:e.id} : null;
        }""", {"x": x, "y": y})
        if hit != {"tag": "CANVAS", "id": "uno-canvas"}:
            raise BrowserError("An unexpected overlay covers the calibrated canvas target")
        self.page.mouse.click(x, y, click_count=2 if double else 1)

    def _recipe(self, name):
        self._steps(self.profile["recipes"][name])

    def _guard(self):
        expected, actual = urlsplit(self.context["environment"]), urlsplit(self.page.url)
        if (expected.scheme, expected.netloc) != (actual.scheme, actual.netloc):
            raise BrowserError("Browser navigated outside the configured Kahua origin")
        self._steps(self.profile["project_guards"])
        self._steps(self.profile["folder_guards"])

    def _download(self, recipe, suffix):
        path = self.output / (str(uuid.uuid4()) + suffix)
        with self.page.expect_download(timeout=self.timeout) as event:
            self._recipe(recipe)
        event.value.save_as(str(path))
        if not path.is_file() or path.stat().st_size == 0:
            raise BrowserError("Browser download did not produce a nonempty file")
        return path

    def _count(self):
        spec = self.profile["export"]
        loc = self._locate(spec["count_locator"])
        value = loc.get_attribute(spec["count_attribute"]) if spec.get("count_attribute") else loc.inner_text()
        match = re.fullmatch(spec["count_pattern"], value or "")
        if not match:
            raise BrowserError("Cannot verify the current log's total item count")
        return int(match.group("count").replace(",", ""))

    @browser_operation
    def check_target(self, project):
        self._context(project)
        if project["target"].get("transport") != "browser":
            raise BrowserError("This destination is not configured for browser transport")
        self._recipe("open_project")
        self._recipe("open_folder")
        self._guard()
        return {"transport": "browser", "url": self.page.url, "target": project["target"], "profile_id": self.contract_id}

    @browser_operation
    def snapshot(self, project):
        self._context(project)
        self._recipe("prepare_snapshot")
        self._guard()
        self._steps(self.profile["snapshot_guards"])
        before = self._count()
        path = self._download("export_csv", ".csv")
        self._guard()
        self._steps(self.profile["snapshot_guards"])
        rows = read_log_csv(path, self.profile, project["target"])
        if before != self._count() or len(rows) != before:
            raise BrowserError("CSV row count differs from the unfiltered log count")
        # Some tenant views omit batch dates or file version. Read those fields
        # from the actual record detail, never substitute expected source data.
        for row in rows:
            if self.profile.get("detail_fields"):
                self._context(project, row, row)
                self._recipe("prepare_snapshot")
                self._select(project, row, row)
                for field, spec in self.profile["detail_fields"].items():
                    loc = self._locate(spec["locator"])
                    if spec.get("read") == "input_value":
                        value = loc.input_value()
                    elif spec.get("read") == "attribute":
                        value = loc.get_attribute(spec["attribute"])
                    else:
                        value = loc.inner_text()
                    if spec.get("pattern"):
                        match = re.fullmatch(spec["pattern"], value or "")
                        if not match:
                            raise BrowserError(f"Cannot parse detail readback for {field}")
                        value = match.group("value")
                    row[field] = parse_browser_value(value, field, self.profile["export"], spec.get("date_format"))
        if rows and self.profile.get("detail_fields"):
            self._context(project)
            self._recipe("prepare_snapshot")
            self._guard()
            if self._count() != before:
                raise BrowserError("Folder changed during record-detail verification")
        return rows

    @browser_operation
    def find(self, project, record):
        rows = [r for r in self.snapshot(project)
                if (r["sheet_number"], r["revision"]) == (record["sheet_number"], record["revision"])]
        if len(rows) > 1:
            raise BrowserError("Duplicate target revisions found")
        return rows[0] if rows else None

    @browser_operation
    def upload(self, project, record, source_root):
        source = inside(Path(source_root), record["source_file"])
        if file_digest(source) != record["sha256"] or source.stat().st_size != record["size_bytes"]:
            raise BrowserError("Source file changed before browser upload")
        self._context(project, record)
        self._guard()
        self._recipe("prepare_upload")
        if self.manual_files:
            self.prompt(f"Select only this file in Kahua: {source}\nLeave the upload metadata dialog open, then press Enter here. ")
        else:
            # Attaches files through the browser control; never drives Finder/Explorer.
            with self.page.expect_file_chooser(timeout=self.timeout) as event:
                self._steps([self.profile["upload_trigger"]])
            event.value.set_files(str(source), timeout=self.timeout)
        self._recipe("upload_metadata")
        self._recipe("commit_upload")
        return {"transport": "browser", "source_sha256": record["sha256"], "file": source.name}

    def _select(self, project, record, row):
        self._context(project, record, row)
        self._guard()
        self._recipe("select_record")
        self._steps(self.profile["record_guards"])

    @browser_operation
    def set_metadata(self, project, record, observed):
        if all(observed.get(name) == record[name] for name in METADATA):
            return {"metadata_already_matches": True}
        self._select(project, record, observed)
        self._recipe("edit_metadata")
        return {"transport": "browser", "target_record_id": observed["target_record_id"]}

    @browser_operation
    def transition(self, project, record, observed, stage):
        self._select(project, record, observed)
        self._recipe(stage)
        return {"transport": "browser", "stage": stage, "target_record_id": observed["target_record_id"]}

    @browser_operation
    def verify_content(self, project, record, observed, *, fresh=False):
        key = (observed["target_record_id"], observed["file_version"])
        if fresh or key not in self.cache:
            self._select(project, record, observed)
            path = self._download("download_file", ".pdf")
            self.cache[key] = {"sha256": file_digest(path), "size_bytes": path.stat().st_size}
        actual = self.cache[key]
        if actual != {"sha256": record["sha256"], "size_bytes": record["size_bytes"]}:
            raise BrowserError("Downloaded browser file does not match the source hash/size")
        return {**observed, **actual}

    def failure_evidence(self, reason):
        tag = str(uuid.uuid4())
        screenshot = self.output / (tag + ".png")
        try:
            self.page.screenshot(path=str(screenshot))
        except Exception:
            screenshot = None
        atomic_json(self.output / (tag + ".json"), {"reason": reason, "url": self.page.url,
                    "screenshot": str(screenshot) if screenshot else None})
