"""Read only the two documented-by-observation Kahua export tables.

No Excel engine is needed at migration runtime. This deliberately reads literal
OpenXML values, rejects formulas and joins DrawingRevision.Drawing through the
export's explicit RowNumber key. Blank current flags remain unknown.
"""

import posixpath
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from .browser import BrowserError


NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
RID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def read_revision_export(path):
    with ZipFile(path) as archive:
        def xml(name):
            if archive.getinfo(name).file_size > 32 * 1024 * 1024:
                raise BrowserError("Kahua export XML exceeds the parser's size bound")
            return ET.fromstring(archive.read(name))

        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = ["".join(s.itertext()) for s in xml("xl/sharedStrings.xml").findall("x:si", NS)]
        relationships = {r.attrib["Id"]: r.attrib["Target"] for r in xml("xl/_rels/workbook.xml.rels")
                         if r.attrib.get("TargetMode") != "External"}
        sheets = {}
        for sheet in xml("xl/workbook.xml").findall("x:sheets/x:sheet", NS):
            name = sheet.attrib["name"]
            if name not in ("Drawing", "DrawingRevision"):
                continue
            target = relationships[sheet.attrib[RID]]
            member = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
            if not member.startswith("xl/") or name in sheets:
                raise BrowserError("Ambiguous Kahua workbook table")
            values = []
            for row in xml(member).findall("x:sheetData/x:row", NS):
                cells = {}
                for cell in row.findall("x:c", NS):
                    if cell.find("x:f", NS) is not None:
                        raise BrowserError("Kahua export verification requires literal values, not formulas")
                    match = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", cell.attrib.get("r", ""))
                    if not match:
                        raise BrowserError("Invalid export cell reference")
                    column = 0
                    for letter in match[1]:
                        column = column * 26 + ord(letter) - ord("A") + 1
                    if column > 1000 or column in cells:
                        raise BrowserError("Invalid or duplicate export column")
                    if cell.attrib.get("t") == "inlineStr":
                        value = "".join(cell.find("x:is", NS).itertext())
                    else:
                        value = cell.findtext("x:v", default="", namespaces=NS)
                        if cell.attrib.get("t") == "s":
                            value = shared[int(value)]
                    cells[column] = value
                if any(cells.values()):
                    values.append(cells)
            if not values:
                raise BrowserError("Kahua export has no table headers")
            headers = values[0]
            if len(set(headers.values())) != len(headers) or not all(headers.values()):
                raise BrowserError("Kahua export has ambiguous table headers")
            sheets[name] = [{headers[i]: row.get(i, "") for i in headers} for row in values[1:]]

    if set(sheets) != {"Drawing", "DrawingRevision"}:
        raise BrowserError("Export is missing Drawing or DrawingRevision")
    drawings = {}
    for row in sheets["Drawing"]:
        for field in ("RowNumber", "RecordKey", "SheetNumber", "Title"):
            if not row.get(field):
                raise BrowserError(f"Exported Drawing lacks {field}")
        key = row["RowNumber"]
        if key in drawings:
            raise BrowserError("Duplicate exported Drawing row key")
        drawings[key] = row
    result, seen, record_keys = [], set(), set()
    for row in sheets["DrawingRevision"]:
        if row.get("Drawing_ReferenceColumn") != "RowNumber" or row.get("Drawing") not in drawings:
            raise BrowserError("Cannot join exported revision to exactly one drawing")
        drawing = drawings[row["Drawing"]]
        key = drawing["SheetNumber"], row.get("Revision")
        if not key[1] or not row.get("RecordKey") or not row.get("InitialFileName"):
            raise BrowserError("Exported revision identity is incomplete")
        if key in seen or row["RecordKey"] in record_keys:
            raise BrowserError("Duplicate exported revision identity")
        seen.add(key)
        record_keys.add(row["RecordKey"])
        result.append({"sheet_number": key[0], "revision": key[1], "title": drawing["Title"],
                       "original_filename": row["InitialFileName"], "target_record_id": row["RecordKey"],
                       "drawing_record_id": drawing["RecordKey"],
                       "exported_current_revision": row.get("CurrentRevision") or None})
    return result
