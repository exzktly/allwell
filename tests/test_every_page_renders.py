"""Every page of the full app, with data, at a desktop and at a phone's two shapes.

The slow lane's other phone tests run on Qt's default offscreen screen, an 800×800 square. This one
puts the app on a real screen — a 1640×980 desktop, a phone held upright (432×936), a phone held
sideways (936×432) — loads a dataset that carries data for the pages it covers, visits every page
(``tests/_page_tour.py``) and measures each for the defects a person finds by looking
(``tests/_layout_checks.py``). Three datasets, three screens: nine app constructions, each in a
process of its own (a full app's widgets are never reclaimed in-process, and nine in one process
slowed from seconds per page to minutes per page), three at a time, about ten minutes in all, so it
is a slow-lane file run on its own::

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

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests._layout_checks import Finding
from tests._page_tour import SHEET_SUFFIX
from tests._page_datasets import DATASETS
from tests._repo_scan import REPO_ROOT
from tests._screens import SCREENS, is_phone


#: ``(dataset, screen, page, check)`` -> why that finding is allowed. A reason says what is true of the
#: page, not that someone will get to it; an entry whose finding is gone fails
#: ``test_the_known_findings_are_all_still_found``, so this only shrinks.
KNOWN: dict[tuple[str, str, str, str], str] = {}

_NO_NANOWELLS = "the dataset has no nanowell record, so the Nanowells pages show their empty state"
_NANOWELL_PAGES = ("Distribution", "Array", "Trajectories", "Position", "Extremes", "Identity", "Statistics")
_NO_SMFISH = "the dataset carries no smFISH spot counts ('No smFISH data was generated during the processing pipeline.')"
_NO_IMAGES = "the dataset folder holds no images, so there is nothing for the Image Browser to generate from"

#: ``(dataset, screen family)`` -> the pages that are still on an empty state with that dataset, and why
#: they cannot be filled. ``screen family`` is ``"desk"`` or ``"phone"``.
EMPTY: dict[tuple[str, str], dict[str, str]] = {
    (kind, family): pages
    for family in ("desk", "phone")
    for kind, pages in (
        ("mini", {**{f"Nanowells/{name}": _NO_NANOWELLS for name in _NANOWELL_PAGES},
                  "Image Browser/Image Table": _NO_IMAGES, "Image Browser/Cell Browser": _NO_IMAGES}),
        ("nanowell", {"smFISH": _NO_SMFISH,
                      "Image Browser/Image Table": _NO_IMAGES, "Image Browser/Cell Browser": _NO_IMAGES}),
        ("images", {**{f"Nanowells/{name}": _NO_NANOWELLS for name in _NANOWELL_PAGES}, "smFISH": _NO_SMFISH}),
    )
}

CASES = [(dataset, screen) for dataset in DATASETS for screen in SCREENS]
#: Each case is a full app in a process of its own; three at a time fits a 4-CPU container.
PARALLEL_CASES = 3
CASE_TIMEOUT_S = 1500

#: The nine cases run inside the first test's fixture setup, which the suite's 90 s per-test timeout counts: that
#: killed the run before any case had finished. The bound is every wave of cases at its own limit.
pytestmark = [pytest.mark.slow,
              pytest.mark.timeout(-(-len(CASES) // PARALLEL_CASES) * CASE_TIMEOUT_S + 300)]


def _run_case(kind: str, screen: str, root: Path) -> list[dict]:
    """The case in a fresh interpreter, with the environment the suite's conftest sets. Raises with the
    child's stderr if it fails."""
    out = root / f"{kind}-{screen}.json"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", ALLWELL_DESKTOP="1",
               ALLWELL_STATE_DIR=str(root / f"{kind}-{screen}-state"),
               ALLWELL_SCRATCH_DIR=str(root / f"{kind}-{screen}-scratch"),
               PYTHONPATH=str(REPO_ROOT))
    done = subprocess.run([sys.executable, "-m", "tests._page_render_case", kind, screen, str(out)],
                          cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=CASE_TIMEOUT_S)
    if done.returncode != 0 or not out.exists():
        raise RuntimeError(f"{kind} on {screen} exited {done.returncode}:\n{done.stderr[-3000:]}")
    return json.loads(out.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def all_cases(tmp_path_factory) -> dict:
    """Every case, three processes at a time. ``(dataset, screen) -> pages``."""
    root = tmp_path_factory.mktemp("page_render")
    with ThreadPoolExecutor(max_workers=PARALLEL_CASES) as pool:
        futures = {case: pool.submit(_run_case, *case, root) for case in CASES}
        return {case: future.result() for case, future in futures.items()}


@pytest.fixture(params=CASES, ids=[f"{d}-{s}" for d, s in CASES])
def rendered(request, all_cases):
    """``((dataset, screen), {page: (findings, showing_empty_state)})`` for one case."""
    pages = all_cases[request.param]
    return request.param, {p["page"]: ([Finding(*f) for f in p["findings"]], p["empty"]) for p in pages}


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
    # A page's Controls sheet is measured over whatever its page shows, empty state included: EMPTY is about pages.
    empty = {page for page, (_f, showing_empty) in result.items() if showing_empty and not page.endswith(SHEET_SUFFIX)}
    expected = set(EMPTY.get((kind, family), {}))
    assert empty == expected, (
        f"{kind} on {screen}: pages on an empty state that are not in EMPTY: {sorted(empty - expected)}; "
        f"in EMPTY but now showing data: {sorted(expected - empty)}")
