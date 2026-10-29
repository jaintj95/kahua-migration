import unittest
from unittest.mock import Mock, call, patch

from kahua_migration.browser import BrowserError
from kahua_migration.browser_dates import parse_selected_date_label, read_calendar_at


class CalendarDateTests(unittest.TestCase):
    def test_selected_date_is_independent_of_an_expected_value(self):
        self.assertEqual(parse_selected_date_label("Monday, April 13, 2026, Selected"), "2026-04-13")
        self.assertEqual(parse_selected_date_label("Thursday, February 29, 2024, Selected"), "2024-02-29")

    def test_missing_selection_invalid_dates_and_wrong_weekdays_stop(self):
        for label in ("Monday, April 13, 2026", "Tuesday, April 13, 2026, Selected",
                      "Thursday, February 29, 2025, Selected", "Monday, April 013, 2026, Selected", None):
            with self.subTest(label=label), self.assertRaises(BrowserError):
                parse_selected_date_label(label)

    def test_calendar_points_and_overlays_are_checked_before_clicking(self):
        page = Mock(viewport_size={"width": 1600, "height": 1000})
        for opening, closing in (((1601, 578), (1569, 578)), ((976, 578), (944, 600)),
                                 ((976, 578), (500, 578))):
            with self.subTest(opening=opening, closing=closing), self.assertRaises(BrowserError):
                read_calendar_at(page, "evidence", opening, closing)
        page.evaluate.return_value = {"tag": "DIV", "id": "other-dialog"}
        with self.assertRaisesRegex(BrowserError, "unexpected overlay"):
            read_calendar_at(page, "evidence", (976, 578), (944, 578))
        page.mouse.click.assert_not_called()

    def test_pointer_calendar_reads_the_observed_value_without_enter_or_escape(self):
        page = Mock(viewport_size={"width": 1600, "height": 1000})
        page.evaluate.return_value = {"tag": "CANVAS", "id": "uno-canvas"}
        with patch("kahua_migration.browser_dates.read_open_calendar", return_value="2025-12-12"):
            self.assertEqual(read_calendar_at(page, "evidence", (976, 578), (944, 578)), "2025-12-12")
        page.keyboard.press.assert_not_called()
        page.get_by_role.return_value.wait_for.assert_called_once_with(state="detached", timeout=30000)

    def test_focus_only_first_click_is_retried_without_retyping_or_saving(self):
        page = Mock(viewport_size={"width": 1600, "height": 1000})
        page.evaluate.return_value = {"tag": "CANVAS", "id": "uno-canvas"}
        page.get_by_role.return_value.count.side_effect = lambda: int(page.mouse.click.call_count >= 2)
        with patch("kahua_migration.browser_dates.read_open_calendar", return_value="2026-04-13"):
            self.assertEqual(read_calendar_at(page, "evidence", (976, 578), (944, 578)), "2026-04-13")
        self.assertEqual(page.mouse.click.call_args_list, [call(976, 578), call(976, 578), call(944, 578)])
        page.keyboard.press.assert_not_called()
        page.keyboard.type.assert_not_called()

    def test_missing_calendar_stops_after_two_guarded_clicks(self):
        page = Mock(viewport_size={"width": 1600, "height": 1000})
        page.evaluate.return_value = {"tag": "CANVAS", "id": "uno-canvas"}
        page.get_by_role.return_value.count.return_value = 0
        with patch("kahua_migration.browser_dates.read_open_calendar") as reader:
            with self.assertRaisesRegex(BrowserError, "did not open"):
                read_calendar_at(page, "evidence", (976, 578), (944, 578))
        self.assertEqual(page.mouse.click.call_args_list, [call(976, 578), call(976, 578)])
        reader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
