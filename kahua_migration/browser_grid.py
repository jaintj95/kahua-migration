"""Traverse rendered Uno grids by stable identities and verified coverage.

Uno restarts aria-rowindex inside each rendered window. It is a cell locator,
not an absolute record number. Absolute positions come from overlapping pages.
"""

import time

from .browser import BrowserError, canvas_relative_rect


def merge_page(collected, rows, key, *, expected_count):
    """Overlapping pages are allowed; gaps and reused indices are not evidence."""
    for row in rows:
        index = row.get("grid_row")
        if type(index) is not int or index < 1 or index > expected_count:
            raise BrowserError("A grid row index is outside its independent total")
        identity = key(row)
        if index in collected and key(collected[index]) != identity:
            raise BrowserError("Grid membership changed while paging")
        if any(i != index and key(old) == identity for i, old in collected.items()):
            raise BrowserError("A grid identity appears at more than one row index")
        collected[index] = row
    return collected


class GridWalker:
    def __init__(self, page, table, guard=lambda: None):
        self.page, self.table, self.guard = page, table, guard

    def rect(self):
        value = self.table.evaluate("""e=>({rect:e.getBoundingClientRect().toJSON(),
            application:e.closest('[role=application]')?.getBoundingClientRect().toJSON(),
            canvas:document.querySelector('canvas#uno-canvas')?.getBoundingClientRect().toJSON()})""")
        rect = canvas_relative_rect(value["rect"], value["application"], value["canvas"], self.page.viewport_size)
        if rect["height"] < 80 or rect["width"] < 100:
            raise BrowserError("The grid has no usable scroll area")
        return rect

    def wheel(self, amount, *, horizontal=False):
        self.guard()
        if not horizontal and not self.table.get_by_role("scrollbar", name="Vertical", exact=True).count():
            return
        rect = self.rect()
        x = min(self.page.viewport_size["width"] - 30, rect["x"] + rect["width"] * .55)
        y = min(self.page.viewport_size["height"] - 60, rect["y"] + rect["height"] * .55)
        if not 0 < x < self.page.viewport_size["width"] or not 0 < y < self.page.viewport_size["height"]:
            raise BrowserError("Grid scroll point is outside the viewport")
        hit = self.page.evaluate("""p=>{const e=document.elementFromPoint(p.x,p.y);
            return e?{tag:e.tagName,id:e.id}:null} """, {"x": x, "y": y})
        if hit != {"tag": "CANVAS", "id": "uno-canvas"}:
            raise BrowserError("An overlay covers the grid scroll area")
        self.page.mouse.move(x, y)
        self.page.mouse.wheel(amount if horizontal else 0, 0 if horizontal else amount)
        self.page.wait_for_timeout(400)

    def collect(self, read, key, expected_count):
        if type(expected_count) is not int or expected_count < 0:
            raise BrowserError("An independent grid total is required for pagination")
        if expected_count == 0:
            if read():
                raise BrowserError("Rows are present in a grid whose observed total is zero")
            return []
        self.wheel(-1_000_000)
        seen, previous, stalled = {}, None, 0
        for _ in range(max(10, expected_count * 2 + 5)):
            self.guard()
            rows = read()
            signature = tuple(key(r) for r in rows)
            if len(set(signature)) != len(signature):
                raise BrowserError("A rendered page contains duplicate identities")
            if previous and signature and not set(previous) & set(signature):
                raise BrowserError("Consecutive grid pages have no verified overlap")
            for row in rows:
                identity = key(row)
                position = seen.get(identity, {}).get("absolute_row", len(seen) + 1)
                seen[identity] = {**row, "absolute_row": position}
            if len(seen) > expected_count:
                raise BrowserError("Grid has more distinct records than its independent total")
            if len(seen) == expected_count:
                return list(seen.values())
            stalled = stalled + 1 if signature == previous else 0
            if stalled >= 3:
                raise BrowserError("Grid stopped moving before all exported rows were observed")
            previous = signature
            self.wheel(max(80, self.rect()["height"] * .65))
        raise BrowserError("Grid pagination exceeded its coverage bound")

    def scan_to_end(self, read, key):
        """Read a directory whose footer excludes folders.

        Traverse overlapping native-grid windows, then confirm the terminal
        window with a full scroll-to-end. This is for folder/project directories;
        drawing verification uses collect with an independently observed total.
        """
        rows = read()
        if not self.table.get_by_role("scrollbar", name="Vertical", exact=True).count():
            if rows and {r["grid_row"] for r in rows} != set(range(1, len(rows) + 1)):
                raise BrowserError("Unscrolled directory has incomplete row indices")
            return rows
        self.wheel(-1_000_000)
        seen, previous, stable = {}, None, 0
        for _ in range(10000):
            rows = read()
            signature = tuple(key(r) for r in rows)
            if not signature or len(set(signature)) != len(signature):
                raise BrowserError("Directory has empty or ambiguous visible identities")
            if previous and not set(previous) & set(signature):
                raise BrowserError("Directory pages have no verified overlap")
            for row in rows:
                identity = key(row)
                position = seen.get(identity, {}).get("absolute_row", len(seen) + 1)
                seen[identity] = {**row, "absolute_row": position}
            stable = stable + 1 if signature == previous else 0
            if stable >= 2:
                self.wheel(1_000_000)
                if tuple(key(r) for r in read()) != signature:
                    raise BrowserError("Directory stalled before reaching its scroll end")
                return list(seen.values())
            previous = signature
            self.wheel(max(80, self.rect()["height"] * .55))
        raise BrowserError("Directory traversal exceeded its bound")

    def seek(self, read, key, wanted, index):
        """Re-resolve the actual visible row; its relative index can change."""
        self.wheel(-1_000_000)
        for _ in range(max(10, index * 2 + 5)):
            self.guard()
            rows = read()
            matches = [r for r in rows if key(r) == wanted]
            if len(matches) > 1:
                raise BrowserError("Duplicate requested identity in the visible grid")
            if matches:
                return matches[0]
            if not rows:
                raise BrowserError("Cannot seek in an empty grid")
            self.wheel(max(80, self.rect()["height"] * .55))
        raise BrowserError("The requested grid record could not be brought into view")


def read_table_rows(table):
    """Read explicit semantic cell indices without assuming offscreen values."""
    return table.evaluate("""e=>[...e.querySelectorAll('[role=row]')].map(r=>({
        row_id:r.id, visual_top:r.getBoundingClientRect().top,
        name:r.getAttribute('aria-label')||'',
        grid_row:Number(r.querySelector('[role=gridcell]')?.getAttribute('aria-rowindex')),
        cells:[...r.querySelectorAll('[role=gridcell]')].map(c=>({
            column:Number(c.getAttribute('aria-colindex')),value:c.getAttribute('aria-label')||''}))
    })).sort((a,b)=>a.visual_top-b.visual_top)""")
