"""Local screenshot controls for Kahua's offset accessibility geometry.

Only exact, unique, high-confidence labels are actionable. Callers must also
resolve the corresponding semantic control and guard their project/folder.
This module makes no service requests and does not operate native file dialogs.
"""

import math
import uuid
from pathlib import Path

from .browser import BrowserError
from .ocr import recognize_image
from .preflight import atomic_json


def text_center(document, text, bounds=None):
    width, height = document["image_width"], document["image_height"]
    if width <= 0 or height <= 0 or not isinstance(text, str) or not text:
        raise BrowserError("Invalid screenshot or visible action label")
    bounds = bounds if bounds is not None else [0, 0, width, height]
    if (len(bounds) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in bounds)
            or not 0 <= bounds[0] < bounds[2] <= width
            or not 0 <= bounds[1] < bounds[3] <= height):
        raise BrowserError("Visual action bounds are outside the screenshot")
    clip = document.get("recognition_clip")
    # Vision can place a recognized edge a fraction of a pixel outside its
    # actual crop. Permit at most half a pixel only when the action bounds are
    # exactly that crop; the click center must still lie inside the real bounds.
    tolerance = .5 if clip and bounds == [clip["x"], clip["y"],
                                          clip["x"] + clip["width"],
                                          clip["y"] + clip["height"]] else 0
    matches = []
    for line in document["lines"]:
        if line["text"] != text:
            continue
        x, y, w, h = (line[k] for k in ("x", "y", "width", "height"))
        if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
            raise BrowserError("Invalid recognized text geometry")
        if (bounds[0] - tolerance <= x and x + w <= bounds[2] + tolerance
                and bounds[1] - tolerance <= y and y + h <= bounds[3] + tolerance
                and bounds[0] <= x + w / 2 <= bounds[2]
                and bounds[1] <= y + h / 2 <= bounds[3]):
            matches.append(line)
    # A second low-confidence candidate still makes the action ambiguous.
    if (len(matches) != 1 or not math.isfinite(matches[0]["confidence"])
            or not .95 <= matches[0]["confidence"] <= 1):
        raise BrowserError(f"Cannot locate a unique, confident visible action: {text}")
    line = matches[0]
    return line["x"] + line["width"] / 2, line["y"] + line["height"] / 2


def recognize(page, executable, path, *, bounds=None):
    if page.get_by_role("textbox", name="Password", exact=True).count():
        raise BrowserError("Refusing to capture sign-in controls")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    viewport = page.viewport_size
    if not viewport:
        raise BrowserError("Screenshot recognition requires a fixed viewport")
    clip = None
    if bounds is not None:
        if (len(bounds) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in bounds)
                or not 0 <= bounds[0] < bounds[2] <= viewport["width"]
                or not 0 <= bounds[1] < bounds[3] <= viewport["height"]):
            raise BrowserError("Recognition bounds are outside the viewport")
        clip = {"x": bounds[0], "y": bounds[1], "width": bounds[2] - bounds[0], "height": bounds[3] - bounds[1]}
    # CSS pixels match mouse coordinates on Retina and Windows display scaling.
    page.screenshot(path=str(path), scale="css", **({"clip": clip} if clip else {}))
    document = recognize_image(executable, path)
    expected = (clip["width"], clip["height"]) if clip else (viewport["width"], viewport["height"])
    if (document["image_width"], document["image_height"]) != expected:
        raise BrowserError("Screenshot pixels do not match the browser viewport")
    if clip:
        # Restrict OCR to the actual popup. Otherwise text behind the menu can
        # merge with its labels on the same line, making a real action unreadable.
        # Keep all output coordinates in viewport space for existing guards.
        for line in document["lines"]:
            line["x"] += clip["x"]
            line["y"] += clip["y"]
        document["image_width"], document["image_height"] = viewport["width"], viewport["height"]
        document["recognition_clip"] = clip
    atomic_json(path.with_suffix(".ocr.json"), document)
    return document


def exact_candidate_bounds(document, text, bounds):
    """A unique exact candidate may define a crop, never an unverified click."""
    candidates = [line for line in document["lines"] if line["text"] == text]
    if len(candidates) != 1:
        raise BrowserError("The visible label has no unique exact candidate for focused recognition")
    line = candidates[0]
    x, y, width, height = (line[k] for k in ("x", "y", "width", "height"))
    if (not all(math.isfinite(v) for v in (x, y, width, height)) or width <= 0 or height <= 0
            or not bounds[0] <= x < x + width <= bounds[2]
            or not bounds[1] <= y < y + height <= bounds[3]):
        raise BrowserError("The visible label candidate falls outside its permitted region")
    return [max(bounds[0], math.floor(x) - 12), bounds[1],
            min(bounds[2], math.ceil(x + width) + 12), bounds[3]]


def click_text(page, executable, output, text, bounds=None, *, refine=False):
    path = Path(output) / ("visual-action-" + str(uuid.uuid4()) + ".png")
    document = recognize(page, executable, path, bounds=bounds)
    try:
        x, y = text_center(document, text, bounds)
    except BrowserError:
        if not refine or bounds is None:
            raise
        bounds = exact_candidate_bounds(document, text, bounds)
        path = path.with_name(path.stem + "-focused.png")
        document = recognize(page, executable, path, bounds=bounds)
        x, y = text_center(document, text, bounds)
    hit = page.evaluate("""({x,y}) => {
        const e = document.elementFromPoint(x,y);
        return e ? {tag:e.tagName, id:e.id} : null;
    }""", {"x": x, "y": y})
    if hit != {"tag": "CANVAS", "id": "uno-canvas"}:
        raise BrowserError("An unexpected overlay covers the visible action")
    atomic_json(path.with_suffix(".action.json"), {"visible_text": text, "x": x, "y": y,
                                                   "screenshot": str(path), "state": "intent"})
    page.mouse.click(x, y)


def install_upload_input_fallback(context):
    """Use Uno's documented upload-input fallback in subsequent documents.

    Chrome's native showOpenFilePicker does not emit Playwright filechooser
    events. Disabling that optional capability before the app loads lets Uno
    select its standard HTML upload implementation. The fallback still uses
    the real application upload form and the selected file's original bytes.
    """
    context.add_init_script("delete window.showOpenFilePicker;")
