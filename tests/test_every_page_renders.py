"""Every page of the full app, with data, at a desktop and at a phone's two shapes.

The slow lane's other phone tests run on Qt's default offscreen screen, an 800×800 square. This one
puts the app on a real screen — a 1640×980 desktop, a phone held upright (432×936), a phone held
sideways (936×432) — loads a dataset that carries data for the pages it covers, visits every page
(``tests/_page_tour.py``) and measures each for the defects a person finds by looking
(``tests/_layout_checks.py``). Three datasets, three screens: nine app constructions, about ten
minutes in one worker, so it is a slow-lane file run on its own::

    scripts/test.sh tests/test_every_page_renders.py -m ""

Set ``ALLWELL_PAGE_SHOTS`` to a directory and each page is also saved there as a PNG
(``<dataset>_<screen>_<page>.png``), which is how the contact sheets a person reviews are made.

Three things are asserted for each (dataset, screen):

1. the tour reached every page the screen offers;
2. no page shows a layout defect, except the cases in :data:`KNOWN`, each with a reason;
3. the pages that should show data do — a page left on its empty state (:data:`EMPTY`) proves
   nothing about how its content fits.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from tests._layout_checks import check_window
from tests._page_datasets import DATASETS, build
from tests._page_tour import tour
from tests._screens import SCREENS, FakeScreen, is_phone, place, pump

pytestmark = pytest.mark.slow

#: ``(dataset, screen, page, check)`` -> why that finding is allowed. A reason says what is true of the
#: page, not that someone will get to it; an entry whose finding is gone fails
#: ``test_the_known_findings_are_all_still_found``, so this only shrinks.
KNOWN: dict[tuple[str, str, str, str], str] = {}

#: ``(dataset, screen family)`` -> the pages that are still on an empty state with that dataset, and why
#: they cannot be filled. ``screen family`` is ``"desk"`` or ``"phone"``.
EMPTY: dict[tuple[str, str], dict[str, str]] = {}

CASES = [(dataset, screen) for dataset in DATASETS for screen in SCREENS]


class Rendered(dict):
    """``page -> (findings, showing_empty_state)``, in tour order."""


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")


def _showing_empty_state(win) -> bool:
    from widgets.empty_state import EmptyState

    return any(w.isVisibleTo(win) for w in win.findChildren(EmptyState))


@pytest.fixture(scope="module", params=CASES, ids=[f"{d}-{s}" for d, s in CASES])
def rendered(request, qapp, tmp_path_factory):
    from allwell import AllWellApp
    from core import phone_layout
    from ui import theme

    kind, screen = request.param
    rect, phone = SCREENS[screen], is_phone(screen)
    shots = os.environ.get("ALLWELL_PAGE_SHOTS")
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
    with FakeScreen(rect):
        data, wells = build(kind, tmp_path_factory.mktemp(f"pages_{kind}"))
        win = AllWellApp(None)
        win.show()
        pump(qapp)
        win._review._load_path(data)
        pump(qapp, 60, 400)
        win._review._set_selected_wells(set(wells), commit=True)
        pump(qapp, 40, 400)
        shell = win._phone
        if phone:
            shell.desk_button.click()
            pump(qapp, 60, 300)
        place(win, rect, qapp)
        result = Rendered()
        try:
            for page in tour(win, qapp, kind=kind, wells=wells, phone=phone):
                pump(qapp, 20, 450)
                if phone:
                    shell._sweep(force=False)
                    pump(qapp, 10, 100)
                result[page] = (check_window(win, shell if phone else None), _showing_empty_state(win))
                if shots:
                    win.grab().save(str(Path(shots) / f"{kind}_{screen}_{_slug(page)}.png"))
            yield request.param, result
        finally:
            phone_layout.set_on(False)
            theme.apply_ui_scale(1.0)
            win.close()


def test_the_tour_reaches_every_page_the_screen_offers(rendered):
    (kind, screen), result = rendered
    from review.features import registry

    offered = [d.name for d in registry.build_feature_registry().top_level()
               if not is_phone(screen) or d.on_phone]
    visited = {page.split("/")[0] for page in result}
    # The Datasets tools and Analyze's views are toured once, with the mini dataset.
    missing = [name for name in offered if name not in visited]
    assert not missing, f"{kind} on {screen}: the tour never reached {missing}"
    if kind == "mini":
        assert any(p.startswith("datasets/") for p in result) and any(p.startswith("analyze/") for p in result)


def test_no_page_has_a_layout_defect_beyond_the_known_ones(rendered):
    (kind, screen), result = rendered
    unexpected = [f"{page}: {finding}" for page, (findings, _empty) in result.items() for finding in findings
                  if (kind, screen, page, finding.check) not in KNOWN]
    assert not unexpected, f"{kind} on {screen}: {len(unexpected)} layout defect(s):\n  " + "\n  ".join(unexpected)


def test_the_known_findings_are_all_still_found(rendered):
    (kind, screen), result = rendered
    found = {(kind, screen, page, finding.check) for page, (findings, _e) in result.items() for finding in findings}
    stale = [key for key in KNOWN if key[:2] == (kind, screen) and key not in found]
    assert not stale, f"these KNOWN cases no longer fire — delete them: {stale}"


def test_the_pages_that_should_show_data_do(rendered):
    (kind, screen), result = rendered
    family = "phone" if is_phone(screen) else "desk"
    empty = {page for page, (_f, showing_empty) in result.items() if showing_empty}
    expected = set(EMPTY.get((kind, family), {}))
    assert empty == expected, (
        f"{kind} on {screen}: pages on an empty state that are not in EMPTY: {sorted(empty - expected)}; "
        f"in EMPTY but now showing data: {sorted(expected - empty)}")
