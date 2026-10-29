import unittest
from unittest.mock import MagicMock

from kahua_migration.browser import BrowserError
from kahua_migration.browser_navigation import AuthenticationRequired
from kahua_migration.browser_session import BrowserSessionRecovery, needs_sign_in


ENVIRONMENT = "https://fixture.kahua.com/"


class SessionRecoveryTests(unittest.TestCase):
    def page(self):
        page = MagicMock()
        page.url = ENVIRONMENT
        page.get_by_role.return_value.count.return_value = 0
        page.is_closed.return_value = False
        page.evaluate.return_value = {"origin": ENVIRONMENT.rstrip("/"), "values": {"session": "synthetic-token"}}
        return page

    def test_failure_replacement_closes_old_tab_without_unload_before_loading_new(self):
        page, fresh = self.page(), self.page()
        page.context.new_page.return_value = fresh
        events = []
        def close(**kwargs):
            self.assertEqual(kwargs, {"run_before_unload": False})
            events.append("old_closed")
            page.is_closed.return_value = True
        page.close.side_effect = close
        fresh.goto.side_effect = lambda *a, **k: events.append("new_loaded")
        recovery = BrowserSessionRecovery(page)
        recovery._wait_until_ready = MagicMock()
        recovery.checkpoint(page, ENVIRONMENT)
        self.assertIs(recovery.replace_failed_tab(page, ENVIRONMENT), fresh)
        self.assertEqual(events, ["old_closed", "new_loaded"])
        page.goto.assert_not_called()
        page.context.close.assert_not_called()

    def test_failed_close_never_loads_a_second_active_worker(self):
        page, fresh = self.page(), self.page()
        page.context.new_page.return_value = fresh
        page.close.side_effect = RuntimeError("browser disconnect")
        recovery = BrowserSessionRecovery(page)
        recovery.checkpoint(page, ENVIRONMENT)
        with self.assertRaisesRegex(BrowserError, "Cannot isolate"):
            recovery.replace_failed_tab(page, ENVIRONMENT)
        fresh.goto.assert_not_called()
        fresh.close.assert_called_once_with(run_before_unload=False)

    def test_already_closed_tab_uses_earlier_checkpoint(self):
        page = self.page()
        recovery = BrowserSessionRecovery(page)
        recovery._wait_until_ready = MagicMock()
        recovery.checkpoint(page, ENVIRONMENT)
        page.is_closed.return_value = True
        recovery.replace_failed_tab(page, ENVIRONMENT)
        page.close.assert_not_called()

    def test_checkpoint_rejects_login_wrong_origin_and_malformed_state(self):
        for mode in ("login", "origin", "malformed"):
            with self.subTest(mode=mode):
                page = self.page()
                if mode == "login":
                    page.get_by_role.return_value.count.return_value = 1
                elif mode == "origin":
                    page.url = "https://elsewhere.example/"
                else:
                    page.evaluate.return_value["values"] = ["invalid"]
                with self.assertRaises(BrowserError):
                    BrowserSessionRecovery(page).checkpoint(page, ENVIRONMENT)

    def test_recovery_cannot_transfer_authentication_to_another_origin(self):
        page = self.page()
        recovery = BrowserSessionRecovery(page)
        recovery.checkpoint(page, ENVIRONMENT)
        with self.assertRaisesRegex(BrowserError, "matching"):
            recovery.replace_failed_tab(page, "https://other.kahua.com/")
        page.context.new_page.assert_not_called()

    def test_session_evaluation_errors_do_not_leak_state(self):
        page = self.page()
        page.evaluate.side_effect = RuntimeError("synthetic-token should not appear in reports")
        with self.assertRaises(BrowserError) as raised:
            BrowserSessionRecovery(page).checkpoint(page, ENVIRONMENT)
        self.assertNotIn("synthetic-token", str(raised.exception))

    def test_dead_tab_does_not_masquerade_as_expired_authentication(self):
        page = self.page()
        page.get_by_role.side_effect = RuntimeError("Target closed")
        self.assertFalse(needs_sign_in(page))

    def test_zero_size_login_shell_with_visible_finder_is_authenticated(self):
        page = self.page()
        password, login, finder = MagicMock(), MagicMock(), MagicMock()
        password.count.return_value = 0
        login.count.return_value = 1
        login.nth.return_value.is_visible.return_value = False
        finder.count.return_value = 1
        finder.is_visible.return_value = True
        page.get_by_role.side_effect = lambda role, name, **_: {
            "Password": password, "Login": login, "Open Project Finder": finder}[name]
        self.assertFalse(needs_sign_in(page))
        finder.is_visible.return_value = False
        self.assertTrue(needs_sign_in(page))
        finder.is_visible.return_value = True
        password.count.return_value = 1
        password.nth.return_value.is_visible.return_value = True
        self.assertTrue(needs_sign_in(page), "An actual lock screen must win over a stale finder")


if __name__ == "__main__":
    unittest.main()
