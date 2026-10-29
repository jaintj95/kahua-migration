"""Independent folder exports and original-file checks in the browser session."""

from pathlib import Path

from .browser import BrowserError
from .browser_export import read_revision_export
from .browser_visual import click_text
from .browser_workflow import open_toolbar_action
from .journal import now
from .preflight import atomic_json, file_digest


def identity(row):
    return row["sheet_number"], row["revision"]


def export_folder(inspector, output, ocr_executable):
    """Export the unfiltered folder and require its visible membership to match."""
    page, output = inspector.page, Path(output)
    output.mkdir(parents=True, exist_ok=True)
    inspector._close_detail()
    inspector._control("button", "View Selector").press("Enter")
    inspector._control("button", "Default").press("Enter")
    inspector._clear_filter_chips()
    inspector._wait_folder_layout()
    search = inspector._control("textbox", "Search")
    if search.input_value():
        raise BrowserError("Clear the drawing-log search before claiming complete membership")
    before = inspector.all_grid()
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
    if len(before) != len(exported) or {identity(r) for r in before} != {identity(r) for r in exported}:
        raise BrowserError("Export and visible folder differ; large-log pagination needs calibration")
    after = inspector.all_grid()
    if {identity(r) for r in after} != {identity(r) for r in before} or len(after) != len(before):
        raise BrowserError("Folder membership changed during export")
    atomic_json(output / "membership.json", {
        "observed_at": now(), "target": inspector.target, "records": exported,
        "workbook_sha256": file_digest(path), "complete_visible_membership": True})
    return exported


def download_original(inspector, record, membership, output, ocr_executable, *, preserve_filter=False):
    """Download one exactly bound original and compare its bytes with the plan.

    A filename alone is insufficient: repeated sheet revisions share filenames.
    Bind the grid's sheet/revision, its detail navigation index, and the complete
    independently exported folder before requesting any download.
    """
    page, output = inspector.page, Path(output)
    output.mkdir(parents=True, exist_ok=True)
    inspector._close_detail()
    if preserve_filter and len(membership) != 1:
        raise BrowserError("Filtered download requires exactly one independently exported revision")
    if not preserve_filter:
        inspector._clear_filter_chips()
    inspector._wait_folder_layout()
    rows = inspector.all_grid()
    if len(rows) != len(membership) or {identity(r) for r in rows} != {identity(r) for r in membership}:
        raise BrowserError("The download folder differs from its independent export")
    matches = [r for r in rows if identity(r) == identity(record)]
    if len(matches) != 1:
        raise BrowserError("Cannot bind the requested original to a unique revision")
    row = matches[0]
    inspector._open_record(row)
    inspector._control("tab", "Details").press("Enter")
    if len(rows) > 1:
        page.get_by_text(f"Item {row.get('absolute_row', row['grid_row'])} of {len(rows)}", exact=True).wait_for(
            state="visible", timeout=30000)
    filename = Path(record["source_file"]).name
    main = page.get_by_role("main", name="File Manager Main Content", exact=True)
    header = main.get_by_role("region")
    if header.count() != 1 or header.get_attribute("aria-label") != filename:
        raise BrowserError("The original-file header differs from the requested revision")
    main.get_by_role("button", name="More", exact=True).press("Enter")
    page.get_by_role("menuitem", name="Download", exact=True).wait_for(state="attached", timeout=30000)
    with page.expect_download(timeout=600000) as event:
        click_text(page, ocr_executable, output, "Download", [1088, 180, 1251, 835])
    download = event.value
    if download.suggested_filename != filename:
        raise BrowserError("Kahua returned a different original filename")
    path = output / filename
    download.save_as(str(path))
    result = {"observed_at": now(), "target": inspector.target,
              "sheet_number": record["sheet_number"], "revision": record["revision"],
              "original_filename": filename, "download_path": str(path.resolve()),
              "sha256": file_digest(path), "size_bytes": path.stat().st_size,
              "detail_index": row.get("absolute_row", row["grid_row"]), "folder_records": len(rows)}
    result["source_match"] = result["sha256"] == record["sha256"] and result["size_bytes"] == record["size_bytes"]
    atomic_json(output / "integrity.json", result)
    if not result["source_match"]:
        raise BrowserError("The downloaded original differs from the audited source bytes")
    # The ordinary download popup uses the same observed Ok command as Export.
    inspector._control("button", "Ok").press("Enter")
    inspector._close_detail()
    return result
