"""Authentication checks that distinguish an active form from a stale shell."""

from .browser import BrowserError


class AuthenticationRequired(BrowserError):
    pass


def needs_sign_in(page):
    """Closed tabs are recoverable; hidden Login shells are not expired auth.

    Kahua can retain a zero-size Login group after successful authentication.
    An active password form always wins. Otherwise a hidden Login shell is
    harmless only when the signed-in project finder is visibly available.
    No credential or form value is read.
    """
    try:
        password = page.get_by_role("textbox", name="Password", exact=True)
        if any(password.nth(i).is_visible() for i in range(password.count())):
            return True
        login = page.get_by_role("group", name="Login", exact=True)
        count = login.count()
        if not count:
            return False
        if any(login.nth(i).is_visible() for i in range(count)):
            return True
        finder = page.get_by_role("option", name="Open Project Finder", exact=True)
        return not (finder.count() == 1 and finder.is_visible())
    except Exception:
        return False  # Recovery will distinguish a dead tab from a dead browser.
