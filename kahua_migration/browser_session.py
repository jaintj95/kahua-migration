"""Replace a failed worker tab without replaying any application action.

Cookies, local storage and IndexedDB remain in the existing browser context.
Tab-scoped session storage is copied in memory at a healthy project boundary.
It is never included in reports, screenshots, logs or new files.
"""

import json
import time
from urllib.parse import urlsplit

from .browser import BrowserError
from .browser_auth import AuthenticationRequired, needs_sign_in


def origin(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise BrowserError("Session recovery requires the configured HTTPS origin")
    return f"{parsed.scheme}://{parsed.netloc}"


class BrowserSessionRecovery:
    def __init__(self, page, *, timeout_ms=60000):
        self.context = page.context
        self.page = page
        self.timeout_ms = timeout_ms
        self._origin = None
        self._session = None

    def checkpoint(self, page, environment):
        """Capture before project work, never by inspecting a stuck dialog."""
        expected = origin(environment)
        if needs_sign_in(page):
            raise AuthenticationRequired("The signed-in session expired; sign in before continuing the queue")
        if origin(page.url) != expected:
            raise BrowserError("Cannot capture a session from a different origin")
        if self._origin is not None and self._origin != expected:
            raise BrowserError("A migration queue cannot transfer sessions between origins")
        try:
            state = page.evaluate("""() => ({origin: location.origin,
                values: Object.fromEntries(Object.entries(sessionStorage))})""")
        except Exception:
            # Do not include browser-evaluation errors, which could contain
            # sensitive application state, in an on-disk run report.
            raise BrowserError("Could not checkpoint the signed-in browser session") from None
        if (not isinstance(state, dict) or state.get("origin") != expected
                or not isinstance(state.get("values"), dict)
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in state["values"].items())):
            raise BrowserError("The browser returned an invalid session checkpoint")
        self._origin, self._session = expected, state["values"]
        self.page = page

    def replace_failed_tab(self, failed_page, environment):
        """Discard the failed UI; preserve every pending journal intent.

        A new blank tab keeps the browser context alive while the old tab is
        closed. It cannot load Kahua until the old tab is confirmed closed.
        No Save, Cancel, OK, unload handler or application retry is invoked.
        """
        if self._session is None or origin(environment) != self._origin:
            raise BrowserError("No matching signed-in checkpoint is available for tab recovery")
        fresh = self.context.new_page()
        try:
            if not failed_page.is_closed():
                failed_page.close(run_before_unload=False)
            if not failed_page.is_closed():
                raise BrowserError("The failed worker tab did not close")
        except Exception:
            fresh.close(run_before_unload=False)
            raise BrowserError("Cannot isolate the failed worker tab; the browser needs attention") from None
        self.page = fresh
        # Restore only on the exact origin, only in the top frame and only in
        # an empty session. Never overwrite tokens refreshed by the application.
        state = json.dumps({"origin": self._origin, "values": self._session})
        fresh.add_init_script("""(() => {
            const state = """ + state + """;
            if (window === window.top && location.origin === state.origin && sessionStorage.length === 0) {
                for (const [key, value] of Object.entries(state.values)) sessionStorage.setItem(key, value);
            }
        })();""")
        try:
            fresh.goto(environment, wait_until="domcontentloaded", timeout=self.timeout_ms)
        except Exception:
            raise BrowserError("The clean worker tab could not load the configured Kahua environment") from None
        self._wait_until_ready(fresh)
        return fresh

    def _wait_until_ready(self, page):
        deadline = time.monotonic() + self.timeout_ms / 1000
        login_since = None
        while time.monotonic() < deadline:
            access = page.get_by_role("button", name="Enable accessibility", exact=True)
            if access.count() == 1 and access.is_visible():
                access.press("Enter", timeout=min(5000, self.timeout_ms))
            if origin(page.url) != self._origin:
                raise AuthenticationRequired("The clean tab redirected away from Kahua; sign-in is required")
            login = needs_sign_in(page)
            if login:
                login_since = login_since or time.monotonic()
                # A loading app may show its login shell briefly before it
                # restores the session. Only report a stable login screen.
                if time.monotonic() - login_since >= 2:
                    raise AuthenticationRequired("Kahua requires sign-in in the clean worker tab")
            else:
                login_since = None
                finder = page.get_by_role("option", name="Open Project Finder", exact=True)
                if finder.count() == 1 and finder.is_visible():
                    return
            page.wait_for_timeout(200)
        if needs_sign_in(page):
            raise AuthenticationRequired("Kahua requires sign-in in the clean worker tab")
        raise BrowserError("The clean worker tab did not become ready within the recovery timeout")
