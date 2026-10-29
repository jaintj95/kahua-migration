import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

from kahua_migration.browser import BrowserError
from kahua_migration.browser_export import read_revision_export


NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def table_xml(rows, formula=False):
    sheet = ET.Element("worksheet", xmlns=NS)
    data = ET.SubElement(sheet, "sheetData")
    for row_index, values in enumerate(rows, 1):
        row = ET.SubElement(data, "row", r=str(row_index))
        for column, value in enumerate(values):
            cell = ET.SubElement(row, "c", r=f"{chr(65 + column)}{row_index}", t="inlineStr")
            if formula and row_index == 2 and column == 0:
                ET.SubElement(cell, "f").text = "1+1"
            ET.SubElement(ET.SubElement(cell, "is"), "t").text = value
    return ET.tostring(sheet)


class KahuaXlsxExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "export.xlsx"
        self.drawings = [
            ["RowNumber", "RecordKey", "SheetNumber", "Title"],
            ["9", "drawing-b", "002", "SECOND SHEET"],
            ["2", "drawing-a", "001", "FIRST SHEET"],
        ]
        self.revisions = [
            ["RecordKey", "Drawing", "Drawing_ReferenceColumn", "Revision", "InitialFileName", "CurrentRevision"],
            ["revision-a", "2", "RowNumber", "3", "001.pdf", ""],
            ["revision-b", "9", "RowNumber", "1", "002.pdf", ""],
        ]

    def write_export(self, *, formula=False):
        with ZipFile(self.path, "w") as archive:
            archive.writestr("xl/workbook.xml", f'''<workbook xmlns="{NS}"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
                <sheets><sheet name="Drawing" r:id="a"/><sheet name="DrawingRevision" r:id="b"/></sheets>
                </workbook>''')
            archive.writestr("xl/_rels/workbook.xml.rels", '''<Relationships>
                <Relationship Id="a" Target="worksheets/sheet1.xml"/>
                <Relationship Id="b" Target="worksheets/sheet2.xml"/>
                </Relationships>''')
            archive.writestr("xl/worksheets/sheet1.xml", table_xml(self.drawings))
            archive.writestr("xl/worksheets/sheet2.xml", table_xml(self.revisions, formula))

    def test_join_uses_explicit_row_key_and_preserves_unknown_current_flags(self):
        self.write_export()
        records = read_revision_export(self.path)
        self.assertEqual([(r["sheet_number"], r["revision"], r["title"]) for r in records],
                         [("001", "3", "FIRST SHEET"), ("002", "1", "SECOND SHEET")])
        self.assertTrue(all(r["exported_current_revision"] is None for r in records))

    def test_duplicate_revision_identity_is_rejected(self):
        self.revisions.append(["another-record", "2", "RowNumber", "3", "001.pdf", ""])
        self.write_export()
        with self.assertRaises(BrowserError):
            read_revision_export(self.path)

    def test_unresolvable_drawing_reference_is_rejected(self):
        self.revisions[1][1] = "999"
        self.write_export()
        with self.assertRaises(BrowserError):
            read_revision_export(self.path)

    def test_formula_is_not_accepted_as_observed_metadata(self):
        self.write_export(formula=True)
        with self.assertRaises(BrowserError):
            read_revision_export(self.path)


if __name__ == "__main__":
    unittest.main()
