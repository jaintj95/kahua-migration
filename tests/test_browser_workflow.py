import unittest

from kahua_migration.browser import BrowserError
from kahua_migration.browser_workflow import verify_review_rows


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            {"sheet_number": "1", "revision": "3", "title": "TITLE SHEET", "batch_name": "Roadway"},
            {"sheet_number": "ROADWAY-SIGN4", "revision": "1", "title": "SIGNATURE SHEET", "batch_name": "Roadway"},
        ]
        self.rows = [["1", "3", "1", "TITLE SHEET", "Roadway"],
                     ["ROADWAY-SIGN4", "1", "1", "SIGNATURE SHEET", "Roadway"]]

    def test_mixed_batch_review_can_be_in_either_sort_order(self):
        verify_review_rows(self.rows[::-1], self.records, "submitted")
        verify_review_rows([r[:4] for r in self.rows], self.records, "accepted")

    def test_wrong_batch_duplicate_extra_missing_or_second_file_version_stops(self):
        bad = [self.rows[:1], self.rows + [self.rows[0]], [self.rows[0], self.rows[0]],
               [self.rows[0][:-1] + ["Other"], self.rows[1]],
               [["1", "3", "2", "TITLE SHEET", "Roadway"], self.rows[1]]]
        for rows in bad:
            with self.subTest(rows=rows), self.assertRaises(BrowserError):
                verify_review_rows(rows, self.records, "submitted")


if __name__ == "__main__":
    unittest.main()
