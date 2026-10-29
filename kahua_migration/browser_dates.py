"""Read Kahua dates from the calendar's explicit selected-date label."""

import re
from datetime import datetime
from pathlib import Path

from .browser import BrowserError
from .journal import now
from .preflight import atomic_json


SELECTED_DATE = re.compile(r"^[A-Za-z]+, [A-Za-z]+ [0-9]{1,2}, [0-9]{4}, Selected$")


def parse_selected_date_label(label):
    if not isinstance(label, str) or not SELECTED_DATE.fullmatch(label):
        raise BrowserError("Calendar selected-date label is missing or unrecognized")
    try:
        value = datetime.strptime(label, "%A, %B %d, %Y, Selected").date()
    except ValueError as exc:
        raise BrowserError("Calendar selected-date label is invalid") from exc
    expected = value.strftime("%A, %B ") + str(value.day) + value.strftime(", %Y, Selected")
    if label != expected:
        raise BrowserError("Calendar weekday and selected date disagree")
    return value.isoformat()


def read_open_calendar(page, output):
    """Read only the actual, uniquely visible Selected day; no expected input."""
    candidates = page.get_by_role("button", name=SELECTED_DATE)
    candidates.first.wait_for(state="attached", timeout=30000)
    active = []
    viewport = page.viewport_size
    for candidate in candidates.all():
        rect = candidate.bounding_box()
        if (candidate.is_visible() and rect and rect["height"] >= 20 and rect["width"] >= 20
                and viewport and 0 <= rect["x"] < viewport["width"]
                and 0 <= rect["y"] < viewport["height"]):
            active.append(candidate)
    if len(active) != 1:
        raise BrowserError("Calendar does not expose one unambiguous selected day")
    label = active[0].get_attribute("aria-label")
    actual = parse_selected_date_label(label)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(path.with_suffix(".png")))
    atomic_json(path.with_suffix(".json"), {"observed_at": now(), "selected_label": label,
                                           "date": actual, "source": "semantic_calendar_selected_day"})
    return actual


def read_focused_date_picker(page, output):
    focused = page.locator(":focus")
    if focused.count() != 1 or focused.get_attribute("aria-label") != "Date Picker":
        raise BrowserError("The date edit did not focus its calendar button")
    focused.press("Enter")
    result = read_open_calendar(page, output)
    close_selected_calendar(page, result)
    return result


def read_calendar_at(page, output, open_point, close_point):
    """Read a calendar at points already bound to a visible date field."""
    viewport = page.viewport_size
    if (not viewport or len(open_point) != 2 or len(close_point) != 2
            or not all(0 <= point[0] < viewport["width"] and 0 <= point[1] < viewport["height"]
                       for point in (open_point, close_point))
            or abs(open_point[1] - close_point[1]) > 1
            or not 24 <= open_point[0] - close_point[0] <= 48):
        raise BrowserError("Calendar points are not bound to one visible date field")
    selected = page.get_by_role("button", name=SELECTED_DATE)
    for attempt in range(2):
        hit = page.evaluate("""({x,y})=>{
            const e=document.elementFromPoint(x,y);return e?{tag:e.tagName,id:e.id}:null;
        }""", {"x": open_point[0], "y": open_point[1]})
        if hit != {"tag": "CANVAS", "id": "uno-canvas"}:
            raise BrowserError("An unexpected overlay covers the date calendar")
        page.mouse.click(*open_point)
        for _ in range(30):
            if selected.count():
                break
            page.wait_for_timeout(100)
        if selected.count():
            break
        # A first pointer click can only activate the date editor after typing.
        # Retry the same bound calendar point once, only while no selected-day
        # control is present. No date or parent-form command is changed.
    else:
        raise BrowserError("The date calendar did not open after two guarded clicks")
    result = read_open_calendar(page, output)
    page.mouse.click(*close_point)
    selected.wait_for(state="detached", timeout=30000)
    return result


def close_selected_calendar(page, selected_iso):
    # Escape propagates to the parent Add Files form in this tenant and cancels
    # the upload wizard. Select its already-selected date to close only the
    # calendar. This writes the same independently observed date back to the field.
    value = datetime.strptime(selected_iso, "%Y-%m-%d").date()
    label = value.strftime("%A, %B ") + str(value.day) + value.strftime(", %Y, Selected")
    selected = page.get_by_role("button", name=label, exact=True)
    selected.press("Enter")
    selected.wait_for(state="detached", timeout=30000)
