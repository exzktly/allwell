"""A deterministic description of what a window shows in phone layout.

PHONE_FORM_TRACKER.md P0.1. The migration moves *how* widgets are found and
given their phone form, and must not change what a person sees. This reduces a
window to one comparable record per visible widget — its class, name, place and
size, font, minimum height, and the state the phone forms set (a stepper's
touch mode, a row's direction, a plot's DPI) — so a before and an after can be
diffed and every difference read.

The record is keyed by a *path*, not by an address: each widget is named
``Class#objectName`` (or ``Class[n]`` for the n-th unnamed sibling of its class)
under its parent's path, so two runs of the same app give the same keys.

``snapshot`` never reads text, tooltips or anything a clock or a dataset
changes. It reads Qt state only, which is what the phone forms write.
"""

from __future__ import annotations

from PySide6.QtWidgets import QBoxLayout, QWidget

#: Bump when a field is added or removed, so a stale baseline fails with its
#: own message instead of as a wall of differences.
SCHEMA = 1


def _kind(w: QWidget):
    """The declared phone-form kind, once the registry exists (P1); ``None``
    before then, so a baseline taken before the migration compares against one
    taken after it only on the fields both have."""
    try:
        from widgets import form_registry
    except ImportError:
        return None
    reader = getattr(form_registry, "kind_of", None)
    return reader(w) if reader is not None else None


def _extra(w: QWidget) -> dict:
    """The state the phone forms set on a widget, for the widgets that have it."""
    out: dict = {}
    if hasattr(w, "isTouchMode"):
        out["touch_mode"] = bool(w.isTouchMode())
    lay = w.layout()
    if isinstance(lay, QBoxLayout):
        out["direction"] = lay.direction().name
    figure = getattr(w, "figure", None)
    if figure is not None and hasattr(figure, "get_dpi"):
        out["dpi"] = round(float(figure.get_dpi()), 3)
    if hasattr(w, "isStacked"):
        out["stacked"] = bool(w.isStacked())
    return out


def _path_segment(w: QWidget, siblings: dict[str, int]) -> str:
    cls = type(w).__name__
    name = w.objectName()
    if name:
        return f"{cls}#{name}"
    n = siblings.get(cls, 0)
    siblings[cls] = n + 1
    return f"{cls}[{n}]"


def snapshot(window: QWidget, root: QWidget | None = None) -> dict[str, dict]:
    """One record per widget under *root* (default *window*) that is visible to
    *window*, keyed by path. Geometry is relative to *window*."""
    root = window if root is None else root
    records: dict[str, dict] = {}

    def walk(w: QWidget, prefix: str) -> None:
        # Pass 1 names every child, in creation order, so an unnamed sibling's
        # index does not depend on which of its siblings are visible.
        siblings: dict[str, int] = {}
        for child in w.children():
            if not isinstance(child, QWidget) or child.isWindow():
                continue
            path = f"{prefix}/{_path_segment(child, siblings)}"
            if child.isVisibleTo(window):
                top_left = child.mapTo(window, child.rect().topLeft())
                font = child.font()
                records[path] = {
                    "x": top_left.x(), "y": top_left.y(),
                    "w": child.width(), "h": child.height(),
                    "min_h": child.minimumHeight(),
                    "font": round(font.pointSizeF(), 2) if font.pointSizeF() > 0 else font.pixelSize(),
                    "kind": _kind(child),
                    **_extra(child),
                }
            walk(child, path)

    start = type(root).__name__
    records[start] = {"w": root.width(), "h": root.height()}
    walk(root, start)
    return records


def diff(before: dict[str, dict], after: dict[str, dict], limit: int = 12) -> list[str]:
    """The first *limit* differences between two snapshots, one line each:
    a widget that appeared, one that went, and each field that changed."""
    lines: list[str] = []
    for path in sorted(before.keys() | after.keys()):
        if path not in after:
            lines.append(f"gone     {path}")
        elif path not in before:
            lines.append(f"appeared {path}")
        else:
            for field in sorted(before[path].keys() | after[path].keys()):
                a, b = before[path].get(field), after[path].get(field)
                if a != b:
                    lines.append(f"changed  {path}  {field}: {a!r} -> {b!r}")
    return lines[:limit] + ([f"... and {len(lines) - limit} more"] if len(lines) > limit else [])


# ── the scenarios ───────────────────────────────────────────────────────────

BASELINE = "tests/fixtures/phone_snapshot_baseline.json"


def fingerprint(qapp) -> dict:
    """What a snapshot's numbers depend on besides the code: the Qt version, the
    application font and the offscreen screen. A baseline taken under another
    fingerprint says nothing about this machine's geometry."""
    from PySide6 import QtCore
    rect = qapp.primaryScreen().availableGeometry()
    return {"schema": SCHEMA, "qt": QtCore.qVersion(),
            "font": qapp.font().family(), "screen": [rect.width(), rect.height()]}


def _pump(qapp, n: int = 20) -> None:
    from PySide6.QtTest import QTest
    for _ in range(n):
        qapp.processEvents()
    QTest.qWait(30)
    for _ in range(n):
        qapp.processEvents()


def _review_pages(win, qapp, take, *, first_page_only: bool) -> None:
    """Every Review section the phone shows, and every page of the ones that
    have page tabs. Review CSV and Batch Export are hidden on a phone."""
    review = win._review
    nav = review._section_nav
    for key, _label in nav.shownItems():
        row = getattr(nav, "_rows", {}).get(key)
        if row is not None and row.isHidden():
            continue
        nav.setCurrentKey(key)
        _pump(qapp)
        bar = review._page_tabs_bar_by_name.get(key)
        if bar is None:
            take(f"review/{key}")
            continue
        for i in range(1 if first_page_only else bar.count()):
            bar.setCurrentIndex(i)
            _pump(qapp)
            take(f"review/{key}/{bar.tabText(i)}")


def collect(win, qapp) -> dict[str, dict[str, dict]]:
    """Put a loaded window in phone layout and snapshot every page of every
    mode, upright; then the first page of each section and both Analyze views
    sideways; then two pages at the large text size, which restyles the window
    with every phone form taken off and put back (the bracket the migration
    moves). Returns ``{scenario: snapshot}``.

    The caller has loaded a dataset into ``win._review`` and left the window
    at the desk. Phone layout is left on."""
    shell = win._phone
    out: dict[str, dict[str, dict]] = {}

    def take(name: str) -> None:
        out[name] = snapshot(win)

    shell.desk_button.click()
    _pump(qapp, 60)

    _review_pages(win, qapp, lambda n: take(n), first_page_only=False)
    win._on_titlebar_mode_seg(2)
    _pump(qapp)
    for tool in shell._h.tool_names():
        shell._pick_tool(tool)
        _pump(qapp)
        take(f"datasets/{tool}")
    win._on_titlebar_mode_seg(1)
    _pump(qapp)
    for view in shell._h.page_views():
        shell._pick_page_view(view)
        _pump(qapp)
        take(f"analyze/{view}")

    # Sideways: this offscreen screen is square, so the shape is forced.
    real = shell.is_landscape
    shell.is_landscape = lambda: True
    try:
        shell._apply_sizes()
        _pump(qapp)
        for view in shell._h.page_views():
            shell._pick_page_view(view)
            _pump(qapp)
            take(f"sideways/analyze/{view}")
        win._on_titlebar_mode_seg(0)
        _pump(qapp)
        _review_pages(win, qapp, lambda n: take(f"sideways/{n}"), first_page_only=True)
    finally:
        shell.is_landscape = real
        shell._apply_sizes()
        _pump(qapp)

    # A text-size change restyles inside phone layout.
    before = shell.text_size()
    shell.set_text_size("large")
    _pump(qapp, 60)
    try:
        review = win._review
        for section, page in (("Home", "Overview"), ("Plotting", "Bar Plots")):
            review._section_nav.setCurrentKey(section)
            _pump(qapp)
            bar = review._page_tabs_bar_by_name[section]
            bar.setCurrentIndex(next(i for i in range(bar.count()) if bar.tabText(i) == page))
            _pump(qapp)
            take(f"large/review/{section}/{page}")
    finally:
        shell.set_text_size(before)
        _pump(qapp, 60)
    return out
