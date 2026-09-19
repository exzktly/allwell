"""Every page of the app has a help topic, and every topic is a real heading.

The help drawer scrolls to a heading in ``README_WellViewer.md`` chosen from the
maps in :mod:`ui.help_topics`. Both halves of that arrangement can rot silently:

* a value that names no heading scrolls nowhere, and the drawer opens at the top
  of a 2600-line manual with no sign that anything was meant to happen;
* a page with no key falls back to its mode overview, which looks like a
  deliberate choice rather than an omission.

Datasets mode shipped in exactly the second state — seven tools, seven written
manual sections, and no keys — so opening the drawer there landed on whichever
Review tab had been visited last.

The maps are Qt-free, so this runs in the fast lane. Page rosters are imported
from the modules that define them (or read out of the source with the ``ast``
module), never re-typed here: a hand-copied roster tests itself, not the app.
"""

from __future__ import annotations

import ast

from tests._repo_scan import parse_source
import re
from pathlib import Path

from ui import help_topics

REPO_ROOT = Path(__file__).resolve().parent.parent

_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$")


def _readme() -> Path:
    """The manual, resolved from ``REPO_ROOT`` at call time so
    ``tests/test_guards_bite.py`` can point this guard at a synthetic tree."""
    return help_topics.readme_candidates(REPO_ROOT)[0]


def _headings() -> list[str]:
    text = _readme().read_text(encoding="utf-8")
    return [m.group(1) for m in (_HEADING.match(ln) for ln in text.splitlines()) if m]


def _analyze_section_titles() -> list[str]:
    """The ``self._section(parent, "<title>", …)`` titles across the whole pane.

    Read from the source rather than by constructing the form: the titles are
    the map's keys, and a new section added without a topic has to fail here.

    Every ``analyze/`` module, not ``analyze/form.py`` alone. The form is
    assembled from mixins — ``seg_params``, ``schema_section``,
    ``form_chrome`` — and a section builder that moves into one of them
    vanishes from a scan pinned to the one file, taking its title with it. That
    is not a quiet loss in either direction: the forward test stops checking a
    section that still exists, and the reverse test reports its perfectly live
    help key as naming a section that no longer exists. Extracting the Filename
    Schema section did exactly that, and the reverse test is what noticed.
    """
    titles: list[str] = []
    # Globbed from this module's own ``REPO_ROOT`` rather than through
    # ``_repo_scan.python_sources``: ``test_guards_bite`` proves this guard by
    # pointing that name at a synthetic tree, and a file list resolved from
    # ``_repo_scan``'s own root would walk the real repository instead, leaving
    # the proof measuring nothing. Parsing still goes through the shared cache
    # (``tests/test_the_fast_lane_stays_fast.py``), which is the part that
    # costs.
    for path in sorted((REPO_ROOT / "analyze").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(parse_source(path)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "_section"):
                continue
            # _section(parent, title, …) — the title is the second positional arg.
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                value = node.args[1].value
                if isinstance(value, str):
                    titles.append(value)
    return titles


def _datasets_page_names() -> list[str]:
    """The Datasets rail labels, imported from the tab's own feature roster.

    ``datasets.tab`` pulls in PySide6 and every tool controller, so the roster is
    read from source here too — this test must stay in the fast lane and must not
    depend on a Qt import succeeding.
    """
    src = (REPO_ROOT / "datasets/tab.py").read_text(encoding="utf-8")
    names: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "_Feature":
            continue
        for kw in node.keywords:
            if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                names.append(kw.value.value)
    return names


def _all_topic_values() -> dict[str, str]:
    """Every mapped heading, keyed by a label naming where it came from."""
    out: dict[str, str] = {}
    for map_name, mapping in (
        ("MODE_OVERVIEW", help_topics.MODE_OVERVIEW),
        ("SECTION_BY_PAGE", help_topics.SECTION_BY_PAGE),
        ("ANALYZE_SECTION_HELP", help_topics.ANALYZE_SECTION_HELP),
        ("DATASETS_PAGE_HELP", help_topics.DATASETS_PAGE_HELP),
    ):
        for key, value in mapping.items():
            out[f"{map_name}[{key!r}]"] = value
    return out


def test_every_topic_names_a_real_heading():
    headings = set(_headings())
    missing = {
        site: value
        for site, value in _all_topic_values().items()
        if value not in headings
    }
    assert not missing, (
        "help topics that name no heading in README_WellViewer.md — the drawer "
        f"would open unscrolled: {missing}"
    )


def test_headings_are_unique():
    """Two identical headings collapse to one anchor, so the drawer would jump
    to whichever came first regardless of which topic asked for it."""
    seen: dict[str, int] = {}
    for heading in _headings():
        seen[heading] = seen.get(heading, 0) + 1
    dupes = {h: n for h, n in seen.items() if n > 1}
    assert not dupes, f"duplicate manual headings: {dupes}"


def test_every_datasets_tool_has_a_topic():
    names = _datasets_page_names()
    assert names, "found no _Feature entries in datasets/tab.py — parser drifted"
    missing = [n for n in names if n not in help_topics.DATASETS_PAGE_HELP]
    assert not missing, (
        "Datasets tools with no entry in help_topics.DATASETS_PAGE_HELP; the "
        f"drawer falls back to the mode overview for them: {missing}"
    )


def test_every_analyze_section_has_a_topic():
    titles = _analyze_section_titles()
    assert titles, "found no _section() calls under analyze/ — parser drifted"
    missing = [t for t in titles if t not in help_topics.ANALYZE_SECTION_HELP]
    assert not missing, (
        "Analyze form sections with no entry in help_topics.ANALYZE_SECTION_HELP; "
        f"the drawer falls back to the Analyze overview for them: {missing}"
    )


def test_no_topic_keys_for_pages_that_no_longer_exist():
    """The reverse direction: a key for a page nobody can reach is a rule that
    reads as covered and is never applied. Checked for the two rosters this
    module can enumerate exactly (Review leaves are resolved at runtime and
    deliberately keep loose aliases, so they are out of scope here)."""
    stale_datasets = sorted(
        set(help_topics.DATASETS_PAGE_HELP) - set(_datasets_page_names()))
    stale_analyze = sorted(
        set(help_topics.ANALYZE_SECTION_HELP) - set(_analyze_section_titles()))
    assert not stale_datasets, f"DATASETS_PAGE_HELP keys naming no rail page: {stale_datasets}"
    assert not stale_analyze, f"ANALYZE_SECTION_HELP keys naming no form section: {stale_analyze}"


def _home_subtab_names() -> list[str]:
    """Home's sub-leaf names, read from ``features/registry.py``.

    Read from source rather than by building the registry: importing it pulls in
    Qt and every feature module, and this file stays in the fast lane. The
    descriptors are the roster the notebook enumerates, so a sub-tab added there
    without a help topic has to fail here.
    """
    src = (REPO_ROOT / "review" / "features" / "registry.py").read_text(
        encoding="utf-8")
    names: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "FeatureDescriptor":
            continue
        kw = {k.arg: k.value for k in node.keywords}
        parent, name = kw.get("parent"), kw.get("name")
        if (isinstance(parent, ast.Constant) and parent.value == "Home"
                and isinstance(name, ast.Constant)):
            names.append(name.value)
    return names


def test_every_home_subtab_has_a_topic():
    """Home is a notebook, and ``current_centre_tab`` stops at the page.

    So every one of Home's tabs resolves to the leaf ``"Home"``, and without a
    key of its own each lands at the top of the Home chapter -- which reads as
    the scroll having failed. ``_review_help_section`` asks Home which sub-tab is
    showing and prefers that key; this is what says the key exists.
    """
    names = _home_subtab_names()
    assert names, "found no Home sub-leaf descriptors — parser drifted"
    missing = [n for n in names if n not in help_topics.SECTION_BY_PAGE]
    assert not missing, (
        "Home sub-tabs with no entry in help_topics.SECTION_BY_PAGE; the drawer "
        f"opens at the top of the Home chapter for them: {missing}"
    )
